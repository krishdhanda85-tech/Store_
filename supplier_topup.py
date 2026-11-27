"""
Auto top-up of a supplier's API balance  (v3 - advanced).

Flow (runs when the supplier's API balance is lower than needed):
  1. bot withdraws USDT from Binance to THAT supplier's wallet  (binance_withdraw.py)
  2. bot gets the TXID (hash)
  3. Telethon sends the hash to THAT supplier's Telegram bot
  4. the bot's reply is checked (ok / error / silent); on an error reply or silence the hash is re-sent
     a few times (blockchain confirmations are often the reason)
  5. wait until the supplier's API balance shows the money, then provider.py buys as usual

What is new in v2
  * Proactive refill   - a background watcher tops a supplier up when its balance falls below `low_balance`
                         (up to `target_balance`), so customers never wait for a top-up during an order.
  * Race safety        - one top-up per supplier at a time (thread lock + DB lock, works across processes).
  * Crash safety       - every withdrawal gets a Binance order id; a row stuck on 'withdrawing' is
                         reconciled automatically against Binance history (found -> continues, absent -> failed).
  * Limits             - global daily cap + per-supplier daily cap; Binance balance is checked before withdrawing.
  * Telegram alerts    - to OWNER_ID via BOT_TOKEN (or ALERT_CHAT_ID): started, credited, failed, stuck, low Binance.
  * Reply verification - supplier bot's reply is saved in the `topups` table and judged ok / fail / unknown.
  * Telethon           - auto-reconnect if the connection dropped.

What is new in v3
  * Emergency pause    - one click on the admin site stops all NEW withdrawals (orders + auto-refill).
  * Wallet change lock - if a supplier's saved address is CHANGED, top-ups to it stay locked for
                         TOPUP_WALLET_COOLDOWN_H hours (default 6): protects against a hijacked admin login.
  * Velocity limit     - at most MAX_TOPUPS_PER_HOUR top-ups per supplier per hour (stops runaway loops).
  * Dry run            - TOPUP_DRY_RUN=1: everything runs except the real withdrawal.
  * Backoff            - auto-refill that keeps failing waits 5, 10, 20, 40, 60 min instead of retrying blindly.
  * Binance low watch  - alert when the Binance USDT balance falls below BINANCE_LOW_ALERT.
  * Balance tracking   - balance before / after each top-up is stored; stats per supplier (today, 7d, 30d,
                         success rate, average time to credit); audit log of every admin action.
  * Health check       - health(db) verifies API-key permissions (withdraw ON, IP restricted, no trading),
                         Binance balance, Telethon session, alerts and each supplier's bot.
  * More networks      - MATIC, ARBITRUM, OPTIMISM, BASE, AVAXC besides BSC / ETH / TRX.
  * Telethon FloodWait is waited out automatically (once).

Two wallet modes (set per supplier on the admin site)
  fixed   - supplier has ONE deposit wallet; you save it once (old behaviour).
  invoice - supplier's bot gives a NEW address + exact amount every time (e.g. FatBunny Hub). Telethon does
            Wallet -> "Top up USDT (BEP20)" -> sends the amount -> reads address + exact amount from the reply;
            the bot then withdraws exact amount + Binance fee to that address and sends the TXID.

Safety (unchanged): wallet addresses come ONLY from the admin site (password re-entered, table topup_wallets)
or SUPPLIER_PAY below; a withdrawal is never repeated; a hash is only re-sent when the bot answered with an
error or stayed silent AND the balance has still not arrived.
"""
import asyncio
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
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
        "fee_buffer": 0.5,                            # extra to cover the Binance withdrawal fee (fixed mode)
        # invoice mode instead of a fixed wallet:  "mode": "invoice", "menu_text": "Wallet", "net_button": "BEP20"
    },
}
ADD_BINANCE_FEE = False                              # invoice mode: False = withdraw exactly the invoice amount
INVOICE_TOLERANCE = 1.0                               # invoice amount may be at most this much above what we asked
DAILY_CAP = 100.0                                     # max USDT this module may withdraw per UTC day (all suppliers)
SUPPLIER_DAILY_CAP = 60.0                             # default max per supplier per UTC day (override on admin site)
_TG = {"TG_API_ID": "PASTE_API_ID", "TG_API_HASH": "PASTE_API_HASH", "TG_SESSION": "PASTE_STRING_SESSION"}
CREDIT_TRIES, CREDIT_DELAY = 18, 10                   # wait up to ~3 min for the balance to show
HASH_RETRIES, HASH_RETRY_DELAY = 3, 45                # re-send the hash (only on error/silence) up to 3 times
STUCK_AFTER = 600                                     # seconds before a 'withdrawing' row with no Binance record is failed
LOCK_TTL = 900                                        # DB lock expires after 15 min (crash protection)
WATCH_INTERVAL = 300                                  # proactive refill check every 5 min
ALERT_COOLDOWN = 3600                                 # same alert at most once per hour
MAX_TOPUPS_PER_HOUR = 4                               # per supplier, failed ones not counted
WALLET_COOLDOWN_H = 6.0                               # lock after a saved address CHANGED (env TOPUP_WALLET_COOLDOWN_H, 0 = off)
BINANCE_LOW_ALERT = 30.0                              # alert when the Binance balance is below this (USDT)
BACKOFF_BASE, BACKOFF_MAX = 300, 3600                 # auto-refill retry delay after failures: 5, 10, 20, 40, 60 min

# words in the supplier bot's reply (lower-case, substring match)
OK_WORDS = ("success", "credited", "received", "added", "approved", "confirmed", "completed", "topped")
FAIL_WORDS = ("invalid", "not found", "error", "fail", "wrong", "unknown", "pending", "unconfirmed",
              "not yet", "try again", "wait", "no such", "not detected")


