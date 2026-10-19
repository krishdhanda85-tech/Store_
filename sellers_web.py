"""Website pages for the multi-seller platform.

  /register           public   a new seller creates an account
  /connect            seller   bot token, Telegram owner ID, Binance + BEP20 payment details, channels
  /connect/test       seller   checks the Binance / BSC connection with the saved details
  /account            seller   change password
  /sellers            owner    list of all sellers: approve, suspend, see their numbers
"""
import time

from flask import flash, redirect, render_template_string, request, session, url_for

import tenancy

_reg_attempts = {}

REGISTER_PAGE = r"""{% extends 'base.html' %}{% block body %}
<div class="card login">
<div class="mark">{{ shop[:1]|upper }}</div>
<h2>Open your own shop</h2>
<p class="muted">Create a seller account. You get your own Telegram bot, your own products and coupons,
and customers pay straight into your own Binance / USDT wallet.</p>
{% if mode == 'approval' %}<p class="muted">New accounts are approved by the platform owner before the bot goes live.</p>{% endif %}
<form method="post">
<label for="sn">Shop name</label>
<input id="sn" name="shop_name" maxlength="40" required value="{{ f.get('shop_name','') }}" autofocus>
<label for="em">E-mail</label>
<input id="em" type="email" name="email" maxlength="120" required value="{{ f.get('email','') }}">
<label for="pw">Password <span class="muted">(at least 8 characters)</span></label>
<input id="pw" type="password" name="password" minlength="8" required>
<button type="submit">Create seller account</button>
</form>
<p class="muted" style="margin:14px 0 0"><a href="{{ url_for('login') }}">Already have an account? Log in</a></p>
</div>{% endblock %}"""

