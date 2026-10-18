"""First-party reseller API for the existing digital shop.

Authentication: Authorization: Bearer <API key> (or X-API-Key).
Lookup uses a SHA-256 hash of the key. An encrypted copy (key_enc) is also kept so the
owner can see their own key again inside the bot (Reseller API screen / docs).
"""
import base64
import hashlib
import json
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, g
try:
    from cryptography.fernet import Fernet
except Exception:   # library missing -> keys are simply not stored for re-display
    Fernet = None

DB_PATH = os.getenv("DB_PATH", "shop.db")
api = Blueprint("reseller_api", __name__)
log = logging.getLogger("reseller_api")
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
    cols = [r[1] for r in c.execute("PRAGMA table_info(api_resellers)").fetchall()]
    if "user_id" not in cols:   # Telegram user id -> API orders are paid from that user's bot wallet
        c.execute("ALTER TABLE api_resellers ADD COLUMN user_id INTEGER")
    if "key_enc" not in cols:   # encrypted copy of the key so the bot can show it again
        c.execute("ALTER TABLE api_resellers ADD COLUMN key_enc TEXT")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_api_resellers_user ON api_resellers(user_id) WHERE user_id IS NOT NULL")
    c.commit(); c.close()


def _fernet():
    if Fernet is None:
        return None
    secret = os.getenv("API_KEY_SECRET") or os.getenv("BOT_TOKEN") or ""
    if not secret:
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def _encrypt_key(key):
    f = _fernet()
    return f.encrypt(key.encode()).decode() if f else None


def get_key_for_user(user_id):
    """The user's own raw API key (decrypted), or None if it was created before this feature."""
    c = connect()
    row = c.execute("SELECT key_hash,key_enc FROM api_resellers WHERE user_id=? AND active=1", (user_id,)).fetchone()
    c.close()
    f = _fernet()
    if not row or not row["key_enc"] or not f:
        return None
    try:
        key = f.decrypt(row["key_enc"].encode()).decode()
    except Exception:
        return None
    if hashlib.sha256(key.encode()).hexdigest() != row["key_hash"]:
        return None
    return key


def _new_key():
    return "rs_live_" + secrets.token_urlsafe(32)


