"""
CupponsHub reseller bridge for the Digital Store bot.

How it works (no change to db.py needed):
  * You create/keep products on the admin site as usual (name, category, YOUR price).
  * You LINK a product to a provider product key (admin site or /linkapi).
  * Shown stock = your own stock items (readymade accounts) + provider stock.
  * When a buyer pays: your own stock is used first; only the missing quantity is
    bought from the provider API, put into your stock, then the normal
    buy_with_balance() debits the buyer and delivers.
  * Products in the same category (API-linked or manual) are listed together.

Env:  CUPPON_API_KEY=ch_live_...   (keep it only in the server env / .env)
"""
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import requests

from db import Database

log = logging.getLogger("provider")

BASE = os.getenv("PROVIDER_API_BASE", "https://cupponhub.shop/reseller/api/")
API_KEY = os.getenv("CUPPON_API_KEY", "")
ENABLED = bool(API_KEY)
MAX_QTY = 500

_buy_lock = threading.Lock()
_cat_cache = {"at": 0.0, "data": []}


class ProviderError(Exception):
    def __init__(self, status, code):
        super().__init__(f"{status}: {code}")
        self.status, self.code = status, code


def _call(method, action, params=None, body=None, idem=None):
    headers = {"X-API-Key": API_KEY}
    if idem:
        headers["Idempotency-Key"] = idem
    r = requests.request(method, BASE, headers=headers, json=body, timeout=20,
                         params={"action": action, **(params or {})})
    try:
        data = r.json()
    except ValueError:
        raise ProviderError(r.status_code, "bad_response")
    if not data.get("ok"):
        raise ProviderError(r.status_code, data.get("error", "unknown"))
    return data


def catalog(max_age: int = 0):
    """Provider products. max_age>0 reuses a recent result (used by the web page)."""
    if max_age and time.time() - _cat_cache["at"] < max_age:
        return _cat_cache["data"]
    data = _call("GET", "products")["products"]
    _cat_cache.update(at=time.time(), data=data)
    return data


