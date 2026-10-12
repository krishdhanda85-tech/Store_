"""
Direct-wallet payment checker (no Binance merchant account needed).

Customer pays YOUR Binance Pay ID and/or YOUR USDT deposit address.
The bot verifies the payment automatically using a READ-ONLY Binance API key:
  - Binance Pay transfers : GET /sapi/v1/pay/transactions
  - On-chain deposits     : GET /sapi/v1/capital/deposit/hisrec

Each order gets a unique payable amount (e.g. 1.00 -> 1.017) so that every
payment can be matched to exactly one order. Each transaction can be used once.

bot.py needs no change: this file keeps the same class/method names.
"""

import hashlib
import hmac
import logging
import os
import random
import sqlite3
import time
from urllib.parse import urlencode

import requests

log = logging.getLogger("digital-shop")

API_BASE = os.getenv("BINANCE_API_BASE", "https://api.binance.com").rstrip("/")
DB_PATH = os.getenv("DB_PATH", "shop.db")
PAY_ID = os.getenv("BINANCE_PAY_ID", "").strip()
DEPOSIT_ADDRESS = os.getenv("DEPOSIT_ADDRESS", "").strip()
DEPOSIT_NETWORK = os.getenv("DEPOSIT_NETWORK", "").strip()
DECIMALS = int(os.getenv("AMOUNT_DECIMALS", "3"))
MAX_SLOTS = 49 if DECIMALS >= 3 else 20
TOLERANCE = 10 ** -(DECIMALS + 2)


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
    c.execute(
        """CREATE TABLE IF NOT EXISTS crypto_payments (
            order_id TEXT PRIMARY KEY,
            payable REAL NOT NULL,
            created_ms INTEGER NOT NULL,
            txid TEXT UNIQUE
        )"""
    )
    c.commit()
    c.close()


def get_payment_info(order_id):
    ensure_schema()
    c = _conn()
    r = c.execute("SELECT * FROM crypto_payments WHERE order_id=?", (order_id,)).fetchone()
    c.close()
    return r


class BinancePayClient:
    # Same constructor as before so bot.py works unchanged (base_url is ignored).
    def __init__(self, api_key="", secret_key="", base_url=""):
        self.api_key = api_key
        self.secret_key = secret_key
        self._cache = {}
        ensure_schema()

    # ------------------------------------------------------------ helpers
    def _signed_get(self, path, params):
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 10000
        query = urlencode(params)
        sig = hmac.new(self.secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()
        r = requests.get(
            f"{API_BASE}{path}?{query}&signature={sig}",
            headers={"X-MBX-APIKEY": self.api_key},
            timeout=20,
        )
        if r.status_code != 200:
            log.error("Binance API %s -> HTTP %s: %s", path, r.status_code, r.text[:300])
            raise RuntimeError(f"Binance API error {r.status_code}: {r.text[:200]}")
        return r.json()

    def _cached(self, key, fn, ttl=5):
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        value = fn()
        self._cache[key] = (now, value)
        return value

    def _pay_history(self):
        def load():
            start = int((time.time() - 6 * 3600) * 1000)
            data = self._signed_get(
                "/sapi/v1/pay/transactions", {"startTime": start, "limit": 100}
            )
            rows = data.get("data", []) if isinstance(data, dict) else data
            return rows or []
        return self._cached("pay", load)

    def _deposits(self):
        def load():
            start = int((time.time() - 6 * 3600) * 1000)
            rows = self._signed_get(
                "/sapi/v1/capital/deposit/hisrec",
                {"coin": "USDT", "status": 1, "startTime": start, "limit": 1000},
            )
            return rows if isinstance(rows, list) else []
        return self._cached("dep", load)

    # ------------------------------------------------------------ reserve
    def _reserve(self, order_id, base_amount):
        c = _conn()
        c.execute("BEGIN IMMEDIATE")
        now_ms = int(time.time() * 1000)
        busy = {
            round(r["payable"], DECIMALS)
            for r in c.execute(
                "SELECT payable FROM crypto_payments WHERE txid IS NULL AND created_ms>?",
                (now_ms - 3600 * 1000,),
            )
        }
        slots = list(range(1, MAX_SLOTS + 1))
        random.shuffle(slots)
        for k in slots:
            payable = round(base_amount + k * 10 ** -DECIMALS, DECIMALS)
            if payable not in busy:
                c.execute(
                    "INSERT OR REPLACE INTO crypto_payments(order_id,payable,created_ms,txid) VALUES(?,?,?,NULL)",
                    (order_id, payable, now_ms),
                )
                c.commit()
                c.close()
                return payable
        c.rollback()
        c.close()
        raise RuntimeError("Too many pending orders for this amount, try again in a few minutes.")

    def _claim(self, order_id, txid):
        c = _conn()
        try:
            cur = c.execute(
                "UPDATE crypto_payments SET txid=? WHERE order_id=? AND txid IS NULL",
                (str(txid), order_id),
            )
            c.commit()
            return cur.rowcount == 1
        except sqlite3.IntegrityError:
            return False  # this transaction already paid another order
        finally:
            c.close()

    # ---------------------------------------------------------- bot hooks
    def create_order(self, merchant_trade_no, amount, description):
        if not self.api_key or not self.secret_key:
            raise RuntimeError("Binance API key/secret not configured.")
        if not PAY_ID and not DEPOSIT_ADDRESS:
            raise RuntimeError("Set BINANCE_PAY_ID and/or DEPOSIT_ADDRESS.")
        payable = self._reserve(merchant_trade_no, float(amount))
        text = f"{payable:.{DECIMALS}f}"
        lines = ["", "", f"Send EXACTLY {text} USDT (different amount cannot be matched):"]
        if PAY_ID:
            lines.append(f"Binance Pay ID: {PAY_ID}")
        if DEPOSIT_ADDRESS:
            net = f" ({DEPOSIT_NETWORK})" if DEPOSIT_NETWORK else ""
            lines.append(f"USDT address{net}: {DEPOSIT_ADDRESS}")
        lines += ["", "After paying, press Check Payment."]
        return {
            "prepay_id": merchant_trade_no,  # bot passes this back to query_order
            "checkout_url": "",              # no website needed, everything stays in the bot
            "amount_text": text,
            "details": "\n".join(lines),
        }

    def query_order(self, order_id):
        row = get_payment_info(order_id)
        if not row:
            return "UNKNOWN"
        if row["txid"]:
            return "PAID"
        payable = row["payable"]
        since = row["created_ms"] - 5000

        if PAY_ID:
            for t in self._pay_history():
                try:
                    if (
                        t.get("orderType") == "C2C"
                        and t.get("currency") == "USDT"
                        and float(t.get("amount", 0)) > 0
                        and abs(float(t["amount"]) - payable) < TOLERANCE
                        and int(t.get("transactionTime", 0)) >= since
                    ):
                        if self._claim(order_id, "pay:" + str(t.get("transactionId"))):
                            return "PAID"
                except (TypeError, ValueError):
                    continue

        if DEPOSIT_ADDRESS:
            for d in self._deposits():
                try:
                    if (
                        str(d.get("address", "")).lower() == DEPOSIT_ADDRESS.lower()
                        and abs(float(d["amount"]) - payable) < TOLERANCE
                        and int(d.get("insertTime", 0)) >= since
                    ):
                        if self._claim(order_id, "dep:" + str(d.get("txId"))):
                            return "PAID"
                except (TypeError, ValueError):
                    continue
        return "PENDING"
