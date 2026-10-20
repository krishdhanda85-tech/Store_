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
import re
import secrets
import sqlite3
import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, g

import tenancy
try:
    from cryptography.fernet import Fernet
except Exception:   # library missing -> keys are simply not stored for re-display
    Fernet = None

api = Blueprint("reseller_api", __name__)
log = logging.getLogger("reseller_api")
MAX_QTY = 20
MTO_STOCK = int(os.getenv("MTO_STOCK", "999") or 999)   # stock shown for made-to-order products
# Auto-delivery products that run out of stock become made-to-order for resellers (set OOS_MTO=0 to turn off).
OOS_MTO = os.getenv("OOS_MTO", "1") != "0"


def connect():
    """Database of the shop that is handling this call (each seller has their own)."""
    c = sqlite3.connect(tenancy.cur().db_path, timeout=15)
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
      contact TEXT,
      otp_text TEXT,
      profile_text TEXT,
      pin_text TEXT,
      idempotency_key TEXT,
      created_at TEXT NOT NULL,
      UNIQUE(reseller_id, idempotency_key),
      FOREIGN KEY(reseller_id) REFERENCES api_resellers(id)
    );
    CREATE INDEX IF NOT EXISTS idx_api_orders_reseller ON api_reseller_orders(reseller_id, created_at);
    CREATE TABLE IF NOT EXISTS api_shop_presence (
      id INTEGER PRIMARY KEY CHECK(id=1), online INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
    );
    INSERT OR IGNORE INTO api_shop_presence(id,online,updated_at) VALUES(1,0,CURRENT_TIMESTAMP);
    """)
    order_cols = [r[1] for r in c.execute("PRAGMA table_info(api_reseller_orders)").fetchall()]
    if "contact" not in order_cols:
        c.execute("ALTER TABLE api_reseller_orders ADD COLUMN contact TEXT")
    for col in ("otp_text", "profile_text", "pin_text"):
        if col not in order_cols:
            c.execute(f"ALTER TABLE api_reseller_orders ADD COLUMN {col} TEXT")
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
    # The key says which shop it belongs to, so one API URL can serve every seller.
    tid = tenancy.cur().id
    prefix = "rs_live_" if tid == tenancy.MAIN_ID else f"rs_t{tid}_"
    return prefix + secrets.token_urlsafe(32)


_KEY_SHOP = re.compile(r"^rs_t(\d+)_")


def _shop_for_key(key):
    """The active shop a key belongs to (old rs_live_ keys belong to the main shop), or None."""
    m = _KEY_SHOP.match(key or "")
    t = tenancy.get_tenant(int(m.group(1)) if m else tenancy.MAIN_ID)
    return t if t and t.status == "active" else None


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
    shop = _shop_for_key(key)
    if not shop:
        return _error("invalid_api_key", 401)
    g._tenant_tok = tenancy.set_current(shop)      # reset by web.py's teardown_request
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


def _notify_owner_mto(order_id, product_name, qty, amount, reseller_name, contact, customer_ref):
    """Best-effort owner alert. Telegram is primary; SMTP email is optional via env vars."""
    lines = ["🛠 NEW MADE-TO-ORDER RESELLER ORDER", f"Order: {order_id}",
             f"Product: {product_name}", f"Quantity: {qty}", f"Amount: {amount:.2f}",
             f"Reseller: {reseller_name}", f"Customer email/contact: {contact or 'Not provided'}",
             f"Customer reference: {customer_ref or 'Not provided'}",
             "Action: prepare the product, then fulfill/mark this order manually."]
    message = "\n".join(lines)
    try:
        tenant = tenancy.cur()
        token = getattr(tenant, "bot_token", "") or os.getenv("BOT_TOKEN", "")
        owner = getattr(tenant, "owner_id", 0) or int(os.getenv("OWNER_ID", "0") or 0)
        if token and owner:
            import requests
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": owner, "text": message}, timeout=8).raise_for_status()
    except Exception:
        log.exception("Could not send made-to-order Telegram alert")
    try:
        host = os.getenv("SMTP_HOST", "").strip()
        to_addr = os.getenv("MTO_NOTIFY_EMAIL", "").strip()
        from_addr = os.getenv("SMTP_FROM", "").strip() or os.getenv("SMTP_USER", "").strip()
        if host and to_addr and from_addr:
            msg = EmailMessage()
            msg["Subject"] = f"Made-to-order reseller order {order_id}"
            msg["From"] = from_addr
            msg["To"] = to_addr
            msg.set_content(message)
            with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as server:
                server.starttls()
                user = os.getenv("SMTP_USER", "").strip()
                password = os.getenv("SMTP_PASSWORD", "")
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
    except Exception:
        log.exception("Could not send made-to-order email alert")


@api.get("/api/v1/seller-status")
def seller_status():
    """Online presence visible to authenticated reseller integrations."""
    c = connect()
    row = c.execute("SELECT online,updated_at FROM api_shop_presence WHERE id=1").fetchone()
    c.close()
    online = bool(row["online"]) if row else False
    if online and row and row["updated_at"]:
        try:
            stamp = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
            online = (datetime.now(timezone.utc) - stamp).total_seconds() <= 180
        except (TypeError, ValueError):
            online = False
    return jsonify({"ok": True, "online": online, "status": "online" if online else "offline",
                    "updated_at": row["updated_at"] if row else None,
                    "message": "Seller online — OTP requests may be submitted" if online else "Seller offline — order will remain pending"})


@api.post("/api/v1/orders/<order_id>/request-otp")
def request_order_otp():
    """Called by a reseller bot when its customer taps Request OTP."""
    c = connect()
    presence = c.execute("SELECT online,updated_at FROM api_shop_presence WHERE id=1").fetchone()
    online = bool(presence["online"]) if presence else False
    if online and presence and presence["updated_at"]:
        try:
            stamp = datetime.fromisoformat(str(presence["updated_at"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
            online = (datetime.now(timezone.utc) - stamp).total_seconds() <= 180
        except (TypeError, ValueError):
            online = False
    if not online:
        c.close()
        return _error("seller_offline", 409)
    c.execute("BEGIN IMMEDIATE")
    row = c.execute("SELECT * FROM api_reseller_orders WHERE id=? AND reseller_id=?", (request.view_args.get("order_id"), g.reseller["id"])).fetchone()
    if not row:
        c.rollback(); c.close(); return _error("order_not_found", 404)
    if row["status"] not in ("processing", "otp_requested"):
        c.rollback(); c.close(); return _error("order_not_waiting_for_otp", 409)
    c.execute("UPDATE api_reseller_orders SET status='otp_requested' WHERE id=? AND reseller_id=?", (row["id"], g.reseller["id"]))
    c.commit()
    fresh = c.execute("SELECT * FROM api_reseller_orders WHERE id=?", (row["id"],)).fetchone()
    c.close()
    return jsonify({"ok": True, "order": _order_json(dict(fresh)), "message": "OTP request sent to seller queue"})


@api.get("/api/v1/products")
def products():
    # Show live stock: local ready stock + linked upstream provider stock.
    c = connect()
    rows = c.execute("""SELECT p.id,p.name,p.description,p.category,p.kind,p.price,
      (SELECT COUNT(*) FROM stock s WHERE s.product_id=p.id AND s.sold=0) AS local_stock
      FROM products p WHERE p.active=1 ORDER BY p.id""").fetchall()
    try:
        provider_rows = c.execute("SELECT product_id, provider_stock FROM provider_map").fetchall()
        provider_stock = {int(r["product_id"]): int(r["provider_stock"] or 0) for r in provider_rows}
    except sqlite3.OperationalError:
        provider_stock = {}
    c.close()
    discount = max(0.0, min(90.0, float(g.reseller.get("price_discount") or 0)))
    out = []
    for r in rows:
        stock = int(r["local_stock"] or 0) + provider_stock.get(int(r["id"]), 0)
        is_auto = str(r["kind"] or "").lower() in ("auto", "automatic")
        oos_mto = OOS_MTO and is_auto and stock <= 0      # sold out -> you make it by hand
        is_manual = (not is_auto) or oos_mto
        unit = round(float(r["price"]) * (1 - discount / 100), 2)
        out.append({"id": r["id"], "name": r["name"], "description": r["description"],
                    "category": r["category"], "kind": r["kind"], "price": unit,
                    # Many reseller bots/panels treat stock <= 0 (including -1) as OUT OF STOCK,
                    # so made-to-order products report a positive number. Change with env MTO_STOCK.
                    "stock": (MTO_STOCK if is_manual else stock), "available": (True if is_manual else bool(stock > 0)),
                    "made_to_order": is_manual,
                    "stock_note": ("made_to_order" if is_manual else None)})
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
    # Return an existing idempotent order before doing any upstream purchase.
    if idem:
        check = connect()
        prior = check.execute(
            "SELECT * FROM api_reseller_orders WHERE reseller_id=? AND idempotency_key=?",
            (g.reseller["id"], idem)
        ).fetchone()
        check.close()
        if prior:
            return jsonify({"ok": True, "order": _order_json(dict(prior)), "replayed": True})

    # Made-to-order products are queued for the owner instead of being rejected for zero stock.
    # API callers can send email/contact as `contact`, `email`, or `customer_email`.
    c = connect()
    product = c.execute("SELECT id,name,price,active,kind FROM products WHERE id=? AND active=1", (pid,)).fetchone()
    c.close()
    if not product:
        return _error("product_not_found", 404)
    is_manual = str(product["kind"] or "").lower() not in ("auto", "automatic")
    if not is_manual and OOS_MTO:
        # Not enough ready stock (local + supplier) -> take the order as made-to-order instead of failing.
        c = connect()
        have = int(c.execute("SELECT COUNT(*) n FROM stock WHERE product_id=? AND sold=0", (pid,)).fetchone()["n"] or 0)
        try:
            have += int((c.execute("SELECT provider_stock FROM provider_map WHERE product_id=?", (pid,)).fetchone() or {"provider_stock": 0})["provider_stock"] or 0)
        except sqlite3.OperationalError:
            pass
        c.close()
        if have < qty:
            is_manual = True
    if is_manual:
        contact = str(body.get("contact") or body.get("email") or body.get("customer_email") or "").strip()[:200] or None
        # contact / customer_ref are optional: the order is still queued for the owner without them.
        c = connect()
        c.execute("BEGIN IMMEDIATE")
        if idem:
            prior = c.execute("SELECT * FROM api_reseller_orders WHERE reseller_id=? AND idempotency_key=?", (g.reseller["id"], idem)).fetchone()
            if prior:
                c.commit(); c.close()
                return jsonify({"ok": True, "order": _order_json(dict(prior)), "replayed": True})
        reseller = c.execute("SELECT * FROM api_resellers WHERE id=? AND active=1", (g.reseller["id"],)).fetchone()
        if not reseller:
            c.rollback(); c.close(); return _error("reseller_disabled", 403)
        discount = max(0.0, min(90.0, float(reseller["price_discount"] or 0)))
        unit = round(float(product["price"]) * (1 - discount / 100), 2)
        amount = round(unit * qty, 2)
        linked = reseller["user_id"]
        if linked:
            w = c.execute("SELECT COALESCE(balance,0) b FROM users WHERE user_id=?", (linked,)).fetchone()
            available = float(w["b"]) if w else 0.0
        else:
            available = float(reseller["balance"])
        if available + 1e-9 < amount:
            c.rollback(); c.close()
            return jsonify({"ok": False, "error": "insufficient_balance", "required": amount, "balance": round(available, 2)}), 402
        order_id = "RSL-" + secrets.token_hex(6).upper()
        timestamp = now()
        if linked:
            c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)-?,2) WHERE user_id=?", (amount, linked))
        else:
            c.execute("UPDATE api_resellers SET balance=ROUND(balance-?,2) WHERE id=?", (amount, reseller["id"]))
        c.execute("INSERT INTO api_reseller_orders(id,reseller_id,product_id,quantity,unit_price,amount,status,items_json,customer_ref,contact,idempotency_key,created_at) VALUES(?,?,?,?,?,?,'processing','[]',?,?,?,?)",
                  (order_id, reseller["id"], pid, qty, unit, amount, customer_ref, contact, idem, timestamp))
        c.commit(); c.close()
        _notify_owner_mto(order_id, product["name"], qty, amount, reseller["name"], contact, customer_ref)
        return jsonify({"ok": True, "order": {"id": order_id, "product_id": pid, "product_name": product["name"],
                      "quantity": qty, "unit_price": unit, "amount": amount, "status": "processing",
                      "items": [], "customer_ref": customer_ref, "contact": contact, "created_at": timestamp},
                      "balance": round(available - amount, 2), "message": "Made-to-order request queued; shop owner notified."}), 201

    # If a linked product needs more than the local stock, fetch the missing
    # quantity from the configured upstream provider before fulfilling the API order.
    try:
        from provider import ShopDB
        if True:
            shop = tenancy.cur().db
            link = shop._link(pid)
            if link:
                local = connect()
                p_row = local.execute(
                    "SELECT id,name,price,active,kind FROM products WHERE id=? AND active=1", (pid,)
                ).fetchone()
                local_count = local.execute(
                    "SELECT COUNT(*) n FROM stock WHERE product_id=? AND sold=0", (pid,)
                ).fetchone()["n"]
                local.close()
                if p_row and p_row["kind"] == "auto":
                    need = max(0, qty - int(local_count or 0))
                    if need:
                        # Do not spend upstream funds if this reseller cannot pay.
                        discount_check = max(0.0, min(90.0, float(g.reseller.get("price_discount") or 0)))
                        unit_check = round(float(p_row["price"]) * (1 - discount_check / 100), 2)
                        amount_check = round(unit_check * qty, 2)
                        linked_user = g.reseller.get("user_id")
                        available_check = wallet_balance(linked_user) if linked_user else float(g.reseller["balance"])
                        if available_check + 1e-9 < amount_check:
                            return jsonify({"ok": False, "error": "insufficient_balance",
                                            "required": amount_check, "balance": round(available_check, 2)}), 402
                        got = shop.fetch_stock(pid, need)      # picks the supplier you chose, with fallback
                        if got == "stock":
                            return _error("insufficient_stock", 409)
                        if got != "ok":
                            return _error("upstream_provider_unavailable", 502)
    except Exception:
        log.exception("Upstream provider fulfillment failed for reseller API order")
        return _error("upstream_provider_unavailable", 502)

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
            "amount": o["amount"], "status": o["status"], "items": items, "customer_ref": o.get("customer_ref"),
            "contact": o.get("contact"), "otp": o.get("otp_text") if o.get("status") == "otp_ready" else None,
            "profile": o.get("profile_text") if o.get("status") == "completed" else None,
            "pin": o.get("pin_text") if o.get("status") == "completed" else None, "created_at": o["created_at"]}


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
    html = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#101827"><title>Reseller API · Developer Portal</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--panel2:#172238;--line:#27344e;--ink:#edf3ff;--muted:#a4b2cb;--accent:#70e1c2;--blue:#8ab4ff;--danger:#ff9a9a}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 15% 0%,#183d4c 0,transparent 35%),var(--bg);color:var(--ink);font:15px/1.65 Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--accent)}.wrap{max-width:1080px;margin:auto;padding:28px 18px 70px}.nav{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:36px}.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:17px}.logo{display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:linear-gradient(135deg,#70e1c2,#6da7ff);color:#071521;font-weight:900}.pill{border:1px solid #31574f;background:#15372f;color:var(--accent);padding:5px 10px;border-radius:999px;font-size:12px;font-weight:800}.hero{padding:30px;border:1px solid var(--line);border-radius:24px;background:linear-gradient(135deg,#15283a,#121a2d 60%);box-shadow:0 18px 60px #0002}.eyebrow{color:var(--accent);font-size:12px;font-weight:900;letter-spacing:.14em;text-transform:uppercase}.hero h1{font-size:clamp(30px,5vw,48px);line-height:1.1;letter-spacing:-.04em;margin:10px 0}.hero p{max-width:690px;color:var(--muted);margin:0 0 22px}.url{display:flex;align-items:center;gap:10px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:#090f1b;flex-wrap:wrap}.url code{flex:1;min-width:220px;overflow-wrap:anywhere;color:#c9f7eb}.btn{border:0;border-radius:10px;padding:10px 14px;background:var(--accent);color:#071521;font-weight:900;cursor:pointer}.btn.secondary{background:#273650;color:var(--ink);border:1px solid #3a4a65}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:18px}.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:22px;min-width:0}.card h2{font-size:19px;margin:0 0 8px;letter-spacing:-.02em}.muted{color:var(--muted)}.method{display:inline-block;font-size:11px;font-weight:900;letter-spacing:.04em;padding:3px 7px;border-radius:6px;background:#183f38;color:var(--accent);margin-right:8px}.endpoint{padding:12px 0;border-bottom:1px solid var(--line)}.endpoint:last-child{border-bottom:0}.endpoint code{overflow-wrap:anywhere}.field{display:grid;gap:7px;margin:12px 0}.field label{font-size:13px;color:var(--muted);font-weight:700}.field input,.field select{width:100%;padding:12px;border-radius:10px;border:1px solid #34425b;background:#0a1120;color:var(--ink);font:inherit}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}.result{white-space:pre-wrap;overflow-wrap:anywhere;background:#080d18;border:1px solid var(--line);border-radius:12px;padding:13px;min-height:60px;margin-top:12px;font:13px/1.6 ui-monospace,SFMono-Regular,monospace;color:#c9f7eb}.warning{border-left:3px solid #e5bd6c;background:#282416;padding:12px 14px;border-radius:8px;color:#e9d9a7;font-size:13px;margin-top:14px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#080d18;padding:14px;border-radius:12px;border:1px solid var(--line);font-size:12px}footer{margin-top:28px;color:var(--muted);font-size:12px;text-align:center}@media(max-width:720px){.grid{grid-template-columns:1fr}.hero{padding:22px}.wrap{padding:18px 12px 45px}.nav{margin-bottom:20px}.url{align-items:stretch}.url .btn{width:100%}}
</style></head><body><div class="wrap">
<header class="nav"><div class="brand"><span class="logo">API</span><span>Reseller Developer Portal</span></div><span class="pill">v1 · JSON</span></header>
<section class="hero"><div class="eyebrow">Connect your store</div><h1>One API. Your reseller workflow.</h1><p>Check live products and stock, view your wallet balance, place orders, and retrieve delivery details from your own bot or website.</p>
<div class="url"><code id="baseUrl">BASE_URL/api/v1/index.php</code><button class="btn" onclick="copyBase()">Copy endpoint</button></div>
<div class="warning">Keep your API key private. Prefer the <b>Authorization: Bearer</b> header. Query-string keys can be saved in browser history and server logs.</div></section>
<div class="grid">
<section class="card"><h2>Quick connection test</h2><p class="muted">Paste your API key here. The test sends it in a header, not in the URL.</p>
<div class="field"><label for="apiKey">API key</label><input id="apiKey" type="password" autocomplete="off" placeholder="rs_live_…"></div>
<div class="field"><label for="action">Test action</label><select id="action"><option value="balance">Wallet balance</option><option value="products">Products & stock</option><option value="orders">Recent orders</option></select></div>
<div class="actions"><button class="btn" onclick="testApi()">Test API</button><button class="btn secondary" onclick="document.getElementById('apiKey').value='';document.getElementById('result').textContent='Result will appear here.'">Clear key</button></div><div id="result" class="result">Result will appear here.</div></section>
<section class="card"><h2>Authentication</h2><p class="muted">Use one of these methods. Do not put the key in the endpoint URL field of a reseller panel.</p>
<div class="endpoint"><span class="method">HEADER</span><code>Authorization: Bearer YOUR_KEY</code></div><div class="endpoint"><span class="method">HEADER</span><code>X-API-Key: YOUR_KEY</code></div><div class="endpoint"><span class="method">QUERY</span><code>?api_key=YOUR_KEY</code> <span class="muted">(testing only)</span></div>
<p class="muted">If you see <code>missing_api_key</code>, the request reached the API but did not include the key in a supported field/header.</p></section>
<section class="card"><h2>Endpoints</h2>
<div class="endpoint"><span class="method">GET</span><code>/api/v1/health</code><div class="muted">Public service check; no key needed.</div></div>
<div class="endpoint"><span class="method">GET</span><code>/api/v1/products</code><div class="muted">Product list, reseller prices, and available stock.</div></div>
<div class="endpoint"><span class="method">GET</span><code>/api/v1/balance</code><div class="muted">Your current wallet balance.</div></div>
<div class="endpoint"><span class="method">POST</span><code>/api/v1/orders</code><div class="muted">Create an order; send product_id and quantity as JSON.</div></div>
<div class="endpoint"><span class="method">GET</span><code>/api/v1/orders</code><div class="muted">Your latest orders.</div></div>
<div class="endpoint"><span class="method">GET</span><code>/api/v1/orders/ORDER_ID</code><div class="muted">Check a single order and its delivery items.</div></div></section>
<section class="card"><h2>Easy mode for reseller panels</h2><p class="muted">For panels that expect a single endpoint plus a separate key field:</p><pre id="panelUrl">BASE_URL/api/v1/index.php</pre><button class="btn secondary" onclick="copyPanel()">Copy panel URL</button><div class="warning">In your panel, put this endpoint in the URL field and your <code>rs_live_…</code> key in the separate API Key field. If the panel only supports one field, check whether it supports query-string authentication.</div></section>
</div><footer>Reseller API · Keep keys private · Use an idempotency key when creating orders to avoid duplicate charges.</footer></div>
<script>
const base = location.origin;
document.getElementById('baseUrl').textContent = base + '/api/v1/index.php';
document.getElementById('panelUrl').textContent = base + '/api/v1/index.php';
function copyText(t){if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(()=>{}).catch(()=>prompt('Copy this:',t));}else prompt('Copy this:',t)}
function copyBase(){copyText(base+'/api/v1/index.php')} function copyPanel(){copyText(base+'/api/v1/index.php')}
async function testApi(){const key=document.getElementById('apiKey').value.trim();const action=document.getElementById('action').value;const out=document.getElementById('result');if(!key){out.textContent='Enter your API key first.';return}out.textContent='Connecting…';try{const r=await fetch(base+'/api/v1/index.php?action='+encodeURIComponent(action),{headers:{'Authorization':'Bearer '+key,'Accept':'application/json'}});const body=await r.text();let pretty=body;try{pretty=JSON.stringify(JSON.parse(body),null,2)}catch(e){}out.textContent='HTTP '+r.status+'
'+pretty}catch(e){out.textContent='Could not connect. Check the deployed web service and endpoint. '+e.message}}
</script></body></html>"""
    html = html.replace("BASE_URL", base)
    return html, 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}