def wallet() -> float:
    return float(_call("GET", "balance")["balance_usdt"])


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class ShopDB(Database):
    """Database + provider-linked products. Drop-in replacement for Database."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        try:
            self._ensure_tables()
        except Exception:
            log.exception("could not create provider tables yet (init() will retry)")

    def init(self):
        super().init()
        self._ensure_tables()

    def _ensure_tables(self):
        c = self.conn()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS provider_map(
            product_id INTEGER PRIMARY KEY,
            provider_key TEXT UNIQUE NOT NULL,
            cost REAL NOT NULL DEFAULT 0,
            provider_stock INTEGER NOT NULL DEFAULT 0,
            last_sync TEXT);
        CREATE TABLE IF NOT EXISTS provider_orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idem TEXT UNIQUE,
            product_id INTEGER,
            qty INTEGER,
            status TEXT,              -- pending / received / stocked / failed
            provider_order_id TEXT,
            total_cost REAL,
            items TEXT,               -- delivered values, kept for recovery
            error TEXT,
            created TEXT);
        """)
        c.commit()
        c.close()

    # ------------------------------------------------------------ link helpers
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

    def link_api(self, pid, provider_key):
        prods = {p["key"]: p for p in catalog()}
        if provider_key not in prods:
            raise ValueError("Unknown provider key. Use /apiproducts to see valid keys.")
        c = self.conn()
        other = c.execute("SELECT product_id FROM provider_map WHERE provider_key=? AND product_id!=?",
                          (provider_key, pid)).fetchone()
        if other:
            c.close()
            raise ValueError(f"That provider product is already linked to product #{other['product_id']}.")
        p = prods[provider_key]
        c.execute("""INSERT INTO provider_map(product_id,provider_key,cost,provider_stock,last_sync)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(product_id) DO UPDATE SET provider_key=excluded.provider_key,
                       cost=excluded.cost, provider_stock=excluded.provider_stock,
                       last_sync=excluded.last_sync""",
                  (pid, provider_key, float(p["price_usdt_from"]), int(p["stock"]), _now()))
        c.commit()
        c.close()

    def unlink_api(self, pid):
        c = self.conn()
        c.execute("DELETE FROM provider_map WHERE product_id=?", (pid,))
        c.commit()
        c.close()

    # ------------------------------------------------------------ merged stock for display
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
        """Refresh provider stock/cost for linked products. Returns alert lines for admins."""
        if not ENABLED:
            return []
        items = {p["key"]: p for p in catalog()}
        alerts = []
        c = self.conn()
        rows = c.execute("SELECT * FROM provider_map").fetchall()
        for m in rows:
            p = items.get(m["provider_key"])
            if p is None:
                c.execute("UPDATE provider_map SET provider_stock=0, last_sync=? WHERE product_id=?",
                          (_now(), m["product_id"]))
                alerts.append(f"⚠️ Provider product <code>{m['provider_key']}</code> (#{m['product_id']}) "
                              f"is no longer listed. Stock set to 0.")
                continue
            cost = float(p["price_usdt_from"])
            c.execute("UPDATE provider_map SET provider_stock=?, cost=?, last_sync=? WHERE product_id=?",
                      (int(p["stock"]), cost, _now(), m["product_id"]))
            prod = Database.get_product(self, m["product_id"])
            if prod and float(prod["price"]) <= cost:
                alerts.append(f"⚠️ LOSS RISK: #{prod['id']} {prod['name']} sells at ${prod['price']:.2f} "
                              f"but provider cost is ${cost:.2f}. Sales via API are blocked until you raise the price.")
        c.commit()
        c.close()
        return alerts

    # ------------------------------------------------------------ buying
    def buy_with_balance(self, uid, pid, qty, unit):
        m = self._link(pid)
        if not m or not ENABLED:
            return super().buy_with_balance(uid, pid, qty, unit)
        with _buy_lock:
            prod = Database.get_product(self, pid)          # raw row: local stock only
            if not prod:
                return "stock"
            need = max(0, qty - int(prod["stock"] or 0))
            if need:
                bal = float(self.get_account(uid)["balance"] or 0)
                if bal + 1e-9 < round(unit * qty, 2):
                    return "balance"                         # never spend provider money for a broke buyer
                if need > int(m["provider_stock"] or 0) or need > MAX_QTY:
                    return "stock"
                if unit <= float(m["cost"]):
                    log.warning("blocked sale below cost: product %s unit %s cost %s", pid, unit, m["cost"])
                    return "stock"
                try:
                    self._fetch_from_provider(prod, m, need)
                except Exception:
                    log.exception("provider purchase failed for product %s", pid)
                    return "stock"
            return super().buy_with_balance(uid, pid, qty, unit)

    def _fetch_from_provider(self, prod, m, need):
        idem = f"shop-{prod['id']}-{uuid.uuid4().hex[:16]}"
        c = self.conn()
        c.execute("INSERT INTO provider_orders(idem,product_id,qty,status,created) VALUES(?,?,?,?,?)",
                  (idem, prod["id"], need, "pending", _now()))
        c.commit()
        c.close()

        res, last = None, None
        for attempt in range(2):                             # same idem key => retry can never double-debit
            try:
                res = _call("POST", "purchase", body={"product": m["provider_key"], "quantity": need}, idem=idem)
                break
            except requests.RequestException as e:
                last = e
            except ProviderError as e:
                if e.status == 503 and attempt == 0:
                    last = e
                    time.sleep(1.5)
                    continue
                self._mark(idem, "failed", error=str(e))
                raise
        if res is None:
            self._mark(idem, "failed", error=str(last))
            raise RuntimeError(f"provider unreachable: {last}")

        o = res["order"]
        items = [str(x) for x in o["items"]]
        # keep the delivered values in our own log BEFORE touching stock (crash-safe recovery)
        self._mark(idem, "received", provider_order_id=o["order_id"],
                   total_cost=float(o["total_usdt"]), items="\n".join(items))
        for it in items:
            self.add_product_link(prod["name"], prod["price"], it, 0)
        self._mark(idem, "stocked")
        c = self.conn()
        c.execute("UPDATE provider_map SET provider_stock=? WHERE product_id=?",
                  (int(o["stock_left"]), prod["id"]))
        c.commit()
        c.close()

    def _mark(self, idem, status, **cols):
        sets = ["status=?"] + [f"{k}=?" for k in cols]
        c = self.conn()
        c.execute(f"UPDATE provider_orders SET {', '.join(sets)} WHERE idem=?", (status, *cols.values(), idem))
        c.commit()
        c.close()

    def stuck_provider_orders(self):
        """Orders where provider money was spent but items never reached stock (should be empty)."""
        c = self.conn()
        rows = c.execute("SELECT * FROM provider_orders WHERE status IN ('pending','received') "
                         "AND created < datetime('now','-5 minutes')").fetchall()
        c.close()
        return rows
