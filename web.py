import os
import hmac
import hashlib
import time

from flask import Flask, request, session, redirect, url_for, render_template, flash
from jinja2 import DictLoader

from db import Database

DB_PATH = os.getenv("DB_PATH", "shop.db")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SHOP_NAME = os.getenv("SHOP_NAME", "Digital Shop")

db = Database(DB_PATH)
db.init()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or hashlib.sha256(
    ("shop-admin:" + ADMIN_PASSWORD).encode()
).hexdigest()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RAILWAY_ENVIRONMENT")),
)

# ---------------------------------------------------------------- templates
TEMPLATES = {
    "base.html": """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ shop }} Admin</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,Arial,sans-serif;background:#12141a;color:#e8eaf0}
a{color:#7aa2ff;text-decoration:none}
nav{display:flex;flex-wrap:wrap;gap:14px;align-items:center;padding:14px 16px;background:#1b1e27;position:sticky;top:0}
nav b{margin-right:auto}
main{max-width:900px;margin:0 auto;padding:16px}
.card{background:#1b1e27;border-radius:12px;padding:16px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.stat{background:#1b1e27;border-radius:12px;padding:14px}
.stat small{color:#9aa3b5;display:block}
.stat span{font-size:22px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid #2a2e3a;vertical-align:top}
.scroll{overflow-x:auto}
input,textarea,button{width:100%;padding:10px;margin:6px 0;border-radius:8px;border:1px solid #333a4a;background:#12141a;color:#e8eaf0;font-size:15px}
button{background:#3b6cff;border:0;font-weight:600;cursor:pointer}
.inline{display:flex;gap:6px;align-items:center}
.inline input{margin:0}.inline button{width:auto;margin:0}
.msg{padding:10px;border-radius:8px;background:#26324f;margin-bottom:12px}
.err{background:#4f2630}
.paid{color:#4cd38a}.pending{color:#f2c14e}.failed{color:#ff6b6b}
@media(min-width:700px){.grid{grid-template-columns:repeat(5,1fr)}}
</style></head><body>
{% if session.get('ok') %}
<nav><b>{{ shop }}</b><a href="{{ url_for('dashboard') }}">Dashboard</a>
<a href="{{ url_for('products') }}">Products</a>
<a href="{{ url_for('orders') }}">Orders</a>
<a href="{{ url_for('userprices') }}">User prices</a>
<a href="{{ url_for('admins') }}">Admins</a>
<a href="{{ url_for('logout') }}">Logout</a></nav>
{% endif %}
<main>
{% with m = get_flashed_messages(with_categories=true) %}
{% for cat, text in m %}<div class="msg {{ 'err' if cat == 'err' else '' }}">{{ text }}</div>{% endfor %}
{% endwith %}
{% block body %}{% endblock %}
</main></body></html>""",
    "login.html": """{% extends 'base.html' %}{% block body %}
<div class="card" style="max-width:380px;margin:60px auto">
<h2>{{ shop }} Admin</h2>
<form method="post">
<input type="password" name="password" placeholder="Password" autofocus required>
<button type="submit">Login</button>
</form></div>{% endblock %}""",
    "dashboard.html": """{% extends 'base.html' %}{% block body %}
<h2>Dashboard</h2>
<div class="grid">
<div class="stat"><small>Total orders</small><span>{{ s.total }}</span></div>
<div class="stat"><small>Paid</small><span class="paid">{{ s.paid }}</span></div>
<div class="stat"><small>Pending</small><span class="pending">{{ s.pending }}</span></div>
<div class="stat"><small>Failed</small><span class="failed">{{ s.failed }}</span></div>
<div class="stat"><small>Revenue (USDT)</small><span>{{ '%.2f'|format(s.revenue) }}</span></div>
</div>
<div class="card" style="margin-top:16px"><h3>Latest orders</h3>
{% include 'orders_table.html' %}</div>{% endblock %}""",
    "orders.html": """{% extends 'base.html' %}{% block body %}
<h2>Orders</h2>
<div class="card">{% include 'orders_table.html' %}</div>{% endblock %}""",
    "orders_table.html": """<div class="scroll"><table>
<tr><th>Order</th><th>User</th><th>Product</th><th>Qty</th><th>Amount</th><th>Status</th><th>Created</th></tr>
{% for o in rows %}<tr>
<td>{{ o.id }}</td>
<td>{{ ('@' ~ o.username) if o.username else o.user_id }}</td>
<td>{{ o.product or o.product_id }}</td>
<td>{{ o.quantity }}</td>
<td>{{ '%.2f'|format(o.amount) }}</td>
<td class="{{ o.status }}">{{ o.status }}{% if o.failure_reason %} ({{ o.failure_reason }}){% endif %}</td>
<td>{{ (o.created_at or '')[:16].replace('T',' ') }}</td>
</tr>{% else %}<tr><td colspan="7">No orders yet.</td></tr>{% endfor %}
</table></div>""",
    "admins.html": """{% extends 'base.html' %}{% block body %}
<h2>Bot admins</h2>
<div class="card"><div class="scroll"><table>
<tr><th>Telegram ID</th><th>Added</th><th></th></tr>
{% for a in rows %}<tr><td>{{ a.user_id }}</td>
<td>{{ (a.added_at or '')[:16].replace('T',' ') }}</td>
<td><form method="post" action="{{ url_for('remove_admin', uid=a.user_id) }}" onsubmit="return confirm('Remove this admin?')">
<button type="submit" style="width:auto;margin:0;background:#c0392b">Remove</button></form></td></tr>
{% else %}<tr><td colspan="3">No extra admins. (The owner always has access.)</td></tr>{% endfor %}
</table></div></div>
<div class="card"><h3>Add admin</h3>
<form method="post" action="{{ url_for('add_admin') }}">
<input name="uid" type="number" placeholder="Telegram numeric ID" required>
<button type="submit">Add admin</button></form></div>{% endblock %}""",
    "userprices.html": """{% extends 'base.html' %}{% block body %}
<h2>User-specific prices</h2>
<div class="card"><div class="scroll"><table>
<tr><th>Username</th><th>Product</th><th>Price</th><th></th></tr>
{% for r in rows %}<tr><td>@{{ r.username }}</td><td>{{ r.product or r.product_id }}</td>
<td>{{ '%.2f'|format(r.price) }}</td>
<td><form method="post" action="{{ url_for('delete_user_price') }}">
<input type="hidden" name="username" value="{{ r.username }}">
<input type="hidden" name="pid" value="{{ r.product_id }}">
<button type="submit" style="width:auto;margin:0;background:#c0392b">Delete</button></form></td></tr>
{% else %}<tr><td colspan="4">No user-specific prices set.</td></tr>{% endfor %}
</table></div></div>
<div class="card"><h3>Set user price</h3>
<form method="post" action="{{ url_for('set_user_price') }}">
<input name="username" placeholder="Telegram username (without @)" required>
<select name="pid" required style="width:100%;padding:10px;margin:6px 0;border-radius:8px;border:1px solid #333a4a;background:#12141a;color:#e8eaf0;font-size:15px">
{% for p in products %}<option value="{{ p.id }}">{{ p.id }} - {{ p.name }} (default {{ p.price }})</option>{% endfor %}
</select>
<input name="price" type="number" step="0.01" min="0" placeholder="Price (USDT)" required>
<button type="submit">Save</button></form></div>{% endblock %}""",
    "products.html": """{% extends 'base.html' %}{% block body %}
<h2>Products</h2>
<div class="card"><div class="scroll"><table>
<tr><th>ID</th><th>Name</th><th>Stock</th><th>Price (USDT)</th></tr>
{% for p in products %}<tr>
<td>{{ p.id }}</td><td>{{ p.name }}</td><td>{{ p.stock }}</td>
<td><form class="inline" method="post" action="{{ url_for('set_price', pid=p.id) }}">
<input name="price" type="number" step="0.01" min="0" value="{{ p.price }}" style="width:90px">
<button type="submit">Save</button></form></td>
</tr>{% else %}<tr><td colspan="4">No products yet. Add stock below.</td></tr>{% endfor %}
</table></div></div>

<div class="card"><h3>Add stock</h3>
<p style="color:#9aa3b5;font-size:13px">One item per line (link / code / key). Each line becomes one stock item.
If the product name already exists, items are added to it.</p>
<form method="post" action="{{ url_for('add_stock') }}">
<input name="name" placeholder="Product name" required>
<input name="price" type="number" step="0.01" min="0" placeholder="Price (USDT)" required>
<textarea name="links" rows="6" placeholder="One item per line" required></textarea>
<button type="submit">Add stock</button>
</form></div>{% endblock %}""",
}
app.jinja_loader = DictLoader(TEMPLATES)


