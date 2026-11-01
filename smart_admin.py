"""Admin website page: Smart Promo (personal messages built from each customer's purchase history)."""
import json
import os
import time

from flask import Blueprint, request, redirect, url_for, flash

import smart_promo as sp

bp = Blueprint("smart_admin", __name__)

REASON_INFO = [
    ("renew", "⏰ Renew", "Their usual repeat time is here (their own cycle, or the default days)."),
    ("pricedrop", "📉 Price drop", "A product they bought before is discounted now."),
    ("crosssell", "🤝 Cross-sell", "Other buyers of their product also bought this."),
    ("loyal", "🏆 Loyal", "Customers with several paid orders: a thank-you plus their favourite."),
    ("winback", "💌 Win-back", "Gone for a long time: 'we missed you' (+ an optional personal code)."),
    ("newcomer", "👋 Newcomer", "Joined but never bought: the Manager's pick."),
]

PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Smart Promo</h2>
<p class="muted">The bot reads each customer's paid orders and sends ONE personal message when there is a real reason,
at the hour they usually buy. Every number in a message comes from your database.</p>

<div class="card"><h3>Status</h3>
<form method="post"><input type="hidden" name="action" value="master">
<label><input type="checkbox" name="on" value="1" {{ 'checked' if master_on }} style="width:auto;margin-right:6px"> Smart messages are ON</label>
<label>Who can get messages <span class="hint">the audience is filtered further by the rules below</span></label>
<select name="audience">{% for k, t in audiences %}<option value="{{ k }}" {{ 'selected' if k == audience }}>{{ t }}</option>{% endfor %}</select>
<button type="submit">Save</button></form>
{% if not key %}<p class="failed" style="margin:8px 0 0">GEMINI_API_KEY is not set: messages still go out, with a fixed (safe) headline instead of an AI one.</p>{% endif %}
</div>

<div class="card"><h3>Results <span class="hint">customers who bought within {{ c.attr_days }} days after a message</span></h3>
<div class="scroll"><table><tr><th>Reason</th><th>Sent</th><th>Bought</th><th>Rate</th><th>Orders</th><th>Revenue</th></tr>
{% for r in rows %}{% if r.sent %}<tr><td>{{ labels.get(r.reason, r.reason) }}</td><td>{{ r.sent }}</td><td>{{ r.buyers }}</td>
<td>{{ r.rate }}%</td><td>{{ r.orders }}</td><td>${{ '%.2f'|format(r.revenue) }}</td></tr>{% endif %}
{% else %}<tr><td colspan="6">No messages yet.</td></tr>{% endfor %}
{% if total.sent %}<tr><th>Total</th><th>{{ total.sent }}</th><th>{{ total.buyers }}</th><th>{{ total.rate }}%</th><th>{{ total.orders }}</th><th>${{ '%.2f'|format(total.revenue) }}</th></tr>{% endif %}
</table></div>
<p class="muted" style="margin:8px 0 0">"Bought" = a purchase after the message. Some of those customers may have bought anyway, so compare reasons with each other rather than treating it as proof.</p>
</div>

<div class="card"><h3>Test it on yourself</h3>
<p class="muted">Sends one preview of a reason to ONE Telegram user (they must have started the bot). It uses a real product and your real settings, nothing is logged, and no cooldown is used. Find a Telegram ID by sending /start to @userinfobot.</p>
<form method="post"><input type="hidden" name="action" value="test">
<label>Telegram user ID</label><input name="uid" inputmode="numeric" placeholder="123456789" required>
<label>Reason</label><select name="reason">{% for k, t, d in info %}<option value="{{ k }}">{{ t }}</option>{% endfor %}</select>
<button type="submit" class="green">Send me a test</button></form>
{% if test_res %}<p class="muted" style="margin:8px 0 0">{{ test_res }}</p>{% endif %}
</div>

<form method="post"><input type="hidden" name="action" value="save">
<div class="card"><h3>Which messages</h3>
{% for k, t, d in info %}<label><input type="checkbox" name="on_{{ k }}" value="1" {{ 'checked' if c['on_' + k] }} style="width:auto;margin-right:6px"> {{ t }} <span class="hint">{{ d }}</span></label>{% endfor %}
</div>

<div class="card"><h3>When someone is "due"</h3>
<label>Default renew time (days) <span class="hint">used until we learn a customer's own repeat gap</span></label><input name="renew_days" type="number" value="{{ c.renew_days }}">
<label>Win-back after (days without a purchase)</label><input name="winback_days" type="number" value="{{ c.winback_days }}">
<label>Loyal after (paid orders)</label><input name="loyal_orders" type="number" value="{{ c.loyal_orders }}">
<label>Cross-sell needs at least (customers who bought both)</label><input name="min_copurchase" type="number" value="{{ c.min_copurchase }}">
</div>