class TopupError(RuntimeError):
    """Top-up could not finish. Nothing was bought from the supplier, so another supplier is safe."""


def _tg(name):
    return (os.getenv(name) or _TG[name]).strip()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _age(ts):
    try:
        return (datetime.now(timezone.utc) - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)).total_seconds()
    except Exception:
        return 0.0


# ----------------------------------------------------------------- alerts
_last_alert = {}


def notify(text, key=None):
    """Telegram message to the owner (BOT_TOKEN + OWNER_ID, or ALERT_CHAT_ID). Never raises, never blocks."""
    tok = os.getenv("BOT_TOKEN", "").strip()
    chat = (os.getenv("ALERT_CHAT_ID") or os.getenv("OWNER_ID") or "").strip()
    if not tok or not chat:
        return
    if key:
        if time.time() - _last_alert.get(key, 0) < ALERT_COOLDOWN:
            return
        _last_alert[key] = time.time()

    def send():
        try:
            import requests
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": "💸 Top-up\n" + text[:3500]}, timeout=10)
        except Exception:
            log.warning("top-up alert could not be sent", exc_info=True)
    threading.Thread(target=send, daemon=True).start()


# ----------------------------------------------------------------- wallet config
def _pay_cfg(name, sid, db=None):
    """Wallet config for a supplier: saved on the admin website first (table topup_wallets), else SUPPLIER_PAY."""
    if db is not None:
        w = _db_wallet(db, sid)
        if w:
            return w
    for k, v in SUPPLIER_PAY.items():
        if (isinstance(k, int) and k == sid) or (isinstance(k, str) and k.strip().lower() == str(name).strip().lower()):
            need_keys = ("bot",) if v.get("mode") == "invoice" else ("address", "bot")
            if not any(str(v.get(x, "")).startswith("PASTE_") or str(v.get(x, "")).startswith("@PASTE_")
                       for x in need_keys):
                return v
    return None


_EVM = r"0x[0-9a-fA-F]{40}"
_ADDR_RE = {"BSC": _EVM, "ETH": _EVM, "TRX": r"T[1-9A-HJ-NP-Za-km-z]{33}",
            "MATIC": _EVM, "ARBITRUM": _EVM, "OPTIMISM": _EVM, "BASE": _EVM, "AVAXC": _EVM}
NETWORKS = tuple(_ADDR_RE)


def check_wallet(address, network, bot, hash_msg, mode="fixed", menu_text="", net_button=""):
    """Validate a wallet entered on the website. Returns cleaned dict or raises ValueError.
    In invoice mode the address is optional: if given, it is a PIN (the supplier's invoice must use exactly it)."""
    address, network, bot, hash_msg = (address or "").strip(), (network or "").strip().upper(), (bot or "").strip(), (hash_msg or "").strip()
    mode, menu_text, net_button = (mode or "fixed").strip().lower(), (menu_text or "").strip(), (net_button or "").strip()
    if mode not in ("fixed", "invoice"):
        raise ValueError("Mode must be fixed or invoice.")
    if network not in _ADDR_RE:
        raise ValueError("Network must be one of: " + ", ".join(NETWORKS))
    if (mode == "fixed" or address) and not re.fullmatch(_ADDR_RE[network], address):
        raise ValueError(f"This is not a valid {network} address.")
    if mode == "invoice" and not (1 <= len(menu_text) <= 40 and 1 <= len(net_button) <= 40):
        raise ValueError("Invoice mode needs the menu text (e.g. Wallet) and the network button text (e.g. BEP20).")
    if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", bot):
        raise ValueError("Bot must look like @supplier_bot")
    if "{hash}" not in hash_msg or len(hash_msg) > 100 or "{" in hash_msg.replace("{hash}", "") or "}" in hash_msg.replace("{hash}", ""):
        raise ValueError("Hash message must contain {hash}, e.g. /deposit {hash}")
    return {"address": address, "network": network, "bot": bot, "hash_msg": hash_msg,
            "mode": mode, "menu_text": menu_text, "net_button": net_button}


