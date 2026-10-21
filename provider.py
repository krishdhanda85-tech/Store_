"""
Multi-supplier bridge for the Digital Store bot.

You can add ANY number of suppliers (APIs) on the admin site -> "Suppliers API".
Each shop product can be linked to one product on each supplier, and YOU choose
where it is bought from:

  * "I choose"  : your chosen supplier is used first (e.g. Gemini @ 0.37 on supplier A)
  * "Cheapest"  : the cheapest supplier that has enough stock is used
  * Fallback    : if the first supplier fails or is out of stock, the next one is tried

Flow when a buyer pays: your own stock is used first; only the missing quantity is
bought from the chosen supplier, put into your stock, then the normal
buy_with_balance() debits the buyer and delivers.

API formats (each supplier can have a different one):
  * cupponhub : the original format  (?action=products / balance / purchase, X-API-Key)
  * generic   : any JSON API - you map the URL paths and field names in a small JSON
                config on the Suppliers page (see GENERIC_DEFAULT below)

Env (optional): CUPPON_API_KEY / PROVIDER_API_BASE still work - they become a supplier
called "CupponHub" automatically, so old setups keep working.
API keys of suppliers are stored ENCRYPTED (same secret as the reseller keys:
API_KEY_SECRET, else BOT_TOKEN).
"""
import base64
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from db import Database

try:
    from cryptography.fernet import Fernet
except Exception:       # library missing -> supplier keys cannot be stored
    Fernet = None

log = logging.getLogger("provider")

ENV_BASE = os.getenv("PROVIDER_API_BASE", "https://cupponhub.shop/reseller/api/")
ENV_KEY = os.getenv("CUPPON_API_KEY", "")
ENABLED = True            # suppliers are managed on the admin site now
MAX_QTY = 500

_buy_lock = threading.RLock()
_cat_cache = {}           # (supplier id, base_url, key) -> (time, [normalized products]); key parts keep shops apart


def _ck(s):
    return (s["id"], s.get("base_url") or "", s.get("key") or "")


def _cache_drop(sid):
    for k in [k for k in _cat_cache if k[0] == sid]:
        _cat_cache.pop(k, None)


class ProviderError(Exception):
    """The supplier answered with an error. rejected=True means it clearly refused the request
    (nothing was bought), so trying another supplier is safe."""

    def __init__(self, status, code, rejected=True):
        super().__init__(f"{status}: {code}")
        self.status, self.code, self.rejected = status, code, rejected


class Unreachable(RuntimeError):
    """Could not talk to the supplier. clean=True means the request never left (connection
    refused/DNS), so nothing was bought and another supplier may be tried."""

    def __init__(self, msg, clean):
        super().__init__(msg)
        self.clean = clean


def _clean_failure(e):
    """True when it is certain no money was spent, so falling back to another supplier is safe."""
    if isinstance(e, ProviderError):
        return e.rejected and (e.status < 500 or e.status == 503)
    return bool(getattr(e, "clean", False))


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ key encryption
def _fernet():
    if Fernet is None:
        return None
    secret = os.getenv("API_KEY_SECRET") or os.getenv("BOT_TOKEN") or ""
    if not secret:
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(text):
    f = _fernet()
    if not f:
        raise ValueError("Cannot store the key safely: set API_KEY_SECRET (and install 'cryptography').")
    return f.encrypt(text.encode()).decode()


def decrypt(token):
    f = _fernet()
    if not f or not token:
        return ""
    try:
        return f.decrypt(token.encode()).decode()
    except Exception:
        return ""


# ------------------------------------------------------------------ format 1: cupponhub
def _ch_call(s, method, action, params=None, body=None, idem=None):
    headers = {"X-API-Key": s["key"]}
    if idem:
        headers["Idempotency-Key"] = idem
    r = requests.request(method, s["base_url"], headers=headers, json=body, timeout=20,
                         params={"action": action, **(params or {})})
    try:
        data = r.json()
    except ValueError:
        raise ProviderError(r.status_code, "bad_response", rejected=False)
    if not data.get("ok"):
        raise ProviderError(r.status_code, data.get("error", "unknown"))
    return data


def _ch_catalog(s):
    return [{"key": str(p["key"]), "name": str(p["name"]),
             "cost": float(p["price_usdt_from"]), "stock": int(p["stock"])}
            for p in _ch_call(s, "GET", "products")["products"]]


def _ch_balance(s):
    return float(_ch_call(s, "GET", "balance")["balance_usdt"])


def _ch_purchase(s, key, qty, idem):
    o = _ch_call(s, "POST", "purchase", body={"product": key, "quantity": qty}, idem=idem)["order"]
    return {"order_id": str(o["order_id"]), "items": [str(x) for x in o["items"]],
            "total": float(o["total_usdt"]), "stock_left": int(o["stock_left"])}