CONNECT_PAGE = r"""{% extends 'base.html' %}{% block body %}
<div class="page-head"><h2>Bot &amp; payments</h2></div>

<div class="card">
<h3>Status</h3>
<p>Account: <b class="{{ 'paid' if t.status == 'active' else ('failed' if t.status == 'suspended' else 'pending') }}">{{ t.status|upper }}</b>
{% if t.status == 'pending' %}<span class="muted"> - waiting for the platform owner to approve you.</span>{% endif %}
{% if t.status == 'suspended' %}<span class="muted"> - contact the platform owner.</span>{% endif %}</p>
<p>Bot:
{% if not t.bot_token %}<b class="pending">not connected yet</b>
{% else %}<b>@{{ t.bot_username }}</b> -
  {% if row.bot_state == 'running' %}<b class="paid">running</b>
  {% elif row.bot_state == 'error' %}<b class="failed">error</b> <span class="muted">{{ row.bot_error }}</span>
  {% elif t.status != 'active' %}<span class="muted">starts after approval</span>
  {% else %}<span class="muted">starting... refresh in a few seconds</span>{% endif %}
  {% if t.bot_username %} <a href="https://t.me/{{ t.bot_username }}" target="_blank" rel="noopener">open bot</a>{% endif %}
{% endif %}</p>
<p>Payments: Binance Pay <b class="{{ 'paid' if t.binance.binance_enabled else 'pending' }}">{{ 'ready' if t.binance.binance_enabled else 'not set' }}</b>
&nbsp;|&nbsp; USDT BEP20 <b class="{{ 'paid' if t.binance.bsc_enabled else 'pending' }}">{{ 'ready' if t.binance.bsc_enabled else 'not set' }}</b></p>
{% if not t.owner_id %}<p class="err" style="padding:10px;border-radius:8px">Add your Telegram ID below - the bot only starts when it is set.</p>{% endif %}
</div>

{% if report %}<div class="card"><h3>Payment test</h3><pre class="mono" style="white-space:pre-wrap;margin:0">{{ report|join('\n') }}</pre></div>{% endif %}

<form method="post" action="{{ url_for('connect_save') }}">
<div class="card">
<h3>1. Your shop &amp; Telegram bot</h3>
<label>Shop name</label>
<input name="shop_name" maxlength="40" value="{{ t.name }}">
<label>Bot token <span class="muted">(create a bot with @BotFather, paste the token)</span></label>
<input name="bot_token" autocomplete="off" placeholder="{{ 'saved - paste a new token only to replace it' if t.bot_token else '123456789:AAExampleToken' }}">
<label>Your Telegram ID <span class="muted">(send /start to @userinfobot to see it) - you become the owner/admin of the bot</span></label>
<input name="owner_tg_id" inputmode="numeric" value="{{ t.owner_id or '' }}" placeholder="e.g. 7095639003">
</div>

<div class="card">
<h3>2. Binance Pay (your own account)</h3>
<p class="muted">Create an API key in Binance with ONLY <b>Enable Reading</b>. Never turn on trading or withdrawals.
Money goes straight into your Binance; we only read the payment list to confirm orders.</p>
<label>Binance API key</label>
<input name="bn_key" autocomplete="off" placeholder="{{ 'saved - leave empty to keep' if row.bn_key_enc else 'paste API key' }}">
<label>Binance API secret</label>
<input name="bn_secret" type="password" autocomplete="off" placeholder="{{ 'saved - leave empty to keep' if row.bn_secret_enc else 'paste API secret' }}">
<label>Binance Pay ID (UID)</label>
<input name="pay_id" inputmode="numeric" value="{{ row.pay_id }}" placeholder="shown to customers when they pay">
</div>

<div class="card">
<h3>3. USDT on BNB Smart Chain (BEP20) - optional</h3>
<label>Your BEP20 deposit address</label>
<input name="deposit_address" value="{{ row.deposit_address }}" placeholder="0x...">
<label>Network name shown to customers <span class="muted">(optional)</span></label>
<input name="deposit_network" value="{{ row.deposit_network }}" placeholder="BNB Smart Chain (BEP20)">
<p class="muted">Use a wallet address you control. Never share a seed phrase or private key with anyone.</p>
</div>

<div class="card">
<h3>4. Channel (optional)</h3>
<p class="muted">Add your bot as an admin of the channel first.</p>
<label>Channel customers must join</label>
<input name="ch_required" value="{{ row.ch_required }}" placeholder="@YourChannel or -100...">
<label>Join link</label>
<input name="ch_url" value="{{ row.ch_url }}" placeholder="https://t.me/YourChannel">
<label>Channel for sales announcements <span class="muted">(blank = same as above)</span></label>
<input name="ch_sales" value="{{ row.ch_sales }}" placeholder="@YourSalesChannel">
</div>

<button type="submit">Save settings</button>
</form>

<form method="post" action="{{ url_for('connect_test') }}" style="margin-top:12px">
<button type="submit" class="grey">Test payments with saved details</button>
</form>

<div class="card" style="margin-top:18px">
<h3>Change password</h3>
<form method="post" action="{{ url_for('account_password') }}">
<label>Current password</label><input type="password" name="old" required>
<label>New password</label><input type="password" name="new" minlength="8" required>
<button type="submit" class="grey">Change password</button>
</form>
</div>
{% endblock %}"""

SELLERS_PAGE = r"""{% extends 'base.html' %}{% block body %}
<div class="page-head"><h2>Sellers</h2></div>
<p class="muted">Sign-up mode: <b>{{ mode }}</b> (set the Railway variable <code>SELLER_SIGNUP</code> to
<code>open</code>, <code>approval</code> or <code>closed</code>). Share <code>{{ url_for('register_seller', _external=True) }}</code> with new sellers.
Each seller has their own database, bot, products, coupons and payments.</p>
<div class="card"><div class="scroll"><table>
<tr><th>ID</th><th>Shop</th><th>E-mail</th><th>Status</th><th>Bot</th><th>Users</th><th>Paid orders</th><th>Sales (USD)</th><th>Products</th><th>Actions</th></tr>
{% for s in rows %}<tr>
<td>{{ s.id }}</td>
<td>{{ s.name }}{% if s.is_main %} <span class="muted">(you)</span>{% endif %}</td>
<td>{{ s.email or '-' }}</td>
<td class="{{ 'paid' if s.status == 'active' else ('failed' if s.status == 'suspended' else 'pending') }}">{{ s.status }}</td>
<td>{% if s.bot_username %}<a href="https://t.me/{{ s.bot_username }}" target="_blank" rel="noopener">@{{ s.bot_username }}</a><br>{% endif %}
<span class="muted">{{ s.bot_state or ('main' if s.is_main else 'not connected') }}{% if s.bot_error %}: {{ s.bot_error }}{% endif %}</span></td>
<td>{{ s.stats.users }}</td><td>{{ s.stats.orders }}</td><td>{{ '%.2f'|format(s.stats.revenue) }}</td><td>{{ s.stats.products }}</td>
<td>{% if not s.is_main %}
{% if s.status != 'active' %}<form method="post" action="{{ url_for('sellers_admin.seller_status', tid=s.id) }}"><input type="hidden" name="status" value="active"><button class="green sm">Approve / activate</button></form>{% endif %}
{% if s.status != 'suspended' %}<form method="post" action="{{ url_for('sellers_admin.seller_status', tid=s.id) }}"><input type="hidden" name="status" value="suspended"><button class="red sm">Suspend</button></form>{% endif %}
{% endif %}</td>
</tr>{% endfor %}
</table></div></div>
{% endblock %}"""


