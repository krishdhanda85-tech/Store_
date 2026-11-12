"""Admin website page: Folders (the folder buttons in the bot's shop list).

Make a folder with ANY name, then tick the products you want inside it. Those products leave the
main grid and show only inside that folder in the bot. Live, no restart.

Used by:  web.py  -> register(app)
Stored in the shop settings:
  'shop_folders'       JSON list of folder names (read by bot.py custom_folders())
  'shop_folder_items'  JSON dict  {folder name: [product ids]}  (read by bot.py)
Old folders made with /folders (name only, no product list) keep working: every product whose
name/category contains the folder name is inside until you save that folder with ticks here.
"""
import json
import re

from flask import Blueprint, request, redirect, url_for, flash

bp = Blueprint("folders_admin", __name__)

KEY = "shop_folders"
ITEMS = "shop_folder_items"
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


def _load_items(db):
    try:
        v = json.loads(db.get_setting(ITEMS, "") or "{}")
    except (ValueError, TypeError):
        return {}
    out = {}
    if isinstance(v, dict):
        for k, ids in v.items():
            if isinstance(ids, list):
                out[k] = [int(i) for i in ids if isinstance(i, (int, str)) and str(i).strip().lstrip("-").isdigit()]
    return out


def _builtin_of(p):
    hay = _sq((p["name"] or "") + " " + (p["category"] or ""))
    for name, words in BUILTIN:
        if any(w in hay for w in words):
            return name
    return None


def _legacy_ids(p_list, name):
    k = _sq(name)
    return [p["id"] for p in p_list if k and k in _sq((p["name"] or "") + " " + (p["category"] or ""))
            and _builtin_of(p) is None]


PAGE = r"""{% extends 'base.html' %}{% block body %}
{% macro picker(products, selected, owner, me) %}
<input type="search" placeholder="Search products..." oninput="ff(this)" style="margin:6px 0">
<div style="max-height:340px;overflow:auto;border:1px solid var(--line);border-radius:12px;padding:4px 12px">
{% for p in products %}
<label class="fp" data-n="{{ p.name|lower }}" style="display:flex;gap:10px;align-items:center;padding:8px 0;margin:0;font-weight:500;cursor:pointer">
<input type="checkbox" name="ids" value="{{ p.id }}" {% if p.id in selected %}checked{% endif %} style="width:auto;margin:0;flex:none">
<span>{{ p.name }}{% if owner.get(p.id) and owner.get(p.id) != me %} <span class="muted">(now in {{ owner[p.id] }})</span>{% endif %}</span></label>
{% endfor %}</div>
{% endmacro %}
<h2>Folders</h2>
<p class="muted">A folder is one button in the bot's shop list. Give it any name and tick the products that go inside.
Ticked products leave the main list and show only inside the folder. Changes are live in the bot, no restart.</p>

<div class="card"><h3>New folder</h3>
<form method="post" action="{{ url_for('folders_admin.save') }}">
<input name="name" maxlength="30" placeholder="Folder name, e.g. Gemini" required>
{{ picker(products, [], owner, '') }}
<button type="submit" style="margin-top:12px">Create folder</button></form></div>

<div class="card"><h3>Your folders ({{ rows|length }})</h3>
{% if rows %}
{% for r in rows %}
<details style="margin:10px 0"><summary style="cursor:pointer"><b>📂 {{ r.name }}</b> <span class="muted">- {{ r.selected|length }} products</span></summary>
<form method="post" action="{{ url_for('folders_admin.save') }}" style="padding-top:8px">
<input type="hidden" name="original" value="{{ r.name }}">
<label>Folder name</label><input name="name" maxlength="30" value="{{ r.name }}" required>
{{ picker(products, r.selected, owner, r.name) }}
<button type="submit" style="margin-top:12px">Save folder</button></form>
<form method="post" action="{{ url_for('folders_admin.delete') }}" style="margin-top:8px" onsubmit="return confirm('Remove this folder? Products stay, they just return to the main list.')">
<input type="hidden" name="name" value="{{ r.name }}"><button type="submit" class="sm">Remove folder</button></form>
</details>
{% endfor %}
{% else %}<p class="muted">No folders yet. Create one above.</p>{% endif %}</div>

<div class="card"><h3>Built-in folders</h3>
<p class="muted" style="margin:0">{{ builtin|join(', ') }} - these always exist when a matching product is in the shop.</p></div>
<script>function ff(i){var q=i.value.toLowerCase();i.parentNode.querySelectorAll('.fp').forEach(function(l){l.style.display=l.dataset.n.indexOf(q)>-1?'':'none'})}</script>
{% endblock %}"""


@bp.route("/folders")
def home():
    import web
    db = web.db
    names = _load(db)
    items = _load_items(db)
    plist = db.list_products()
    products = sorted(({"id": p["id"], "name": p["name"] or "", "category": p["category"] or ""} for p in plist),
                      key=lambda x: x["name"].lower())
    rows, owner = [], {}
    for f in names:
        sel = items[f] if f in items else _legacy_ids(plist, f)
        rows.append({"name": f, "selected": sel})
        for i in sel:
            owner.setdefault(i, f)
    return web.render_str(PAGE, rows=rows, products=products, owner=owner, builtin=[b[0] for b in BUILTIN])


@bp.route("/folders/save", methods=["POST"])
def save():
    import web
    db = web.db
    name = re.sub(r"\s+", " ", (request.form.get("name") or "")).replace(" / ", " ").strip()[:30]
    original = (request.form.get("original") or "").strip()
    names = _load(db)
    items = _load_items(db)
    valid = {p["id"] for p in db.list_products()}
    ids = []
    for v in request.form.getlist("ids"):
        if v.isdigit() and int(v) in valid and int(v) not in ids:
            ids.append(int(v))
    if not _sq(name):
        flash("Type a folder name.")
        return redirect(url_for("folders_admin.home"))
    taken = [c.casefold() for c in names if c != original] + [b[0].casefold() for b in BUILTIN]
    if name.casefold() in taken:
        flash("A folder with that name already exists.")
        return redirect(url_for("folders_admin.home"))
    if original:
        if original not in names:
            flash("Folder not found.")
            return redirect(url_for("folders_admin.home"))
        names[names.index(original)] = name
        items.pop(original, None)
    else:
        names.append(name)
    for k in list(items):                       # a product lives in one folder only
        items[k] = [i for i in items[k] if i not in ids]
    items[name] = ids
    items = {k: v for k, v in items.items() if k in names}
    db.set_setting(KEY, json.dumps(names))
    db.set_setting(ITEMS, json.dumps(items))
    flash(f"Folder saved: {name} ({len(ids)} products)")
    return redirect(url_for("folders_admin.home"))


@bp.route("/folders/delete", methods=["POST"])
def delete():
    import web
    db = web.db
    name = (request.form.get("name") or "").strip()
    names = _load(db)
    left = [c for c in names if c != name]
    if len(left) != len(names):
        items = _load_items(db)
        items.pop(name, None)
        db.set_setting(KEY, json.dumps(left))
        db.set_setting(ITEMS, json.dumps(items))
        flash(f"Folder removed: {name}")
    return redirect(url_for("folders_admin.home"))


def register(app):
    app.register_blueprint(bp)