<div class="card"><h3>No spam</h3>
<label>Max 1 message per customer every (days)</label><input name="cooldown_days" type="number" value="{{ c.cooldown_days }}">
<label>Same product again to the same customer after (days)</label><input name="product_cooldown_days" type="number" value="{{ c.product_cooldown_days }}">
<label>Max messages per hourly run</label><input name="max_per_run" type="number" value="{{ c.max_per_run }}">
</div>

<div class="card"><h3>Timing</h3>
<label><input type="checkbox" name="smart_time" value="1" {{ 'checked' if c.smart_time }} style="width:auto;margin-right:6px"> Send near each customer's usual buying hour</label>
<label>+/- hours around it</label><input name="window" type="number" value="{{ c.window }}">
<label>Your timezone: minutes from UTC <span class="hint">India = 330, UAE = 240, UK = 0 (winter), US East = -300</span></label><input name="tz_offset_min" type="number" value="{{ c.tz_offset_min }}">
<label>Quiet hours: no messages from (hour 0-23)</label><input name="quiet_start" type="number" value="{{ c.quiet_start }}">
<label>... until (hour 0-23)</label><input name="quiet_end" type="number" value="{{ c.quiet_end }}">
<p class="muted">Server time now: {{ now_local }} (your timezone)</p>
</div>

<div class="card"><h3>Message extras</h3>
<label><input type="checkbox" name="names" value="1" {{ 'checked' if c.names }} style="width:auto;margin-right:6px"> Greet by first name <span class="hint">(known after the customer's next visit to the bot)</span></label>
<label><input type="checkbox" name="language" value="1" {{ 'checked' if c.language }} style="width:auto;margin-right:6px"> Write in the customer's language <span class="hint">(English, 中文, Español)</span></label>
<label><input type="checkbox" name="urgency" value="1" {{ 'checked' if c.urgency }} style="width:auto;margin-right:6px"> Real urgency <span class="hint">flash-deal countdown and "only N left", shown only when true</span></label>
<label><input type="checkbox" name="reviews" value="1" {{ 'checked' if c.reviews }} style="width:auto;margin-right:6px"> Show the product rating <span class="hint">only with 3+ real reviews</span></label>
</div>

<div class="card"><h3>Win-back discount</h3>
<label>Personal one-time code, % off <span class="hint">0 = no code. This costs you margin, so start small (e.g. 10)</span></label><input name="winback_pct" type="number" value="{{ c.winback_pct }}">
<label>Code valid for (days)</label><input name="winback_code_days" type="number" value="{{ c.winback_code_days }}">
<label>Count a purchase as a result if within (days)</label><input name="attr_days" type="number" value="{{ c.attr_days }}">
<button type="submit">Save settings</button>
</div>
</form>
{% endblock %}"""

AUDIENCES = [("all", "Everyone who started the bot"), ("buyers", "Buyers only"), ("nobuy", "Never bought"),
             ("balance", "Customers with wallet balance"), ("inactive", "Buyers inactive for 14+ days")]


@bp.route("/smart-promo", methods=["GET", "POST"])
def home():
    import web
    db = web.db
    if request.method == "POST":
        f = request.form
        act = f.get("action")
        if act == "master":
            db.set_setting("promo_dm_on", 1 if f.get("on") else 0)
            if f.get("audience") in dict(AUDIENCES):
                db.set_setting("promo_dm_audience", f["audience"])
            flash("Saved. " + ("Smart messages are ON." if f.get("on") else "Smart messages are OFF."))
        elif act == "save":
            sp.save_cfg(db, f)
            flash("Settings saved. They apply from the next hourly run.")
        elif act == "test":
            try:
                uid = int(str(f.get("uid", "")).strip())
            except ValueError:
                flash("Telegram user ID must be a number.", "err")
                return redirect(url_for("smart_admin.home"))
            reason = f.get("reason")
            if reason not in {r[0] for r in REASON_INFO}:
                return redirect(url_for("smart_admin.home"))
            db.set_setting("sp_test_res", "")
            db.set_setting("sp_test_req", json.dumps({"uid": uid, "reason": reason, "ts": time.time()}))
            flash("Test requested. It reaches that Telegram user in a few seconds; the result shows below after you reload.")
        return redirect(url_for("smart_admin.home"))
    c = sp.get_cfg(db)
    rows, total = sp.results(db)
    v = str(db.get_setting("promo_dm_on", "") or "")
    master_on = (v == "1") if v != "" else os.getenv("PROMO_DM", "0") == "1"
    from datetime import datetime, timedelta, timezone
    now_local = (datetime.now(timezone.utc) + timedelta(minutes=c["tz_offset_min"])).strftime("%H:%M")
    return web.render_str(PAGE, c=c, rows=rows, total=total, info=REASON_INFO,
                          labels={k: t for k, t, _ in REASON_INFO}, master_on=master_on,
                          audience=db.get_setting("promo_dm_audience", os.getenv("PROMO_DM_AUDIENCE", "all")),
                          audiences=AUDIENCES, key=bool(os.getenv("GEMINI_API_KEY", "").strip()),
                          test_res=str(db.get_setting("sp_test_res", "") or ""), now_local=now_local)


def register(app):
    app.register_blueprint(bp)