def render(name, **kw):
    return render_template(name, shop=SHOP_NAME, **kw)


# ------------------------------------------------------------------- auth
_attempts = {}


def _blocked(ip):
    now = time.time()
    _attempts[ip] = [t for t in _attempts.get(ip, []) if now - t < 300]
    return len(_attempts[ip]) >= 5


@app.before_request
def guard():
    if request.endpoint in ("login", "static"):
        return None
    if not session.get("ok"):
        return redirect(url_for("login"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        if not ADMIN_PASSWORD:
            flash("ADMIN_PASSWORD is not set on the server.", "err")
        elif _blocked(ip):
            flash("Too many attempts. Try again in 5 minutes.", "err")
        elif hmac.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session.clear()
            session["ok"] = True
            return redirect(url_for("dashboard"))
        else:
            _attempts.setdefault(ip, []).append(time.time())
            flash("Wrong password.", "err")
    return render("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------ pages
ORDER_SQL = """SELECT o.*, p.name AS product, u.username AS username
FROM orders o
LEFT JOIN products p ON p.id = o.product_id
LEFT JOIN users u ON u.user_id = o.user_id
ORDER BY o.created_at DESC"""


def fetch_orders(limit):
    c = db.conn()
    rows = c.execute(ORDER_SQL + " LIMIT ?", (limit,)).fetchall()
    c.close()
    return rows


@app.route("/")
def dashboard():
    return render("dashboard.html", s=db.payment_stats(), rows=fetch_orders(15))


@app.route("/orders")
def orders():
    return render("orders.html", rows=fetch_orders(200))


@app.route("/products")
def products():
    return render("products.html", products=db.list_products())


@app.route("/products/add", methods=["POST"])
def add_stock():
    name = request.form.get("name", "").strip()
    links = [x.strip() for x in request.form.get("links", "").splitlines() if x.strip()]
    try:
        price = float(request.form.get("price", ""))
    except ValueError:
        price = -1
    if not name or not links or price < 0:
        flash("Name, valid price and at least one item are required.", "err")
        return redirect(url_for("products"))
    for link in links:
        db.add_product_link(name, price, link, 0)
    flash(f"Added {len(links)} item(s) to {name}.")
    return redirect(url_for("products"))


@app.route("/products/<int:pid>/price", methods=["POST"])
def set_price(pid):
    try:
        price = float(request.form.get("price", ""))
        if price < 0:
            raise ValueError
    except ValueError:
        flash("Invalid price.", "err")
        return redirect(url_for("products"))
    db.set_product_price(pid, price)
    flash("Price updated.")
    return redirect(url_for("products"))


@app.route("/admins")
def admins():
    c = db.conn()
    rows = c.execute("SELECT * FROM admins ORDER BY added_at DESC").fetchall()
    c.close()
    return render("admins.html", rows=rows)


@app.route("/admins/add", methods=["POST"])
def add_admin():
    try:
        uid = int(request.form.get("uid", ""))
    except ValueError:
        flash("Invalid Telegram ID.", "err")
        return redirect(url_for("admins"))
    db.add_admin(uid, 0)
    flash(f"Admin {uid} added.")
    return redirect(url_for("admins"))


@app.route("/admins/<int:uid>/remove", methods=["POST"])
def remove_admin(uid):
    db.remove_admin(uid)
    flash(f"Admin {uid} removed.")
    return redirect(url_for("admins"))


@app.route("/userprices")
def userprices():
    c = db.conn()
    rows = c.execute(
        """SELECT up.username, up.product_id, up.price, p.name AS product
        FROM user_prices up LEFT JOIN products p ON p.id = up.product_id
        ORDER BY up.username"""
    ).fetchall()
    c.close()
    return render("userprices.html", rows=rows, products=db.list_products())


@app.route("/userprices/set", methods=["POST"])
def set_user_price():
    username = request.form.get("username", "").strip().lstrip("@")
    try:
        pid = int(request.form.get("pid", ""))
        price = float(request.form.get("price", ""))
        if price < 0 or not username:
            raise ValueError
    except ValueError:
        flash("Valid username, product and price required.", "err")
        return redirect(url_for("userprices"))
    db.set_user_price(username, pid, price)
    flash(f"Price set for @{username}.")
    return redirect(url_for("userprices"))


@app.route("/userprices/delete", methods=["POST"])
def delete_user_price():
    c = db.conn()
    c.execute(
        "DELETE FROM user_prices WHERE username=? AND product_id=?",
        (request.form.get("username", ""), request.form.get("pid", 0)),
    )
    c.commit()
    c.close()
    flash("User price deleted.")
    return redirect(url_for("userprices"))
