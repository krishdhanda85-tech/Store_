"""Public storefront: /shop shows the MAIN shop's products (same database as the Telegram bot).

Read-only. Nothing secret is shown (no cost, no stock lines, no prompts). Buying happens in the bot.
Env (optional): BOT_USERNAME=yourbot  (fallback if the main shop has no bot username saved)
"""
import os

from flask import render_template_string, jsonify

import tenancy

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ shop }} - Products</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--line:#27344e;--ink:#edf3ff;--muted:#a4b2cb;--accent:#70e1c2;--red:#ff9a9a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1000px;margin:auto;padding:22px 14px 60px}
h1{margin:0 0 4px;font-size:28px}.muted{color:var(--muted)}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}
.bar input,.bar select{flex:1;min-width:150px;padding:11px;border-radius:10px;border:1px solid var(--line);background:#0a1120;color:var(--ink);font:inherit}
.btn{display:inline-block;padding:10px 14px;border-radius:10px;background:var(--accent);color:#071521;font-weight:800;text-decoration:none}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;display:flex;flex-direction:column;gap:8px}
.card h3{margin:0;font-size:17px}.cat{font-size:12px;color:var(--accent);font-weight:700}
.price{font-size:20px;font-weight:800}.old{color:var(--muted);text-decoration:line-through;font-size:14px;margin-left:6px}
.tag{font-size:12px;font-weight:800;padding:3px 8px;border-radius:999px;align-self:flex-start}
.in{background:#15372f;color:var(--accent)}.out{background:#3a1c1c;color:var(--red)}.mto{background:#2b2a14;color:#e9d9a7}
.desc{color:var(--muted);font-size:13px;white-space:pre-line}
footer{margin-top:30px;text-align:center;color:var(--muted);font-size:12px}
</style></head><body><div class="wrap">
<h1>{{ shop }}</h1><div class="muted">Live products and prices. Orders are placed in our Telegram bot.</div>
<div class="bar"><input id="q" placeholder="Search products..." oninput="f()">
<select id="c" onchange="f()"><option value="">All categories</option>{% for c in cats %}<option>{{ c }}</option>{% endfor %}</select>
{% if bot %}<a class="btn" href="https://t.me/{{ bot }}">Open Telegram bot</a>{% endif %}</div>
<div class="grid" id="g">
{% for p in products %}<div class="card" data-n="{{ (p.name ~ ' ' ~ p.category)|lower }}" data-c="{{ p.category }}">
<div class="cat">{{ p.category or 'General' }}</div><h3>{{ p.name }}</h3>
<div><span class="price">{{ p.price }}</span>{% if p.old_price %}<span class="old">{{ p.old_price }}</span>{% endif %}</div>
{% if p.state == 'in' %}<span class="tag in">In stock</span>{% elif p.state == 'mto' %}<span class="tag mto">Made to order</span>{% else %}<span class="tag out">Out of stock</span>{% endif %}
{% if p.description %}<div class="desc">{{ p.description }}</div>{% endif %}
{% if bot %}<a class="btn" href="https://t.me/{{ bot }}">Buy on Telegram</a>{% endif %}</div>
{% else %}<div class="muted">No products yet.</div>{% endfor %}</div>
<footer>Developers: <a href="/api/docs" style="color:var(--accent)">API documentation</a></footer></div>
<script>function f(){var q=document.getElementById('q').value.toLowerCase(),c=document.getElementById('c').value;
document.querySelectorAll('#g .card').forEach(function(e){e.style.display=(e.dataset.n.indexOf(q)>-1&&(!c||e.dataset.c===c))?'':'none'})}</script>
</body></html>"""


def _products():
    t = tenancy.get_tenant(tenancy.MAIN_ID)
    if t is None:
        return "Shop", "", []
    with tenancy.use(t):
        db = tenancy.db
        rows = db.list_products()
        extra = {}
        try:                                    # linked supplier stock counts as stock, like in the API
            c = db.conn()
            extra = {int(r["product_id"]): int(r["provider_stock"] or 0)
                     for r in c.execute("SELECT product_id, provider_stock FROM provider_map").fetchall()}
            c.close()
        except Exception:
            extra = {}
        out = []
        for r in rows:
            stock = int(r["stock"] or 0) + extra.get(int(r["id"]), 0)
            manual = (r["kind"] or "auto") != "auto"
            state = "mto" if manual else ("in" if stock > 0 else "out")
            out.append({"id": r["id"], "name": r["name"], "category": r["category"] or "",
                        "price": r["price"], "old_price": r["old_price"] or None,
                        "description": r["description"] or "", "state": state})
        bot = (getattr(t, "bot_username", "") or os.getenv("BOT_USERNAME", "")).lstrip("@")
        return t.name, bot, out


def register(app):
    @app.route("/shop")
    def storefront():
        shop, bot, products = _products()
        cats = sorted({p["category"] for p in products if p["category"]})
        return render_template_string(PAGE, shop=shop, bot=bot, products=products, cats=cats)

    @app.route("/shop/products.json")
    def storefront_json():
        shop, bot, products = _products()
        return jsonify({"ok": True, "shop": shop, "products": products})
