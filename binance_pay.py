"""Payment verifier for the Telegram shop: BSC BEP20 USDT (on-chain) + Binance Pay (UID transfer).

BSC variables:  NODEREAL_RPC_URL, DEPOSIT_ADDRESS, USDT_CONTRACT_ADDRESS
                (optional) USDT_DECIMALS=18, BSC_CONFIRMATIONS=3, DEPOSIT_NETWORK
Binance Pay:    BINANCE_API_KEY, BINANCE_SECRET_KEY, BINANCE_PAY_ID
                (optional) BINANCE_API_BASE=https://api.binance.com, BINANCE_PROXY
                Key permission: ONLY "Enable Reading". Never enable trading/withdrawals.
Common:         AMOUNT_DECIMALS=3, DB_PATH=shop.db

A method is offered only if its variables are set. Never put a wallet seed phrase anywhere.
"""
import hashlib, hmac, logging, os, sqlite3, time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
import requests

log = logging.getLogger("digital-shop")

# ---- BSC on-chain ----
RPC_URL = os.getenv("NODEREAL_RPC_URL", "").strip()
DEPOSIT_ADDRESS = os.getenv("DEPOSIT_ADDRESS", "").strip()
USDT_CONTRACT_ADDRESS = os.getenv("USDT_CONTRACT_ADDRESS", "").strip()
DEPOSIT_NETWORK = os.getenv("DEPOSIT_NETWORK", "BNB Smart Chain (BEP20)").strip()
TOKEN_DECIMALS = int(os.getenv("USDT_DECIMALS", "18"))
CONFIRMATIONS = max(1, int(os.getenv("BSC_CONFIRMATIONS", "3")))
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# ---- Binance Pay (UID transfer) ----
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_SECRET = (os.getenv("BINANCE_SECRET_KEY") or os.getenv("BINANCE_API_SECRET") or "").strip()
PAY_ID = (os.getenv("BINANCE_PAY_ID") or os.getenv("BINANCE_PAY_UID") or "").strip()
BINANCE_BASE = os.getenv("BINANCE_API_BASE", "https://api.binance.com").strip().rstrip("/")
BINANCE_PROXY = os.getenv("BINANCE_PROXY", "").strip()
BPAY_CACHE_SECONDS = 20          # the pay-history endpoint is rate-limit heavy; share one call
BPAY_WINDOW_MS = 60 * 60 * 1000  # look back 1 hour

# ---- common ----
DB_PATH = os.getenv("DB_PATH", "shop.db")
DECIMALS = int(os.getenv("AMOUNT_DECIMALS", "2"))
MAX_SLOTS = 49 if DECIMALS >= 2 else 20

BSC_ENABLED = bool(RPC_URL and DEPOSIT_ADDRESS and USDT_CONTRACT_ADDRESS)
BINANCE_ENABLED = bool(BINANCE_API_KEY and BINANCE_SECRET and PAY_ID)

def public_url():
    url = os.getenv("PUBLIC_URL", "").strip()
    if not url:
        domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        url = ("https://" + domain) if domain else ""
    return url.rstrip("/")

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c

def ensure_schema():
    c = _conn()
    c.execute("""CREATE TABLE IF NOT EXISTS crypto_payments (
        order_id TEXT PRIMARY KEY, payable REAL NOT NULL,
        created_ms INTEGER NOT NULL, txid TEXT UNIQUE)""")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(crypto_payments)")}
    if "method" not in cols:  # old rows were all BSC
        c.execute("ALTER TABLE crypto_payments ADD COLUMN method TEXT DEFAULT 'bsc'")
    c.commit(); c.close()

def get_payment_info(order_id):
    ensure_schema(); c = _conn()
    r = c.execute("SELECT * FROM crypto_payments WHERE order_id=?", (order_id,)).fetchone()
    c.close(); return r