def _wallets_table(db):
    c = db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS topup_wallets(
        supplier_id INTEGER PRIMARY KEY, address TEXT, network TEXT, bot TEXT, hash_msg TEXT, updated TEXT)""")
    have = {r[1] for r in c.execute("PRAGMA table_info(topup_wallets)").fetchall()}
    for col in ("mode", "menu_text", "net_button", "locked_until"):
        if col not in have:
            c.execute(f"ALTER TABLE topup_wallets ADD COLUMN {col} TEXT")
    c.commit()
    c.close()


def _db_wallet(db, sid):
    try:
        _wallets_table(db)
        c = db.conn()
        r = c.execute("SELECT address, network, bot, hash_msg, mode, menu_text, net_button, locked_until "
                      "FROM topup_wallets WHERE supplier_id=?", (sid,)).fetchone()
        c.close()
        if not r:
            return None
        return {"address": r[0] or "", "network": r[1], "bot": r[2], "hash_msg": r[3], "mode": r[4] or "fixed",
                "menu_text": r[5] or "", "net_button": r[6] or "", "locked_until": r[7] or "",
                "min_topup": 5.0, "fee_buffer": 0.5}
    except Exception:
        log.warning("topup_wallets read failed", exc_info=True)
        return None


def save_wallet(db, sid, w):
    _wallets_table(db)
    old = _db_wallet(db, sid)
    changed = bool(old and old["address"] and old["address"].lower() != (w["address"] or "").lower())
    try:
        hrs = float(os.getenv("TOPUP_WALLET_COOLDOWN_H", WALLET_COOLDOWN_H))
    except ValueError:
        hrs = WALLET_COOLDOWN_H
    locked = (datetime.now(timezone.utc) + timedelta(hours=hrs)).strftime("%Y-%m-%d %H:%M:%S") if changed and hrs > 0 else None
    c = db.conn()
    c.execute("INSERT INTO topup_wallets(supplier_id,address,network,bot,hash_msg,updated,mode,menu_text,net_button,locked_until) "
              "VALUES(?,?,?,?,?,?,?,?,?,?) "
              "ON CONFLICT(supplier_id) DO UPDATE SET address=excluded.address, network=excluded.network, "
              "bot=excluded.bot, hash_msg=excluded.hash_msg, updated=excluded.updated, mode=excluded.mode, "
              "menu_text=excluded.menu_text, net_button=excluded.net_button, "
              "locked_until=CASE WHEN excluded.locked_until IS NOT NULL THEN excluded.locked_until ELSE topup_wallets.locked_until END",
              (sid, w["address"], w["network"], w["bot"], w["hash_msg"], _now(),
               w.get("mode", "fixed"), w.get("menu_text", ""), w.get("net_button", ""), locked))
    c.commit()
    c.close()
    a = w["address"]
    short = (a[:6] + "..." + a[-6:]) if a else "invoice mode"
    log.warning("top-up wallet saved for supplier %s: %s (%s, %s)", sid, short, w["network"], w.get("mode", "fixed"))
    notify(f"{'⚠️ ADDRESS CHANGED' if changed else 'Wallet saved'} for supplier {sid}: {short} "
           f"({w['network']}, {w.get('mode', 'fixed')})" + (f"\nTop-ups to it are locked until {locked} UTC." if locked else ""))


def delete_wallet(db, sid):
    _wallets_table(db)
    c = db.conn()
    c.execute("DELETE FROM topup_wallets WHERE supplier_id=?", (sid,))
    c.commit()
    c.close()


# ----------------------------------------------------------------- per-supplier settings (topup_cfg)
_CFG_COLS = {"low_balance": "REAL", "target_balance": "REAL", "daily_cap": "REAL", "auto": "INTEGER"}


def _cfg_table(db):
    c = db.conn()
    c.execute("CREATE TABLE IF NOT EXISTS topup_cfg(supplier_id INTEGER PRIMARY KEY, min_topup REAL, fee_buffer REAL)")
    have = {r[1] for r in c.execute("PRAGMA table_info(topup_cfg)").fetchall()}
    for col, typ in _CFG_COLS.items():
        if col not in have:
            c.execute(f"ALTER TABLE topup_cfg ADD COLUMN {col} {typ}")
    c.commit()
    c.close()


def _overrides(db, sid):
    """Settings saved from the admin website (min/fee/low/target/daily cap/auto). Wallet/bot never come from here."""
    try:
        _cfg_table(db)
        c = db.conn()
        r = c.execute("SELECT min_topup, fee_buffer, low_balance, target_balance, daily_cap, auto "
                      "FROM topup_cfg WHERE supplier_id=?", (sid,)).fetchone()
        c.close()
        if not r:
            return {}
        out = {}
        for k, v in zip(("min_topup", "fee_buffer", "low_balance", "target_balance", "daily_cap", "auto"), r):
            if v is not None:
                out[k] = int(v) if k == "auto" else float(v)
        return out
    except Exception:
        log.warning("topup_cfg read failed", exc_info=True)
        return {}


def save_settings(db, sid, **v):
    """Save settings from the admin site. Missing keys keep their old value."""
    _cfg_table(db)
    old = _overrides(db, sid)
    new = {"min_topup": 5.0, "fee_buffer": 0.5, **old, **{k: x for k, x in v.items() if x is not None}}
    c = db.conn()
    c.execute("INSERT INTO topup_cfg(supplier_id,min_topup,fee_buffer,low_balance,target_balance,daily_cap,auto) "
              "VALUES(?,?,?,?,?,?,?) ON CONFLICT(supplier_id) DO UPDATE SET min_topup=excluded.min_topup, "
              "fee_buffer=excluded.fee_buffer, low_balance=excluded.low_balance, target_balance=excluded.target_balance, "
              "daily_cap=excluded.daily_cap, auto=excluded.auto",
              (sid, new["min_topup"], new["fee_buffer"], new.get("low_balance"), new.get("target_balance"),
               new.get("daily_cap"), int(new.get("auto") or 0)))
    c.commit()
    c.close()


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
            c = TelegramClient(StringSession(_tg("TG_SESSION")), int(_tg("TG_API_ID")), _tg("TG_API_HASH"),
                               connection_retries=5, auto_reconnect=True)
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
        if not _client.is_connected():                  # dropped connection: reconnect
            await _client.connect()
        async with _client.conversation(bot, timeout=wait) as conv:
            await conv.send_message(text)
            try:
                r = await conv.get_response()
                return r.raw_text
            except asyncio.TimeoutError:
                return None
    for attempt in (1, 2):
        try:
            return asyncio.run_coroutine_threadsafe(go(), _loop).result(wait + 20)
        except Exception as e:
            secs = getattr(e, "seconds", None)
            if attempt == 1 and type(e).__name__ == "FloodWaitError" and isinstance(secs, int) and secs <= 60:
                log.warning("Telegram FloodWait %ss, waiting", secs)
                time.sleep(secs + 1)
                continue
            raise TopupError(f"could not message {bot}: {e}")


def bot_reachable(bot):
    """True if Telegram knows this @username and it is a bot (health check). Sends nothing."""
    _start()

    async def go():
        if not _client.is_connected():
            await _client.connect()
        e = await _client.get_entity(bot)
        return bool(getattr(e, "bot", False))
    return asyncio.run_coroutine_threadsafe(go(), _loop).result(25)


def _flat_buttons(msg):
    return [b for row in (msg.buttons or []) for b in row]


_AMT_RE = re.compile(r"exactly\s+([0-9]+(?:[.,][0-9]+)?)", re.I)


def fetch_invoice(cfg, amount, wait=45):
    """Invoice mode, steps 1-3 on the supplier bot: open the wallet menu, press the network button, send the amount.
    Returns {"address", "amount": Decimal exact amount to receive, "text"}. Moves NO money."""
    _start()
    net = cfg["network"]
    needle = (cfg.get("net_button") or net).lower()

    async def go():
        if not _client.is_connected():
            await _client.connect()
        async with _client.conversation(cfg["bot"], timeout=wait, total_timeout=wait * 8) as conv:
            await conv.send_message(cfg.get("menu_text") or "Wallet")
            btn = None
            for _ in range(3):                           # the wallet message carries the inline buttons
                r = await conv.get_response()
                btn = next((b for b in _flat_buttons(r) if needle in (b.text or "").lower()), None)
                if btn:
                    break
            if not btn:
                raise TopupError(f"button with '{needle}' not found in {cfg['bot']} wallet menu")
            await btn.click()
            await conv.get_response()                    # "Enter USDT amount ..."
            await conv.send_message(format(Decimal(str(amount)).quantize(Decimal("0.01")), "f").rstrip("0").rstrip("."))
            for _ in range(4):                           # invoice message (QR + caption with address / amount)
                t = (await conv.get_response()).raw_text or ""
                if re.search(_ADDR_RE[net], t):
                    return t
            raise TopupError("supplier bot did not send an address")
    try:
        text = asyncio.run_coroutine_threadsafe(go(), _loop).result(wait * 8 + 20)
    except TopupError:
        raise
    except Exception as e:
        raise TopupError(f"invoice request to {cfg['bot']} failed: {e}")
    m = re.search(_ADDR_RE[net], text)
    a = _AMT_RE.search(text)
    if not m or not a:
        raise TopupError("could not read address/amount from the supplier's message")
    return {"address": m.group(0), "amount": Decimal(a.group(1).replace(",", ".")), "text": text}


def test_invoice(db, s):
    """Admin 'test' button: runs the invoice steps with the minimum amount and shows what the bot returned. No money moves."""
    cfg = _pay_cfg(s.get("name"), s["id"], db)
    if not cfg or cfg.get("mode") != "invoice":
        raise TopupError("this supplier is not in invoice mode")
    cfg = {**cfg, **_overrides(db, s["id"])}
    return fetch_invoice(cfg, cfg.get("min_topup", 5))


def judge_reply(reply):
    """'ok' (supplier confirms), 'fail' (error / not confirmed yet / silent) or 'unknown' (can't tell)."""
    if not reply:
        return "fail"
    t = reply.lower()
    if any(w in t for w in FAIL_WORDS):
        return "fail"
    if any(w in t for w in OK_WORDS):
        return "ok"
    return "unknown"


# ----------------------------------------------------------------- state table
_NEW_COLS = {"order_ref": "TEXT", "reply": "TEXT", "attempts": "INTEGER", "source": "TEXT",
             "bal_before": "REAL", "bal_after": "REAL"}


def _table(db):
    c = db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS topups(
        id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id INTEGER, amount REAL,
        wid TEXT, txid TEXT UNIQUE, status TEXT, error TEXT, created TEXT, updated TEXT)""")
    have = {r[1] for r in c.execute("PRAGMA table_info(topups)").fetchall()}
    for col, typ in _NEW_COLS.items():
        if col not in have:
            c.execute(f"ALTER TABLE topups ADD COLUMN {col} {typ}")
    if "oid" in have:                                   # a DB where an older version managed to add "oid": keep its order ids
        try:
            c.execute("UPDATE topups SET order_ref=oid WHERE order_ref IS NULL AND oid IS NOT NULL")
        except Exception:
            pass
    c.execute("CREATE TABLE IF NOT EXISTS topup_lock(supplier_id INTEGER PRIMARY KEY, owner TEXT, expires REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS topup_state(k TEXT PRIMARY KEY, v TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS topup_audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, detail TEXT)")
    c.commit()
    c.close()
    _cfg_table(db)


def _set(db, tid, status, **cols):
    c = db.conn()
    keys = ["status", "updated", *cols]
    c.execute(f"UPDATE topups SET {', '.join(k + '=?' for k in keys)} WHERE id=?",
              (status, _now(), *cols.values(), tid))
    c.commit()
    c.close()


def _row(db, tid):
    c = db.conn()
    r = c.execute("SELECT * FROM topups WHERE id=?", (tid,)).fetchone()
    c.close()
    return dict(r) if r else None


def _open_row(db, sid):
    c = db.conn()
    r = c.execute("SELECT * FROM topups WHERE supplier_id=? AND status IN "
                  "('withdrawing','withdrawn','hash_ready','sent') ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    c.close()
    return dict(r) if r else None


def _spent_today(db, sid=None):
    c = db.conn()
    q, a = "SELECT COALESCE(SUM(amount),0) FROM topups WHERE created>=? AND status!='failed'", \
           [datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")]
    if sid is not None:
        q += " AND supplier_id=?"
        a.append(sid)
    r = c.execute(q, a).fetchone()
    c.close()
    return float(r[0])


def resolve_row(db, tid, status):
    """Admin: manually close a stuck top-up as 'failed' (money did NOT leave) or 'credited' (money arrived)."""
    if status not in ("failed", "credited"):
        raise ValueError("status must be failed or credited")
    _table(db)
    _set(db, tid, status, error="resolved manually by admin")
    log.warning("top-up #%s resolved manually as %s", tid, status)


# ----------------------------------------------------------------- pause switch, audit log, limits
def audit(db, action, detail="", actor="admin"):
    """Append to the audit log. Never raises."""
    try:
        _table(db)
        c = db.conn()
        c.execute("INSERT INTO topup_audit(ts,actor,action,detail) VALUES(?,?,?,?)", (_now(), actor, action, (detail or "")[:500]))
        c.execute("DELETE FROM topup_audit WHERE id < (SELECT MAX(id) FROM topup_audit) - 1000")
        c.commit()
        c.close()
    except Exception:
        log.warning("audit write failed", exc_info=True)


def audit_rows(db, limit=15):
    try:
        _table(db)
        c = db.conn()
        rows = [dict(r) for r in c.execute("SELECT * FROM topup_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
        c.close()
        return rows
    except Exception:
        return []


def is_paused(db):
    try:
        _table(db)
        c = db.conn()
        r = c.execute("SELECT v FROM topup_state WHERE k='paused'").fetchone()
        c.close()
        return bool(r and r[0] == "1")
    except Exception:
        return False


def set_paused(db, on, actor="admin"):
    _table(db)
    c = db.conn()
    c.execute("INSERT INTO topup_state(k,v) VALUES('paused',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", ("1" if on else "0",))
    c.commit()
    c.close()
    audit(db, "pause" if on else "resume", "", actor)
    notify("⏸ Top-ups PAUSED by admin. No new withdrawals." if on else "▶️ Top-ups resumed by admin.")


def _recent_count(db, sid, secs=3600):
    since = (datetime.now(timezone.utc) - timedelta(seconds=secs)).strftime("%Y-%m-%d %H:%M:%S")
    c = db.conn()
    r = c.execute("SELECT COUNT(*) FROM topups WHERE supplier_id=? AND created>=? AND status!='failed'", (sid, since)).fetchone()
    c.close()
    return int(r[0])


def stats(db):
    """Per supplier: spent today / 7d / 30d, credited vs failed (30d) and the average minutes until credited."""
    _table(db)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    today = now.strftime("%Y-%m-%d 00:00:00")
    c = db.conn()
    rows = [dict(r) for r in c.execute("SELECT supplier_id, amount, status, created, updated FROM topups "
                                       "WHERE created>=? AND supplier_id>0", (since,)).fetchall()]
    c.close()
    try:
        names = {s["id"]: s["name"] for s in db.list_suppliers()}
    except Exception:
        names = {}
    out = {}
    for r in rows:
        o = out.setdefault(r["supplier_id"], {"id": r["supplier_id"], "name": names.get(r["supplier_id"], r["supplier_id"]),
                                              "today": 0.0, "d7": 0.0, "d30": 0.0, "ok": 0, "fail": 0, "_m": []})
        amt = float(r["amount"] or 0)
        if r["status"] != "failed":
            o["d30"] += amt
            if _age(r["created"]) <= 7 * 86400:
                o["d7"] += amt
            if r["created"] >= today:
                o["today"] += amt
        if r["status"] == "credited":
            o["ok"] += 1
            o["_m"].append(max(0.0, _age(r["created"]) - _age(r["updated"])) / 60)
        elif r["status"] == "failed":
            o["fail"] += 1
    res = []
    for o in out.values():
        m = o.pop("_m")
        o["avg_min"] = round(sum(m) / len(m), 1) if m else None
        res.append(o)
    return sorted(res, key=lambda x: -x["d30"])


# ----------------------------------------------------------------- locks (thread + DB, so several processes are safe too)
_tlocks = {}
_tguard = threading.Lock()


def _thread_lock(sid):
    with _tguard:
        return _tlocks.setdefault(sid, threading.Lock())


class _Guard:
    """One top-up per supplier at a time. Waits for the thread lock, refuses if another PROCESS holds the DB lock."""

    def __init__(self, db, sid):
        self.db, self.sid, self.owner = db, sid, f"{os.getpid()}-{threading.get_ident()}-{time.time()}"
        self.tl = _thread_lock(sid)
        self.have_db = False

    def __enter__(self):
        if not self.tl.acquire(timeout=CREDIT_TRIES * CREDIT_DELAY + 60):
            raise TopupError("another top-up for this supplier is still running")
        try:
            c = self.db.conn()
            c.execute("DELETE FROM topup_lock WHERE expires<?", (time.time(),))
            try:
                c.execute("INSERT INTO topup_lock(supplier_id,owner,expires) VALUES(?,?,?)",
                          (self.sid, self.owner, time.time() + LOCK_TTL))
                c.commit()
                self.have_db = True
            except sqlite3.IntegrityError:
                c.rollback()
                raise TopupError("a top-up for this supplier is already running in another process")
            finally:
                c.close()
        except BaseException:
            self.tl.release()
            raise
        return self

    def __exit__(self, *a):
        try:
            if self.have_db:
                c = self.db.conn()
                c.execute("DELETE FROM topup_lock WHERE supplier_id=? AND owner=?", (self.sid, self.owner))
                c.commit()
                c.close()
        finally:
            self.tl.release()


def _wait_balance(get_balance, s, need):
    for _ in range(CREDIT_TRIES):
        try:
            if get_balance(s) + 1e-9 >= need:
                return True
        except Exception:
            log.warning("balance poll failed", exc_info=True)
        time.sleep(CREDIT_DELAY)
    return False


def _enough(get_balance, s, need):
    try:
        return get_balance(s) + 1e-9 >= need
    except Exception:
        return False


# ----------------------------------------------------------------- reconcile a stuck 'withdrawing' row
def _reconcile(db, row, name):
    """Row says 'withdrawing' = request may or may not have reached Binance. Ask Binance by order id.
    Returns the updated row (continue the flow) or None (it failed: a new top-up may start)."""
    tid = row["id"]
    if not row.get("order_ref"):
        raise TopupError(f"top-up #{tid} is unresolved (old row without order id). "
                         "Check Binance history, then resolve it on the Top-up page.")
    try:
        h = binance_withdraw.find_by_order_id(row["order_ref"])
    except RuntimeError as e:
        raise TopupError(f"top-up #{tid}: cannot check Binance yet ({e})")
    if h:
        if h.get("status") in (1, 3, 5):
            _set(db, tid, "failed", error=f"Binance status {h.get('status')}", wid=str(h.get("id")))
            notify(f"Top-up #{tid} for {name}: Binance cancelled/rejected the withdrawal. Nothing was sent.")
            return None
        _set(db, tid, "withdrawn", wid=str(h.get("id")))
        log.warning("top-up #%s: found on Binance after interruption (wid %s), continuing", tid, h.get("id"))
        return _row(db, tid)
    if _age(row.get("created")) > STUCK_AFTER:
        _set(db, tid, "failed", error="no withdrawal found on Binance (reconciled)")
        notify(f"Top-up #{tid} for {name}: no matching withdrawal on Binance after {STUCK_AFTER // 60} min. "
               "Marked failed, nothing left Binance.", key=f"stuck{tid}")
        return None
    raise TopupError(f"top-up #{tid}: waiting to confirm with Binance, will resume on the next check")


# ----------------------------------------------------------------- send the hash (with reply check + retry)
def _deliver_hash(db, tid, cfg, txid, s, need, get_balance):
    msg = cfg["hash_msg"].format(hash=txid)
    for attempt in range(1, HASH_RETRIES + 1):
        reply = send_to_bot(cfg["bot"], msg)
        verdict = judge_reply(reply)
        _set(db, tid, "sent", reply=(reply or "")[:500], attempts=attempt)   # recorded BEFORE any wait: crash-safe
        log.info("top-up #%s: hash sent to %s (try %s) verdict=%s reply=%r", tid, cfg["bot"], attempt, verdict, (reply or "")[:120])
        if verdict in ("ok", "unknown"):
            return verdict
        if attempt < HASH_RETRIES:
            time.sleep(HASH_RETRY_DELAY)
            if _enough(get_balance, s, need):           # money arrived while waiting: no need to re-send
                return "ok"
    notify(f"Top-up #{tid}: {cfg['bot']} did not confirm the hash after {HASH_RETRIES} tries. "
           f"Last reply: {(reply or 'no reply')[:150]}", key=f"nohash{tid}")
    return "fail"


# ----------------------------------------------------------------- main entry
def ensure_funds(db, s, need, get_balance):
    """Make sure supplier `s` has >= `need` USDT on its API. Blocking (can take a few minutes).
    Same signature as v1, so provider.py needs no change."""
    cfg = _pay_cfg(s.get("name"), s["id"], db)
    if not cfg:
        raise TopupError("no SUPPLIER_PAY config for this supplier")
    cfg = {**cfg, **_overrides(db, s["id"])}
    if not binance_withdraw.keys_ready():
        raise TopupError("Binance withdraw key/secret not filled")
    _table(db)
    with _Guard(db, s["id"]):
        return _ensure(db, s, cfg, need, get_balance)


def _ensure(db, s, cfg, need, get_balance):
    sid, name = s["id"], s.get("name") or s["id"]
    bal = float(get_balance(s))
    if bal + 1e-9 >= need:
        c = db.conn()                                   # money already arrived: close old 'sent' rows
        c.execute("UPDATE topups SET status='credited', updated=? WHERE supplier_id=? AND status='sent'", (_now(), sid))
        c.commit()
        c.close()
        return bal

    row = _open_row(db, sid)
    if row and row["status"] == "withdrawing":
        row = _reconcile(db, row, name)                 # None -> it failed, start fresh below

    if not row:
        if is_paused(db):
            raise TopupError("top-ups are paused by the admin")
        if (cfg.get("locked_until") or "") > _now():
            notify(f"{name}: wallet was changed recently, top-ups are locked until {cfg['locked_until']} UTC.", key=f"lock-{sid}")
            raise TopupError(f"wallet was changed recently: top-ups locked until {cfg['locked_until']} UTC")
        if _recent_count(db, sid) >= MAX_TOPUPS_PER_HOUR:
            notify(f"⚠️ {name}: {MAX_TOPUPS_PER_HOUR} top-ups in the last hour. Stopped to be safe. Check the supplier.", key=f"vel-{sid}")
            raise TopupError(f"too many top-ups for this supplier in the last hour (limit {MAX_TOPUPS_PER_HOUR})")
        buf = 0 if cfg.get("mode") == "invoice" else cfg.get("fee_buffer", 0.5)   # invoice mode adds the exact Binance fee later
        amount = max(Decimal(str(round(need - bal + buf, 6))), Decimal(str(cfg.get("min_topup", 5))))
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_UP)
        to_addr = cfg.get("address")
        if cfg.get("mode") == "invoice":                # the supplier bot tells us where to pay and how much (exactly)
            inv = fetch_invoice(cfg, amount)
            pin = cfg.get("address")
            if not re.fullmatch(_ADDR_RE[cfg["network"]], inv["address"]) or (pin and inv["address"].lower() != pin.lower()):
                notify(f"⚠️ {name}: invoice address {inv['address'][:8]}... does not match the saved pin. Nothing sent.",
                       key=f"pin-{sid}")
                raise TopupError("invoice address does not match the saved address")
            if not (amount <= inv["amount"] <= amount + Decimal(str(INVOICE_TOLERANCE))):
                raise TopupError(f"invoice amount {inv['amount']} is not what we asked ({amount}); nothing sent")
            to_addr = inv["address"]
            amount = inv["amount"]                      # send EXACTLY the invoice amount (BEP20 fee is ~0.0001, far inside the supplier's 0.02 tolerance)
            if ADD_BINANCE_FEE:                         # optional: add the live Binance fee on top (off by default)
                try:
                    amount = amount + binance_withdraw.withdraw_fee(cfg.get("network"))[0]
                except RuntimeError as e:
                    raise TopupError(f"could not read the Binance fee: {e}")
            log.info("invoice from %s: withdraw %s exactly to %s", name, amount, to_addr)
        if _spent_today(db) + float(amount) > DAILY_CAP:
            notify(f"Daily top-up cap {DAILY_CAP} USDT reached. Needed {amount} for {name}.", key="cap-all")
            raise TopupError(f"daily top-up cap {DAILY_CAP} reached")
        scap = float(cfg.get("daily_cap") or SUPPLIER_DAILY_CAP)
        if _spent_today(db, sid) + float(amount) > scap:
            notify(f"Daily cap for {name} ({scap} USDT) reached. Needed {amount}.", key=f"cap-{sid}")
            raise TopupError(f"daily cap for this supplier ({scap}) reached")
        if binance_withdraw.dry_run():
            notify(f"DRY RUN: would withdraw {amount} USDT for {name} to {str(to_addr)[:8]}...{str(to_addr)[-6:]}. Nothing sent.", key=f"dry-{sid}")
            raise TopupError(f"dry-run mode: would withdraw {amount} USDT, nothing sent")
        have = None                                     # enough money on Binance? (fails fast, nothing is created)
        try:
            have = binance_withdraw.spot_balance()
        except RuntimeError:
            log.warning("could not read Binance balance, trying the withdrawal anyway", exc_info=True)
        if have is not None and have < amount:
            notify(f"Binance balance too low: {have} USDT, need {amount} for {name}. Add funds.", key="binance-low")
            raise TopupError(f"Binance balance too low ({have} < {amount})")
        c = db.conn()
        cur = c.execute("INSERT INTO topups(supplier_id,amount,status,created,updated,source,bal_before) VALUES(?,?,?,?,?,?,?)",
                        (sid, float(amount), "withdrawing", _now(), _now(), cfg.get("mode", "fixed"), bal))
        tid = cur.lastrowid
        oid = f"tu{tid}"
        c.execute("UPDATE topups SET order_ref=? WHERE id=?", (oid, tid))
        c.commit()
        c.close()
        try:
            wid = binance_withdraw.withdraw(amount, to_addr, cfg.get("network"), order_id=oid)
        except ValueError as e:                         # rejected locally (cap), nothing left Binance
            _set(db, tid, "failed", error=str(e))
            raise TopupError(str(e))
        except RuntimeError as e:
            msg = str(e)
            if msg.startswith("Binance HTTP 4") or "451" in msg:   # Binance refused: nothing sent
                _set(db, tid, "failed", error=msg[:300])
                notify(f"Top-up #{tid} for {name} refused by Binance: {msg[:200]}", key="binance-refused")
            raise TopupError(msg)                        # network error: stays 'withdrawing', reconciled next time
        _set(db, tid, "withdrawn", wid=str(wid))
        row = {"id": tid, "status": "withdrawn", "wid": str(wid), "txid": None}
        log.info("top-up #%s: withdrew %s to supplier %s (wid %s)", tid, amount, sid, wid)
        notify(f"Top-up #{tid}: withdrew {amount} USDT to {name} ({to_addr[:8]}...{to_addr[-6:]}). Waiting for the hash.")

    tid = row["id"]
    if row["status"] == "withdrawn":
        try:
            txid = binance_withdraw.wait_txid(row["wid"])
        except RuntimeError as e:                       # cancelled / rejected by Binance
            _set(db, tid, "failed", error=str(e)[:300])
            notify(f"Top-up #{tid} for {name} failed: {str(e)[:200]}")
            raise TopupError(str(e))
        if not txid:
            raise TopupError(f"top-up #{tid}: hash not ready yet, will resume on the next order")
        _set(db, tid, "hash_ready", txid=txid)
        row["status"], row["txid"] = "hash_ready", txid

    if row["status"] == "hash_ready":
        _deliver_hash(db, tid, cfg, row["txid"], s, need, get_balance)

    if _wait_balance(get_balance, s, need):
        try:
            final = float(get_balance(s))
        except Exception:
            final = None
        _set(db, tid, "credited", **({"bal_after": final} if final is not None else {}))
        notify(f"Top-up #{tid} credited ✅ {name} balance: " + (f"{final:.2f} USDT" if final is not None else "ok"))
        return final if final is not None else float(need)
    notify(f"Top-up #{tid} for {name}: hash sent but balance not credited yet. Will keep checking.",
           key=f"nocredit{tid}")
    raise TopupError(f"top-up #{tid}: hash sent but balance not credited yet (will check again next order)")


# ----------------------------------------------------------------- proactive refill (background watcher)
_fail = {}                                        # supplier id -> (consecutive failures, next try timestamp)


def _watch_binance():
    if not binance_withdraw.keys_ready():
        return
    try:
        have = binance_withdraw.spot_balance()
    except Exception:
        return
    if have < Decimal(str(BINANCE_LOW_ALERT)):
        notify(f"Binance balance is low: {have} USDT (alert level {BINANCE_LOW_ALERT}). Auto-refill needs funds there.", key="binance-low-watch")


def refill_once(db, list_suppliers, get_supplier, get_balance):
    """For every supplier with auto-refill on: if API balance < low_balance, top up to target_balance.
    A supplier that keeps failing is retried after 5, 10, 20, 40, 60 min instead of every cycle."""
    _table(db)
    if is_paused(db):
        return
    _watch_binance()
    for s0 in list_suppliers():
        sid = s0["id"]
        try:
            cfg = _pay_cfg(s0["name"], sid, db)
            if not cfg:
                continue
            cfg = {**cfg, **_overrides(db, sid)}
            low, tgt = float(cfg.get("low_balance") or 0), float(cfg.get("target_balance") or 0)
            if not cfg.get("auto") or low <= 0 or tgt <= low:
                continue
            if time.time() < _fail.get(sid, (0, 0))[1]:
                continue
            s = get_supplier(sid)
            if get_balance(s) >= low:
                _fail.pop(sid, None)
                continue
            log.info("auto-refill: %s below %.2f, topping up to %.2f", s0["name"], low, tgt)
            ensure_funds(db, s, tgt, get_balance)
            _fail.pop(sid, None)
        except TopupError as e:
            n = _fail.get(sid, (0, 0))[0] + 1
            wait = min(BACKOFF_MAX, BACKOFF_BASE * 2 ** (n - 1))
            _fail[sid] = (n, time.time() + wait)
            log.warning("auto-refill for %s: %s (failure %s, next try in %s min)", s0.get("name"), e, n, int(wait // 60))
            notify(f"Auto-refill for {s0.get('name')} could not finish: {str(e)[:200]}\n"
                   f"(failure {n}, next try in {int(wait // 60)} min)", key=f"auto-{sid}")
        except Exception:
            log.exception("auto-refill error for %s", s0.get("name"))


_watcher = None


def start_watcher(get_db, list_suppliers, get_supplier, get_balance, interval=WATCH_INTERVAL):
    """Start ONE background thread per process. `get_db()` returns the main shop database."""
    global _watcher
    with _lk:
        if _watcher:
            return False

        def loop():
            time.sleep(30)
            while True:
                try:
                    db = get_db()
                    if db is not None:
                        refill_once(db, lambda: list_suppliers(db), lambda sid: get_supplier(db, sid), get_balance)
                except Exception:
                    log.exception("auto-refill watcher error")
                time.sleep(interval)
        _watcher = threading.Thread(target=loop, daemon=True, name="topup-watcher")
        _watcher.start()
        return True


# ----------------------------------------------------------------- health check
def health(db):
    """List of {"name", "ok", "detail"}. Moves no money. The Telegram part can take up to ~30 s."""
    out = []

    def add(name, ok, detail=""):
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    keys = binance_withdraw.keys_ready()
    add("Binance API key", keys, "" if keys else "BINANCE_WD_KEY / BINANCE_WD_SECRET not set")
    if keys:
        try:
            r = binance_withdraw.api_restrictions()
            add("Withdraw permission ON", r.get("enableWithdrawals"), "" if r.get("enableWithdrawals") else "enable Withdrawals on the key")
            add("IP restricted", r.get("ipRestrict"), "" if r.get("ipRestrict") else "set the trusted IP on the key: otherwise a leaked key can withdraw from anywhere")
            risky = [k for k in ("enableSpotAndMarginTrading", "enableMargin", "enableFutures", "permitsUniversalTransfer", "enableInternalTransfer")
                     if r.get(k)]
            add("No extra permissions", not risky, ("remove: " + ", ".join(risky)) if risky else "withdraw-only key")
        except Exception as e:
            add("Binance key permissions", False, str(e)[:150])
        try:
            have = binance_withdraw.spot_balance()
            add("Binance balance", have >= Decimal(str(BINANCE_LOW_ALERT)), f"{have} {binance_withdraw.cfg('WD_COIN')}")
        except Exception as e:
            add("Binance balance", False, str(e)[:150])
    tg_set = not any(_tg(k).startswith("PASTE_") for k in _TG)
    add("Telegram session", tg_set, "" if tg_set else "TG_API_ID / TG_API_HASH / TG_SESSION not set")
    add("Owner alerts", bool(os.getenv("BOT_TOKEN") and (os.getenv("ALERT_CHAT_ID") or os.getenv("OWNER_ID"))),
        "BOT_TOKEN + OWNER_ID needed")
    add("Top-ups running", not is_paused(db), "PAUSED" if is_paused(db) else "")
    tg_ok = False
    if tg_set:
        try:
            _start()
            tg_ok = True
            add("Telegram login", True)
        except Exception as e:
            add("Telegram login", False, str(e)[:150])
    try:
        sups = db.list_suppliers()
    except Exception:
        sups = []
    for s in sups:
        cfg = _pay_cfg(s.get("name"), s["id"], db)
        if not cfg:
            continue
        add(f"{s['name']}: wallet", True, f"{cfg.get('mode', 'fixed')} / {cfg.get('network')} / {cfg.get('bot')}")
        if (cfg.get("locked_until") or "") > _now():
            add(f"{s['name']}: wallet lock", False, f"changed recently, locked until {cfg['locked_until']} UTC")
        if tg_ok:
            try:
                add(f"{s['name']}: bot {cfg['bot']}", bot_reachable(cfg["bot"]), "")
            except Exception as e:
                add(f"{s['name']}: bot {cfg['bot']}", False, str(e)[:120])
    return out
