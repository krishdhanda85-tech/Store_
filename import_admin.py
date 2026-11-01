"""Admin website page: Import products from a supplier in one go (search, multi-select, bulk markup)."""
import re

from flask import Blueprint, request, redirect, url_for, flash

bp = Blueprint("import_admin", __name__)

PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Import from supplier</h2>
<p class="muted">Tick the products, set your profit once, press Add. Each one is created, priced and linked to the supplier in one step.</p>

{% if not sups %}<div class="card">No supplier connected yet. <a href="{{ url_for('suppliers_admin.suppliers') }}">Add a supplier</a> first.</div>
{% else %}
{% if sups|length > 1 %}<div class="card"><label>Supplier</label>
<select onchange="location='{{ url_for('import_admin.home') }}?sid='+this.value">{% for s in sups %}<option value="{{ s.id }}" {{ 'selected' if s.id == cur.id }}>{{ s.name }} ({{ s['items']|length }})</option>{% endfor %}</select></div>{% endif %}

<form method="post" action="{{ url_for('import_admin.create') }}" id="imp">
<input type="hidden" name="sid" value="{{ cur.id }}">

<div class="card"><h3>1. Your profit</h3>
<label>Markup % on the supplier cost</label><input id="pct" type="number" step="1" min="0" value="40" oninput="recalc()">
<label>Plus a fixed amount ($)</label><input id="fix" type="number" step="0.05" min="0" value="0" oninput="recalc()">
<label>Round the price</label>
<select id="rnd" onchange="recalc()"><option value="five" selected>Up to the next 5 cents (1.23 → 1.25)</option><option value="x99">Ending .99 (1.23 → 1.99)</option><option value="none">No rounding</option></select>
<p class="muted" style="margin:6px 0 0">You can still edit any single price below. Those edits are kept when you change the markup; press "Reset prices" to overwrite them.</p>
<button type="button" class="grey sm" onclick="resetPrices()">Reset prices</button></div>

<div class="card"><h3>2. Apply to all selected <span class="hint">optional</span></h3>
<label>Category <span class="hint">same category = grouped under one button in the bot</span></label><input name="category" list="cats" placeholder="e.g. AI tools">
<datalist id="cats">{% for c in cats %}<option value="{{ c }}">{% endfor %}</datalist>
<label>Delivery time shown to the customer</label><input name="delivery" placeholder="e.g. Instant">
<label>Warranty</label><input name="warranty" placeholder="e.g. 30 days"></div>

<div class="card"><h3>3. Choose products</h3>
<input id="q" placeholder="Search..." oninput="filt()" autocomplete="off">
<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="instock" checked style="width:auto" onchange="filt()"> Only in stock</label>
<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="hideadded" checked style="width:auto" onchange="filt()"> Hide already added</label>
<div style="display:flex;gap:8px;margin:8px 0"><button type="button" class="grey sm" onclick="selShown(true)">Select all shown</button><button type="button" class="grey sm" onclick="selShown(false)">Clear</button></div>
<div id="list">
{% for a in cur['items'] %}{% set added = (cur.id ~ '|' ~ a.key) in linked %}
<div class="sub imp-row" data-name="{{ a.name|lower }}" data-stock="{{ a.stock }}" data-cost="{{ '%.4f' % a.cost }}" data-added="{{ 1 if added else 0 }}" style="padding:10px;margin:6px 0">
<label style="display:flex;gap:10px;align-items:flex-start;margin:0"><input type="checkbox" class="pick" name="pick" value="{{ loop.index0 }}" style="width:auto;margin-top:6px" onchange="count()" {{ 'disabled' if added }}>
<span style="flex:1"><input name="name_{{ loop.index0 }}" value="{{ a.name }}" maxlength="120" {{ 'disabled' if added }}>
<span class="muted" style="font-size:13px">cost ${{ '%.2f' % a.cost }} · stock {{ a.stock }}{% if added %} · <b class="paid">already added</b>{% endif %}</span></span></label>
<input type="hidden" name="key_{{ loop.index0 }}" value="{{ a.key }}">
<div style="display:flex;gap:8px;align-items:center;margin-top:6px;padding-left:30px"><span class="muted">Sell for $</span><input class="pr" name="price_{{ loop.index0 }}" type="number" step="0.01" min="0.01" style="max-width:110px" oninput="this.dataset.manual=1;prof(this.closest('.imp-row'))" {{ 'disabled' if added }}><span class="pf muted"></span></div>
</div>
{% else %}<p class="muted">This supplier returned no products.</p>{% endfor %}
</div></div>

<div style="position:sticky;bottom:0;padding:10px 0;background:var(--bg,#0f1020)">
<button type="submit" class="green" id="go" disabled>Add 0 products</button></div>
</form>

