"""
Auto top-up of a supplier's API balance.

Flow (only used when the supplier's API balance is lower than the product cost):
  1. bot withdraws USDT from Binance to THAT supplier's wallet  (binance_withdraw.py)
  2. bot gets the TXID (hash)
  3. Telethon sends the hash to THAT supplier's Telegram bot (the bot where the payment was made)
  4. wait until the supplier's API balance shows the money
  5. provider.py then buys from the API as usual

If the API balance is already enough, none of this runs (direct API purchase).

Safety: wallet addresses come ONLY from the admin site (password re-entered, table topup_wallets) or SUPPLIER_PAY below, a daily cap, a DB table `topups`
that remembers every step (a withdrawal is never repeated, a hash is never sent twice).
"""
import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_UP

import binance_withdraw

log = logging.getLogger("topup")

# ----------------------------------------------------------------- CONFIG
# Key = supplier name exactly as on the admin site "Suppliers API" (any case) or the supplier id (int).
SUPPLIER_PAY = {
    "PASTE_SUPPLIER_NAME": {
        "address": "PASTE_SUPPLIER_WALLET_ADDRESS",   # supplier's deposit wallet
        "network": "BSC",                             # BSC / TRX / ETH ... must match the address
        "bot": "@PASTE_SUPPLIER_BOT",                 # Telegram bot where the hash is sent
        "hash_msg": "{hash}",                         # exact text sent, e.g. "/deposit {hash}"
        "min_topup": 5.0,                             # never withdraw less than this
        "fee_buffer": 0.5,                            # extra to cover the Binance withdrawal fee
    },
}
DAILY_CAP = 100.0                                     # max USDT this module may withdraw per UTC day
_TG = {"TG_API_ID": "PASTE_API_ID", "TG_API_HASH": "PASTE_API_HASH", "TG_SESSION": "PASTE_STRING_SESSION"}
CREDIT_TRIES, CREDIT_DELAY = 18, 10                   # wait up to ~3 min for the balance to show


class TopupError(RuntimeError):
    """Top-up could not finish. Nothing was bought from the supplier, so another supplier is safe."""


def _tg(name):
    return (os.getenv(name) or _TG[name]).strip()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _pay_cfg(name, sid, db=None):
    """Wallet config for a supplier: saved on the admin website first (table topup_wallets), else SUPPLIER_PAY."""
    if db is not None:
        w = _db_wallet(db, sid)
        if w:
            return w
    for k, v in SUPPLIER_PAY.items():
        if (isinstance(k, int) and k == sid) or (isinstance(k, str) and k.strip().lower() == str(name).strip().lower()):
            if not any(str(v.get(x, "")).startswith("PASTE_") or str(v.get(x, "")).startswith("@PASTE_")
                       for x in ("address", "bot")):
                return v
    return None


_ADDR_RE = {"BSC": r"0x[0-9a-fA-F]{40}", "ETH": r"0x[0-9a-fA-F]{40}", "TRX": r"T[1-9A-HJ-NP-Za-km-z]{33}"}
NETWORKS = tuple(_ADDR_RE)


def check_wallet(address, network, bot, hash_msg):
    """Validate a wallet entered on the website. Returns cleaned dict or raises ValueError."""
    import re
    address, network, bot, hash_msg = (address or "").strip(), (network or "").strip().upper(), (bot or "").strip(), (hash_msg or "").strip()
    if network not in _ADDR_RE:
        raise ValueError("Network must be one of: " + ", ".join(NETWORKS))
    if not re.fullmatch(_ADDR_RE[network], address):
        raise ValueError(f"This is not a valid {network} address.")
    if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", bot):
        raise ValueError("Bot must look like @supplier_bot")
    if "{hash}" not in hash_msg or len(hash_msg) > 100 or "{" in hash_msg.replace("{hash}", "") or "}" in hash_msg.replace("{hash}", ""):
        raise ValueError("Hash message must contain {hash}, e.g. /deposit {hash}")
    return {"address": address, "network": network, "bot": bot, "hash_msg": hash_msg}


