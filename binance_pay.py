"""Payment verifier for the Telegram shop: BSC BEP20 USDT (on-chain) + Binance Pay (UID transfer).

BSC variables:  NODEREAL_RPC_URL, DEPOSIT_ADDRESS, USDT_CONTRACT_ADDRESS
                (optional) USDT_DECIMALS=18, BSC_CONFIRMATIONS=3, DEPOSIT_NETWORK
Binance Pay:    BINANCE_API_KEY, BINANCE_SECRET_KEY, BINANCE_PAY_ID
                (optional) BINANCE_API_BASE=https://api.binance.com, BINANCE_PROXY
                Key permission: ONLY "Enable Reading". Never enable trading/withdrawals.
Common:         DB_PATH=shop.db

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
DECIMALS = 2  # normal USDT precision; no artificial triple-decimal amount slots

BSC_ENABLED = bool(RPC_URL and DEPOSIT_ADDRESS and USDT_CONTRACT_ADDRESS)
BINANCE_ENABLED = bool(BINANCE_API_KEY and BINANCE_SECRET and PAY_ID)

def public_url():
    url = os.getenv("PUBLIC_URL", "").strip()
    if not url:
        domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        url = ("https://" + domain) if domain else ""
    return url.rstrip("/")

def _connect(db_path):
    c = sqlite3.connect(db_path, timeout=15)
    c.row_factory = sqlite3.Row
    return c

def ensure_schema(db_path=None):
    c = _connect(db_path or DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS crypto_payments (
        order_id TEXT PRIMARY KEY, payable REAL NOT NULL,
        created_ms INTEGER NOT NULL, txid TEXT UNIQUE)""")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(crypto_payments)")}
    if "method" not in cols:  # old rows were all BSC
        c.execute("ALTER TABLE crypto_payments ADD COLUMN method TEXT DEFAULT 'bsc'")
    if "submitted_txid" not in cols:
        c.execute("ALTER TABLE crypto_payments ADD COLUMN submitted_txid TEXT")
    c.commit(); c.close()

def get_payment_info(order_id, db_path=None):
    ensure_schema(db_path); c = _connect(db_path or DB_PATH)
    r = c.execute("SELECT * FROM crypto_payments WHERE order_id=?", (order_id,)).fetchone()
    c.close(); return r