<script>
function rows(){return Array.prototype.slice.call(document.querySelectorAll('.imp-row'));}
function calc(cost){
  var p=cost*(1+(parseFloat(document.getElementById('pct').value)||0)/100)+(parseFloat(document.getElementById('fix').value)||0);
  var m=document.getElementById('rnd').value;
  if(m==='five')p=Math.ceil(p/0.05-1e-9)*0.05;
  else if(m==='x99'){var c=Math.ceil(p-1e-9);p=(c-0.01>=p-1e-9)?c-0.01:c+0.99;}
  return Math.max(p,cost+0.01);
}
function prof(r){
  var c=parseFloat(r.dataset.cost),i=r.querySelector('.pr'),p=parseFloat(i.value),o=r.querySelector('.pf');
  if(isNaN(p)){o.textContent='';return;}
  var d=p-c;o.textContent=(d>=0?'profit +$':'loss -$')+Math.abs(d).toFixed(2);o.className='pf '+(d>0?'paid':'failed');
}
function recalc(){rows().forEach(function(r){var i=r.querySelector('.pr');if(i.disabled||i.dataset.manual)return;i.value=calc(parseFloat(r.dataset.cost)).toFixed(2);prof(r);});}
function resetPrices(){rows().forEach(function(r){delete r.querySelector('.pr').dataset.manual;});recalc();}
function filt(){
  var q=document.getElementById('q').value.toLowerCase().trim(),st=document.getElementById('instock').checked,ha=document.getElementById('hideadded').checked;
  rows().forEach(function(r){
    var hide=(q&&r.dataset.name.indexOf(q)<0)||(st&&+r.dataset.stock<=0)||(ha&&r.dataset.added==='1');
    r.hidden=hide;
  });
}
function selShown(on){rows().forEach(function(r){var c=r.querySelector('.pick');if(!r.hidden&&!c.disabled)c.checked=on;});count();}
function count(){var n=document.querySelectorAll('.pick:checked').length,b=document.getElementById('go');b.disabled=!n;b.textContent='Add '+n+' product'+(n==1?'':'s');}
recalc();filt();count();
document.getElementById('imp').addEventListener('submit',function(e){
  var bad=0;rows().forEach(function(r){var c=r.querySelector('.pick');if(c.checked){var p=parseFloat(r.querySelector('.pr').value);if(!(p>0))bad++;else if(p<=parseFloat(r.dataset.cost))bad+=0.001;}});
  if(bad>=1){e.preventDefault();alert('Some selected products have no price.');return;}
  if(bad>0&&!confirm('Some selected products are priced at or below their cost (you would lose money). Add them anyway?'))e.preventDefault();
});
</script>
{% endif %}
{% endblock %}"""


@bp.route("/import")
def home():
    import web
    db = web.db
    sups, errs = db.catalog_options(max_age=60)
    for e in errs:
        flash(e, "err")
    cur = None
    if sups:
        sid = request.args.get("sid", type=int)
        cur = next((s for s in sups if s["id"] == sid), sups[0])
    linked = set()
    for d in db.sources_by_product().values():
        for o in d["offers"]:
            linked.add(f"{o['supplier_id']}|{o['provider_key']}")
    cats = sorted({(p["category"] or "").strip() for p in db.list_products() if (p["category"] or "").strip()})
    return web.render_str(PAGE, sups=sups, cur=cur, linked=linked, cats=cats)


@bp.route("/import/create", methods=["POST"])
def create():
    import web
    db = web.db
    f = request.form
    try:
        sid = int(f.get("sid", "0"))
    except ValueError:
        return redirect(url_for("import_admin.home"))
    category = (f.get("category") or "").strip()[:60] or None
    delivery = (f.get("delivery") or "").strip()[:60] or None
    warranty = (f.get("warranty") or "").strip()[:60] or None
    picks = [p for p in f.getlist("pick") if p.isdigit()][:300]
    ok, problems, below_cost = 0, [], 0
    for i in picks:
        name = re.sub(r"\s+", " ", f.get(f"name_{i}", "")).strip()[:120]
        key = f.get(f"key_{i}", "")
        try:
            price = round(float(f.get(f"price_{i}", "")), 2)
        except ValueError:
            price = 0
        if not name or not key or price <= 0:
            problems.append(f"{name or key or '#' + i}: name and price are needed")
            continue
        try:
            existed = any(p["name"] == name for p in db.list_products())
            pid = db.add_product(name, price, "auto")
            fields = {}
            if category:
                fields["category"] = category
            if delivery:
                fields["delivery"] = delivery
            if warranty:
                fields["warranty"] = warranty
            if not existed:
                fields["price"] = price
            if fields:
                db.update_product(pid, **fields)
            db.add_source(pid, sid, key)
            ok += 1
            src = db.sources_by_product().get(pid)
            if src and price <= min(o["cost"] for o in src["offers"]):
                below_cost += 1
        except Exception as e:
            problems.append(f"{name}: {str(e)[:120]}")
    if ok:
        flash(f"{ok} product{'s' if ok != 1 else ''} added and linked to the supplier."
              + (f" {below_cost} priced at or below cost, check the prices." if below_cost else ""))
    for p in problems[:8]:
        flash(p, "err")
    if len(problems) > 8:
        flash(f"... and {len(problems) - 8} more problems.", "err")
    return redirect(url_for("products") if ok else url_for("import_admin.home", sid=sid))


def register(app):
    app.register_blueprint(bp)