def _wallets_table(db):
    c = db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS topup_wallets(
        supplier_id INTEGER PRIMARY KEY, address TEXT, network TEXT, bot TEXT, hash_msg TEXT, updated TEXT)""")
    c.commit()
    c.close()


def _db_wallet(db, sid):
    try:
        _wallets_table(db)
        c = db.conn()
        r = c.execute("SELECT address, network, bot, hash_msg FROM topup_wallets WHERE supplier_id=?", (sid,)).fetchone()
        c.close()
        if not r:
            return None
        return {"address": r[0], "network": r[1], "bot": r[2], "hash_msg": r[3], "min_topup": 5.0, "fee_buffer": 0.5}
    except Exception:
        log.warning("topup_wallets read failed", exc_info=True)
        return None


def save_wallet(db, sid, w):
    _wallets_table(db)
    c = db.conn()
    c.execute("INSERT INTO topup_wallets(supplier_id,address,network,bot,hash_msg,updated) VALUES(?,?,?,?,?,?) "
              "ON CONFLICT(supplier_id) DO UPDATE SET address=excluded.address, network=excluded.network, "
              "bot=excluded.bot, hash_msg=excluded.hash_msg, updated=excluded.updated",
              (sid, w["address"], w["network"], w["bot"], w["hash_msg"], _now()))
    c.commit()
    c.close()
    log.warning("top-up wallet saved for supplier %s: %s...%s (%s)", sid, w["address"][:6], w["address"][-6:], w["network"])


def delete_wallet(db, sid):
    _wallets_table(db)
    c = db.conn()
    c.execute("DELETE FROM topup_wallets WHERE supplier_id=?", (sid,))
    c.commit()
    c.close()


def _overrides(db, sid):
    """min_topup / fee_buffer saved from the admin website (table topup_cfg). Wallet/bot never come from here."""
    try:
        c = db.conn()
        c.execute("CREATE TABLE IF NOT EXISTS topup_cfg(supplier_id INTEGER PRIMARY KEY, min_topup REAL, fee_buffer REAL)")
        r = c.execute("SELECT min_topup, fee_buffer FROM topup_cfg WHERE supplier_id=?", (sid,)).fetchone()
        c.close()
        return {"min_topup": float(r[0]), "fee_buffer": float(r[1])} if r else {}
    except Exception:
        log.warning("topup_cfg read failed", exc_info=True)
        return {}


def has_config(name, sid, db=None):
    return _pay_cfg(name, sid, db) is not None


# ----------------------------------------------------------------- Telethon (own thread + loop)
_lk = threading.Lock()
_loop = None
_client = None


def _start():
    global _loop, _client
    with _lk:
        if _client:
            return
        if any(_tg(k).startswith("PASTE_") for k in _TG):
            raise TopupError("Telethon not configured (TG_API_ID / TG_API_HASH / TG_SESSION)")
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True, name="telethon").start()

        async def boot():
            c = TelegramClient(StringSession(_tg("TG_SESSION")), int(_tg("TG_API_ID")), _tg("TG_API_HASH"))
            await c.connect()
            if not await c.is_user_authorized():
                raise TopupError("Telethon session is not logged in. Run make_session.py again.")
            return c
        try:
            _client = asyncio.run_coroutine_threadsafe(boot(), loop).result(45)
        except TopupError:
            raise
        except Exception as e:
            raise TopupError(f"Telethon start failed: {e}")
        _loop = loop


def send_to_bot(bot, text, wait=60):
    """Send `text` to the supplier bot and return its first reply text (None if no reply in time)."""
    _start()

    async def go():
        async with _client.conversation(bot, timeout=wait) as conv:
            await conv.send_message(text)
            try:
                r = await conv.get_response()
                return r.raw_text
            except asyncio.TimeoutError:
                return None
    try:
        return asyncio.run_coroutine_threadsafe(go(), _loop).result(wait + 20)
    except Exception as e:
        raise TopupError(f"could not message {bot}: {e}")


# ----------------------------------------------------------------- state table
def _table(db):
    c = db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS topups(
        id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id INTEGER, amount REAL,
        wid TEXT, txid TEXT UNIQUE, status TEXT, error TEXT, created TEXT, updated TEXT)""")
    c.commit()
    c.close()


def _set(db, tid, status, **cols):
    c = db.conn()
    keys = ["status", "updated", *cols]
    c.execute(f"UPDATE topups SET {', '.join(k + '=?' for k in keys)} WHERE id=?",
              (status, _now(), *cols.values(), tid))
    c.commit()
    c.close()


