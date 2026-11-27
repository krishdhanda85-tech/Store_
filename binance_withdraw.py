"""
[v3] Owner-only Binance withdrawal to your supplier wallet (separate from the receive/Pay code).

Fill the CONFIG block below (values here are used when the Railway variable of the same name is empty).
Safety built in: only ONE fixed address (WD_ADDRESS), a max amount per withdrawal, and /withdraw needs
an explicit "yes". Use a SEPARATE Binance API key with "Enable Withdrawals" and trusted IP = the proxy IP.

v2: withdraw() takes an order_id (Binance `withdrawOrderId`). Binance refuses a second withdrawal with the
same order id, and find_by_order_id() can look a withdrawal up again later. That makes top-ups crash-safe:
if the bot dies right after sending the request, it can still find out whether the money left Binance.

v3: signed requests sync their clock with Binance (error -1021 fixed automatically), read-only calls retry on
network errors / 429 / 5xx, api_restrictions() lets the health check verify the key's permissions, and
TOPUP_DRY_RUN=1 turns every real withdrawal off for testing.
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


_offset = 0                                  # ms: Binance server time minus local time
_RETRY_HTTP = (429, 500, 502, 503, 504)


def _proxies():
    proxy = cfg("BINANCE_WD_PROXY")
    return {"https": proxy, "http": proxy} if proxy else None


def _sync_time():
    global _offset
    try:
        r = requests.get(f"{BASE}/api/v3/time", proxies=_proxies(), timeout=10)
        _offset = int(r.json()["serverTime"]) - int(time.time() * 1000)
    except Exception:
        pass


def dry_run():
    """TOPUP_DRY_RUN=1 -> supplier_topup does everything except the real withdrawal."""
    return os.getenv("TOPUP_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")


def _signed(method, path, params, retry=None):
    """Signed Binance call. Read-only (GET) calls retry on network errors / 429 / 5xx; a withdrawal (POST) is NEVER
    retried blindly (supplier_topup reconciles it by order id), except for the clock error -1021 where Binance
    rejected the request before doing anything."""
    retry = (method == "GET") if retry is None else retry
    attempt, resynced = 0, False
    while True:
        attempt += 1
        p = dict(params, timestamp=int(time.time() * 1000) + _offset, recvWindow=10000)
        qs = urlencode(p)
        sig = hmac.new(cfg("BINANCE_WD_SECRET").encode(), qs.encode(), hashlib.sha256).hexdigest()
        try:
            r = requests.request(method, f"{BASE}{path}?{qs}&signature={sig}",
                                 headers={"X-MBX-APIKEY": cfg("BINANCE_WD_KEY")}, proxies=_proxies(), timeout=25)
        except requests.RequestException as e:
            if retry and attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            raise RuntimeError(f"Binance network error: {type(e).__name__}")
        try:
            data = r.json()
        except ValueError:
            data = {"msg": r.text[:200]}
        if isinstance(data, dict) and data.get("code") == -1021 and not resynced:
            resynced = True                       # our clock is off: resync once and sign again
            _sync_time()
            continue
        if r.status_code == 451:
            raise RuntimeError("Binance 451: this proxy IP is in a blocked region. Use a non-US proxy.")
        if r.status_code in _RETRY_HTTP and retry and attempt < 3:
            time.sleep(2 ** (attempt - 1))
            continue
        if r.status_code != 200:
            msg = data.get("msg", data) if isinstance(data, dict) else data
            raise RuntimeError(f"Binance HTTP {r.status_code}: {msg}")
        return data


def api_restrictions():
    """Permissions of the withdrawal API key (used by the health check)."""
    return _signed("GET", "/sapi/v1/account/apiRestrictions", {})


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


def withdraw(amount, address=None, network=None, order_id=None):
    """Send `amount` to `address` (default WD_ADDRESS). Returns Binance withdrawal id.
    supplier_topup passes the supplier's own wallet from the admin site / SUPPLIER_PAY only.
    order_id (<=32 chars) makes the request idempotent on Binance's side."""
    amount = parse_amount(amount)
    params = {"coin": cfg("WD_COIN"), "network": (network or cfg("WD_NETWORK")),
              "address": (address or cfg("WD_ADDRESS")), "amount": format(amount, "f")}
    if order_id:
        params["withdrawOrderId"] = str(order_id)[:32]
    data = _signed("POST", "/sapi/v1/capital/withdraw/apply", params)
    return data.get("id")


def withdraw_fee(network=None):
    """(fee, minimum) Binance charges for WD_COIN on this network. The fee is taken OUT of the amount you send,
    so to make the supplier receive exactly X you must withdraw X + fee."""
    data = _signed("GET", "/sapi/v1/capital/config/getall", {})
    coin, net = cfg("WD_COIN"), (network or cfg("WD_NETWORK"))
    for c in data:
        if c.get("coin") == coin:
            for n in c.get("networkList", []):
                if n.get("network") == net:
                    return Decimal(str(n["withdrawFee"])), Decimal(str(n.get("withdrawMin", "0")))
    raise RuntimeError(f"Binance fee for {coin} on {net} not found")


def find_by_order_id(order_id):
    """The Binance withdrawal sent with this order_id, or None if Binance has no such withdrawal."""
    rows = _signed("GET", "/sapi/v1/capital/withdraw/history",
                   {"coin": cfg("WD_COIN"), "withdrawOrderId": str(order_id)[:32], "limit": 5})
    for r in rows:
        if str(r.get("withdrawOrderId") or "") == str(order_id)[:32]:
            return r
    return None


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
