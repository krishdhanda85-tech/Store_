"""BSC BEP20 USDT payment verifier for the existing Telegram shop.

Required Railway variables:
  NODEREAL_RPC_URL, DEPOSIT_ADDRESS, USDT_CONTRACT_ADDRESS
Optional:
  USDT_DECIMALS=18, BSC_CONFIRMATIONS=3, AMOUNT_DECIMALS=3, DB_PATH=shop.db
No Binance API key or private key is used. Never put a wallet seed phrase here.
"""
import logging, os, random, sqlite3, time
from decimal import Decimal, InvalidOperation
import requests

log = logging.getLogger("digital-shop")
RPC_URL = os.getenv("NODEREAL_RPC_URL", "").strip()
DEPOSIT_ADDRESS = os.getenv("DEPOSIT_ADDRESS", "").strip()
USDT_CONTRACT_ADDRESS = os.getenv("USDT_CONTRACT_ADDRESS", "").strip()
DEPOSIT_NETWORK = os.getenv("DEPOSIT_NETWORK", "BNB Smart Chain (BEP20)").strip()
DB_PATH = os.getenv("DB_PATH", "shop.db")
DECIMALS = int(os.getenv("AMOUNT_DECIMALS", "3"))
TOKEN_DECIMALS = int(os.getenv("USDT_DECIMALS", "18"))
CONFIRMATIONS = max(1, int(os.getenv("BSC_CONFIRMATIONS", "3")))
MAX_SLOTS = 49 if DECIMALS >= 3 else 20
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

PAY_ID = ""  # compatibility with bot.py imports; Binance Pay is not used.

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
    c.commit(); c.close()

def get_payment_info(order_id):
    ensure_schema(); c = _conn()
    r = c.execute("SELECT * FROM crypto_payments WHERE order_id=?", (order_id,)).fetchone()
    c.close(); return r

class BinancePayClient:
    """Legacy class name retained so existing bot.py imports still work."""
    def __init__(self, api_key="", secret_key="", base_url=""):
        self._cache = {}; ensure_schema()

    def _rpc(self, method, params):
        if not RPC_URL: raise RuntimeError("Set NODEREAL_RPC_URL in Railway Variables.")
        r = requests.post(RPC_URL, json={"jsonrpc":"2.0", "id":1, "method":method, "params":params}, timeout=20)
        r.raise_for_status(); data=r.json()
        if data.get("error"): raise RuntimeError(f"BSC RPC error: {data['error']}")
        return data.get("result")

    def _reserve(self, order_id, base_amount):
        c=_conn(); c.execute("BEGIN IMMEDIATE")
        now=int(time.time()*1000)
        busy={round(r["payable"], DECIMALS) for r in c.execute("SELECT payable FROM crypto_payments WHERE txid IS NULL AND created_ms>?", (now-3600*1000,))}
        slots=list(range(1, MAX_SLOTS+1)); random.shuffle(slots)
        for k in slots:
            payable=round(float(base_amount)+k*10**-DECIMALS, DECIMALS)
            if payable not in busy:
                c.execute("INSERT OR REPLACE INTO crypto_payments(order_id,payable,created_ms,txid) VALUES(?,?,?,NULL)",(order_id,payable,now))
                c.commit(); c.close(); return payable
        c.rollback(); c.close(); raise RuntimeError("Too many pending orders for this amount. Try again shortly.")

    def _claim(self, order_id, txid):
        c=_conn()
        try:
            cur=c.execute("UPDATE crypto_payments SET txid=? WHERE order_id=? AND txid IS NULL",(str(txid),order_id)); c.commit(); return cur.rowcount==1
        except sqlite3.IntegrityError: return False
        finally: c.close()

    def create_order(self, merchant_trade_no, amount, description):
        if not RPC_URL: raise RuntimeError("Set NODEREAL_RPC_URL in Railway Variables.")
        if not DEPOSIT_ADDRESS or not USDT_CONTRACT_ADDRESS: raise RuntimeError("Set DEPOSIT_ADDRESS and USDT_CONTRACT_ADDRESS in Railway Variables.")
        if not DEPOSIT_ADDRESS.startswith("0x") or len(DEPOSIT_ADDRESS)!=42: raise RuntimeError("DEPOSIT_ADDRESS does not look like a BSC address.")
        if not USDT_CONTRACT_ADDRESS.startswith("0x") or len(USDT_CONTRACT_ADDRESS)!=42: raise RuntimeError("USDT_CONTRACT_ADDRESS does not look like a token contract address.")
        payable=self._reserve(merchant_trade_no, float(amount)); text=f"{payable:.{DECIMALS}f}"
        lines=[f"Send EXACTLY {text} USDT on BNB Smart Chain (BEP20):", f"USDT address: {DEPOSIT_ADDRESS}", "", "Use the BNB Smart Chain (BEP20) network only.", "After paying, press Check Payment."]
        return {"prepay_id":merchant_trade_no,"checkout_url":"","amount_text":text,"details":"\n".join(lines)}

    def query_order(self, order_id):
        row=get_payment_info(order_id)
        if not row: return "UNKNOWN"
        if row["txid"]: return "PAID"
        if not RPC_URL or not DEPOSIT_ADDRESS or not USDT_CONTRACT_ADDRESS:
            raise RuntimeError("Missing NODEREAL_RPC_URL, DEPOSIT_ADDRESS or USDT_CONTRACT_ADDRESS.")
        try:
            latest=int(self._rpc("eth_blockNumber", []),16)
            # ~5 minutes of BSC blocks; the amount slot plus order creation time narrows matching.
            from_block=max(0, latest-1800)
            topic_to="0x" + DEPOSIT_ADDRESS.lower().replace("0x", "").rjust(64,"0")
            logs=self._rpc("eth_getLogs", [{"fromBlock":hex(from_block),"toBlock":hex(latest),"address":USDT_CONTRACT_ADDRESS,"topics":[TRANSFER_TOPIC,None,topic_to]}]) or []
            payable=Decimal(str(row["payable"]))
            since=int(row["created_ms"])-5000
            for item in logs:
                try:
                    block=int(item["blockNumber"],16)
                    if latest-block+1 < CONFIRMATIONS: continue
                    txid=item["transactionHash"].lower()
                    # Fetch block timestamp to avoid matching old transfers of the same amount.
                    blockdata=self._rpc("eth_getBlockByNumber", [hex(block), False])
                    timestamp=int(blockdata["timestamp"],16)*1000
                    if timestamp < since: continue
                    raw=int(item["data"],16)
                    received=Decimal(raw)/(Decimal(10)**TOKEN_DECIMALS)
                    if abs(received-payable) > Decimal(1)/(Decimal(10)**(DECIMALS+2)): continue
                    if self._claim(order_id, "bsc:"+txid): return "PAID"
                except (KeyError, ValueError, TypeError, InvalidOperation):
                    continue
            return "PENDING"
        except Exception:
            log.exception("BSC payment verification failed for order %s", order_id)
            raise
