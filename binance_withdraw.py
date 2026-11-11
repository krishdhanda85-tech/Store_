"""
Owner-only Binance withdrawal to your supplier wallet (separate from the receive/Pay code).

Fill the CONFIG block below (values here are used when the Railway variable of the same name is empty).
Safety built in: only ONE fixed address (WD_ADDRESS), a max amount per withdrawal, and /withdraw needs
an explicit "yes". Use a SEPARATE Binance API key with "Enable Withdrawals" and trusted IP = the proxy IP.
"""
import hashlib
import hmac
import os
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import requests

# ----------------------------------------------------------------- CONFIG (hard-coded)
_CFG = {
    "BINANCE_WD_PROXY": "",
    "BINANCE_WD_KEY": "PASTE_WITHDRAW_API_KEY",
    "BINANCE_WD_SECRET": "PASTE_WITHDRAW_API_SECRET",
    "WD_ADDRESS": "PASTE_SUPPLIER_WALLET_ADDRESS",
    "WD_COIN": "USDT",
    "WD_NETWORK": "BSC",          # BSC = BEP20, TRX = TRC20, ETH = ERC20 ... must match the supplier's address
    "WD_MAX": "60",               # biggest amount allowed per withdrawal
}
BASE = os.getenv("BINANCE_API_BASE", "https://api.binance.com").rstrip("/")


def cfg(name):
    return (os.getenv(name) or _CFG[name]).strip()


def configured():
    return not any(cfg(k).startswith("PASTE_") for k in ("BINANCE_WD_KEY", "BINANCE_WD_SECRET", "WD_ADDRESS"))


def _signed(method, path, params):
    params = dict(params, timestamp=int(time.time() * 1000), recvWindow=10000)
    qs = urlencode(params)
    sig = hmac.new(cfg("BINANCE_WD_SECRET").encode(), qs.encode(), hashlib.sha256).hexdigest()
    proxy = cfg("BINANCE_WD_PROXY")
    proxies = {"https": proxy, "http": proxy} if proxy else None
    r = requests.request(method, f"{BASE}{path}?{qs}&signature={sig}",
                         headers={"X-MBX-APIKEY": cfg("BINANCE_WD_KEY")}, proxies=proxies, timeout=25)
    try:
        data = r.json()
    except ValueError:
        data = {"msg": r.text[:200]}
    if r.status_code == 451:
        raise RuntimeError("Binance 451: this proxy IP is in a blocked region. Use a non-US proxy.")
    if r.status_code != 200:
        raise RuntimeError(f"Binance HTTP {r.status_code}: {data.get('msg', data)}")
    return data


def parse_amount(text):
    try:
        a = Decimal(str(text).replace(",", "."))
    except InvalidOperation:
        raise ValueError("Amount must be a number, e.g. 50")
    if a <= 0:
        raise ValueError("Amount must be above 0.")
    if a > Decimal(cfg("WD_MAX")):
        raise ValueError(f"Amount is above the limit of {cfg('WD_MAX')} {cfg('WD_COIN')} per withdrawal.")
    return a


def spot_balance():
    """Free balance of WD_COIN in the withdrawal account."""
    data = _signed("GET", "/api/v3/account", {"omitZeroBalances": "true"})
    coin = cfg("WD_COIN")
    for b in data.get("balances", []):
        if b["asset"] == coin:
            return Decimal(b["free"])
    return Decimal(0)


def keys_ready():
    """API key + secret filled (used by per-supplier top-ups, which bring their own address)."""
    return not any(cfg(k).startswith("PASTE_") for k in ("BINANCE_WD_KEY", "BINANCE_WD_SECRET"))


def withdraw(amount, address=None, network=None):
    """Send `amount` to `address` (default WD_ADDRESS). Returns Binance withdrawal id.
    supplier_topup passes the supplier's own wallet from its fixed SUPPLIER_PAY list only."""
    amount = parse_amount(amount)
    data = _signed("POST", "/sapi/v1/capital/withdraw/apply", {
        "coin": cfg("WD_COIN"), "network": (network or cfg("WD_NETWORK")),
        "address": (address or cfg("WD_ADDRESS")), "amount": format(amount, "f")})
    return data.get("id")


def wait_txid(wid, tries=18, delay=10):
    """Poll Binance until the withdrawal has an on-chain hash (TXID). Returns it, or None if not ready yet."""
    for _ in range(tries):
        rows = _signed("GET", "/sapi/v1/capital/withdraw/history", {"coin": cfg("WD_COIN"), "limit": 20})
        for r in rows:
            if str(r.get("id")) == str(wid) and r.get("txId"):
                return r["txId"]
            if str(r.get("id")) == str(wid) and r.get("status") in (1, 3, 5):   # cancelled / rejected / failure
                raise RuntimeError(f"Binance withdrawal status {r.get('status')}: it was not sent.")
        time.sleep(delay)
    return None
