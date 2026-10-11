"""
Binance Pay adapter.

This module intentionally keeps Binance credentials server-side.
The exact Binance Pay API signing/request format can change, so keep the
adapter isolated from the Telegram bot. Configure BINANCE_API_KEY and
BINANCE_SECRET_KEY and implement/update these two methods against the
current Binance Pay Merchant API documentation for your account.

Do not put secret keys in Telegram messages or source code.
"""

import hashlib
import hmac
import json
import time
import uuid
import requests

class BinancePayClient:
    def __init__(self, api_key="", secret_key="", base_url="https://bpay.binanceapi.com"):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")

    def _headers(self, body):
        timestamp = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        payload = timestamp + "\n" + nonce + "\n" + body + "\n"
        signature = hmac.new(self.secret_key.encode(), payload.encode(), hashlib.sha512).hexdigest().upper()
        return {
            "Content-Type": "application/json",
            "BinancePay-Timestamp": timestamp,
            "BinancePay-Nonce": nonce,
            "BinancePay-Certificate-SN": self.api_key,
            "BinancePay-Signature": signature,
        }

    def create_order(self, merchant_trade_no, amount, description):
        if not self.api_key or not self.secret_key:
            raise RuntimeError("Binance Pay credentials are not configured.")
        payload = {
            "env": {"terminalType": "APP"},
            "merchantTradeNo": merchant_trade_no,
            "orderAmount": float(amount),
            "currency": "USDT",
            "goods": {
                "goodsType": "01",
                "goodsCategory": "Z000",
                "referenceGoodsId": merchant_trade_no,
                "goodsName": description,
                "goodsDetail": description,
            },
        }
        body = json.dumps(payload, separators=(",", ":"))
        r = requests.post(self.base_url + "/binancepay/openapi/v2/order", headers=self._headers(body), data=body, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "SUCCESS":
            raise RuntimeError(data.get("errorMessage", "Binance Pay order creation failed"))
        result = data.get("data", {})
        return {
            "prepay_id": result.get("prepayId") or result.get("checkoutUrl"),
            "checkout_url": result.get("checkoutUrl") or result.get("universalUrl"),
        }

    def query_order(self, merchant_trade_no):
        if not self.api_key or not self.secret_key:
            raise RuntimeError("Binance Pay credentials are not configured.")
        payload = {"merchantTradeNo": merchant_trade_no}
        body = json.dumps(payload, separators=(",", ":"))
        r = requests.post(self.base_url + "/binancepay/openapi/v2/order/query", headers=self._headers(body), data=body, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "SUCCESS":
            raise RuntimeError(data.get("errorMessage", "Binance Pay query failed"))
        return data.get("data", {}).get("status", "UNKNOWN")
