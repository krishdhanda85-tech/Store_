import json

import provider
from flask import Blueprint, request, redirect, url_for, flash

admin_suppliers = Blueprint("suppliers_admin", __name__)

PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Suppliers API</h2>
<p class="muted">Add as many supplier APIs as you want. On each product you then choose which supplier it is bought from
(or let the bot pick the cheapest one that has stock). API keys are stored encrypted and never shown again.</p>

<div class="card"><h3>Add supplier</h3>
<form method="post"><input type="hidden" name="action" value="add">
<label>Name</label><input name="name" required maxlength="60" placeholder="e.g. Supplier A">
<label>API format</label>
<select name="fmt" onchange="document.getElementById('cfgbox').style.display=this.value==='generic'?'block':'none'">
{% for k, v in formats.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}
</select>
<label>Base URL <span class="hint">from the supplier's API documentation</span></label>
<input name="base_url" required placeholder="https://supplier.example.com/api/">
<label>API key</label><input name="key" type="password" required autocomplete="off">
<div id="cfgbox" style="display:none">
<label>Field mapping (JSON) <span class="hint">only for "Generic JSON API". Change only what differs from your supplier's docs</span></label>
<textarea name="cfg" rows="14" spellcheck="false" style="font-family:monospace;font-size:12px">{{ default_cfg }}</textarea>
</div>
<button type="submit">Add supplier</button></form></div>

<div class="card"><h3>Your suppliers</h3>
{% for s in rows %}
<details class="sub" style="margin-bottom:10px"><summary><b>{{ s.name }}</b>
 <span class="muted">· {{ formats.get(s.fmt, s.fmt).split(' (')[0] }} · {{ s.key_hint or 'no key' }} · {{ counts.get(s.id, 0) }} linked product(s) ·</span>
 <span class="{{ 'paid' if s.enabled else 'failed' }}">{{ 'Active' if s.enabled else 'Disabled' }}</span></summary><div>
{% if s.from_env %}<p class="muted">Comes from the server variables CUPPON_API_KEY / PROVIDER_API_BASE.</p>{% else %}<p class="muted">{{ s.base_url }}</p>{% endif %}
<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
<form method="post"><input type="hidden" name="action" value="test"><input type="hidden" name="sid" value="{{ s.id }}"><button type="submit" class="green sm">Test connection</button></form>
<form method="post"><input type="hidden" name="action" value="toggle"><input type="hidden" name="sid" value="{{ s.id }}"><button type="submit" class="grey sm">{{ 'Disable' if s.enabled else 'Enable' }}</button></form>
{% if not s.from_env %}<form method="post" onsubmit="return confirm('Delete this supplier? Products linked to it lose this source.')"><input type="hidden" name="action" value="delete"><input type="hidden" name="sid" value="{{ s.id }}"><button type="submit" class="red sm">Delete</button></form>{% endif %}
</div>
<form method="post"><input type="hidden" name="action" value="edit"><input type="hidden" name="sid" value="{{ s.id }}">
<label>Name</label><input name="name" value="{{ s.name }}" maxlength="60">
{% if not s.from_env %}
<label>Base URL</label><input name="base_url" value="{{ s.base_url }}">
<label>New API key <span class="hint">leave empty to keep the current one</span></label><input name="key" type="password" autocomplete="off">
{% endif %}
{% if s.fmt == 'generic' %}
<label>Field mapping (JSON)</label>
<textarea name="cfg" rows="12" spellcheck="false" style="font-family:monospace;font-size:12px">{{ s.cfg_text }}</textarea>
{% endif %}
<button type="submit">Save</button></form>
</div></details>
{% else %}<p class="muted">No suppliers yet. Add the first one above.</p>{% endfor %}
</div>

<div class="card"><h3>How it works</h3>
<p>1. Add your suppliers here. &nbsp; 2. Open <a href="{{ url_for('products') }}">Products</a> and, under a product, add the supplier product(s) that sell it.
&nbsp; 3. Choose <b>I choose the supplier</b> or <b>Cheapest</b>. Your selling price stays the one you set.</p>
<p class="muted" style="margin:0">Example: Gemini costs 0.37 at supplier A and 0.50 at supplier B. Link both, choose A first, keep the fallback on.</p></div>
{% endblock %}"""


def register(app, db):
    @admin_suppliers.route("/suppliers", methods=["GET", "POST"])
    def suppliers():
        if request.method == "POST":
            act = request.form.get("action", "")
            f = request.form
            try:
                sid = int(f.get("sid", "0") or 0)
                if act == "add":
                    db.add_supplier(f.get("name"), f.get("fmt", "cupponhub"), f.get("base_url"),
                                    f.get("key"), f.get("cfg", ""))
                    flash("Supplier added. Press 'Test connection' to check it.")
                elif act == "edit":
                    db.update_supplier(sid, name=f.get("name"), base_url=f.get("base_url"),
                                       key=f.get("key"), cfg_text=f.get("cfg"))
                    flash("Saved.")
                elif act == "toggle":
                    s = db.get_supplier(sid)
                    if s:
                        db.set_supplier_enabled(sid, not s["enabled"])
                        flash("Supplier enabled." if not s["enabled"] else "Supplier disabled.")
                elif act == "delete":
                    db.delete_supplier(sid)
                    flash("Supplier deleted.")
                elif act == "test":
                    flash(db.test_supplier(sid))
            except Exception as e:
                flash(str(e)[:250], "err")
            return redirect(url_for("suppliers_admin.suppliers"))
        rows = db.list_suppliers()
        for s in rows:
            s["cfg_text"] = json.dumps(provider._gcfg(s), indent=2)
        import web
        return web.render_str(PAGE, rows=rows, counts=db.supplier_link_counts(), formats=provider.FORMATS,
                              default_cfg=json.dumps(provider.GENERIC_DEFAULT, indent=2))

    app.register_blueprint(admin_suppliers)
