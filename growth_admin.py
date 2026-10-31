"""Admin website pages for the growth & retention features (menu group "Growth").

Works for the main shop and for every seller: `db` is the proxy of the logged-in shop.
"""
import secrets
from html.parser import HTMLParser

from flask import Blueprint, request, redirect, url_for, flash

import growth
from growth import AUDIENCES, _parse_levels, _parse_vip

bp = Blueprint("growth_admin", __name__)

HEAD = r"""{% extends 'base.html' %}{% block body %}
<div class="seg" style="margin-bottom:14px;display:flex;gap:6px;flex-wrap:wrap">
{% for ep, label in tabs %}<a class="chip {{ 'on' if request.endpoint == ep }}" href="{{ url_for(ep) }}">{{ label }}</a>{% endfor %}
</div>
"""

TABS = [("growth_admin.home", "Settings"), ("growth_admin.promos", "Promo codes"), ("growth_admin.deals", "Flash deals"),
        ("growth_admin.bulk", "Bulk prices"), ("growth_admin.broadcast", "Broadcast"),
        ("growth_admin.reviews", "Reviews"), ("growth_admin.leaderboard", "Leaderboard")]


def _page(body, **kw):
    import web
    return web.render_str(HEAD + body + "{% endblock %}", tabs=TABS, **kw)


def _db():
    import web
    return web.db


def _product(pid):
    """Active product row (with its price) or None."""
    for p in _db().list_products():
        if p["id"] == pid:
            return p
    return None


def _back(ep):
    return redirect(url_for(ep))


_TG_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "blockquote", "tg-spoiler", "span"}


