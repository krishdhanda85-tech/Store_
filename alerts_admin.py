"""Admin website page: Stock alerts + Activity log.

* Lists products that are OUT of stock and products that are running LOW (auto-delivery products only,
  made-to-order products have no stock).
* Activity log: other parts of the shop write short lines here (e.g. Compare auto mode "added 3 products").
* Settings: low-stock limit, optional ONE daily Telegram summary, optional Telegram message when Auto mode
  adds products.

Used by:  web.py  -> register(app)
          bot.py  -> summary(db)           (daily stock summary job)
          compare_admin.py -> log_event(db, text)
"""
import json
import os
import time
from datetime import datetime, timezone

from flask import Blueprint, request, redirect, url_for, flash

bp = Blueprint("alerts_admin", __name__)

LOG_KEY = "activity_log"
LOG_MAX = 200


def _threshold(db):
    try:
        env = int(os.getenv("LOW_STOCK_THRESHOLD", "3") or 3)
    except ValueError:
        env = 3
    try:
        return max(1, int(float(db.get_setting("lowstock_threshold", env))))
    except (TypeError, ValueError):
        return env


def summary(db):
    """-> (out_of_stock, low_stock, made_to_order): three lists of dicts {id, name, category, stock}"""
    limit = _threshold(db)
    out, low, manual = [], [], []
    for p in db.list_products():
        row = {"id": p["id"], "name": p["name"] or "", "category": (p["category"] or "").strip(),
               "stock": int(p["stock"] or 0)}
        if (p["kind"] or "auto") == "manual":
            manual.append(row)
        elif row["stock"] <= 0:
            out.append(row)
        elif row["stock"] <= limit:
            low.append(row)
    key = lambda r: (r["category"].casefold(), r["name"].casefold())
    return sorted(out, key=key), sorted(low, key=lambda r: (r["stock"], r["name"].casefold())), sorted(manual, key=key)


def _load_log(db):
    try:
        v = json.loads(db.get_setting(LOG_KEY, "[]") or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def log_event(db, text):
    """Add one line to the Activity log (newest first, last 200 kept). Never raises."""
    try:
        rows = _load_log(db)
        rows.insert(0, {"t": int(time.time()), "m": str(text)[:300]})
        db.set_setting(LOG_KEY, json.dumps(rows[:LOG_MAX]))
    except Exception:
        pass


def _fmt(ts):
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Stock alerts</h2>
<p class="muted">Products that are out of stock or running low. Add stock from <a href="{{ url_for('products') }}">Products</a>.</p>

<div class="card"><h3>Out of stock ({{ out|length }})</h3>
{% if out %}<table><tr><th>Product</th><th>Category</th></tr>
{% for r in out %}<tr><td>{{ r.name }}</td><td class="muted">{{ r.category or '-' }}</td></tr>{% endfor %}</table>
{% else %}<p class="muted">Nothing is out of stock.</p>{% endif %}</div>

<div class="card"><h3>Running low ({{ low|length }})</h3>
<p class="muted" style="margin:0 0 8px">{{ limit }} or fewer left.</p>
{% if low %}<table><tr><th>Product</th><th>Category</th><th>Left</th></tr>
{% for r in low %}<tr><td>{{ r.name }}</td><td class="muted">{{ r.category or '-' }}</td><td>{{ r.stock }}</td></tr>{% endfor %}</table>
{% else %}<p class="muted">No product is running low.</p>{% endif %}</div>

<div class="card"><h3>Settings</h3>
<form method="post" action="{{ url_for('alerts_admin.save') }}">
<label>Low stock limit (products with this many or fewer are "low")</label>
<input name="limit" type="number" min="1" step="1" value="{{ limit }}">
<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" name="digest" value="1" style="width:auto" {{ 'checked' if digest }}> Send ONE Telegram summary per day (out of stock / low)</label>
<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" name="notify" value="1" style="width:auto" {{ 'checked' if notify }}> Message me on Telegram when Auto mode adds products</label>
<div style="margin-top:8px"><button type="submit">Save</button></div></form></div>

<div class="card"><h3>Activity</h3>
{% if events %}<table><tr><th>When</th><th>What</th></tr>
{% for e in events %}<tr><td class="muted" style="white-space:nowrap">{{ e.when }}</td><td>{{ e.m }}</td></tr>{% endfor %}</table>
<form method="post" action="{{ url_for('alerts_admin.clear') }}" style="margin-top:8px"><button type="submit" class="sm">Clear activity</button></form>
{% else %}<p class="muted">No activity yet. Auto mode and other automatic jobs write here.</p>{% endif %}</div>
{% endblock %}"""


@bp.route("/alerts")
def home():
    import web
    db = web.db
    out, low, _m = summary(db)
    events = [{"when": _fmt(e.get("t")), "m": e.get("m", "")} for e in _load_log(db)[:100] if isinstance(e, dict)]
    return web.render_str(PAGE, out=out, low=low, limit=_threshold(db), events=events,
                          digest=str(db.get_setting("lowstock_digest", "0")) == "1",
                          notify=str(db.get_setting("auto_import_notify", "0")) == "1")


@bp.route("/alerts/save", methods=["POST"])
def save():
    import web
    db = web.db
    f = request.form
    try:
        limit = max(1, int(float(f.get("limit", 3))))
    except ValueError:
        limit = 3
    db.set_setting("lowstock_threshold", str(limit))
    db.set_setting("lowstock_digest", "1" if f.get("digest") else "0")
    db.set_setting("auto_import_notify", "1" if f.get("notify") else "0")
    flash("Stock alert settings saved.")
    return redirect(url_for("alerts_admin.home"))


@bp.route("/alerts/clear", methods=["POST"])
def clear():
    import web
    web.db.set_setting(LOG_KEY, "[]")
    flash("Activity cleared.")
    return redirect(url_for("alerts_admin.home"))


def register(app):
    app.register_blueprint(bp)