def get_reseller_by_user(user_id):
    """Used by the bot: the reseller row linked to this Telegram user, or None."""
    c = connect()
    row = c.execute("SELECT * FROM api_resellers WHERE user_id=?", (user_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def create_for_user(user_id, name):
    """Used by the bot: create (once) a reseller linked to this user and return the raw key (shown only once).
    Starts with 0% discount, so the user pays the normal shop price from their wallet."""
    key = _new_key()
    c = connect()
    try:
        c.execute("INSERT INTO api_resellers(name,key_hash,balance,active,price_discount,created_at,user_id,key_enc) VALUES(?,?,0,1,0,?,?,?)",
                  ((name or str(user_id))[:80], hashlib.sha256(key.encode()).hexdigest(), now(), user_id, _encrypt_key(key)))
        c.commit()
    except sqlite3.IntegrityError:
        c.close()
        return None
    c.close()
    return key


def regenerate_key(user_id):
    """Used by the bot: replace the user's key (old one stops working). Returns the new raw key or None."""
    key = _new_key()
    c = connect()
    cur = c.execute("UPDATE api_resellers SET key_hash=?, key_enc=? WHERE user_id=?",
                    (hashlib.sha256(key.encode()).hexdigest(), _encrypt_key(key), user_id))
    c.commit(); c.close()
    return key if cur.rowcount else None


def wallet_balance(user_id):
    c = connect()
    r = c.execute("SELECT COALESCE(balance,0) b FROM users WHERE user_id=?", (user_id,)).fetchone()
    c.close()
    return float(r["b"]) if r else 0.0


def _error(message, status):
    return jsonify({"ok": False, "error": message}), status


def _params():
    """Merge query string, form fields and JSON body, so any client style works."""
    data = {}
    data.update(request.args.to_dict())
    data.update(request.form.to_dict())
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        data.update(body)
    return data


def _extract_key():
    raw = request.headers.get("Authorization", "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    if raw:
        return raw          # "Authorization: KEY" without the word Bearer
    for h in ("X-API-Key", "X-Api-Key", "Api-Key", "apikey"):
        v = request.headers.get(h, "").strip()
        if v:
            return v
    p = _params()
    for k in ("api_key", "apikey", "key", "token"):
        if p.get(k):
            return str(p[k]).strip()
    return ""


PUBLIC_PATHS = {"/api/v1/health", "/api/docs", "/api", "/api/"}


@api.before_app_request
def auth_reseller():
    if not request.path.startswith("/api"):
        return None
    if request.method == "OPTIONS":
        return ("", 204)
    if request.path in PUBLIC_PATHS:
        return None
    key = _extract_key()
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


@api.after_app_request
def cors(resp):
    if request.path.startswith("/api"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, X-API-Key, Content-Type, Idempotency-Key"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@api.app_errorhandler(404)
def _nf(e):
    if request.path.startswith("/api"):
        return _error("not_found", 404)
    return e


@api.app_errorhandler(405)
def _mna(e):
    if request.path.startswith("/api"):
        return _error("method_not_allowed", 405)
    return e


@api.after_app_request
def _api_compat_and_log(resp):
    """Log what panels (e.g. Goatsell) call, and add common success/status/message fields
    next to "ok" so panels that look for those keys also understand the reply."""
    if not request.path.startswith("/api"):
        return resp
    try:
        p = _params()
        shown = {k: ("***" if k.lower() in ("api_key", "apikey", "key", "token") else str(v)[:40]) for k, v in p.items()}
        log.warning("API %s %s -> %s params=%s ua=%s", request.method, request.path, resp.status_code, shown,
                    (request.headers.get("User-Agent") or "")[:60])
        if resp.is_json:
            data = resp.get_json(silent=True)
            if isinstance(data, dict) and "ok" in data:
                data.setdefault("success", bool(data["ok"]))
                data.setdefault("status", "success" if data["ok"] else "error")
                if not data["ok"]:
                    data.setdefault("message", data.get("error"))
                resp.set_data(json.dumps(data))
    except Exception:
        pass
    return resp


@api.app_errorhandler(500)
def _ise(e):
    if request.path.startswith("/api"):
        return _error("server_error", 500)
    return e


@api.get("/api/v1/health")
def health():
    return jsonify({"ok": True, "service": "reseller-api", "version": "1"})


@api.get("/api/v1/balance")
def balance():
    uid = g.reseller.get("user_id")
    bal = wallet_balance(uid) if uid else float(g.reseller["balance"])
    return jsonify({"ok": True, "reseller_id": g.reseller["id"], "balance": round(bal, 2)})


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
    body = _params()
    try:
        pid = int(body.get("product_id") or body.get("id") or body.get("product"))
        qty = int(body.get("quantity") or body.get("qty") or 1)
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
    linked = reseller["user_id"]
    if linked:   # wallet topped up through the bot's Add Funds
        w = c.execute("SELECT COALESCE(balance,0) b FROM users WHERE user_id=?", (linked,)).fetchone()
        available = float(w["b"]) if w else 0.0
    else:        # legacy: balance added by the admin
        available = float(reseller["balance"])
    if available + 1e-9 < amount:
        c.rollback(); c.close(); return jsonify({"ok": False, "error": "insufficient_balance", "required": amount, "balance": round(available, 2)}), 402
    order_id = "RSL-" + secrets.token_hex(6).upper()
    timestamp = now()
    for item in stock:
        c.execute("UPDATE stock SET sold=1 WHERE id=? AND sold=0", (item["id"],))
    if linked:
        c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)-?,2) WHERE user_id=?", (amount, linked))
    else:
        c.execute("UPDATE api_resellers SET balance=ROUND(balance-?,2) WHERE id=?", (amount, reseller["id"]))
    c.execute("INSERT INTO api_reseller_orders(id,reseller_id,product_id,quantity,unit_price,amount,status,items_json,customer_ref,idempotency_key,created_at) VALUES(?,?,?,?,?,?,'paid',?,?,?,?)",
              (order_id, reseller["id"], pid, qty, unit, amount, json.dumps([x["link"] for x in stock]), customer_ref, idem, timestamp))
    c.commit(); c.close()
    return jsonify({"ok": True, "order": {"id": order_id, "product_id": pid, "product_name": product["name"], "quantity": qty,
                  "unit_price": unit, "amount": amount, "status": "paid", "items": [x["link"] for x in stock], "customer_ref": customer_ref, "created_at": timestamp}, "balance": round(available - amount, 2)}), 201


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


# ---------- Easy mode: ONE URL for panels like Goatsell ----------
# URL:  https://YOUR-DOMAIN/api/v1/index.php   (also /api/v1 and /api/v1/)
# Key:  header, ?api_key=..., or form/json field "api_key"
# action = balance | products | buy | order | orders | health
@api.route("/api/v1/index.php", methods=["GET", "POST"])
@api.route("/api/v1", methods=["GET", "POST"])
@api.route("/api/v1/", methods=["GET", "POST"])
def single_endpoint():
    p = _params()
    action = str(p.get("action") or p.get("cmd") or p.get("method") or "products").lower()
    if action in ("balance", "profile", "me"):
        return balance()
    if action in ("products", "services", "list", "stock", "items"):
        return products()
    if action in ("buy", "order_create", "create_order", "add", "purchase", "neworder"):
        return create_order()
    if action in ("orders", "history"):
        return list_orders()
    if action in ("order", "status", "get_order", "check"):
        oid = str(p.get("order_id") or p.get("id") or "")
        return get_order(oid)
    if action == "health":
        return health()
    return _error("unknown_action", 400)


@api.get("/api/docs")
@api.get("/api")
def docs():
    base = request.url_root.rstrip("/")
    html = f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Reseller API</title>
<style>body{{font:16px system-ui;max-width:760px;margin:20px auto;padding:0 14px;line-height:1.5}}code,pre{{background:#eef;padding:2px 6px;border-radius:6px;overflow-wrap:anywhere}}pre{{padding:10px;overflow-x:auto}}</style>
<h2>Reseller API</h2>
<p><b>URL:</b> <code>{base}/api/v1/index.php</code></p>
<p><b>API key:</b> get it from the bot (Reseller API). Send as <code>Authorization: Bearer KEY</code>, <code>X-API-Key: KEY</code> or <code>?api_key=KEY</code>.</p>
<h3>Quick test</h3><pre>curl "{base}/api/v1/index.php?action=balance&amp;api_key=YOUR_KEY"</pre>
<h3>Actions</h3>
<ul>
<li><code>action=products</code> list, price, stock</li>
<li><code>action=balance</code> wallet balance</li>
<li><code>action=buy&amp;product_id=1&amp;quantity=2</code> place order (max 20). Add <code>idempotency_key</code> so retries never double-charge</li>
<li><code>action=order&amp;order_id=RSL-XXXX</code> order status and items</li>
<li><code>action=orders</code> last 100 orders</li>
</ul>
<p>Every reply is JSON: <code>{{"ok": true, ...}}</code> or <code>{{"ok": false, "error": "..."}}</code>.
Errors: 401 bad key, 402 low balance, 409 no stock, 404 not found.</p>
<p>Classic REST also works: <code>GET /api/v1/products</code>, <code>GET /api/v1/balance</code>, <code>POST /api/v1/orders</code>.</p>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}
