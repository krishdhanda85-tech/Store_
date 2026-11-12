"""Admin website page: Folders (the folder buttons in the bot's shop list).

Same thing as the /folders command in Telegram: add a name, and every product whose name or category
contains it goes inside that folder in the bot (and leaves the main grid). Live, no restart.

Used by:  web.py  -> register(app)
Stored in the shop settings under 'shop_folders' (JSON list), read by bot.py custom_folders().
"""
import json
import re

from flask import Blueprint, request, redirect, url_for, flash

bp = Blueprint("folders_admin", __name__)

KEY = "shop_folders"
# built-in brand folders of the bot (same words as FOLDERS in bot.py)
BUILTIN = [("Netflix", ("netflix",)), ("Spotify", ("spotify",)), ("Crunchyroll", ("crunchyroll", "crunchy")),
           ("Amazon Prime", ("amazonprime", "primevideo", "primevedio")), ("YouTube", ("youtube",)),
           ("Claude", ("claude",))]


def _sq(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").casefold())


def _load(db):
    try:
        v = json.loads(db.get_setting(KEY, "") or "[]")
        return [x for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _builtin_of(p):
    hay = _sq((p["name"] or "") + " " + (p["category"] or ""))
    for name, words in BUILTIN:
        if any(w in hay for w in words):
            return name
    return None


def _custom_of(p, folders):
    hay = _sq((p["name"] or "") + " " + (p["category"] or ""))
    for f in folders:
        k = _sq(f)
        if k and k in hay:
            return f
    return None


PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Folders</h2>
<p class="muted">A folder is one button in the bot's shop list. Add a name (for example <b>Gemini</b>) and every product whose
<b>name or category contains it</b> moves inside that folder automatically. Changes are live in the bot, no restart.</p>

<div class="card"><h3>Add a folder</h3>
<form method="post" action="{{ url_for('folders_admin.add') }}" style="display:flex;gap:8px;flex-wrap:wrap">
<input name="name" maxlength="30" placeholder="e.g. Gemini" required style="flex:1;min-width:180px">
<button type="submit">Add folder</button></form></div>

<div class="card"><h3>Your folders ({{ rows|length }})</h3>
{% if rows %}
{% for r in rows %}
<details style="margin:8px 0"><summary style="cursor:pointer"><b>📂 {{ r.name }}</b> <span class="muted">— {{ r.prods|length }} products</span></summary>
<div style="padding:6px 0 0 12px">
{% if r.prods %}<ul style="margin:4px 0">{% for n in r.prods %}<li>{{ n }}</li>{% endfor %}</ul>
{% else %}<p class="muted">No product matches this name yet.</p>{% endif %}
<form method="post" action="{{ url_for('folders_admin.delete') }}" onsubmit="return confirm('Remove this folder? Products stay, they just return to the main list.')">
<input type="hidden" name="name" value="{{ r.name }}"><button type="submit" class="sm">Remove folder</button></form>
</div></details>
{% endfor %}
{% else %}<p class="muted">No folders yet. Add one above.</p>{% endif %}</div>

<div class="card"><h3>Built-in folders</h3>
<p class="muted" style="margin:0">{{ builtin|join(', ') }} — these always exist when a matching product is in the shop.</p></div>
{% endblock %}"""


@bp.route("/folders")
def home():
    import web
    db = web.db
    folders = _load(db)
    products = db.list_products()
    rows = []
    for f in folders:
        items = sorted((p["name"] or "") for p in products if _builtin_of(p) is None and _custom_of(p, folders) == f)
        rows.append({"name": f, "prods": items})
    return web.render_str(PAGE, rows=rows, builtin=[b[0] for b in BUILTIN])


@bp.route("/folders/add", methods=["POST"])
def add():
    import web
    db = web.db
    name = (request.form.get("name") or "").strip()[:30]
    cur = _load(db)
    taken = [c.casefold() for c in cur] + [b[0].casefold() for b in BUILTIN]
    if not _sq(name):
        flash("Type a folder name.")
    elif name.casefold() in taken:
        flash("That folder already exists.")
    else:
        cur.append(name)
        db.set_setting(KEY, json.dumps(cur))
        flash(f"Folder added: {name}")
    return redirect(url_for("folders_admin.home"))


@bp.route("/folders/delete", methods=["POST"])
def delete():
    import web
    db = web.db
    name = (request.form.get("name") or "").strip()
    cur = _load(db)
    left = [c for c in cur if c.casefold() != name.casefold()]
    if len(left) != len(cur):
        db.set_setting(KEY, json.dumps(left))
        flash(f"Folder removed: {name}")
    return redirect(url_for("folders_admin.home"))


def register(app):
    app.register_blueprint(bp)