class BinancePayClient:
    """Handles both 'bsc' and 'bpay' methods for ONE shop (tenant).

    cfg=None  -> the main shop: values come from Railway variables (old behaviour).
    cfg=dict  -> a seller's own payment setup: api_key, secret, pay_id, deposit_address,
                 network (optional). The BSC RPC URL, USDT contract, proxy and Binance base
                 URL are platform-wide (Railway variables) and shared by every shop.
    db_path   -> the shop's own database file (payment rows are stored per shop).
    """
    def __init__(self, cfg=None, db_path=None):
        cfg = cfg or {}
        self._bp_cache = None  # (timestamp, rows)
        self.db_path = db_path or DB_PATH
        self.api_key = (cfg.get("api_key") if cfg else BINANCE_API_KEY) or ""
        self.secret = (cfg.get("secret") if cfg else BINANCE_SECRET) or ""
        self.pay_id = (cfg.get("pay_id") if cfg else PAY_ID) or ""
        self.deposit_address = (cfg.get("deposit_address") if cfg else DEPOSIT_ADDRESS) or ""
        self.deposit_network = (cfg.get("network") or DEPOSIT_NETWORK) if cfg else DEPOSIT_NETWORK
        self.rpc_url = RPC_URL
        self.contract = USDT_CONTRACT_ADDRESS
        ensure_schema(self.db_path)

    # ---------- what this shop has configured ----------
    @property
    def binance_enabled(self):
        return bool(self.api_key and self.secret and self.pay_id)

    @property
    def bsc_enabled(self):
        return bool(self.rpc_url and self.deposit_address and self.contract)

    def _conn(self):
        return _connect(self.db_path)

    # ---------- BSC ----------
    def _rpc(self, method, params):
        if not self.rpc_url: raise RuntimeError("BSC RPC is not configured on the platform (NODEREAL_RPC_URL).")
        r = requests.post(self.rpc_url, json={"jsonrpc":"2.0", "id":1, "method":method, "params":params}, timeout=20)
        r.raise_for_status(); data = r.json()
        if data.get("error"): raise RuntimeError(f"BSC RPC error: {data['error']}")
        return data.get("result")

    # ---------- Binance Pay ----------
    def _bpay_history(self, force=False):
        now = time.time()
        if not force and self._bp_cache and now - self._bp_cache[0] < BPAY_CACHE_SECONDS:
            return self._bp_cache[1]
        if not self.binance_enabled:
            raise RuntimeError("Binance Pay is not configured: set the Binance API key, secret and Pay ID.")
        ts = int(now * 1000)
        qs = urlencode({"startTimestamp": ts - BPAY_WINDOW_MS, "endTimestamp": ts,
                        "limit": 100, "timestamp": ts, "recvWindow": 10000})
        sig = hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        proxies = {"https": BINANCE_PROXY, "http": BINANCE_PROXY} if BINANCE_PROXY else None
        r = requests.get(f"{BINANCE_BASE}/sapi/v1/pay/transactions?{qs}&signature={sig}",
                         headers={"X-MBX-APIKEY": self.api_key}, proxies=proxies, timeout=20)
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
        """Create an immutable payment intent; never overwrite an existing/paid order."""
        order_id = str(order_id or "").strip()
        if not order_id or len(order_id) > 128 or any(ord(ch) < 32 for ch in order_id):
            raise ValueError("Invalid payment order ID.")
        amount = Decimal(str(base_amount))
        if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000"):
            raise ValueError("Invalid payment amount.")
        payable_d = amount.quantize(Decimal("0.01"))
        if payable_d <= 0:
            raise ValueError("Payment amount is too small.")
        payable = float(payable_d)
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT payable,method,txid FROM crypto_payments WHERE order_id=?", (order_id,)).fetchone()
            if old:
                if Decimal(str(old["payable"])) != payable_d or (old["method"] or "bsc") != method:
                    raise ValueError("This order ID is already bound to a different payment. Create a new order ID.")
                # Idempotent retry: retain original creation time, submitted hash and paid state.
                c.commit()
                return float(old["payable"])
            c.execute("INSERT INTO crypto_payments(order_id,payable,created_ms,txid,method,submitted_txid) VALUES(?,?,?,NULL,?,NULL)",
                      (order_id, payable, int(time.time() * 1000), method))
            c.commit()
            return payable
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def submit_tx_hash(self, order_id, tx_hash):
        """Attach one user-provided transaction hash; reject reused hashes and malformed input."""
        tx_hash = str(tx_hash or "").strip().lower()
        if not (tx_hash.startswith("0x") and len(tx_hash) == 66 and all(ch in "0123456789abcdef" for ch in tx_hash[2:])):
            return "invalid"
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT submitted_txid,txid FROM crypto_payments WHERE order_id=?", (order_id,)).fetchone()
            if not row:
                c.rollback(); return "missing"
            if row["txid"]:
                c.rollback(); return "paid"
            used = c.execute("SELECT 1 FROM crypto_payments WHERE (submitted_txid=? OR txid=?) AND order_id<>?", (tx_hash, "bsc:"+tx_hash, order_id)).fetchone()
            if used:
                c.rollback(); return "duplicate"
            c.execute("UPDATE crypto_payments SET submitted_txid=? WHERE order_id=?", (tx_hash, order_id))
            c.commit(); return "ok"
        finally:
            c.close()

    def release(self, order_id):
        """Free the reserved amount of a cancelled, still-unpaid order."""
        c = self._conn()
        c.execute("DELETE FROM crypto_payments WHERE order_id=? AND txid IS NULL", (order_id,))
        c.commit(); c.close()

    def _claim(self, order_id, txid):
        c = self._conn()
        try:
            cur = c.execute("UPDATE crypto_payments SET txid=? WHERE order_id=? AND txid IS NULL", (str(txid), order_id))
            c.commit(); return cur.rowcount == 1
        except sqlite3.IntegrityError: return False
        finally: c.close()

    def create_order(self, merchant_trade_no, amount, description, method="bsc"):
        if method == "bpay":
            if not self.binance_enabled:
                raise RuntimeError("Binance Pay is not configured.")
            self._bpay_history()  # preflight: fail now (e.g. 451) instead of after the user has paid
            where = ["Send EXACTLY {text} USDT via Binance Pay:", f"Binance Pay ID: {self.pay_id}"]
        else:
            if not self.rpc_url: raise RuntimeError("BSC RPC is not configured on the platform (NODEREAL_RPC_URL).")
            if not self.deposit_address or not self.contract: raise RuntimeError("BEP20 deposit address is not set.")
            if not self.deposit_address.startswith("0x") or len(self.deposit_address) != 42: raise RuntimeError("Deposit address does not look like a BSC address.")
            if not self.contract.startswith("0x") or len(self.contract) != 42: raise RuntimeError("USDT_CONTRACT_ADDRESS does not look like a token contract address.")
            where = ["Send EXACTLY {text} USDT on BNB Smart Chain (BEP20):", f"USDT address: {self.deposit_address}"]
            method = "bsc"
        payable = self._reserve(merchant_trade_no, float(amount), method)
        text = f"{payable:.{DECIMALS}f}"
        tail = "After paying, submit the transaction hash (TXN). Payment is verified automatically after confirmations." if method == "bsc" else "After paying, press Check Payment."
        details = "\n".join([where[0].format(text=text), where[1], "", tail])
        return {"prepay_id": merchant_trade_no, "checkout_url": "", "amount_text": text, "details": details}

    def query_order(self, order_id):
        row = get_payment_info(order_id, self.db_path)
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
        if not self.bsc_enabled:
            raise RuntimeError("BEP20 is not configured (RPC, deposit address or USDT contract missing).")
        txid = str(row["submitted_txid"] or "").strip().lower()
        if not txid:
            return "PENDING"
        tx = self._rpc("eth_getTransactionByHash", [txid])
        if not tx or str(tx.get("hash") or "").lower() != txid:
            return "PENDING"
        # Refuse an RPC endpoint pointed at the wrong EVM network.
        chain_id = int(self._rpc("eth_chainId", []), 16)
        if chain_id != 56:  # BNB Smart Chain mainnet
            raise RuntimeError(f"Wrong EVM network: expected BSC chainId 56, got {chain_id}.")
        receipt = self._rpc("eth_getTransactionReceipt", [txid])
        if not receipt or int(receipt.get("status", "0x0"), 16) != 1:
            return "PENDING"
        if str(receipt.get("transactionHash") or "").lower() != txid:
            return "PENDING"
        latest = int(self._rpc("eth_blockNumber", []), 16)
        block = int(receipt.get("blockNumber", "0x0"), 16)
        if latest - block + 1 < CONFIRMATIONS:
            return "PENDING"

        # Bind the receipt to the canonical transaction/block and reject old hashes.
        # A successful transaction from before this deposit was created is NOT payment.
        tx_block = int(tx.get("blockNumber") or "0x0", 16)
        if tx_block != block:
            return "PENDING"
        tx_hash = str(tx.get("hash") or "").lower()
        if tx_hash != txid:
            return "PENDING"
        block_data = self._rpc("eth_getBlockByNumber", [hex(block), False])
        if not block_data or int(block_data.get("number") or "0x0", 16) != block:
            return "PENDING"
        canonical_hash = str(block_data.get("hash") or "").lower()
        if not canonical_hash or str(receipt.get("blockHash") or "").lower() != canonical_hash:
            return "PENDING"
        if str(tx.get("blockHash") or "").lower() != canonical_hash:
            return "PENDING"
        block_timestamp = int(block_data.get("timestamp") or "0x0", 16)
        created_ms = int(row["created_ms"])
        if block_timestamp * 1000 < created_ms:
            log.warning("Rejecting pre-existing BSC transaction for order %s: tx=%s block_time=%s created_ms=%s",
                        order_id, txid, block_timestamp, created_ms)
            return "PENDING"

        payable = Decimal(str(row["payable"]))
        deposit_to = self.deposit_address.lower()
        token = self.contract.lower()
        found = False
        for item in receipt.get("logs", []):
            try:
                if str(item.get("address", "")).lower() != token:
                    continue
                topics = item.get("topics") or []
                if len(topics) < 3 or str(topics[0]).lower() != TRANSFER_TOPIC:
                    continue
                recipient = "0x" + str(topics[2])[-40:].lower()
                if recipient != deposit_to:
                    continue
                received = Decimal(int(item.get("data", "0x0"), 16)) / (Decimal(10) ** TOKEN_DECIMALS)
                if received == payable:
                    found = True
                    break
            except (ValueError, TypeError, InvalidOperation):
                continue
        if not found:
            return "PENDING"
        # The unique txid constraint prevents a transaction from crediting two deposits.
        if self._claim(order_id, "bsc:" + txid):
            return "PAID"
        current = get_payment_info(order_id, self.db_path)
        return "PAID" if current and current["txid"] == "bsc:" + txid else "PENDING"
