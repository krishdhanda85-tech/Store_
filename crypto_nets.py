"""Owner-managed crypto deposit networks (no code change needed to add a coin/network).

Stored as JSON in the shop's `settings` table (key 'crypto_nets'), so it works for every shop/tenant
and survives restarts/backups.  Managed from Telegram by the owner:

  /nets                                         list everything
  /addnet key | coin | network | address | memo(optional)
  /setaddr key | new address | memo(optional)
  /togglenet key                                show / hide in the bot
  /delnet key

The built-in BEP20 method remains compatible with Railway variables. Extra EVM token networks can
be configured from the website with an RPC URL, chain ID, token contract, decimals and confirmations.
Those networks are automatically verified on-chain. Non-EVM networks are not auto-verified by this adapter.
"""
import json
import re

SETTING = "crypto_nets"
KEY_RE = re.compile(r"^[a-z0-9]{2,12}$")
RESERVED = {"bsc", "bpay", "upi", "crypto", "all"}

_EVM_WORDS = ("bep20", "bep-20", "bsc", "erc20", "erc-20", "ethereum", "polygon", "matic", "arbitrum",
              "optimism", "base", "plasma", "avalanche", "avax", "evm", "linea", "opbnb")


def _load(db):
    raw = db.get_setting(SETTING, "")
    try:
        data = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [n for n in data if isinstance(n, dict) and n.get("key")]


def _save(db, nets):
    db.set_setting(SETTING, json.dumps(nets, ensure_ascii=False))


def all_nets(db):
    return _load(db)


def active_nets(db):
    return [n for n in _load(db) if n.get("active", True) and n.get("address")]


def get(db, key):
    key = (key or "").strip().lower()
    for n in _load(db):
        if n["key"] == key:
            return n
    return None


def check_address(network, address):
    """Catch obvious typos/wrong-chain addresses (funds sent to a bad address are lost). Returns error text or None."""
    net = (network or "").lower()
    if len(address) < 10 or len(address) > 200 or re.search(r"\s", address):
        return "Address looks wrong (too short/long or contains spaces)."
    if "trc" in net or "tron" in net:
        if not (address.startswith("T") and len(address) == 34):
            return "A TRC20/Tron address starts with T and is 34 characters long."
    elif any(w in net for w in _EVM_WORDS):
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
            return "An EVM address (BEP20/ERC20/Plasma/Polygon...) starts with 0x and is 42 characters long."
    return None


def add(db, key, coin, network, address, memo="", rpc_url="", chain_id="", contract="", decimals="6", confirmations="12"):
    key = (key or "").strip().lower()
    if not KEY_RE.match(key) or key in RESERVED:
        raise ValueError("Key must be 2-12 letters/digits (e.g. trc20, plasma, btc) and not a reserved word.")
    coin = (coin or "").strip().upper()
    network = (network or "").strip()
    address = (address or "").strip()
    if not coin or len(coin) > 12 or not network or len(network) > 40:
        raise ValueError("Coin (e.g. USDT) and network name (e.g. Tron (TRC20)) are required.")
    err = check_address(network, address)
    if err:
        raise ValueError(err)
    nets = _load(db)
    if any(n["key"] == key for n in nets):
        raise ValueError(f"'{key}' already exists. Use /setaddr {key} | <address> to change it.")
    if len(nets) >= 12:
        raise ValueError("Maximum 12 extra networks.")
    evm = any(w in network.lower() for w in _EVM_WORDS)
    cfg = {"key": key, "coin": coin, "network": network, "address": address,
           "memo": (memo or "").strip()[:60], "active": True,
           "rpc_url": (rpc_url or "").strip(), "chain_id": int(chain_id or 0),
           "contract": (contract or "").strip(), "decimals": int(decimals or 6),
           "confirmations": max(1, int(confirmations or 12)), "auto_verify": False}
    if evm and cfg["rpc_url"] and cfg["chain_id"] > 0 and re.fullmatch(r"0x[0-9a-fA-F]{40}", cfg["contract"]) and 0 <= cfg["decimals"] <= 36:
        cfg["auto_verify"] = True
    nets.append(cfg)
    _save(db, nets)


def set_address(db, key, address, memo=None):
    nets = _load(db)
    for n in nets:
        if n["key"] == (key or "").strip().lower():
            err = check_address(n["network"], address.strip())
            if err:
                raise ValueError(err)
            n["address"] = address.strip()
            if memo is not None:
                n["memo"] = memo.strip()[:60]
            _save(db, nets)
            return
    raise ValueError("No such network key. See /nets")


def toggle(db, key):
    nets = _load(db)
    for n in nets:
        if n["key"] == (key or "").strip().lower():
            n["active"] = not n.get("active", True)
            _save(db, nets)
            return n["active"]
    raise ValueError("No such network key. See /nets")


def remove(db, key):
    nets = _load(db)
    keep = [n for n in nets if n["key"] != (key or "").strip().lower()]
    if len(keep) == len(nets):
        raise ValueError("No such network key. See /nets")
    _save(db, keep)


def label(n):
    return f"{n['coin']} · {n['network']}"


def update_config(db, key, rpc_url, chain_id, contract, decimals, confirmations):
    nets = _load(db)
    for n in nets:
        if n["key"] == (key or "").strip().lower():
            n["rpc_url"] = (rpc_url or "").strip()
            n["chain_id"] = int(chain_id or 0)
            n["contract"] = (contract or "").strip()
            n["decimals"] = int(decimals or 6)
            n["confirmations"] = max(1, int(confirmations or 12))
            n["auto_verify"] = bool(n["rpc_url"] and n["chain_id"] > 0 and
                re.fullmatch(r"0x[0-9a-fA-F]{40}", n["contract"]) and 0 <= n["decimals"] <= 36)
            _save(db, nets)
            return n
    raise ValueError("No such network key.")
