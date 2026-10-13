import html as htmllib
import os
import hmac
import hashlib
import time

import requests

from flask import Flask, request, session, redirect, url_for, render_template, flash
from jinja2 import DictLoader

from db import Database

DB_PATH = os.getenv("DB_PATH", "shop.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SHOP_NAME = os.getenv("SHOP_NAME", "Digital Shop")

db = Database(DB_PATH)
db.init()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024   # QR uploads
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
body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,Arial,sans-serif;background:radial-gradient(ellipse at top,#18243a 0%,#0b1020 52%,#090d16 100%);color:#edf2ff;line-height:1.5}
a{color:#8bb8ff;text-decoration:none}a:hover{color:#c2d8ff}
nav{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:14px 18px;background:rgba(15,23,42,.94);border-bottom:1px solid #263550;position:sticky;top:0;z-index:5;backdrop-filter:blur(14px);box-shadow:0 8px 30px #0003}
nav b{margin-right:auto;font-size:18px;letter-spacing:.3px;color:#fff}
nav a{padding:8px 10px;border-radius:9px;font-size:13px;font-weight:650}nav a:hover{background:#1e3150}
main{max-width:1180px;margin:0 auto;padding:24px 18px}
.card{background:linear-gradient(145deg,#172238,#111a2b);border:1px solid #293a58;border-radius:18px;padding:20px;margin-bottom:18px;box-shadow:0 12px 32px #0002}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
.stat{background:linear-gradient(145deg,#1c2b43,#142034);border:1px solid #2a3e5e;border-radius:16px;padding:18px;box-shadow:0 8px 24px #0002}
.stat small{color:#9aa3b5;display:block}
.stat span{font-size:25px;font-weight:800;letter-spacing:-.5px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid #2a3b56;vertical-align:top}
.scroll{overflow-x:auto}
input,textarea,button{width:100%;padding:10px;margin:6px 0;border-radius:10px;border:1px solid #34445f;background:#0d1524;color:#e8eaf0;font-size:15px}
button{background:linear-gradient(135deg,#1677ff,#315cf5);border:0;font-weight:750;cursor:pointer;box-shadow:0 5px 14px #2563eb30;transition:transform .15s,filter .15s}
.inline{display:flex;gap:6px;align-items:center}
button:hover{filter:brightness(1.12);transform:translateY(-1px)}.inline input{margin:0}.inline button{width:auto;margin:0}
.msg{padding:12px 14px;border-radius:12px;background:#1d3559;border:1px solid #31517d;margin-bottom:14px}
.err{background:#4f2630}
.paid{color:#4cd38a}.pending,.processing{color:#f2c14e}.failed,.refunded{color:#ff6b6b}
label{display:block;color:#9aa3b5;font-size:13px;margin-top:8px}
select{width:100%;padding:10px;margin:6px 0;border-radius:10px;border:1px solid #34445f;background:#0d1524;color:#e8eaf0;font-size:15px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.row{display:flex;gap:8px;flex-wrap:wrap}.row button{flex:1;min-width:110px}
.red{background:#c0392b}.green{background:#1f9d57}.grey{background:#3a4152}
.muted{color:#9aa3b5;font-size:13px}.mono{font-family:ui-monospace,monospace;word-break:break-all}
.badge{display:inline-block;background:#f2c14e;color:#12141a;border-radius:10px;padding:0 7px;font-size:12px;font-weight:700}
@media(min-width:700px){.grid{grid-template-columns:repeat(5,1fr)}}@media(max-width:600px){main{padding:14px 10px}nav{gap:4px;padding:10px}nav a{font-size:12px;padding:7px}.card{padding:14px;border-radius:14px}.grid{gap:9px}.stat{padding:12px}.stat span{font-size:21px}.two{grid-template-columns:1fr}}
</style></head><body>
{% if session.get('ok') %}
<nav><b>{{ shop }}</b><a href="{{ url_for('dashboard') }}">Dashboard</a>
<a href="{{ url_for('products') }}">Products</a>
<a href="{{ url_for('manual') }}">Pending orders{% if pending_count %} <span class="badge">{{ pending_count }}</span>{% endif %}</a>
<a href="{{ url_for('upipay') }}">UPI payments{% if upi_pending %} <span class="badge">{{ upi_pending }}</span>{% endif %}</a>
<a href="{{ url_for('upi') }}">UPI setup</a>
<a href="{{ url_for('orders') }}">Orders</a>
<a href="{{ url_for('users') }}">Users</a>
<a href="{{ url_for('tickets') }}">Support{% if open_tickets %} <span class="badge">{{ open_tickets }}</span>{% endif %}</a>
<a href="{{ url_for('settings') }}">Settings</a>
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
<div class="stat"><small>To deliver</small><span class="processing">{{ pending_count }}</span></div>
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
<select name="pid" required style="width:100%;padding:10px;margin:6px 0;border-radius:10px;border:1px solid #34445f;background:#0d1524;color:#e8eaf0;font-size:15px">
{% for p in products %}<option value="{{ p.id }}">{{ p.id }} - {{ p.name }} (default {{ p.price }})</option>{% endfor %}
</select>
<input name="price" type="number" step="0.01" min="0" placeholder="Price (USDT)" required>
<button type="submit">Save</button></form></div>{% endblock %}""",
    "products.html": """{% extends 'base.html' %}{% block body %}
<h2>Products</h2>
{% for p in products %}
<div class="card">
<b>#{{ p.id }} · {{ p.name }}</b>
<span class="muted"> · {{ 'Made to order' if p.kind == 'manual' else ('Stock: ' ~ p.stock) }}</span>
<form method="post" action="{{ url_for('edit_product', pid=p.id) }}">
<label>Name</label><input name="name" value="{{ p.name }}" required>
<div class="two">
<div><label>Price (USDT)</label><input name="price" type="number" step="0.01" min="0" value="{{ p.price }}" required></div>
<div><label>Old price (optional - shows a % discount)</label><input name="old_price" type="number" step="0.01" min="0" value="{{ p.old_price or '' }}"></div>
</div>
<label>Category (same category = grouped under one blue button; empty = listed directly)</label>
<input name="category" value="{{ p.category or '' }}">
<label>Type</label>
<select name="kind">
<option value="auto" {{ 'selected' if p.kind != 'manual' }}>Automatic delivery (from stock)</option>
<option value="manual" {{ 'selected' if p.kind == 'manual' }}>Made to order (customer sends an email, you deliver by hand)</option>
</select>
<label>Delivery time (shown to the customer, e.g. by hand - within 24 hours)</label>
<input name="delivery" value="{{ p.delivery or '' }}">
<label>Warranty (e.g. 90 days - empty shows None)</label>
<input name="warranty" value="{{ p.warranty or '' }}">
<label>Question asked before buying (made-to-order only)</label>
<input name="prompt" value="{{ p.prompt or '' }}" placeholder="Send an email that you haven't linked to Spotify before.">
<label>Description (one point per line)</label>
<textarea name="description" rows="4">{{ p.description or '' }}</textarea>
<button type="submit">Save product</button>
</form>
<form method="post" action="{{ url_for('delete_product', pid=p.id) }}" onsubmit="return confirm('Delete this product? It disappears from the shop. Old orders keep their history.')">
<button class="red" type="submit">Delete product</button></form></div>
{% else %}<div class="card">No products yet. Add one below.</div>{% endfor %}

<div class="card"><h3>Add product</h3>
<form method="post" action="{{ url_for('add_product') }}">
<input name="name" placeholder="Product name" required>
<input name="price" type="number" step="0.01" min="0" placeholder="Price (USDT)" required>
<select name="kind">
<option value="auto">Automatic delivery (add stock below)</option>
<option value="manual">Made to order (you deliver by hand)</option>
</select>
<button type="submit">Add product</button></form></div>

<div class="card"><h3>Add stock</h3>
<p class="muted">One item per line (link / code / key). Each line becomes one stock item.
If the product name already exists, items are added to it.</p>
<form method="post" action="{{ url_for('add_stock') }}">
<input name="name" placeholder="Product name" required>
<input name="price" type="number" step="0.01" min="0" placeholder="Price (USDT)" required>
<textarea name="links" rows="6" placeholder="One item per line" required></textarea>
<button type="submit">Add stock</button>
</form></div>{% endblock %}""",
    "manual.html": """{% extends 'base.html' %}{% block body %}
<h2>Pending orders</h2>
<p class="muted">Made-to-order items to deliver by hand. Marking an order done (or refunding it) tells the customer in the bot - once.</p>
{% for o in rows %}
<div class="card">
<b>{{ o.product or o.product_id }}</b> <span class="muted">· ${{ '%.2f'|format(o.amount) }} · {{ o.id }} · {{ (o.created_at or '')[:16].replace('T',' ') }}</span><br>
<span class="muted">Customer: {{ ('@' ~ o.username) if o.username else o.user_id }} ({{ o.user_id }})</span>
<div class="row" style="margin-top:8px;align-items:center">
<span class="mono" id="c{{ loop.index }}" style="flex:1">{{ o.contact }}</span>
<button type="button" class="grey" style="flex:0 0 auto;min-width:80px" onclick="navigator.clipboard.writeText(document.getElementById('c{{ loop.index }}').innerText);this.innerText='Copied'">Copy</button>
</div>
<form method="post">
<input name="note" placeholder="Message to the customer (optional)">
<div class="row">
<button class="green" formaction="{{ url_for('manual_done', oid=o.id) }}">Mark done</button>
<button class="grey" formaction="{{ url_for('manual_note', oid=o.id) }}">Send note only</button>
<button class="red" formaction="{{ url_for('manual_reject', oid=o.id) }}" onclick="return confirm('Refund this order to the customer wallet?')">Refund</button>
</div></form></div>
{% else %}<div class="card">Nothing to deliver right now.</div>{% endfor %}{% endblock %}""",
    "users.html": """{% extends 'base.html' %}{% block body %}
<h2>Users</h2>
<div class="card"><form method="get"><div class="inline">
<input name="q" value="{{ q }}" placeholder="Telegram ID or username (empty = newest)">
<button type="submit">Search</button></div></form></div>
{% for u in rows %}
<div class="card">
<b>{{ ('@' ~ u.username) if u.username else 'No username' }}</b> <span class="muted">· ID {{ u.user_id }} · joined {{ (u.created_at or '')[:10] }}</span><br>
<span class="muted">Balance ${{ '%.2f'|format(u.balance or 0) }} · Spent ${{ '%.2f'|format(u.spent or 0) }} · Referral earned ${{ '%.2f'|format(u.ref_earned or 0) }}</span>
<form method="post" action="{{ url_for('topup', uid=u.user_id) }}">
<div class="two"><input name="amount" type="number" step="0.01" placeholder="Amount (negative removes)" required>
<input name="note" placeholder="Note to the customer (optional)"></div>
<button type="submit">Change wallet balance</button></form></div>
{% else %}<div class="card">No users found.</div>{% endfor %}{% endblock %}""",
    "tickets.html": """{% extends 'base.html' %}{% block body %}
<h2>Support tickets</h2>
<div class="card"><div class="scroll"><table>
<tr><th>#</th><th>User</th><th>Type</th><th>Status</th><th>Opened</th></tr>
{% for t in rows %}<tr>
<td><a href="{{ url_for('ticket', tid=t.id) }}">#{{ t.id }}</a></td>
<td>{{ ('@' ~ t.username) if t.username else t.user_id }}</td><td>{{ t.kind }}</td>
<td class="{{ 'pending' if t.status == 'open' else 'paid' }}">{{ t.status }}</td>
<td>{{ (t.created_at or '')[:16].replace('T',' ') }}</td></tr>
{% else %}<tr><td colspan="5">No tickets yet.</td></tr>{% endfor %}
</table></div></div>{% endblock %}""",
    "ticket.html": """{% extends 'base.html' %}{% block body %}
<h2>Ticket #{{ t.id }} <span class="muted">· {{ t.kind }} · {{ t.status }}</span></h2>
<p class="muted">Customer: {{ ('@' ~ t.username) if t.username else t.user_id }} ({{ t.user_id }})</p>
{% for m in msgs %}<div class="card" style="{{ 'border-left:3px solid #3b6cff' if m.sender == 'admin' else '' }}">
<small class="muted">{{ 'You' if m.sender == 'admin' else 'Customer' }} · {{ (m.created_at or '')[:16].replace('T',' ') }}</small><br>
{{ m.text }}{% if m.photo_id %}<div style="margin-top:10px"><a href="{{ url_for('ticket_photo', mid=m.id) }}" target="_blank"><img src="{{ url_for('ticket_photo', mid=m.id) }}" alt="photo" loading="lazy" style="max-width:100%;max-height:420px;border-radius:8px"></a></div>{% endif %}</div>{% endfor %}
<div class="card"><form method="post" action="{{ url_for('ticket_reply', tid=t.id) }}">
<textarea name="text" rows="4" placeholder="Your reply (sent to the customer in the bot)" required></textarea>
<button type="submit">Send reply</button></form>
<form method="post" action="{{ url_for('ticket_status', tid=t.id) }}">
<input type="hidden" name="status" value="{{ 'closed' if t.status == 'open' else 'open' }}">
<button class="grey" type="submit">{{ 'Close ticket' if t.status == 'open' else 'Reopen ticket' }}</button></form></div>
{% endblock %}""",
    "settings.html": """{% extends 'base.html' %}{% block body %}
<h2>Settings</h2>
<div class="card"><h3>Referral commission</h3>
<p class="muted">Percent of everything a referred customer buys that the person who invited them earns (added to their wallet). Applies to purchases from now on.</p>
<form method="post" action="{{ url_for('save_settings') }}">
<input name="referral_percent" type="number" step="0.1" min="0" max="100" value="{{ pct }}" required>
<button type="submit">Save</button></form></div>{% endblock %}""",
    "upi.html": """{% extends 'base.html' %}{% block body %}
<h2>UPI setup</h2>
<div class="card"><h3>Rate (INR per 1 USDT)</h3>
<p class="muted">Shown next to every USDT price in the bot. Applies to new requests; open ones keep the rate they were created with.</p>
<form method="post" action="{{ url_for('upi_rate') }}"><div class="inline">
<input name="rate" type="number" step="0.01" min="1" value="{{ rate }}" required>
<button type="submit">Save</button></div></form></div>
<div class="card"><h3>Add a UPI account</h3>
<p class="muted">The bot uses your active accounts one after another (rotation). The QR image is optional (PNG or JPG, up to 2 MB).</p>
<form method="post" action="{{ url_for('upi_add') }}" enctype="multipart/form-data">
<label>Name / label (only you see it)</label><input name="label" placeholder="e.g. PhonePe - Rahul">
<label>UPI ID</label><input name="upi_id" placeholder="name@bank" required>
<label>QR image</label><input name="qr" type="file" accept="image/png,image/jpeg">
<button type="submit">Add UPI</button></form></div>
{% for a in rows %}
<div class="card">
<b>{{ a.label or 'UPI' }}</b> <span class="{{ 'paid' if a.active else 'failed' }}">· {{ 'ON' if a.active else 'OFF' }}</span><br>
<span class="mono">{{ a.upi_id }}</span>
{% if a.has_qr %}<div style="margin:10px 0"><img src="{{ url_for('upi_qr', aid=a.id) }}" alt="QR" style="max-width:180px;border-radius:8px;background:#fff"></div>{% endif %}
<form method="post" enctype="multipart/form-data" action="{{ url_for('upi_qr_set', aid=a.id) }}">
<input name="qr" type="file" accept="image/png,image/jpeg" required>
<div class="row"><button class="grey" type="submit">{{ 'Replace QR' if a.has_qr else 'Upload QR' }}</button>
<button class="grey" formaction="{{ url_for('upi_toggle', aid=a.id) }}" formnovalidate>{{ 'Turn OFF' if a.active else 'Turn ON' }}</button>
<button class="red" formaction="{{ url_for('upi_delete', aid=a.id) }}" formnovalidate onclick="return confirm('Delete this UPI account?')">Delete</button></div>
</form></div>
{% else %}<div class="card">No UPI account yet. Add one above - the UPI button appears in the bot as soon as one is ON.</div>{% endfor %}{% endblock %}""",
    "upipay.html": """{% extends 'base.html' %}{% block body %}
<h2>UPI payments</h2>
<div class="card"><form method="get"><div class="inline">
<input name="q" value="{{ q }}" placeholder="Find by UTR, request ID, user ID or @username">
<button type="submit">Find</button></div></form>
<p class="muted">The bot only alerts you about new payments. Check the UTR in your bank app, then approve or reject here.</p>
<p class="muted" style="margin-bottom:0">{% if q %}Search results (all statuses). <a href="{{ url_for('upipay') }}">Back to waiting</a>{% elif view == 'all' %}Latest 100. <a href="{{ url_for('upipay') }}">Only waiting</a>{% else %}Waiting for you to verify. <a href="{{ url_for('upipay', view='all') }}">Show all</a>{% endif %}</p></div>
{% for d in rows %}
<div class="card">
<b>₹{{ '%.2f'|format(d.inr_amount or 0) }}</b> → <b>${{ '%.2f'|format(d.amount) }}</b>
<span class="{{ 'paid' if d.status == 'paid' else ('failed' if d.status in ('rejected','failed') else 'pending') }}">· {{ {'upi_review':'waiting for you','upi_open':'no UTR yet','paid':'approved','rejected':'rejected','failed':'expired/cancelled'}.get(d.status, d.status) }}</span><br>
<span class="muted">{{ d.id }} · {{ (d.created_at or '')[:16].replace('T',' ') }} UTC · rate ₹{{ d.rate }} · UPI {{ d.upi_id }}</span><br>
<span class="muted">Customer: {{ ('@' ~ d.username) if d.username else d.user_id }} ({{ d.user_id }})</span>
<div class="row" style="margin-top:8px;align-items:center"><span class="mono" style="flex:1">UTR: {{ d.utr or '-' }}</span></div>
{% if d.status != 'paid' %}
<form method="post">
<div class="row">
<button class="green" formaction="{{ url_for('upipay_approve', did=d.id) }}" onclick="return confirm('Add ${{ '%.2f'|format(d.amount) }} to the customer wallet?')">Approve & add ${{ '%.2f'|format(d.amount) }}</button>
{% if d.status in ('upi_review','upi_open') %}<button class="red" formaction="{{ url_for('upipay_reject', did=d.id) }}" onclick="return confirm('Reject this payment?')">Reject</button>{% endif %}
</div></form>{% endif %}</div>
{% else %}<div class="card">Nothing here.</div>{% endfor %}{% endblock %}""",
}
app.jinja_loader = DictLoader(TEMPLATES)


def render(name, **kw):
    if session.get("ok"):
        kw.setdefault("pending_count", len(db.pending_manual()))
        kw.setdefault("open_tickets", len(db.all_tickets("open")))
        kw.setdefault("upi_pending", db.upi_review_count())
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


# ------------------------------------------------- product editing
def _num(value, allow_empty=False):
    value = (value or "").strip()
    if not value and allow_empty:
        return None
    number = float(value)
    if number < 0:
        raise ValueError
    return number


@app.route("/products/new", methods=["POST"])
def add_product():
    name = request.form.get("name", "").strip()
    kind = request.form.get("kind", "auto")
    try:
        price = _num(request.form.get("price"))
    except ValueError:
        price = None
    if not name or price is None or kind not in ("auto", "manual"):
        flash("Name and a valid price are required.", "err")
        return redirect(url_for("products"))
    db.add_product(name, price, kind)
    flash(f"Product '{name}' added." + (" Now add stock below." if kind == "auto" else ""))
    return redirect(url_for("products"))


@app.route("/products/<int:pid>/edit", methods=["POST"])
def edit_product(pid):
    f = request.form
    try:
        fields = dict(
            name=f.get("name", "").strip(),
            price=_num(f.get("price")),
            old_price=_num(f.get("old_price"), allow_empty=True),
            category=(f.get("category", "").strip() or None),
            kind=f.get("kind", "auto"),
            delivery=(f.get("delivery", "").strip() or None),
            warranty=(f.get("warranty", "").strip() or None),
            prompt=(f.get("prompt", "").strip() or None),
            description=(f.get("description", "").strip() or None),
        )
        if not fields["name"] or fields["kind"] not in ("auto", "manual"):
            raise ValueError
    except ValueError:
        flash("Check the name, price and old price.", "err")
        return redirect(url_for("products"))
    db.update_product(pid, **fields)
    flash("Product saved.")
    return redirect(url_for("products"))


@app.route("/products/<int:pid>/delete", methods=["POST"])
def delete_product(pid):
    result = db.delete_product(pid)
    if result == "ok":
        flash("Product deleted (hidden from the shop).")
    elif result == "waiting":
        flash("This product still has made-to-order orders waiting. Finish or refund them on the Pending orders page first.", "err")
    else:
        flash("Product not found.", "err")
    return redirect(url_for("products"))


# ------------------------------------------------- messages to customers (sent once by the bot)
def _note(note):
    return f"\n\n💬 {htmllib.escape(note)}" if note else ""


def _msg(uid, en, zh):
    db.queue_message(uid, zh if db.get_user_language(uid) == "zh" else en)


# ------------------------------------------------- made-to-order orders
@app.route("/manual")
def manual():
    return render("manual.html", rows=db.pending_manual())


@app.route("/manual/<oid>/done", methods=["POST"])
def manual_done(oid):
    note = request.form.get("note", "").strip()
    o = db.complete_manual(oid, note or None)
    if not o:
        flash("That order is not waiting any more.", "err")
        return redirect(url_for("manual"))
    p = db.get_product_any(o["product_id"])
    name = htmllib.escape(p["name"]) if p else "your product"
    _msg(o["user_id"],
         f"✅ <b>Your order is done</b>\n\n🧾 <code>{o['id']}</code>\n📦 {name}{_note(note)}",
         f"✅ <b>您的订单已完成</b>\n\n🧾 <code>{o['id']}</code>\n📦 {name}{_note(note)}")
    flash(f"Order {oid} marked done - the customer is being notified.")
    return redirect(url_for("manual"))


@app.route("/manual/<oid>/reject", methods=["POST"])
def manual_reject(oid):
    note = request.form.get("note", "").strip()
    o = db.reject_manual(oid, note or None)
    if not o:
        flash("That order is not waiting any more.", "err")
        return redirect(url_for("manual"))
    p = db.get_product_any(o["product_id"])
    name = htmllib.escape(p["name"]) if p else "your product"
    _msg(o["user_id"],
         f"↩️ <b>Order refunded</b>\n\n🧾 <code>{o['id']}</code>\n📦 {name}\n💰 ${o['amount']:.2f} was returned to your balance.{_note(note)}",
         f"↩️ <b>订单已退款</b>\n\n🧾 <code>{o['id']}</code>\n📦 {name}\n💰 ${o['amount']:.2f} 已退回您的余额。{_note(note)}")
    flash(f"Order {oid} refunded to the customer's wallet.")
    return redirect(url_for("manual"))


@app.route("/manual/<oid>/note", methods=["POST"])
def manual_note(oid):
    note = request.form.get("note", "").strip()
    o = db.get_order(oid)
    if not o or not note:
        flash("Write a note first.", "err")
        return redirect(url_for("manual"))
    _msg(o["user_id"],
         f"💬 <b>Update on your order</b> <code>{o['id']}</code>\n\n{htmllib.escape(note)}",
         f"💬 <b>订单更新</b> <code>{o['id']}</code>\n\n{htmllib.escape(note)}")
    flash("Note sent to the customer.")
    return redirect(url_for("manual"))


# ------------------------------------------------- users / wallet
@app.route("/users")
def users():
    q = request.args.get("q", "")
    return render("users.html", rows=db.find_users(q), q=q)


@app.route("/users/<int:uid>/topup", methods=["POST"])
def topup(uid):
    try:
        amount = round(float(request.form.get("amount", "")), 2)
        if amount == 0 or abs(amount) > 100000:
            raise ValueError
    except ValueError:
        flash("Enter a non-zero amount.", "err")
        return redirect(url_for("users", q=str(uid)))
    note = request.form.get("note", "").strip()
    db.admin_topup(uid, amount)
    if amount > 0:
        _msg(uid, f"➕ <b>${amount:.2f} added to your balance.</b>{_note(note)}",
             f"➕ <b>${amount:.2f} 已充值到您的余额。</b>{_note(note)}")
    else:
        _msg(uid, f"➖ <b>${-amount:.2f} was removed from your balance.</b>{_note(note)}",
             f"➖ <b>已从您的余额扣除 ${-amount:.2f}。</b>{_note(note)}")
    flash(f"Balance changed by {amount:+.2f} for {uid}.")
    return redirect(url_for("users", q=str(uid)))


# ------------------------------------------------- support
@app.route("/tickets")
def tickets():
    return render("tickets.html", rows=db.all_tickets())


@app.route("/tickets/<int:tid>")
def ticket(tid):
    t = db.get_ticket(tid)
    if not t:
        return redirect(url_for("tickets"))
    c = db.conn()
    u = c.execute("SELECT username FROM users WHERE user_id=?", (t["user_id"],)).fetchone()
    c.close()
    info = dict(t)
    info["username"] = u["username"] if u else None
    return render("ticket.html", t=info, msgs=db.ticket_msgs(tid))


@app.route("/tickets/photo/<int:mid>")
def ticket_photo(mid):
    """Show a photo the customer attached to a ticket (fetched from Telegram with the bot token)."""
    c = db.conn()
    row = c.execute("SELECT photo_id FROM ticket_msgs WHERE id=?", (mid,)).fetchone()
    c.close()
    if not row or not row["photo_id"] or not BOT_TOKEN:
        return ("", 404)
    try:
        info = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                            params={"file_id": row["photo_id"]}, timeout=10).json()
        path = info["result"]["file_path"]
        data = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}", timeout=20).content
    except Exception:
        return ("Could not load the photo from Telegram.", 502)
    mime = _image_mime(data) or "application/octet-stream"
    resp = app.response_class(data, mimetype=mime)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.route("/tickets/<int:tid>/reply", methods=["POST"])
def ticket_reply(tid):
    t = db.get_ticket(tid)
    text = request.form.get("text", "").strip()
    if not t or not text:
        flash("Write a reply first.", "err")
        return redirect(url_for("tickets"))
    db.add_ticket_msg(tid, "admin", text)
    _msg(t["user_id"],
         f"💬 <b>Support reply</b> · ticket #{tid}\n\n{htmllib.escape(text)}",
         f"💬 <b>客服回复</b> · 工单 #{tid}\n\n{htmllib.escape(text)}")
    flash("Reply sent to the customer.")
    return redirect(url_for("ticket", tid=tid))


@app.route("/tickets/<int:tid>/status", methods=["POST"])
def ticket_status(tid):
    status = request.form.get("status", "closed")
    db.set_ticket_status(tid, "open" if status == "open" else "closed")
    return redirect(url_for("ticket", tid=tid))


# ------------------------------------------------- settings
@app.route("/settings")
def settings():
    return render("settings.html", pct=f"{db.referral_percent():g}")


@app.route("/settings/save", methods=["POST"])
def save_settings():
    try:
        pct = float(request.form.get("referral_percent", ""))
        if not (0 <= pct <= 100):
            raise ValueError
    except ValueError:
        flash("Enter a percentage between 0 and 100.", "err")
        return redirect(url_for("settings"))
    db.set_setting("referral_percent", pct)
    flash(f"Referral commission set to {pct:g}%.")
    return redirect(url_for("settings"))


# ------------------------------------------------- UPI (accounts, QR, rate, payments to verify)
MAX_QR_BYTES = 2 * 1024 * 1024


def _image_mime(data: bytes):
    """Detect PNG / JPEG from the file itself (never trust the browser's content type)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


def _read_qr(field="qr"):
    """Returns (bytes, mime) of an uploaded QR, (None, None) if nothing was chosen, or raises ValueError."""
    f = request.files.get(field)
    if not f or not f.filename:
        return None, None
    data = f.read(MAX_QR_BYTES + 1)
    if len(data) > MAX_QR_BYTES:
        raise ValueError("The QR image is too big (max 2 MB).")
    mime = _image_mime(data)
    if not mime:
        raise ValueError("The QR must be a PNG or JPG image.")
    return data, mime


@app.route("/upi")
def upi():
    return render("upi.html", rows=db.upi_list(), rate=f"{db.inr_rate():g}")


@app.route("/upi/rate", methods=["POST"])
def upi_rate():
    try:
        rate = float(request.form.get("rate", "").replace(",", "."))
        if not (1 <= rate <= 100000):
            raise ValueError
    except ValueError:
        flash("Enter a rate between 1 and 100000.", "err")
        return redirect(url_for("upi"))
    db.set_inr_rate(rate)
    flash(f"Rate saved: 1 USDT = ₹{rate:g}.")
    return redirect(url_for("upi"))


@app.route("/upi/add", methods=["POST"])
def upi_add():
    upi_id = request.form.get("upi_id", "").strip()
    label = request.form.get("label", "").strip()[:60]
    if not upi_id or len(upi_id) > 100 or " " in upi_id:
        flash("Enter a valid UPI ID (like name@bank).", "err")
        return redirect(url_for("upi"))
    try:
        qr, mime = _read_qr()
    except ValueError as e:
        flash(str(e), "err")
        return redirect(url_for("upi"))
    db.upi_add(label or upi_id, upi_id, qr, mime)
    flash("UPI account added. It is ON now.")
    return redirect(url_for("upi"))


@app.route("/upi/<int:aid>/qr", methods=["GET"])
def upi_qr(aid):
    a = db.upi_get(aid)
    if not a or not a["qr"]:
        return ("", 404)
    resp = app.response_class(bytes(a["qr"]), mimetype=a["qr_mime"] or "image/png")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


@app.route("/upi/<int:aid>/qr", methods=["POST"])
def upi_qr_set(aid):
    try:
        qr, mime = _read_qr()
    except ValueError as e:
        flash(str(e), "err")
        return redirect(url_for("upi"))
    if not qr or not db.upi_get(aid):
        flash("Choose a QR image first.", "err")
        return redirect(url_for("upi"))
    db.upi_set_qr(aid, qr, mime)
    flash("QR saved.")
    return redirect(url_for("upi"))


@app.route("/upi/<int:aid>/toggle", methods=["POST"])
def upi_toggle(aid):
    db.upi_toggle(aid)
    return redirect(url_for("upi"))


@app.route("/upi/<int:aid>/delete", methods=["POST"])
def upi_delete(aid):
    db.upi_delete(aid)
    flash("UPI account deleted.")
    return redirect(url_for("upi"))


@app.route("/upipay")
def upipay():
    q = request.args.get("q", "").strip()
    view = request.args.get("view", "review")
    if q:
        rows = db.upi_payments(q)
    elif view == "all":
        rows = db.upi_payments()
    else:
        rows = db.upi_payments(status="upi_review")
    return render("upipay.html", rows=rows, q=q, view=view)


@app.route("/upipay/<did>/approve", methods=["POST"])
def upipay_approve(did):
    d = db.approve_upi(did, "web")
    if not d:
        flash("That payment was already handled.", "err")
        return redirect(url_for("upipay"))
    bal = db.get_account(d["user_id"])["balance"] or 0
    _msg(d["user_id"],
         f"🎉 <b>Deposit received</b>\n\n➕ ${d['amount']:.2f}\n👛 Balance: <b>${bal:.2f}</b>",
         f"🎉 <b>充值已到账</b>\n\n➕ ${d['amount']:.2f}\n👛 余额：<b>${bal:.2f}</b>")
    flash(f"Approved - ${d['amount']:.2f} added to {d['user_id']}'s wallet and the customer is being told.")
    return redirect(request.referrer or url_for("upipay"))


@app.route("/upipay/<did>/reject", methods=["POST"])
def upipay_reject(did):
    d = db.reject_upi(did, "web")
    if not d:
        flash("That payment was already handled.", "err")
        return redirect(url_for("upipay"))
    utr = htmllib.escape(d["utr"] or "-")
    _msg(d["user_id"],
         f"❌ <b>UPI payment not verified</b>\n\n🧾 <code>{d['id']}</code>\n🔢 UTR: <code>{utr}</code>\n\nIf you did pay, open Support and send a screenshot.",
         f"❌ <b>UPI 付款未核实</b>\n\n🧾 <code>{d['id']}</code>\n🔢 UTR: <code>{utr}</code>\n\n如果您已付款，请联系客服并发送截图。")
    flash("Payment rejected and the customer was told.")
    return redirect(request.referrer or url_for("upipay"))