def _open_row(db, sid):
    c = db.conn()
    r = c.execute("SELECT * FROM topups WHERE supplier_id=? AND status IN "
                  "('withdrawing','withdrawn','hash_ready','sent') ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    c.close()
    return dict(r) if r else None


def _spent_today(db):
    c = db.conn()
    r = c.execute("SELECT COALESCE(SUM(amount),0) FROM topups WHERE created>=? AND status!='failed'",
                  (datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00"),)).fetchone()
    c.close()
    return float(r[0])


def _wait_balance(get_balance, s, need):
    for _ in range(CREDIT_TRIES):
        try:
            if get_balance(s) + 1e-9 >= need:
                return True
        except Exception:
            log.warning("balance poll failed", exc_info=True)
        time.sleep(CREDIT_DELAY)
    return False


# ----------------------------------------------------------------- main entry
def ensure_funds(db, s, need, get_balance):
    """Make sure supplier `s` has >= `need` USDT on its API. Blocking (can take a few minutes)."""
    cfg = _pay_cfg(s.get("name"), s["id"], db)
    if not cfg:
        raise TopupError("no SUPPLIER_PAY config for this supplier")
    cfg = {**cfg, **_overrides(db, s["id"])}
    if not binance_withdraw.keys_ready():
        raise TopupError("Binance withdraw key/secret not filled")
    _table(db)
    sid = s["id"]
    bal = float(get_balance(s))
    if bal + 1e-9 >= need:
        c = db.conn()                                   # money already arrived: close old 'sent' rows
        c.execute("UPDATE topups SET status='credited', updated=? WHERE supplier_id=? AND status='sent'",
                  (_now(), sid))
        c.commit()
        c.close()
        return bal

    row = _open_row(db, sid)
    if row and row["status"] == "withdrawing":
        raise TopupError(f"top-up #{row['id']} is unresolved (withdrawal may have left Binance). "
                         "Check Binance history, then set its status in table topups.")
    if not row:
        amount = max(Decimal(str(need - bal + cfg.get("fee_buffer", 0.5))), Decimal(str(cfg.get("min_topup", 5))))
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_UP)
        if _spent_today(db) + float(amount) > DAILY_CAP:
            raise TopupError(f"daily top-up cap {DAILY_CAP} reached")
        c = db.conn()
        cur = c.execute("INSERT INTO topups(supplier_id,amount,status,created,updated) VALUES(?,?,?,?,?)",
                        (sid, float(amount), "withdrawing", _now(), _now()))
        tid = cur.lastrowid
        c.commit()
        c.close()
        try:
            wid = binance_withdraw.withdraw(amount, cfg["address"], cfg.get("network"))
        except ValueError as e:                         # rejected locally (cap), nothing left Binance
            _set(db, tid, "failed", error=str(e))
            raise TopupError(str(e))
        except RuntimeError as e:
            msg = str(e)
            if msg.startswith("Binance HTTP 4") or "451" in msg:   # Binance refused: nothing sent
                _set(db, tid, "failed", error=msg[:300])
            raise TopupError(msg)
        _set(db, tid, "withdrawn", wid=str(wid))
        row = {"id": tid, "status": "withdrawn", "wid": str(wid), "txid": None}
        log.info("top-up #%s: withdrew %s to supplier %s (wid %s)", tid, amount, sid, wid)

    tid = row["id"]
    if row["status"] == "withdrawn":
        try:
            txid = binance_withdraw.wait_txid(row["wid"])
        except RuntimeError as e:                       # cancelled / rejected by Binance
            _set(db, tid, "failed", error=str(e)[:300])
            raise TopupError(str(e))
        if not txid:
            raise TopupError(f"top-up #{tid}: hash not ready yet, will resume on the next order")
        _set(db, tid, "hash_ready", txid=txid)
        row["status"], row["txid"] = "hash_ready", txid

    if row["status"] == "hash_ready":
        reply = send_to_bot(cfg["bot"], cfg["hash_msg"].format(hash=row["txid"]))
        _set(db, tid, "sent")                           # sent exactly once
        log.info("top-up #%s: hash sent to %s, reply: %r", tid, cfg["bot"], (reply or "")[:120])

    if _wait_balance(get_balance, s, need):
        _set(db, tid, "credited")
        return float(get_balance(s))
    raise TopupError(f"top-up #{tid}: hash sent but balance not credited yet (will check again next order)")
