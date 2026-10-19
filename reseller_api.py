"""First-party reseller API for the existing digital shop.

Authentication: Authorization: Bearer <API key> (or X-API-Key).
Keys are shown only once on creation; only SHA-256 hashes are stored.
"""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, g

DB_PATH = os.getenv("DB_PATH", "shop.db")
api = Blueprint("reseller_api", __name__)
MAX_QTY = 20


def connect():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    return c


def now():
    return datetime.now(timezone.utc).isoformat()


def init_schema():
    c = connect()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS api_resellers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      key_hash TEXT NOT NULL UNIQUE,
      balance REAL NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1,
      price_discount REAL NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS api_reseller_orders (
      id TEXT PRIMARY KEY,
      reseller_id INTEGER NOT NULL,
      product_id INTEGER NOT NULL,
      quantity INTEGER NOT NULL,
      unit_price REAL NOT NULL,
      amount REAL NOT NULL,
      status TEXT NOT NULL,
      items_json TEXT NOT NULL DEFAULT '[]',
      customer_ref TEXT,
      idempotency_key TEXT,
      created_at TEXT NOT NULL,
      UNIQUE(reseller_id, idempotency_key),
      FOREIGN KEY(reseller_id) REFERENCES api_resellers(id)
    );
    CREATE INDEX IF NOT EXISTS idx_api_orders_reseller ON api_reseller_orders(reseller_id, created_at);
    """)
    c.commit(); c.close()


def _error(message, status):
    return jsonify({"ok": False, "error": message}), status


@api.before_app_request
def auth_reseller():
    if not request.path.startswith("/api/v1/"):
        return None
    raw = request.headers.get("Authorization", "")
    key = raw[7:].strip() if raw.lower().startswith("bearer ") else request.headers.get("X-API-Key", "").strip()
    if not key:
        return _error("missing_api_key", 401)
    digest = hashlib.sha256(key.encode()).hexdigest()
    c = connect()
    row = c.execute("SELECT * FROM api_resellers WHERE key_hash=? AND active=1", (digest,)).fetchone()
    c.close()
    if not row:
        return _error("invalid_api_key", 401)
    g.reseller = dict(row)
    return None


@api.get("/api/v1/health")
def health():
    return jsonify({"ok": True, "service": "reseller-api", "version": "1"})


@api.get("/api/v1/balance")
def balance():
    return jsonify({"ok": True, "reseller_id": g.reseller["id"], "balance": round(g.reseller["balance"], 2)})


@api.get("/api/v1/products")
def products():
    c = connect()
    rows = c.execute("""SELECT p.id,p.name,p.description,p.category,p.kind,p.price,
      (SELECT COUNT(*) FROM stock s WHERE s.product_id=p.id AND s.sold=0) AS stock
      FROM products p WHERE p.active=1 ORDER BY p.id""").fetchall()
    c.close()
    discount = max(0.0, min(90.0, float(g.reseller.get("price_discount") or 0)))
    out = []
    for r in rows:
        unit = round(float(r["price"]) * (1 - discount / 100), 2)
        out.append({"id": r["id"], "name": r["name"], "description": r["description"],
                    "category": r["category"], "kind": r["kind"], "price": unit,
                    "stock": int(r["stock"]), "available": bool(r["stock"] > 0)})
    return jsonify({"ok": True, "currency": "shop_balance_units", "products": out})


@api.post("/api/v1/orders")
def create_order():
    body = request.get_json(silent=True) or {}
    try:
        pid = int(body.get("product_id"))
        qty = int(body.get("quantity", 1))
    except (TypeError, ValueError):
        return _error("product_id_and_quantity_must_be_integers", 400)
    if pid < 1 or qty < 1 or qty > MAX_QTY:
        return _error(f"quantity_must_be_between_1_and_{MAX_QTY}", 400)
    customer_ref = str(body.get("customer_ref", ""))[:100] or None
    idem = request.headers.get("Idempotency-Key", "").strip()[:128] or str(body.get("idempotency_key", "")).strip()[:128] or None
    c = connect()
    c.execute("BEGIN IMMEDIATE")
    if idem:
        prior = c.execute("SELECT * FROM api_reseller_orders WHERE reseller_id=? AND idempotency_key=?", (g.reseller["id"], idem)).fetchone()
        if prior:
            c.commit(); c.close()
            return jsonify({"ok": True, "order": _order_json(dict(prior)), "replayed": True})
    reseller = c.execute("SELECT * FROM api_resellers WHERE id=? AND active=1", (g.reseller["id"],)).fetchone()
    product = c.execute("SELECT id,name,price,active,kind FROM products WHERE id=? AND active=1", (pid,)).fetchone()
    if not reseller:
        c.rollback(); c.close(); return _error("reseller_disabled", 403)
    if not product:
        c.rollback(); c.close(); return _error("product_not_found", 404)
    if product["kind"] != "auto":
        c.rollback(); c.close(); return _error("manual_products_not_supported_by_api", 409)
    stock = c.execute("SELECT id,link FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT ?", (pid, qty)).fetchall()
    if len(stock) != qty:
        c.rollback(); c.close(); return _error("insufficient_stock", 409)
    discount = max(0.0, min(90.0, float(reseller["price_discount"] or 0)))
    unit = round(float(product["price"]) * (1 - discount / 100), 2)
    amount = round(unit * qty, 2)
    if float(reseller["balance"]) + 1e-9 < amount:
        c.rollback(); c.close(); return jsonify({"ok": False, "error": "insufficient_balance", "required": amount, "balance": round(float(reseller["balance"]), 2)}), 402
    order_id = "RSL-" + secrets.token_hex(6).upper()
    timestamp = now()
    for item in stock:
        c.execute("UPDATE stock SET sold=1 WHERE id=? AND sold=0", (item["id"],))
    c.execute("UPDATE api_resellers SET balance=ROUND(balance-?,2) WHERE id=?", (amount, reseller["id"]))
    c.execute("INSERT INTO api_reseller_orders(id,reseller_id,product_id,quantity,unit_price,amount,status,items_json,customer_ref,idempotency_key,created_at) VALUES(?,?,?,?,?,?,'paid',?,?,?,?)",
              (order_id, reseller["id"], pid, qty, unit, amount, json.dumps([x["link"] for x in stock]), customer_ref, idem, timestamp))
    c.commit(); c.close()
    return jsonify({"ok": True, "order": {"id": order_id, "product_id": pid, "product_name": product["name"], "quantity": qty,
                  "unit_price": unit, "amount": amount, "status": "paid", "items": [x["link"] for x in stock], "customer_ref": customer_ref, "created_at": timestamp}, "balance": round(float(reseller["balance"]) - amount, 2)}), 201


def _order_json(o):
    try: items = json.loads(o.get("items_json") or "[]")
    except (ValueError, TypeError): items = []
    return {"id": o["id"], "product_id": o["product_id"], "quantity": o["quantity"], "unit_price": o["unit_price"],
            "amount": o["amount"], "status": o["status"], "items": items, "customer_ref": o.get("customer_ref"), "created_at": o["created_at"]}


@api.get("/api/v1/orders")
def list_orders():
    c = connect()
    rows = c.execute("SELECT * FROM api_reseller_orders WHERE reseller_id=? ORDER BY created_at DESC LIMIT 100", (g.reseller["id"],)).fetchall()
    c.close()
    return jsonify({"ok": True, "orders": [_order_json(dict(r)) for r in rows]})


@api.get("/api/v1/orders/<order_id>")
def get_order(order_id):
    c = connect()
    row = c.execute("SELECT * FROM api_reseller_orders WHERE id=? AND reseller_id=?", (order_id, g.reseller["id"])).fetchone()
    c.close()
    if not row: return _error("order_not_found", 404)
    return jsonify({"ok": True, "order": _order_json(dict(row))})