class BinancePayClient:
    """Name kept so existing bot.py imports work. Handles both 'bsc' and 'bpay' methods."""
    def __init__(self, api_key="", secret_key="", base_url=""):
        self._bp_cache = None  # (timestamp, rows)
        ensure_schema()

    # ---------- BSC ----------
    def _rpc(self, method, params):
        if not RPC_URL: raise RuntimeError("Set NODEREAL_RPC_URL in Railway Variables.")
        r = requests.post(RPC_URL, json={"jsonrpc":"2.0", "id":1, "method":method, "params":params}, timeout=20)
        r.raise_for_status(); data = r.json()
        if data.get("error"): raise RuntimeError(f"BSC RPC error: {data['error']}")
        return data.get("result")

    # ---------- Binance Pay ----------
    def _bpay_history(self, force=False):
        now = time.time()
        if not force and self._bp_cache and now - self._bp_cache[0] < BPAY_CACHE_SECONDS:
            return self._bp_cache[1]
        if not BINANCE_ENABLED:
            raise RuntimeError("Set BINANCE_API_KEY, BINANCE_SECRET_KEY and BINANCE_PAY_ID in Railway Variables.")
        ts = int(now * 1000)
        qs = urlencode({"startTimestamp": ts - BPAY_WINDOW_MS, "endTimestamp": ts,
                        "limit": 100, "timestamp": ts, "recvWindow": 10000})
        sig = hmac.new(BINANCE_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
        proxies = {"https": BINANCE_PROXY, "http": BINANCE_PROXY} if BINANCE_PROXY else None
        r = requests.get(f"{BINANCE_BASE}/sapi/v1/pay/transactions?{qs}&signature={sig}",
                         headers={"X-MBX-APIKEY": BINANCE_API_KEY}, proxies=proxies, timeout=20)
        if r.status_code == 451:
            raise RuntimeError("Binance returned 451: this server's IP/region is blocked. "
                               "Move the service to a non-US region or set BINANCE_PROXY.")
        if r.status_code != 200:
            raise RuntimeError(f"Binance HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        if data.get("success") is False or str(data.get("code", "000000")) != "000000":
            raise RuntimeError(f"Binance error: {str(data)[:200]}")
        rows = data.get("data") or []
        self._bp_cache = (now, rows)
        return rows

    # ---------- shared ----------
    def _reserve(self, order_id, base_amount, method):
        """Pick a unique payable amount among pending orders of the same method."""
        c = _conn(); c.execute("BEGIN IMMEDIATE")
        now = int(time.time() * 1000)
        busy = {round(r["payable"], DECIMALS) for r in c.execute(
            "SELECT payable FROM crypto_payments WHERE txid IS NULL AND created_ms>? "
            "AND COALESCE(method,'bsc')=?", (now - 3600 * 1000, method))}
        slots = list(range(1, MAX_SLOTS + 1)); random.shuffle(slots)
        for k in slots:
            payable = round(float(base_amount) + k * 10 ** -DECIMALS, DECIMALS)
            if payable not in busy:
                c.execute("INSERT OR REPLACE INTO crypto_payments(order_id,payable,created_ms,txid,method) "
                          "VALUES(?,?,?,NULL,?)", (order_id, payable, now, method))
                c.commit(); c.close(); return payable
        c.rollback(); c.close(); raise RuntimeError("Too many pending orders for this amount. Try again shortly.")

    def release(self, order_id):
        """Free the reserved amount of a cancelled, still-unpaid order."""
        c = _conn()
        c.execute("DELETE FROM crypto_payments WHERE order_id=? AND txid IS NULL", (order_id,))
        c.commit(); c.close()

    def _claim(self, order_id, txid):
        c = _conn()
        try:
            cur = c.execute("UPDATE crypto_payments SET txid=? WHERE order_id=? AND txid IS NULL", (str(txid), order_id))
            c.commit(); return cur.rowcount == 1
        except sqlite3.IntegrityError: return False
        finally: c.close()

    def create_order(self, merchant_trade_no, amount, description, method="bsc"):
        if method == "bpay":
            if not BINANCE_ENABLED:
                raise RuntimeError("Binance Pay is not configured.")
            self._bpay_history()  # preflight: fail now (e.g. 451) instead of after the user has paid
            where = ["Send EXACTLY {text} USDT via Binance Pay:", f"Binance Pay ID: {PAY_ID}"]
        else:
            if not RPC_URL: raise RuntimeError("Set NODEREAL_RPC_URL in Railway Variables.")
            if not DEPOSIT_ADDRESS or not USDT_CONTRACT_ADDRESS: raise RuntimeError("Set DEPOSIT_ADDRESS and USDT_CONTRACT_ADDRESS in Railway Variables.")
            if not DEPOSIT_ADDRESS.startswith("0x") or len(DEPOSIT_ADDRESS) != 42: raise RuntimeError("DEPOSIT_ADDRESS does not look like a BSC address.")
            if not USDT_CONTRACT_ADDRESS.startswith("0x") or len(USDT_CONTRACT_ADDRESS) != 42: raise RuntimeError("USDT_CONTRACT_ADDRESS does not look like a token contract address.")
            where = ["Send EXACTLY {text} USDT on BNB Smart Chain (BEP20):", f"USDT address: {DEPOSIT_ADDRESS}"]
            method = "bsc"
        payable = self._reserve(merchant_trade_no, float(amount), method)
        text = f"{payable:.{DECIMALS}f}"
        details = "\n".join([where[0].format(text=text), where[1], "", "After paying, press Check Payment."])
        return {"prepay_id": merchant_trade_no, "checkout_url": "", "amount_text": text, "details": details}

    def query_order(self, order_id):
        row = get_payment_info(order_id)
        if not row: return "UNKNOWN"
        if row["txid"]: return "PAID"
        try:
            if (row["method"] or "bsc") == "bpay":
                return self._query_bpay(order_id, row)
            return self._query_bsc(order_id, row)
        except Exception:
            log.exception("payment verification failed for order %s", order_id)
            raise

    def _query_bpay(self, order_id, row):
        payable = Decimal(str(row["payable"]))
        since = int(row["created_ms"]) - 5000
        tol = Decimal(1) / (Decimal(10) ** (DECIMALS + 2))
        for tx in self._bpay_history():
            try:
                if str(tx.get("currency", "")).upper() != "USDT": continue
                if "REFUND" in str(tx.get("orderType", "")).upper(): continue
                amt = Decimal(str(tx.get("amount")))
                if amt <= 0: continue  # outgoing
                if int(tx.get("transactionTime", 0)) < since: continue
                if abs(amt - payable) > tol: continue
                tid = str(tx.get("transactionId") or tx.get("orderId") or "")
                if not tid: continue
                if self._claim(order_id, "bpay:" + tid): return "PAID"
            except (KeyError, ValueError, TypeError, InvalidOperation):
                continue
        return "PENDING"

    def _query_bsc(self, order_id, row):
        if not BSC_ENABLED:
            raise RuntimeError("Missing NODEREAL_RPC_URL, DEPOSIT_ADDRESS or USDT_CONTRACT_ADDRESS.")
        latest = int(self._rpc("eth_blockNumber", []), 16)
        # ~5 minutes of BSC blocks; the amount slot plus order creation time narrows matching.
        from_block = max(0, latest - 1800)
        topic_to = "0x" + DEPOSIT_ADDRESS.lower().replace("0x", "").rjust(64, "0")
        logs = self._rpc("eth_getLogs", [{"fromBlock": hex(from_block), "toBlock": hex(latest),
                                          "address": USDT_CONTRACT_ADDRESS,
                                          "topics": [TRANSFER_TOPIC, None, topic_to]}]) or []
        payable = Decimal(str(row["payable"]))
        since = int(row["created_ms"]) - 5000
        for item in logs:
            try:
                block = int(item["blockNumber"], 16)
                if latest - block + 1 < CONFIRMATIONS: continue
                txid = item["transactionHash"].lower()
                # Fetch block timestamp to avoid matching old transfers of the same amount.
                blockdata = self._rpc("eth_getBlockByNumber", [hex(block), False])
                timestamp = int(blockdata["timestamp"], 16) * 1000
                if timestamp < since: continue
                raw = int(item["data"], 16)
                received = Decimal(raw) / (Decimal(10) ** TOKEN_DECIMALS)
                if abs(received - payable) > Decimal(1) / (Decimal(10) ** (DECIMALS + 2)): continue
                if self._claim(order_id, "bsc:" + txid): return "PAID"
            except (KeyError, ValueError, TypeError, InvalidOperation):
                continue
        return "PENDING"