def register(app, render, render_str):
    from flask import Blueprint
    bp = Blueprint("sellers_admin", __name__)

    # -------------------------------------------------- public: seller sign-up
    @app.route("/register", methods=["GET", "POST"])
    def register_seller():
        mode = tenancy.signup_mode()
        if mode == "closed":
            flash("New seller sign-ups are closed.", "err")
            return redirect(url_for("login"))
        f = request.form if request.method == "POST" else {}
        if request.method == "POST":
            ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
            now = time.time()
            _reg_attempts[ip] = [x for x in _reg_attempts.get(ip, []) if now - x < 3600]
            if len(_reg_attempts[ip]) >= 8:
                flash("Too many sign-up attempts. Try again in an hour.", "err")
            else:
                _reg_attempts[ip].append(now)
                try:
                    tid = tenancy.create_seller(f.get("email"), f.get("password"), f.get("shop_name"))
                except tenancy.SellerError as e:
                    flash(str(e), "err")
                else:
                    session.clear()
                    session["ok"] = True
                    session["tid"] = tid
                    flash("Account created. Now connect your bot and payment details below.")
                    return redirect(url_for("connect"))
        return render("register.html", f=f, mode=mode)

    # -------------------------------------------------- seller: bot + payments
    @app.route("/connect")
    def connect():
        t = tenancy.cur()
        if t.is_main:
            flash("The main shop is configured with Railway variables.")
            return redirect(url_for("dashboard"))
        return render("connect.html", t=t, row=t.row, report=None)

    @app.route("/connect/save", methods=["POST"])
    def connect_save():
        t = tenancy.cur()
        if t.is_main:
            return redirect(url_for("dashboard"))
        try:
            tenancy.save_connection(t.id, request.form)
            flash("Saved. Your bot goes live within a few seconds once your account is active.")
        except tenancy.SellerError as e:
            flash(str(e), "err")
        return redirect(url_for("connect"))

    @app.route("/connect/test", methods=["POST"])
    def connect_test():
        t = tenancy.cur()
        if t.is_main:
            return redirect(url_for("dashboard"))
        report = tenancy.check_payments(t)
        return render("connect.html", t=t, row=t.row, report=report)

    @app.route("/account/password", methods=["POST"])
    def account_password():
        t = tenancy.cur()
        if t.is_main:
            return redirect(url_for("dashboard"))
        try:
            tenancy.change_password(t.id, request.form.get("old"), request.form.get("new"))
            flash("Password changed.")
        except tenancy.SellerError as e:
            flash(str(e), "err")
        return redirect(url_for("connect"))

    # -------------------------------------------------- owner: all sellers
    @bp.route("/sellers")
    def sellers():
        rows = tenancy.all_rows()
        for r in rows:
            t = tenancy.get_tenant(r["id"])
            r["stats"] = tenancy.seller_stats(t) if t else dict(users=0, orders=0, revenue=0.0, products=0)
        return render_str(SELLERS_PAGE, rows=rows, mode=tenancy.signup_mode())

    @bp.route("/sellers/<int:tid>/status", methods=["POST"])
    def seller_status(tid):
        try:
            tenancy.set_status(tid, request.form.get("status", ""))
            flash("Seller updated. Their bot starts or stops within seconds.")
        except tenancy.SellerError as e:
            flash(str(e), "err")
        return redirect(url_for("sellers_admin.sellers"))

    app.register_blueprint(bp)


TEMPLATES = {
    "register.html": REGISTER_PAGE,
    "connect.html": CONNECT_PAGE,
}
