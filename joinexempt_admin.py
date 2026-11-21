"""Admin website page: Join-check exemptions ("Free pass" users).

Users listed here can use the bot WITHOUT joining the required channel/group.
Everyone else still has to join, exactly as before.

Used by:  web.py  -> register(app)
          bot.py  -> is_exempt(db, user_id), add/remove/load (also /exempt and /unexempt commands)

Stored in the shop's `settings` table under key "join_exempt" as JSON {"<telegram_id>": "<note>"}.
Works per shop (each seller has their own list) because it uses the same db as the rest of the site.
"""
import json
import re

from flask import Blueprint, request, redirect, url_for, flash

bp = Blueprint("joinexempt_admin", __name__)
KEY = "join_exempt"


def load(db):
    try:
        v = json.loads(db.get_setting(KEY, "{}") or "{}")
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _save(db, data):
    db.set_setting(KEY, json.dumps(data))


def is_exempt(db, user_id):
    try:
        return str(int(user_id)) in load(db)
    except (TypeError, ValueError):
        return False


def add(db, user_id, note=""):
    data = load(db)
    data[str(int(user_id))] = (note or "").strip()[:80]
    _save(db, data)


def remove(db, user_id):
    data = load(db)
    existed = data.pop(str(int(user_id)), None) is not None
    _save(db, data)
    return existed


PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Join-check exemptions</h2>
<p class="muted">Users added here can use the bot <b>without joining</b> the required channel/group. Everyone else must still join.
Find a buyer's Telegram ID in <a href="{{ url_for('users') }}">Users</a>.</p>

<div class="card"><h3>Add a user</h3>
<form method="post" action="{{ url_for('joinexempt_admin.add_user') }}">
<label>Telegram user ID(s) (numbers; separate several with space, comma or new line)</label>
<textarea name="ids" rows="2" placeholder="123456789"></textarea>
<label>Note (optional, e.g. buyer name)</label>
<input name="note" maxlength="80">
<div style="margin-top:8px"><button type="submit">Add</button></div></form></div>

<div class="card"><h3>Exempt users ({{ rows|length }})</h3>
{% if rows %}<table><tr><th>Telegram ID</th><th>Note</th><th></th></tr>
{% for r in rows %}<tr><td><code>{{ r.id }}</code></td><td class="muted">{{ r.note or '-' }}</td>
<td><form method="post" action="{{ url_for('joinexempt_admin.remove_user') }}" style="margin:0">
<input type="hidden" name="id" value="{{ r.id }}"><button type="submit" class="sm">Remove</button></form></td></tr>{% endfor %}</table>
{% else %}<p class="muted">Nobody is exempt yet.</p>{% endif %}</div>
{% endblock %}"""


@bp.route("/join-exempt")
def home():
    import web
    data = load(web.db)
    rows = [{"id": k, "note": v} for k, v in sorted(data.items(), key=lambda kv: int(kv[0]))]
    return web.render_str(PAGE, rows=rows)


@bp.route("/join-exempt/add", methods=["POST"])
def add_user():
    import web
    ids = re.findall(r"\d{5,15}", request.form.get("ids", ""))
    if not ids:
        flash("Enter at least one valid Telegram user ID (numbers only).")
        return redirect(url_for("joinexempt_admin.home"))
    for i in ids:
        add(web.db, int(i), request.form.get("note", ""))
    flash(f"{len(ids)} user(s) can now use the bot without joining.")
    return redirect(url_for("joinexempt_admin.home"))


@bp.route("/join-exempt/remove", methods=["POST"])
def remove_user():
    import web
    try:
        remove(web.db, int(request.form.get("id", "")))
        flash("Removed. This user must join the channel again.")
    except ValueError:
        flash("Invalid ID.")
    return redirect(url_for("joinexempt_admin.home"))


def register(app):
    app.register_blueprint(bp)