# ------------------------------------------------------------------ format 2: generic JSON
# Edit this on the Suppliers page. Paths are added to the Base URL. Field names can be dotted
# ("data.items"). Only change what differs from your supplier's documentation.
GENERIC_DEFAULT = {
    "auth_header": "Authorization",        # e.g. "X-API-Key"
    "auth_prefix": "Bearer ",              # use "" when the key is sent as-is
    "products": {"method": "GET", "path": "/products", "list": "products",
                 "key": "id", "name": "name", "cost": "price", "stock": "stock"},
    "balance": {"method": "GET", "path": "/balance", "field": "balance"},
    "purchase": {"method": "POST", "path": "/purchase", "send": "json",
                 "product_param": "product_id", "qty_param": "quantity",
                 "idem_header": "Idempotency-Key",
                 "order_id": "order_id", "items": "items", "item_field": "",
                 "total": "total", "stock_left": "stock_left"},
}


def _dig(obj, path):
    if path in (None, ""):
        return obj
    for part in str(path).split("."):
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def _gcfg(s):
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in GENERIC_DEFAULT.items()}
    for k, v in (s.get("cfg") or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def _g_req(s, section, params=None, body=None, idem=None):
    cfg = _gcfg(s)
    sec = cfg[section]
    headers = {cfg["auth_header"]: f"{cfg['auth_prefix']}{s['key']}"}
    if idem and sec.get("idem_header"):
        headers[sec["idem_header"]] = idem
    url = s["base_url"].rstrip("/") + "/" + str(sec["path"]).lstrip("/")
    kw = {"headers": headers, "timeout": 20}
    send = sec.get("send", "json")
    if body is not None:
        if send == "form":
            kw["data"] = body
        elif send == "query":
            params = {**(params or {}), **body}
        else:
            kw["json"] = body
    if params:
        kw["params"] = params
    r = requests.request(sec.get("method", "GET"), url, **kw)
    try:
        data = r.json()
    except ValueError:
        raise ProviderError(r.status_code, "bad_response", rejected=False)
    if r.status_code >= 400:
        msg = _dig(data, "error") or _dig(data, "message") or "error"
        raise ProviderError(r.status_code, str(msg)[:80])
    if isinstance(data, dict) and (data.get("ok") is False or data.get("success") is False):
        raise ProviderError(r.status_code, str(data.get("error") or data.get("message") or "rejected")[:80])
    return data


def _g_catalog(s):
    sec = _gcfg(s)["products"]
    data = _g_req(s, "products")
    lst = _dig(data, sec.get("list"))
    if not isinstance(lst, list):
        raise ProviderError(200, "products_list_not_found_check_config", rejected=False)
    out = []
    for p in lst:
        key = _dig(p, sec["key"])
        if key is None:
            continue
        try:
            cost = float(_dig(p, sec["cost"]) or 0)
            stock = int(float(_dig(p, sec["stock"]) or 0))
        except (TypeError, ValueError):
            continue
        out.append({"key": str(key), "name": str(_dig(p, sec["name"]) or key), "cost": cost, "stock": stock})
    return out


_BALANCE_GUESSES = ("balance", "balance_usdt", "balance_usd", "wallet", "wallet_balance", "available_balance",
                    "credit", "credits", "funds", "amount", "data.balance", "data.wallet", "data.credits",
                    "data.amount", "data.available_balance", "result.balance", "user.balance",
                    "account.balance", "profile.balance", "data.user.balance", "data.0.balance")


def _to_number(v):
    """float from 1.5 / "1.5" / "$1,234.50" / {"balance": 1.5}; None when it is not a number."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, dict):
        for k in ("balance", "amount", "usdt", "usd", "value", "available"):
            n = _to_number(v.get(k))
            if n is not None:
                return n
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = "".join(ch for ch in str(v) if ch.isdigit() or ch in ".-")
    try:
        return float(t) if t not in ("", ".", "-") else None
    except ValueError:
        return None


def _g_balance(s):
    sec = _gcfg(s)["balance"]
    data = _g_req(s, "balance")
    # the configured field first, then the usual names, so a different field name still works
    for path in [sec.get("field"), *_BALANCE_GUESSES]:
        if path in (None, ""):
            continue
        n = _to_number(_dig(data, path))
        if n is not None:
            return n
    keys = ", ".join(list(data.keys())[:8]) if isinstance(data, dict) else type(data).__name__
    raise ProviderError(200, f"no balance field found. Response keys: {keys}", rejected=False)


def _g_purchase(s, key, qty, idem):
    sec = _gcfg(s)["purchase"]
    data = _g_req(s, "purchase", body={sec["product_param"]: key, sec["qty_param"]: qty}, idem=idem)
    raw = _dig(data, sec.get("items"))
    if isinstance(raw, str):
        items = [x.strip() for x in raw.splitlines() if x.strip()]
    elif isinstance(raw, list):
        items = []
        for x in raw:
            if isinstance(x, dict):
                v = _dig(x, sec.get("item_field")) if sec.get("item_field") else json.dumps(x, ensure_ascii=False)
                items.append(str(v))
            else:
                items.append(str(x))
    else:
        items = []
    try:
        total = float(_dig(data, sec.get("total")) or 0)
    except (TypeError, ValueError):
        total = 0.0
    left = _dig(data, sec.get("stock_left"))
    try:
        left = int(float(left)) if left is not None else None
    except (TypeError, ValueError):
        left = None
    return {"order_id": str(_dig(data, sec.get("order_id")) or idem), "items": items,
            "total": total, "stock_left": left}


ADAPTERS = {
    "cupponhub": (_ch_catalog, _ch_balance, _ch_purchase),
    "generic": (_g_catalog, _g_balance, _g_purchase),
}
FORMATS = {
    "cupponhub": "CupponHub style (?action=products / balance / purchase)",
    "generic": "Generic JSON API (map the fields in the config)",
}


def _adapter(s):
    a = ADAPTERS.get(s.get("fmt"))
    if not a:
        raise ProviderError(0, f"unknown_format_{s.get('fmt')}")
    if not s.get("key") or not s.get("base_url"):
        raise ProviderError(0, "supplier_not_configured")
    return a


def catalog_for(s, max_age=0):
    """Normalized product list of one supplier: [{key, name, cost, stock}]."""
    hit = _cat_cache.get(_ck(s))
    if max_age and hit and time.time() - hit[0] < max_age:
        return hit[1]
    data = _adapter(s)[0](s)
    _cat_cache[_ck(s)] = (time.time(), data)
    return data


def balance_for(s):
    return _adapter(s)[1](s)


def _supplier(r):
    d = dict(r)
    if d.get("from_env"):
        d["base_url"], d["key"] = ENV_BASE, ENV_KEY
    else:
        d["key"] = decrypt(d.get("key_enc"))
    try:
        d["cfg"] = json.loads(d.get("cfg") or "{}")
    except ValueError:
        d["cfg"] = {}
    d["key_hint"] = ("••••" + d["key"][-4:]) if d["key"] else ""
    return d


class ShopDB(Database):
    """Database + supplier-linked products. Drop-in replacement for Database."""

    def __init__(self, path, main=None):
        # The main shop is the one living in DB_PATH. Only it may use the owner's CUPPON_API_KEY.
        if main is None:
            main = os.path.abspath(path) == os.path.abspath(os.getenv("DB_PATH", "shop.db"))
        self.is_main = bool(main)
        super().__init__(path)
        try:
            self._ensure_tables()
        except Exception:
            log.exception("could not create supplier tables yet (init() will retry)")

    def init(self):
        super().init()
        self._ensure_tables()

    # ------------------------------------------------------------ schema + migration
    def _ensure_tables(self):
        c = self.conn()
        try:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS suppliers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                fmt TEXT NOT NULL DEFAULT 'cupponhub',
                base_url TEXT,
                key_enc TEXT,
                cfg TEXT,
                from_env INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created TEXT);
            CREATE TABLE IF NOT EXISTS product_sources(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                supplier_id INTEGER NOT NULL,
                provider_key TEXT NOT NULL,
                cost REAL NOT NULL DEFAULT 0,
                stock INTEGER NOT NULL DEFAULT 0,
                last_sync TEXT,
                UNIQUE(product_id, supplier_id),
                UNIQUE(supplier_id, provider_key));
            CREATE TABLE IF NOT EXISTS source_settings(
                product_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'manual',     -- manual / cheapest
                chosen INTEGER,                          -- supplier tried first in manual mode
                fallback INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS provider_orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idem TEXT UNIQUE,
                product_id INTEGER,
                qty INTEGER,
                status TEXT,              -- pending / received / stocked / failed / partial / unknown
                provider_order_id TEXT,
                total_cost REAL,
                items TEXT,               -- delivered values, kept for recovery
                error TEXT,
                created TEXT);
            """)
            po = [r[1] for r in c.execute("PRAGMA table_info(provider_orders)").fetchall()]
            if "supplier_id" not in po:
                c.execute("ALTER TABLE provider_orders ADD COLUMN supplier_id INTEGER")
                c.commit()

            cols = [r[1] for r in c.execute("PRAGMA table_info(provider_map)").fetchall()]
            if not cols:                                         # fresh install
                c.execute(self._MAP_SQL)
                c.commit()
            elif "supplier_id" not in cols:                      # old single-provider install
                self._migrate_old_map(c)
            self._ensure_env_supplier(c)
            c.commit()
        finally:
            c.close()

    _MAP_SQL = """CREATE TABLE IF NOT EXISTS provider_map(
        product_id INTEGER PRIMARY KEY,
        supplier_id INTEGER,
        provider_key TEXT NOT NULL,
        cost REAL NOT NULL DEFAULT 0,
        provider_stock INTEGER NOT NULL DEFAULT 0,
        last_sync TEXT)"""

    def _ensure_env_supplier(self, c, force=False):
        """CUPPON_API_KEY from the environment becomes a supplier called CupponHub."""
        r = c.execute("SELECT id FROM suppliers WHERE from_env=1").fetchone()
        if r:
            return r["id"]
        if not self.is_main:
            # A seller's shop never gets the owner's supplier (CupponHub / coupon API).
            if not force:
                return None
            cur = c.execute("INSERT INTO suppliers(name,fmt,from_env,enabled,created) VALUES(?,?,?,?,?)",
                            ("Supplier", "cupponhub", 0, 0, _now()))
            return cur.lastrowid
        if not ENV_KEY and not force:
            return None
        cur = c.execute("INSERT INTO suppliers(name,fmt,from_env,enabled,created) VALUES(?,?,?,?,?)",
                        ("CupponHub", "cupponhub", 1, 1 if ENV_KEY else 0, _now()))
        return cur.lastrowid

    def _migrate_old_map(self, c):
        """provider_map used to be one row per product with UNIQUE(provider_key). Rebuild it."""
        c.execute("BEGIN IMMEDIATE")
        sid = self._ensure_env_supplier(c, force=True)
        c.execute("ALTER TABLE provider_map RENAME TO provider_map_old")
        c.execute(self._MAP_SQL)
        c.execute("INSERT INTO provider_map(product_id,supplier_id,provider_key,cost,provider_stock,last_sync) "
                  "SELECT product_id,?,provider_key,cost,provider_stock,last_sync FROM provider_map_old", (sid,))
        c.execute("INSERT OR IGNORE INTO product_sources(product_id,supplier_id,provider_key,cost,stock,last_sync) "
                  "SELECT product_id,?,provider_key,cost,provider_stock,last_sync FROM provider_map_old", (sid,))
        c.execute("INSERT OR IGNORE INTO source_settings(product_id,mode,chosen,fallback) "
                  "SELECT product_id,'manual',?,1 FROM provider_map_old", (sid,))
        c.execute("DROP TABLE provider_map_old")
        c.commit()
        log.info("migrated provider_map to multi-supplier schema")

    # ------------------------------------------------------------ suppliers CRUD
    def list_suppliers(self, enabled_only=False):
        c = self.conn()
        rows = c.execute("SELECT * FROM suppliers" + (" WHERE enabled=1" if enabled_only else "")
                         + " ORDER BY id").fetchall()
        c.close()
        return [_supplier(r) for r in rows]

    def get_supplier(self, sid):
        c = self.conn()
        r = c.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
        c.close()
        return _supplier(r) if r else None

    @staticmethod
    def _clean_cfg(fmt, cfg_text):
        if fmt != "generic":
            return None
        cfg_text = (cfg_text or "").strip()
        if not cfg_text:
            return json.dumps({})
        try:
            cfg = json.loads(cfg_text)
        except ValueError as e:
            raise ValueError(f"Config is not valid JSON: {e}")
        if not isinstance(cfg, dict):
            raise ValueError("Config must be a JSON object { ... }.")
        return json.dumps(cfg)

    @staticmethod
    def _clean_url(url):
        url = (url or "").strip()
        if not (url.startswith("https://") or url.startswith("http://")):
            raise ValueError("Base URL must start with https://")
        return url

    def add_supplier(self, name, fmt, base_url, key, cfg_text=""):
        name = (name or "").strip()[:60]
        if not name:
            raise ValueError("Supplier name is required.")
        if fmt not in ADAPTERS:
            raise ValueError("Unknown API format.")
        key = (key or "").strip()
        if not key:
            raise ValueError("API key is required.")
        url, cfg = self._clean_url(base_url), self._clean_cfg(fmt, cfg_text)
        enc = encrypt(key)
        c = self.conn()
        cur = c.execute("INSERT INTO suppliers(name,fmt,base_url,key_enc,cfg,enabled,created) VALUES(?,?,?,?,?,1,?)",
                        (name, fmt, url, enc, cfg, _now()))
        c.commit()
        sid = cur.lastrowid
        c.close()
        return sid

    def update_supplier(self, sid, name=None, base_url=None, key=None, cfg_text=None):
        s = self.get_supplier(sid)
        if not s:
            raise ValueError("Supplier not found.")
        sets, vals = [], []
        if name is not None and name.strip():
            sets.append("name=?"); vals.append(name.strip()[:60])
        if not s["from_env"]:
            if base_url is not None and base_url.strip():
                sets.append("base_url=?"); vals.append(self._clean_url(base_url))
            if key is not None and key.strip():
                sets.append("key_enc=?"); vals.append(encrypt(key.strip()))
        if cfg_text is not None and s["fmt"] == "generic":
            sets.append("cfg=?"); vals.append(self._clean_cfg("generic", cfg_text))
        if sets:
            c = self.conn()
            c.execute(f"UPDATE suppliers SET {', '.join(sets)} WHERE id=?", (*vals, sid))
            c.commit()
            c.close()
            _cache_drop(sid)

    def set_supplier_enabled(self, sid, on):
        c = self.conn()
        c.execute("UPDATE suppliers SET enabled=? WHERE id=?", (1 if on else 0, sid))
        c.commit()
        c.close()
        self.refresh_all_maps()

    def delete_supplier(self, sid):
        s = self.get_supplier(sid)
        if not s:
            return
        if s["from_env"]:
            raise ValueError("This supplier comes from the CUPPON_API_KEY variable. Disable it instead of deleting.")
        c = self.conn()
        c.execute("DELETE FROM product_sources WHERE supplier_id=?", (sid,))
        c.execute("DELETE FROM suppliers WHERE id=?", (sid,))
        c.commit()
        c.close()
        _cache_drop(sid)
        self.refresh_all_maps()

    def test_supplier(self, sid):
        """Returns a short human text. Raises on failure."""
        s = self.get_supplier(sid)
        if not s:
            raise ValueError("Supplier not found.")
        items = catalog_for(s)
        msg = f"{s['name']}: OK, {len(items)} products"
        try:
            msg += f", wallet {balance_for(s):.4f}"
        except Exception as e:
            msg += f" (balance not available: {str(e)[:200]})"
        return msg

    def supplier_link_counts(self):
        c = self.conn()
        rows = c.execute("SELECT supplier_id, COUNT(*) n FROM product_sources GROUP BY supplier_id").fetchall()
        c.close()
        return {r["supplier_id"]: r["n"] for r in rows}

    # ------------------------------------------------------------ source selection
    def _candidates(self, c, pid):
        """Suppliers to try for a product, best first. Each: dict with supplier_id, provider_key, cost, stock."""
        rows = [dict(r) for r in c.execute(
            "SELECT ps.*, s.name AS sname FROM product_sources ps JOIN suppliers s ON s.id=ps.supplier_id "
            "WHERE ps.product_id=? AND s.enabled=1", (pid,)).fetchall()]
        if not rows:
            return []
        st = c.execute("SELECT * FROM source_settings WHERE product_id=?", (pid,)).fetchone()
        mode = st["mode"] if st else "manual"
        chosen = st["chosen"] if st else None
        fb = bool(st["fallback"]) if st else True
        by_cost = sorted(rows, key=lambda r: (r["cost"], r["supplier_id"]))
        if mode == "cheapest":
            return sorted(rows, key=lambda r: (r["stock"] <= 0, r["cost"], r["supplier_id"]))
        first = [r for r in rows if r["supplier_id"] == chosen]
        if not first:
            return by_cost if fb else by_cost[:1]
        return first + ([r for r in by_cost if r["supplier_id"] != chosen] if fb else [])

    def _refresh_map(self, c, pid):
        """provider_map = what the rest of the app sees for a product (active cost + best stock)."""
        cands = self._candidates(c, pid)
        if not cands:
            c.execute("DELETE FROM provider_map WHERE product_id=?", (pid,))
            return
        active = next((r for r in cands if r["stock"] > 0), cands[0])
        best_stock = max(int(r["stock"] or 0) for r in cands)     # one order is filled by one supplier
        c.execute("""INSERT INTO provider_map(product_id,supplier_id,provider_key,cost,provider_stock,last_sync)
                     VALUES(?,?,?,?,?,?)
                     ON CONFLICT(product_id) DO UPDATE SET supplier_id=excluded.supplier_id,
                       provider_key=excluded.provider_key, cost=excluded.cost,
                       provider_stock=excluded.provider_stock, last_sync=excluded.last_sync""",
                  (pid, active["supplier_id"], active["provider_key"], active["cost"], best_stock, _now()))

    def refresh_all_maps(self):
        c = self.conn()
        pids = {r["product_id"] for r in c.execute("SELECT product_id FROM product_sources").fetchall()}
        pids |= {r["product_id"] for r in c.execute("SELECT product_id FROM provider_map").fetchall()}
        for pid in pids:
            self._refresh_map(c, pid)
        c.commit()
        c.close()

    # ------------------------------------------------------------ linking products to suppliers
    def add_source(self, pid, supplier_id, provider_key):
        s = self.get_supplier(supplier_id)
        if not s:
            raise ValueError("Supplier not found.")
        prods = {p["key"]: p for p in catalog_for(s, max_age=60)}
        if provider_key not in prods:
            raise ValueError("Unknown supplier product key. Use /apiproducts to see valid keys.")
        p = prods[provider_key]
        c = self.conn()
        try:
            other = c.execute("SELECT product_id FROM product_sources WHERE supplier_id=? AND provider_key=? "
                              "AND product_id!=?", (supplier_id, provider_key, pid)).fetchone()
            if other:
                raise ValueError(f"That supplier product is already linked to product #{other['product_id']}.")
            c.execute("""INSERT INTO product_sources(product_id,supplier_id,provider_key,cost,stock,last_sync)
                         VALUES(?,?,?,?,?,?)
                         ON CONFLICT(product_id,supplier_id) DO UPDATE SET provider_key=excluded.provider_key,
                           cost=excluded.cost, stock=excluded.stock, last_sync=excluded.last_sync""",
                      (pid, supplier_id, provider_key, p["cost"], p["stock"], _now()))
            c.execute("INSERT OR IGNORE INTO source_settings(product_id,mode,chosen,fallback) VALUES(?,'manual',?,1)",
                      (pid, supplier_id))
            self._refresh_map(c, pid)
            c.commit()
        finally:
            c.close()

    def remove_source(self, pid, source_id):
        c = self.conn()
        try:
            r = c.execute("SELECT supplier_id FROM product_sources WHERE id=? AND product_id=?",
                          (source_id, pid)).fetchone()
            if not r:
                return
            c.execute("DELETE FROM product_sources WHERE id=?", (source_id,))
            left = c.execute("SELECT COUNT(*) n FROM product_sources WHERE product_id=?", (pid,)).fetchone()["n"]
            if not left:
                c.execute("DELETE FROM source_settings WHERE product_id=?", (pid,))
            else:
                c.execute("UPDATE source_settings SET chosen=NULL WHERE product_id=? AND chosen=?",
                          (pid, r["supplier_id"]))
            self._refresh_map(c, pid)
            c.commit()
        finally:
            c.close()

    def set_source_mode(self, pid, mode, chosen, fallback):
        if mode not in ("manual", "cheapest"):
            raise ValueError("Unknown mode.")
        c = self.conn()
        try:
            if not c.execute("SELECT 1 FROM product_sources WHERE product_id=?", (pid,)).fetchone():
                raise ValueError("Link a supplier product first.")
            c.execute("""INSERT INTO source_settings(product_id,mode,chosen,fallback) VALUES(?,?,?,?)
                         ON CONFLICT(product_id) DO UPDATE SET mode=excluded.mode, chosen=excluded.chosen,
                           fallback=excluded.fallback""", (pid, mode, chosen, 1 if fallback else 0))
            self._refresh_map(c, pid)
            c.commit()
        finally:
            c.close()

    # legacy helpers (bot commands /linkapi /unlinkapi and old callers)
    def link_api(self, pid, provider_key, supplier_id=None):
        if supplier_id is None:
            sups = self.list_suppliers(enabled_only=True)
            hit = [s for s in sups if provider_key in {p["key"] for p in catalog_for(s, max_age=60)}]
            if not hit:
                raise ValueError("Unknown supplier product key. Use /apiproducts to see valid keys.")
            supplier_id = hit[0]["id"]
        self.add_source(pid, int(supplier_id), provider_key)

    def unlink_api(self, pid, supplier_id=None):
        c = self.conn()
        if supplier_id is None:
            c.execute("DELETE FROM product_sources WHERE product_id=?", (pid,))
        else:
            c.execute("DELETE FROM product_sources WHERE product_id=? AND supplier_id=?", (pid, supplier_id))
        if not c.execute("SELECT 1 FROM product_sources WHERE product_id=?", (pid,)).fetchone():
            c.execute("DELETE FROM source_settings WHERE product_id=?", (pid,))
        self._refresh_map(c, pid)
        c.commit()
        c.close()

    def sources_by_product(self):
        """{product_id: {mode, chosen, fallback, offers:[{id,supplier_id,sname,provider_key,cost,stock}]}}"""
        c = self.conn()
        rows = c.execute("SELECT ps.*, s.name AS sname FROM product_sources ps JOIN suppliers s ON s.id=ps.supplier_id "
                         "ORDER BY ps.product_id, ps.cost").fetchall()
        sets = {r["product_id"]: r for r in c.execute("SELECT * FROM source_settings").fetchall()}
        c.close()
        out = {}
        for r in rows:
            out.setdefault(r["product_id"], {"offers": []})["offers"].append(dict(r))
        for pid, d in out.items():
            st = sets.get(pid)
            d["mode"] = st["mode"] if st else "manual"
            d["chosen"] = (st["chosen"] if st else None) or d["offers"][0]["supplier_id"]
            d["fallback"] = bool(st["fallback"]) if st else True
        return out

    def catalog_options(self, max_age=60):
        """For the products page: ([{id, name, items:[{key,name,cost,stock}]}], [error texts])."""
        sups = [s for s in self.list_suppliers(enabled_only=True) if s["key"]]
        out, errs = [], []
        if not sups:
            return out, errs

        def one(s):
            try:
                return s, catalog_for(s, max_age=max_age), None
            except Exception as e:
                stale = _cat_cache.get(_ck(s))
                return s, (stale[1] if stale else []), str(e)[:150]

        with ThreadPoolExecutor(max_workers=min(8, len(sups))) as ex:
            for s, items, err in ex.map(one, sups):
                out.append({"id": s["id"], "name": s["name"], "items": items})
                if err:
                    errs.append(f"Supplier {s['name']}: {err}")
        return out, errs

    # ------------------------------------------------------------ merged stock for display
    def _links(self):
        try:
            c = self.conn()
            rows = c.execute("SELECT * FROM provider_map").fetchall()
            c.close()
        except Exception:
            return {}
        return {r["product_id"]: r for r in rows}

    def _link(self, pid):
        return self._links().get(pid)

    def _merge(self, row, links):
        if row is None:
            return None
        d = dict(row)
        m = links.get(d["id"])
        if m:
            d["stock"] = int(d.get("stock") or 0) + int(m["provider_stock"] or 0)
            d["api_key"] = m["provider_key"]
            d["api_cost"] = m["cost"]
            d["api_stock"] = int(m["provider_stock"] or 0)
        return d

    def list_products(self, *a, **k):
        links = self._links()
        return [self._merge(r, links) for r in super().list_products(*a, **k)]

    def get_product(self, pid, *a, **k):
        return self._merge(super().get_product(pid, *a, **k), self._links())

    def get_product_any(self, pid, *a, **k):
        return self._merge(super().get_product_any(pid, *a, **k), self._links())

    def search_products(self, *a, **k):
        links = self._links()
        return [self._merge(r, links) for r in super().search_products(*a, **k)]

    # ------------------------------------------------------------ periodic sync
    def sync(self):
        """Refresh stock/cost of every linked supplier product. Returns alert lines for admins."""
        sups = [s for s in self.list_suppliers(enabled_only=True) if s["key"]]
        alerts, results = [], {}
        if sups:
            def one(s):
                try:
                    return s, {p["key"]: p for p in catalog_for(s)}, None
                except Exception as e:
                    return s, None, e

            with ThreadPoolExecutor(max_workers=min(8, len(sups))) as ex:
                for s, cat, err in ex.map(one, sups):
                    if err is not None:
                        log.warning("supplier %s sync failed: %s", s["name"], err)
                        alerts.append(f"⚠️ Supplier {s['name']} is not reachable - its stock was not refreshed.")
                    else:
                        results[s["id"]] = cat
        c = self.conn()
        try:
            names = {s["id"]: s["name"] for s in self.list_suppliers()}
            rows = c.execute("SELECT * FROM product_sources").fetchall()
            for r in rows:
                cat = results.get(r["supplier_id"])
                if cat is None:
                    continue
                p = cat.get(r["provider_key"])
                if p is None:
                    c.execute("UPDATE product_sources SET stock=0, last_sync=? WHERE id=?", (_now(), r["id"]))
                    alerts.append(f"⚠️ {names.get(r['supplier_id'], '?')}: product <code>{r['provider_key']}</code> "
                                  f"(#{r['product_id']}) is no longer listed. Stock set to 0.")
                else:
                    c.execute("UPDATE product_sources SET cost=?, stock=?, last_sync=? WHERE id=?",
                              (p["cost"], p["stock"], _now(), r["id"]))
            for pid in {r["product_id"] for r in rows}:
                self._refresh_map(c, pid)
                prod = Database.get_product(self, pid)
                if not prod:
                    continue
                for m in self._candidates(c, pid):
                    if int(m["stock"] or 0) > 0 and float(prod["price"]) <= float(m["cost"]):
                        alerts.append(f"⚠️ LOSS RISK: #{prod['id']} {prod['name']} sells at ${prod['price']:.2f} "
                                      f"but {m['sname']} cost is ${m['cost']:.2f}. Sales via that supplier are "
                                      f"blocked until you raise the price.")
            c.commit()
        finally:
            c.close()
        return alerts

    # ------------------------------------------------------------ buying
    def fetch_stock(self, pid, need, unit=None):
        """Buy `need` items for product `pid` from the best supplier and put them in our stock.
        Returns 'ok' / 'stock' (no supplier could supply it) / 'error' (supplier failed)."""
        with _buy_lock:
            prod = Database.get_product(self, pid)
            if not prod or need > MAX_QTY:
                return "stock"
            c = self.conn()
            cands = self._candidates(c, pid)
            c.close()
            had_error = False
            for m in cands:
                if int(m["stock"] or 0) < need:
                    continue
                if unit is not None and unit <= float(m["cost"]):
                    log.warning("blocked sale below cost: product %s unit %s cost %s (%s)",
                                pid, unit, m["cost"], m["sname"])
                    continue
                try:
                    self._fetch_from_provider(prod, m, need)
                    return "ok"
                except Exception as e:
                    had_error = True
                    log.exception("purchase failed: product %s via %s", pid, m["sname"])
                    if not _clean_failure(e):          # money may have been spent: never try another supplier
                        return "error"
            return "error" if had_error else "stock"

    def buy_with_balance(self, uid, pid, qty, unit):
        if not self._link(pid):
            return super().buy_with_balance(uid, pid, qty, unit)
        with _buy_lock:
            prod = Database.get_product(self, pid)          # raw row: local stock only
            if not prod:
                return "stock"
            need = max(0, qty - int(prod["stock"] or 0))
            if need:
                bal = float(self.get_account(uid)["balance"] or 0)
                if bal + 1e-9 < round(unit * qty, 2):
                    return "balance"                         # never spend supplier money for a broke buyer
                if self.fetch_stock(pid, need, unit) != "ok":
                    return "stock"
            return super().buy_with_balance(uid, pid, qty, unit)

    def _fetch_from_provider(self, prod, m, need):
        s = self.get_supplier(m["supplier_id"])
        if not s:
            raise ProviderError(0, "supplier_missing")
        adapter = _adapter(s)
        idem = f"shop-{prod['id']}-{uuid.uuid4().hex[:16]}"
        c = self.conn()
        c.execute("INSERT INTO provider_orders(idem,product_id,qty,status,created,supplier_id) VALUES(?,?,?,?,?,?)",
                  (idem, prod["id"], need, "pending", _now(), s["id"]))
        c.commit()
        c.close()

        res, last, maybe_sent = None, None, False
        for attempt in range(2):                             # same idem key => retry can never double-debit
            try:
                res = adapter[2](s, m["provider_key"], need, idem)
                break
            except requests.RequestException as e:
                last = e
                if not isinstance(e, requests.ConnectionError):
                    maybe_sent = True                        # e.g. read timeout: the order may have gone through
            except ProviderError as e:
                if e.status == 503 and attempt == 0:
                    last = e
                    time.sleep(1.5)
                    continue
                self._mark(idem, "failed" if e.rejected else "unknown", error=str(e))
                raise
        if res is None:
            self._mark(idem, "unknown" if maybe_sent else "failed", error=str(last))
            raise Unreachable(f"supplier unreachable: {last}", clean=not maybe_sent)

        items = [str(x) for x in res["items"]]
        # keep the delivered values in our own log BEFORE touching stock (crash-safe recovery)
        self._mark(idem, "received", provider_order_id=res["order_id"],
                   total_cost=float(res["total"]), items="\n".join(items))
        for it in items:
            self.add_product_link(prod["name"], prod["price"], it, 0)
        if len(items) != need:
            self._mark(idem, "partial", error=f"asked {need}, supplier returned {len(items)}")
            raise RuntimeError(f"supplier returned {len(items)} of {need} items")
        self._mark(idem, "stocked")
        c = self.conn()
        left = res.get("stock_left")
        if left is None:
            c.execute("UPDATE product_sources SET stock=MAX(0, stock-?) WHERE product_id=? AND supplier_id=?",
                      (need, prod["id"], s["id"]))
        else:
            c.execute("UPDATE product_sources SET stock=? WHERE product_id=? AND supplier_id=?",
                      (int(left), prod["id"], s["id"]))
        self._refresh_map(c, prod["id"])
        c.commit()
        c.close()

    def _mark(self, idem, status, **cols):
        sets = ["status=?"] + [f"{k}=?" for k in cols]
        c = self.conn()
        c.execute(f"UPDATE provider_orders SET {', '.join(sets)} WHERE idem=?", (status, *cols.values(), idem))
        c.commit()
        c.close()

    def stuck_provider_orders(self):
        """Orders where supplier money may have been spent but items never fully reached stock."""
        c = self.conn()
        rows = c.execute("SELECT * FROM provider_orders WHERE status IN ('pending','received','partial','unknown') "
                         "AND created < datetime('now','-5 minutes')").fetchall()
        c.close()
        return rows
