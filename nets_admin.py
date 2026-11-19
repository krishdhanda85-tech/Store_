"""Admin website page: Crypto coins (deposit networks).

Same thing as the Telegram commands /nets /addnet /setaddr /togglenet /delnet, but from the website:
add a coin + network + wallet address, change the address, show/hide it in the bot, delete it.

Data lives in crypto_nets.py (settings key 'crypto_nets'), so the bot sees every change immediately.
Used by:  web.py -> register(app)
"""
from flask import Blueprint, request, redirect, url_for, flash

import crypto_nets

bp = Blueprint("nets_admin", __name__)

PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Crypto coins</h2>
<p class="muted">Coins / networks customers can deposit with. Changes show in the bot at once.
Every coin added here is checked by an admin in Telegram (Approve / Reject) after the customer sends the TXID.</p>

<div class="card"><h3>Add a coin</h3>
<form method="post" action="{{ url_for('nets_admin.add') }}">
<label>Key <span class="hint">2-12 letters/digits, e.g. trc20, plasma, btc</span></label>
<input name="key" required maxlength="12" placeholder="trc20" autocapitalize="none">
<label>Coin <span class="hint">e.g. USDT, BTC</span></label>
<input name="coin" required maxlength="12" placeholder="USDT">
<label>Network <span class="hint">e.g. Tron (TRC20), Bitcoin</span></label>
<input name="network" required maxlength="40" placeholder="Tron (TRC20)">
<label>Wallet address</label>
<input name="address" required maxlength="200" placeholder="T..." autocomplete="off" autocapitalize="none">
<label>Memo / tag <span class="hint">optional</span></label>
<input name="memo" maxlength="60">
<div style="margin-top:10px"><button type="submit">Add coin</button></div></form></div>

<div class="card"><h3>Your coins ({{ nets|length }})</h3>
{% if nets %}
{% for n in nets %}
<div style="border-top:1px solid rgba(128,128,128,.25);padding:12px 0">
<div><b>{{ n.coin }} · {{ n.network }}</b> <span class="muted">({{ n.key }})</span>
{% if n.get('active', True) %}<span class="badge">Visible</span>{% else %}<span class="muted">Hidden</span>{% endif %}</div>
<form method="post" action="{{ url_for('nets_admin.address', key=n.key) }}">
<label>Wallet address</label>
<input name="address" value="{{ n.address }}" required maxlength="200" autocomplete="off" autocapitalize="none">
<label>Memo / tag</label>
<input name="memo" value="{{ n.get('memo', '') }}" maxlength="60">
<div style="margin-top:8px"><button type="submit" class="sm">Save address</button></div></form>
<div style="display:flex;gap:8px;margin-top:8px">
<form method="post" action="{{ url_for('nets_admin.toggle', key=n.key) }}"><button type="submit" class="sm">{{ 'Hide' if n.get('active', True) else 'Show' }}</button></form>
<form method="post" action="{{ url_for('nets_admin.delete', key=n.key) }}" onsubmit="return confirm('Delete {{ n.coin }} · {{ n.network }}?')"><button type="submit" class="sm">Delete</button></form>
</div></div>
{% endfor %}
{% else %}<p class="muted">No coins added yet.</p>{% endif %}</div>
{% endblock %}"""


def _back():
    return redirect(url_for("nets_admin.home"))


@bp.route("/nets")
def home():
    import web
    return web.render_str(PAGE, nets=crypto_nets.all_nets(web.db))


@bp.route("/nets/add", methods=["POST"])
def add():
    import web
    f = request.form
    try:
        crypto_nets.add(web.db, f.get("key", ""), f.get("coin", ""), f.get("network", ""),
                        f.get("address", ""), f.get("memo", ""))
        flash("Coin added.")
    except ValueError as e:
        flash(str(e), "err")
    return _back()


@bp.route("/nets/<key>/address", methods=["POST"])
def address(key):
    import web
    try:
        crypto_nets.set_address(web.db, key, request.form.get("address", ""), request.form.get("memo", ""))
        flash("Address saved.")
    except ValueError as e:
        flash(str(e), "err")
    return _back()


@bp.route("/nets/<key>/toggle", methods=["POST"])
def toggle(key):
    import web
    try:
        on = crypto_nets.toggle(web.db, key)
        flash("Now visible in the bot." if on else "Now hidden from the bot.")
    except ValueError as e:
        flash(str(e), "err")
    return _back()


@bp.route("/nets/<key>/delete", methods=["POST"])
def delete(key):
    import web
    try:
        crypto_nets.remove(web.db, key)
        flash("Coin deleted.")
    except ValueError as e:
        flash(str(e), "err")
    return _back()


def register(app):
    app.register_blueprint(bp)