class _TagCheck(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.problem = [], None

    def handle_starttag(self, tag, attrs):
        if tag not in _TG_TAGS:
            self.problem = f"<{tag}> is not supported by Telegram (use b, i, u, s, code, pre, a)."
        else:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.problem = self.problem or f"</{tag}> does not match an opening tag."
        else:
            self.stack.pop()


def _html_problem(text):
    """Telegram rejects a message with broken HTML, so check it before queueing it for everyone."""
    p = _TagCheck()
    try:
        p.feed(text)
        p.close()
    except Exception:
        return "The HTML could not be read."
    return p.problem or (f"<{p.stack[-1]}> is never closed." if p.stack else None)


def _levels_ok(text, vip=False):
    parts = [p for p in (text or "").replace(";", ",").split(",") if p.strip()]
    parsed = _parse_vip(text) if vip else _parse_levels(text)
    return len(parts) == len(parsed)


# ------------------------------------------------------------------ settings
HOME = r"""
<h2>Growth settings</h2>
<p class="muted">Everything here is live in the bot right after you save. Leave a box empty to turn that feature off.</p>
<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">
{% for label, value in stats %}<div class="card" style="flex:1;min-width:130px;margin:0"><div class="muted">{{ label }}</div><h3 style="margin:4px 0 0">{{ value }}</h3></div>{% endfor %}
</div>
<div class="card"><form method="post">
<label>Deposit bonus <span class="hint">format: top-up amount:bonus %, separated by commas. Example <code>10:5,50:10</code> = top up $10+ get +5%, $50+ get +10%</span></label>
<input name="deposit_bonus" value="{{ s.deposit_bonus }}" placeholder="10:5,50:10">
<label>Cashback on every purchase (%) <span class="hint">0 = off. Paid into the wallet after each completed order</span></label>
<input name="cashback_percent" type="number" step="0.1" min="0" max="50" value="{{ s.cashback_percent }}">
<label>VIP levels <span class="hint">format: total spent:name:discount %. Example <code>50:Silver:2,200:Gold:5</code>. The discount applies automatically to every order</span></label>
<input name="vip_levels" value="{{ s.vip_levels }}" placeholder="50:Silver:2,200:Gold:5">
<label>Low-balance reminder below ($) <span class="hint">customers who bought before get one reminder every 3 days. 0 = off</span></label>
<input name="low_balance_alert" type="number" step="0.1" min="0" value="{{ s.low_balance_alert }}">
<label><input type="checkbox" name="reviews_enabled" value="1" {{ 'checked' if s.reviews_enabled }} style="width:auto;margin-right:6px"> Ask customers to rate their orders (stars shown on products)</label>
<label>FAQ text <span class="hint">shown by the FAQ button and /faq. HTML allowed: &lt;b&gt; &lt;i&gt; &lt;code&gt;. Empty = default FAQ</span></label>
<textarea name="faq_text" rows="9">{{ s.faq_text }}</textarea>
<button type="submit">Save</button></form></div>
<div class="card"><h3>Products customers are waiting for</h3>
<p class="muted">People who tapped "Notify me" on a sold-out product. They get a message automatically when you add stock.</p>
<div class="scroll"><table><tr><th>Product</th><th>Waiting</th></tr>
{% for r in waiting %}<tr><td>{{ r.name or r.product_id }}</td><td>{{ r.n }}</td></tr>
{% else %}<tr><td colspan="2">Nobody is waiting for a restock.</td></tr>{% endfor %}</table></div></div>
"""


@bp.route("/growth", methods=["GET", "POST"])
def home():
    db = _db()
    if request.method == "POST":
        f = request.form
        bonus, vip = f.get("deposit_bonus", "").strip(), f.get("vip_levels", "").strip()
        if not _levels_ok(bonus) or not _levels_ok(vip, vip=True):
            flash("Check the format of Deposit bonus (10:5,50:10) and VIP levels (50:Silver:2,200:Gold:5).", "err")
            return _back("growth_admin.home")
        try:
            cashback = float(f.get("cashback_percent") or 0)
            low = float(f.get("low_balance_alert") or 0)
            if not (0 <= cashback <= 50) or low < 0:
                raise ValueError
        except ValueError:
            flash("Cashback must be 0-50 and the low-balance amount cannot be negative.", "err")
            return _back("growth_admin.home")
        db.set_setting("deposit_bonus", bonus)
        db.set_setting("vip_levels", vip)
        db.set_setting("cashback_percent", cashback)
        db.set_setting("low_balance_alert", low)
        db.set_setting("reviews_enabled", 1 if f.get("reviews_enabled") else 0)
        db.set_setting("faq_text", f.get("faq_text", "").strip()[:3000])
        flash("Growth settings saved. They are live in the bot now.")
        return _back("growth_admin.home")
    s = type("S", (), {})()
    s.deposit_bonus = db.get_setting("deposit_bonus", "")
    s.vip_levels = db.get_setting("vip_levels", "")
    s.cashback_percent = db.get_setting("cashback_percent", 0.0)
    s.low_balance_alert = db.get_setting("low_balance_alert", 1.0)
    s.reviews_enabled = db.get_setting("reviews_enabled", 1)
    s.faq_text = db.get_setting("faq_text", "")
    ratings = db.rating_summary()
    avg = round(sum(r["a"] * r["n"] for r in ratings) / max(1, sum(r["n"] for r in ratings)), 2) if ratings else "-"
    stats = [("Active promo codes", sum(1 for p in db.promo_list() if p["active"])),
             ("Live flash deals", len(db.flash_list(only_live=True))),
             ("Bulk price rules", len(db.tiers_all())),
             ("Restock alerts waiting", sum(r["n"] for r in db.restock_counts())),
             ("Average rating", avg)]
    return _page(HOME, s=s, stats=stats, waiting=db.restock_counts())


# ------------------------------------------------------------------ promo codes
PROMOS = r"""
<h2>Promo codes</h2>
<p class="muted"><b>Percent</b> codes give a discount on the customer's next purchase. <b>Credit</b> codes add free money to the wallet.
Each customer can use a code once. Customers enter codes in 💰 Wallet → 🎟 Promo code (or <code>/promo CODE</code>).</p>
<div class="card"><div class="scroll"><table>
<tr><th>Code</th><th>Type</th><th>Value</th><th>Used</th><th>Expires</th><th>Status</th><th></th></tr>
{% for p in rows %}<tr><td><code>{{ p.code }}</code></td><td>{{ p.kind }}</td>
<td>{{ ('%g%%' % p.value) if p.kind == 'percent' else ('$%.2f' % p.value) }}</td>
<td>{{ p.used or 0 }}{{ (' / ' ~ p.max_uses) if p.max_uses else '' }}</td>
<td>{{ (p.expires_at or '')[:16].replace('T', ' ') or 'never' }}</td>
<td class="{{ 'paid' if p.active else 'failed' }}">{{ 'Active' if p.active else 'Off' }}</td>
<td style="display:flex;gap:6px">
<form method="post"><input type="hidden" name="action" value="toggle"><input type="hidden" name="code" value="{{ p.code }}"><button class="grey sm">{{ 'Turn off' if p.active else 'Turn on' }}</button></form>
<form method="post" onsubmit="return confirm('Delete this code?')"><input type="hidden" name="action" value="delete"><input type="hidden" name="code" value="{{ p.code }}"><button class="red sm">Delete</button></form></td></tr>
{% else %}<tr><td colspan="7">No promo codes yet.</td></tr>{% endfor %}</table></div></div>
<div class="card"><h3>New promo code</h3><form method="post"><input type="hidden" name="action" value="add">
<label>Code <span class="hint">letters/numbers, leave empty to generate one</span></label><input name="code" maxlength="32" placeholder="WELCOME10">
<label>Type</label><select name="kind"><option value="percent">Percent discount on next purchase</option><option value="credit">Free wallet credit ($)</option></select>
<label>Value <span class="hint">percent (1-90) or dollars</span></label><input name="value" type="number" step="0.01" min="0.01" required>
<label>Maximum total uses <span class="hint">0 = unlimited</span></label><input name="max_uses" type="number" min="0" value="0">
<label>Valid for (days) <span class="hint">empty = never expires</span></label><input name="days" type="number" min="1" step="1">
<button type="submit">Create code</button></form></div>
"""


@bp.route("/growth/promos", methods=["GET", "POST"])
def promos():
    db = _db()
    if request.method == "POST":
        f = request.form
        act = f.get("action")
        try:
            if act == "add":
                code = (f.get("code") or "").strip().upper() or "".join(
                    secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
                days = f.get("days", "").strip()
                code = db.promo_add(code, f.get("kind"), float(f.get("value") or 0), int(f.get("max_uses") or 0),
                                    float(days) if days else None)
                flash(f"Promo code {code} created.")
            elif act == "toggle":
                db.promo_toggle(f.get("code", ""))
            elif act == "delete":
                db.promo_delete(f.get("code", ""))
                flash("Code deleted.")
        except ValueError as e:
            flash(str(e) or "Please check the values.", "err")
        return _back("growth_admin.promos")
    return _page(PROMOS, rows=db.promo_list())


# ------------------------------------------------------------------ flash deals
DEALS = r"""
<h2>Flash deals</h2>
<p class="muted">A time-limited price on one product. It shows a ⚡ on the product button, a countdown on the card and is posted to your sales group.
It replaces the normal price while it runs (custom per-user prices still win).</p>
<div class="card"><div class="scroll"><table>
<tr><th>Product</th><th>Normal</th><th>Deal price</th><th>Ends (UTC)</th><th>Status</th><th></th></tr>
{% for d in rows %}<tr><td>{{ d.product or d.product_id }}</td><td>${{ '%.2f'|format(d.base or 0) }}</td><td><b>${{ '%.2f'|format(d.price) }}</b></td>
<td>{{ d.ends_at[:16].replace('T', ' ') }}</td><td class="{{ 'paid' if d.live else 'failed' }}">{{ 'Live' if d.live else 'Ended' }}</td>
<td>{% if d.live %}<form method="post"><input type="hidden" name="action" value="end"><input type="hidden" name="fid" value="{{ d.id }}"><button class="red sm">End now</button></form>{% endif %}</td></tr>
{% else %}<tr><td colspan="6">No flash deals yet.</td></tr>{% endfor %}</table></div></div>
<div class="card"><h3>Start a flash deal</h3><form method="post"><input type="hidden" name="action" value="add">
<label>Product</label><select name="pid" required>{% for p in products %}<option value="{{ p.id }}">{{ p.name }} (now ${{ '%.2f'|format(p.price) }})</option>{% endfor %}</select>
<label>Deal price ($)</label><input name="price" type="number" step="0.01" min="0.01" required>
<label>Lasts (hours)</label><input name="hours" type="number" step="0.5" min="0.5" max="720" value="6" required>
<label><input type="checkbox" name="notify" value="1" style="width:auto;margin-right:6px"> Also message all customers in the bot</label>
<button type="submit">Start deal</button></form></div>
"""


@bp.route("/growth/deals", methods=["GET", "POST"])
def deals():
    db = _db()
    if request.method == "POST":
        f = request.form
        try:
            if f.get("action") == "add":
                pid = int(f.get("pid"))
                price, hours = float(f.get("price")), float(f.get("hours"))
                p = _product(pid)
                if not p or price <= 0 or hours <= 0:
                    raise ValueError
                if price >= float(p["price"]):
                    flash("Heads up: the deal price is not lower than the normal price, so customers will not see a discount.", "err")
                db.flash_add(pid, price, hours, bool(f.get("notify")))
                flash("Flash deal started. It is announced in the bot within 2 minutes.")
            elif f.get("action") == "end":
                db.flash_end(int(f.get("fid")))
                flash("Deal ended.")
        except (ValueError, TypeError):
            flash("Please choose a product, a price and a duration.", "err")
        return _back("growth_admin.deals")
    return _page(DEALS, rows=db.flash_list(), products=db.list_products())


# ------------------------------------------------------------------ bulk prices
BULK = r"""
<h2>Bulk prices</h2>
<p class="muted">Cheaper price per item when a customer buys more. Example: normal $10, 5+ → $9, 20+ → $8. The bot shows the levels on the product
card and nudges customers to add a few more items. Custom per-user prices are not reduced further.</p>
<div class="card"><div class="scroll"><table>
<tr><th>Product</th><th>Buy at least</th><th>Price each</th><th>Discount</th><th></th></tr>
{% for t in rows %}<tr><td>{{ t.name or t.product_id }}</td><td>{{ t.min_qty }}</td><td>${{ '%.2f'|format(t.price) }}</td>
<td>{{ ('-%.0f%%' % ((1 - t.price / t.base) * 100)) if t.base else '' }}</td>
<td><form method="post"><input type="hidden" name="action" value="delete"><input type="hidden" name="pid" value="{{ t.product_id }}"><input type="hidden" name="qty" value="{{ t.min_qty }}"><button class="red sm">Delete</button></form></td></tr>
{% else %}<tr><td colspan="5">No bulk prices yet.</td></tr>{% endfor %}</table></div></div>
<div class="card"><h3>Add a bulk level</h3><form method="post"><input type="hidden" name="action" value="add">
<label>Product</label><select name="pid" required>{% for p in products %}<option value="{{ p.id }}">{{ p.name }} (now ${{ '%.2f'|format(p.price) }})</option>{% endfor %}</select>
<label>Buy at least (quantity)</label><input name="qty" type="number" min="2" step="1" required>
<label>Price per item ($)</label><input name="price" type="number" step="0.01" min="0.01" required>
<button type="submit">Save (same quantity = update)</button></form></div>
"""


@bp.route("/growth/bulk", methods=["GET", "POST"])
def bulk():
    db = _db()
    if request.method == "POST":
        f = request.form
        try:
            pid, qty = int(f.get("pid")), int(f.get("qty"))
            if f.get("action") == "delete":
                db.tier_delete(pid, qty)
                flash("Bulk level deleted.")
            else:
                price = float(f.get("price"))
                p = _product(pid)
                if not p or qty < 2 or price <= 0:
                    raise ValueError
                if price >= float(p["price"]):
                    flash("Heads up: this bulk price is not lower than the normal price.", "err")
                db.tier_set(pid, qty, price)
                flash("Bulk price saved.")
        except (ValueError, TypeError):
            flash("Enter a product, a quantity of 2 or more, and a price.", "err")
        return _back("growth_admin.bulk")
    return _page(BULK, rows=db.tiers_all(), products=db.list_products())


# ------------------------------------------------------------------ broadcast
BROADCAST = r"""
<h2>Broadcast</h2>
<p class="muted">Send a message to your customers in the bot (new stock, offers, news). It is delivered in the background, about 5 users per second.
You can also use <code>/broadcast your message</code> inside the bot.</p>
{% if waiting %}<div class="msg">{{ waiting }} message(s) are still waiting to be delivered.</div>{% endif %}
<div class="card"><form method="post" onsubmit="return confirm('Send this message now?')">
<label>Send to</label><select name="audience">{% for k, v in audiences.items() %}<option value="{{ k }}">{{ v }} ({{ counts[k] }})</option>{% endfor %}</select>
<label>Message <span class="hint">HTML allowed: &lt;b&gt;bold&lt;/b&gt; &lt;i&gt;italic&lt;/i&gt; &lt;code&gt;code&lt;/code&gt; &lt;a href="https://..."&gt;link&lt;/a&gt;. Max 3500 characters</span></label>
<textarea name="text" rows="8" maxlength="3500" required placeholder="🔥 New stock just landed! Open 🛍 Products to see it."></textarea>
<button type="submit">Send broadcast</button></form></div>
<div class="card"><h3>Recent broadcasts</h3><div class="scroll"><table><tr><th>When (UTC)</th><th>Audience</th><th>Users</th><th>Message</th></tr>
{% for b in history %}<tr><td>{{ b.created_at[:16].replace('T', ' ') }}</td><td>{{ b.audience }}</td><td>{{ b.n }}</td><td>{{ (b.text or '')[:90] }}</td></tr>
{% else %}<tr><td colspan="4">Nothing sent yet.</td></tr>{% endfor %}</table></div></div>
"""


@bp.route("/growth/broadcast", methods=["GET", "POST"])
def broadcast():
    db = _db()
    if request.method == "POST":
        text = (request.form.get("text") or "").strip()
        audience = request.form.get("audience", "all")
        if not text or audience not in AUDIENCES or len(text) > 3500:
            flash("Write a message (max 3500 characters) and choose who gets it.", "err")
        elif _html_problem(text):
            flash("Fix the formatting first: " + _html_problem(text), "err")
        else:
            n = db.broadcast_queue(text, audience)
            flash(f"Broadcast queued for {n} user(s).")
        return _back("growth_admin.broadcast")
    counts = {k: len(db.audience_ids(k)) for k in AUDIENCES}
    return _page(BROADCAST, audiences=AUDIENCES, counts=counts, history=db.broadcast_history(), waiting=db.outbox_waiting())


# ------------------------------------------------------------------ reviews
REVIEWS = r"""
<h2>Reviews</h2>
<p class="muted">Customers are asked to rate an order a few hours after delivery (switch this off in Settings). The average is shown on the product card.</p>
<div class="card"><h3>By product</h3><div class="scroll"><table><tr><th>Product</th><th>Average</th><th>Reviews</th></tr>
{% for r in summary %}<tr><td>{{ r.name }}</td><td>{{ '%.1f'|format(r.a) }} ⭐</td><td>{{ r.n }}</td></tr>
{% else %}<tr><td colspan="3">No reviews yet.</td></tr>{% endfor %}</table></div></div>
<div class="card"><h3>Latest reviews</h3><div class="scroll"><table><tr><th>When</th><th>Product</th><th>Customer</th><th>Rating</th><th>Comment</th><th></th></tr>
{% for r in rows %}<tr><td>{{ (r.created_at or '')[:10] }}</td><td>{{ r.product or r.product_id }}</td><td>{{ ('@' ~ r.username) if r.username else r.user_id }}</td>
<td>{{ '⭐' * r.rating }}</td><td>{{ r.comment or '' }}</td>
<td><form method="post" onsubmit="return confirm('Delete this review?')"><input type="hidden" name="rid" value="{{ r.id }}"><button class="red sm">Delete</button></form></td></tr>
{% else %}<tr><td colspan="6">No reviews yet.</td></tr>{% endfor %}</table></div></div>
"""


@bp.route("/growth/reviews", methods=["GET", "POST"])
def reviews():
    db = _db()
    if request.method == "POST":
        try:
            db.review_delete(int(request.form.get("rid")))
            flash("Review deleted.")
        except (ValueError, TypeError):
            pass
        return _back("growth_admin.reviews")
    return _page(REVIEWS, rows=db.reviews_list(), summary=db.rating_summary())


# ------------------------------------------------------------------ leaderboard
BOARD = r"""
<h2>Referral leaderboard</h2>
<p class="muted">Customers see the top 10 of the last 30 days (names are partly hidden) in 🎁 Referral → 🏆 Leaderboard.
Use the form below to pay a monthly prize to the top 3.</p>
<div class="seg" style="display:flex;gap:6px;margin-bottom:10px">
<a class="chip {{ 'on' if days == 30 }}" href="{{ url_for('growth_admin.leaderboard', days=30) }}">Last 30 days</a>
<a class="chip {{ 'on' if not days }}" href="{{ url_for('growth_admin.leaderboard', days=0) }}">All time</a></div>
<div class="card"><div class="scroll"><table><tr><th>#</th><th>Customer</th><th>Telegram ID</th><th>Invited</th><th>Referral earnings (all time)</th></tr>
{% for r in rows %}<tr><td>{{ loop.index }}</td><td>{{ ('@' ~ r.username) if r.username else '-' }}</td><td>{{ r.uid }}</td><td>{{ r.n }}</td><td>${{ '%.2f'|format(r.earned) }}</td></tr>
{% else %}<tr><td colspan="5">No referrals in this period.</td></tr>{% endfor %}</table></div></div>
<div class="card"><h3>Pay prizes to the top 3 (last 30 days)</h3>
<form method="post" onsubmit="return confirm('Add these amounts to the wallets of the current top 3?')">
<label>1st place ($)</label><input name="p1" type="number" step="0.01" min="0" value="0">
<label>2nd place ($)</label><input name="p2" type="number" step="0.01" min="0" value="0">
<label>3rd place ($)</label><input name="p3" type="number" step="0.01" min="0" value="0">
<button type="submit">Pay prizes</button></form></div>
"""


@bp.route("/growth/leaderboard", methods=["GET", "POST"])
def leaderboard():
    db = _db()
    if request.method == "POST":
        try:
            prizes = [float(request.form.get(k) or 0) for k in ("p1", "p2", "p3")]
            if any(p < 0 for p in prizes):
                raise ValueError
        except ValueError:
            flash("Enter valid prize amounts.", "err")
            return _back("growth_admin.leaderboard")
        top = db.referral_leaderboard(days=30, limit=3)
        paid = 0
        for i, r in enumerate(top):
            if prizes[i] > 0:
                db.reward_user(r["uid"], prizes[i], f"Referral champion #{i + 1} this month")
                paid += 1
        flash(f"Prizes paid to {paid} customer(s)." if paid else "Nothing to pay: enter an amount above 0 or wait for referrals.")
        return _back("growth_admin.leaderboard")
    days = request.args.get("days", "30")
    days = 30 if days != "0" else 0
    return _page(BOARD, rows=db.referral_leaderboard(days=days or None, limit=20), days=days)


def register(app, _db_unused=None):
    app.register_blueprint(bp)
