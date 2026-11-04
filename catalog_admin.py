"""Admin website page: Supplier catalog.

ONE page that lists every product of every connected supplier (e.g. all 687), shows which supplier sells it,
and lets you tick only the products you want and add them to your shop in one go.

* Supplier name, supplier cost and supplier stock are shown on every row.
* Filter by supplier, search by name, only in stock, hide the ones you already added.
* Card layout: tap a card to select it. You choose the selling price (markup % pre-fills it, every price can be edited by hand).
* A product that is already in your shop (same name) just gets the extra supplier linked as a source.
Replaces the old "Import products" and "Compare & import" pages.
"""
import re

import provider
from flask import Blueprint, request, redirect, url_for, flash

bp = Blueprint("catalog_admin", __name__)

PAGE = r"""{% extends 'base.html' %}{% block body %}
<style>
.cg-top{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.cg-top h2{margin:0}
.cg-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:12px}
.cg-st{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:10px 12px;box-shadow:var(--shadow)}
.cg-st b{display:block;font-size:22px;line-height:1.2;font-weight:800}
.cg-st span{font-size:12px;color:var(--muted)}
.cg-st.hot{background:var(--accent);border-color:var(--accent)}
.cg-st.hot b,.cg-st.hot span{color:var(--surface)}
.cg-search{position:relative}
.cg-search input{margin:0 0 8px;padding-left:40px;border-radius:14px}
.cg-search i{position:absolute;left:14px;top:13px;width:14px;height:14px;border:2px solid var(--muted);border-radius:50%;pointer-events:none}
.cg-search i:after{content:'';position:absolute;width:2px;height:7px;background:var(--muted);right:-4px;bottom:-6px;transform:rotate(-45deg)}
.cg-chips{display:flex;gap:6px;overflow-x:auto;padding:2px 0 8px;-webkit-overflow-scrolling:touch;scrollbar-width:none}
.cg-chips::-webkit-scrollbar{display:none}
.cg-chip{flex:none;width:auto;min-height:0!important;margin:0;padding:7px 14px;border-radius:999px;border:1px solid var(--line2);background:var(--surface);color:var(--muted);font-size:13px;font-weight:600;cursor:pointer}
.cg-chip.on{background:var(--ink);color:var(--surface);border-color:var(--ink)}
.cg-row2{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.cg-row2 select{flex:1;min-width:130px;margin:0;border-radius:12px}
.cg-set{background:var(--accent-soft);border:1px solid var(--line);border-radius:16px;padding:12px 14px;margin:2px 0 12px}
.cg-set summary{cursor:pointer;display:flex;justify-content:space-between;align-items:center;list-style:none;font-size:14px;font-weight:600}
.cg-set summary::-webkit-details-marker{display:none}
.cg-set summary b{background:var(--accent);color:var(--surface);font-size:12px;padding:4px 12px;border-radius:999px}
.cg-setbody{margin-top:12px}
.cg-2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.cg-setbody label{font-size:12.5px;color:var(--muted)}
.cg-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:2px 0 10px}
.cg-tools .muted{margin-left:auto;font-size:12.5px}
.cg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(185px,1fr));gap:10px}
@media(max-width:640px){.cg-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
.cc{position:relative;display:flex;flex-direction:column;background:var(--surface);border:1.5px solid var(--line);border-radius:18px;padding:12px;cursor:pointer;box-shadow:var(--shadow);transition:transform .12s,border-color .12s}
.cc:active{transform:scale(.98)}
.cc[hidden]{display:none}
.cc.on{border-color:var(--accent);background:var(--accent-soft)}
.cc.added{opacity:.5;cursor:default}
.cc .tick{position:absolute;top:10px;right:10px;width:24px;height:24px;border-radius:50%;border:1.5px solid var(--line2);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:800;color:transparent;background:var(--surface)}
.cc.on .tick{background:var(--accent);border-color:var(--accent);color:var(--surface)}
.cc .logo{width:42px;height:42px;border-radius:13px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:18px;margin-bottom:10px}
.cc input.cn{margin:0 0 6px;padding:4px 6px;min-height:0;font-size:14px;font-weight:700;line-height:1.25;border:1px solid transparent;background:transparent;border-radius:8px;text-overflow:ellipsis}
.cc input.cn:focus{border-color:var(--accent);background:var(--field)}
.cc .meta{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.cc .sp{font-size:11.5px;padding:2px 9px;border-radius:999px;background:var(--grey);color:var(--greyink);font-weight:700}
.cc .st{font-size:11.5px;font-weight:700}
.cc .st:before{content:'';display:inline-block;width:7px;height:7px;border-radius:50%;background:currentColor;margin-right:5px;vertical-align:1px}
.cc .box{margin-top:auto;background:var(--bg);border-radius:12px;padding:8px 10px}
.cc.on .box{background:var(--surface)}
.cc .cost{font-size:11.5px;color:var(--muted);display:flex;justify-content:space-between}
.cc .sell{display:flex;align-items:center;font-size:20px;font-weight:800;margin-top:2px}
.cc .sell input{width:100%;margin:0 0 0 2px;padding:2px 4px;min-height:0;font-size:20px;font-weight:800;border:0;background:transparent;box-shadow:none;text-align:left}
.cc .sell input:focus{box-shadow:none;background:transparent;border:0}
.cc .pf{font-size:12px;font-weight:700;min-height:16px}
.cg-bar{position:sticky;bottom:10px;z-index:6;margin-top:14px}
.cg-bar button{margin:0;display:flex;justify-content:space-between;align-items:center;gap:10px;border-radius:18px;padding:13px 18px;font-weight:800;box-shadow:0 10px 28px -10px #000a}
.cg-bar small{display:block;font-weight:500;font-size:12px;opacity:.85}
</style>

<div class="cg-top"><h2>Supplier catalog</h2>
<span class="muted">{{ sups|length }} supplier{{ '' if sups|length == 1 else 's' }} connected</span></div>

{% if not sups %}<div class="card">No supplier connected yet. <a href="{{ url_for('suppliers_admin.suppliers') }}">Add a supplier</a> first.</div>
{% else %}
<div class="cg-stats">
<div class="cg-st"><b>{{ total }}</b><span>Products</span></div>
<div class="cg-st"><b id="stk">0</b><span>In stock</span></div>
<div class="cg-st hot"><b id="selc">0</b><span>Selected</span></div>
</div>

<form method="post" action="{{ url_for('catalog_admin.create') }}" id="imp">

<div class="cg-search"><i></i><input id="q" placeholder="Search product" oninput="filt()" autocomplete="off"></div>

<div class="cg-chips" id="chips">
<button type="button" class="cg-chip on" data-c="">All</button>
{% for c in scats %}<button type="button" class="cg-chip" data-c="{{ c }}">{{ c }}</button>{% endfor %}
</div>

<div class="cg-row2">
<select id="sup" onchange="filt()"><option value="">All suppliers</option>{% for s in sups %}<option value="{{ s.id }}">{{ s.name }} ({{ s['items']|length }})</option>{% endfor %}</select>
<select id="sort" onchange="sortit()"><option value="name">Sort: A to Z</option><option value="cheap">Cheapest first</option><option value="profit">Most profit first</option><option value="stock">Most stock first</option></select>
<button type="button" class="cg-chip on" id="instock" onclick="tg(this)">In stock</button>
<button type="button" class="cg-chip on" id="hideadded" onclick="tg(this)">Hide added</button>
</div>

<details class="cg-set" id="set"><summary><span id="sumtxt">Markup 40% · Round 5c</span><b>Edit</b></summary>
<div class="cg-setbody">
<div class="cg-2">
<div><label>Markup %</label><input id="pct" type="number" step="1" min="0" value="40" oninput="recalc()"></div>
<div><label>+ Fixed ($)</label><input id="fix" type="number" step="0.05" min="0" value="0" oninput="recalc()"></div></div>
<label>Round</label><select id="rnd" onchange="recalc()"><option value="five" selected>Next 5 cents (1.23 → 1.25)</option><option value="x99">Ending .99</option><option value="none">No rounding</option></select>
<label>Category for all selected <span class="hint">empty = supplier's category</span></label>
<input name="category" list="cats" placeholder="e.g. AI tools"><datalist id="cats">{% for c in cats %}<option value="{{ c }}">{% endfor %}</datalist>
<p class="muted" style="margin:4px 0 6px">Prices you type by hand are kept when you change the markup.</p>
<button type="button" class="grey sm" onclick="resetPrices()">Reset prices</button>
</div></details>

<div class="cg-tools">
<button type="button" class="grey sm" onclick="selShown(true)">Select all shown</button>
<button type="button" class="grey sm" onclick="selShown(false)">Clear</button>
<span class="muted" id="shown"></span></div>

<div class="cg-grid" id="list">
{% for r in rows %}
<div class="cc imp-row{{ ' added' if r.added }}" data-name="{{ r.name|lower }}" data-sid="{{ r.sid }}" data-cat="{{ r.category }}" data-stock="{{ r.stock }}" data-cost="{{ '%.4f' % r.cost }}" data-added="{{ 1 if r.added else 0 }}">
<input type="checkbox" class="pick" name="pick" value="{{ loop.index0 }}" hidden {{ 'disabled' if r.added }}>
<span class="tick">&#10003;</span>
<div class="logo" style="background:hsl({{ r.hue }} 70% 50% / .16);color:hsl({{ r.hue }} 60% 42%)">{{ (r.name[:1] or '?')|upper }}</div>
<input class="cn" name="name_{{ loop.index0 }}" value="{{ r.name }}" maxlength="120" title="{{ r.name }}" {{ 'disabled' if r.added }}>
<div class="meta"><span class="sp">{{ r.sname }}</span>
{% if r.added %}<span class="st paid">In shop</span>{% else %}<span class="st {{ 'paid' if r.stock > 0 else 'failed' }}">{{ ('stock ' ~ r.stock) if r.stock > 0 else 'out of stock' }}</span>{% endif %}</div>
<div class="box"><div class="cost"><span>Cost ${{ '%.2f' % r.cost }}</span><span class="pf"></span></div>
<div class="sell">$<input class="pr" name="price_{{ loop.index0 }}" type="number" step="0.01" min="0.01" aria-label="Sell price" {{ 'disabled' if r.added }}></div></div>
<input type="hidden" name="sid_{{ loop.index0 }}" value="{{ r.sid }}"><input type="hidden" name="key_{{ loop.index0 }}" value="{{ r.key }}">
</div>
{% endfor %}
</div>

<div class="cg-bar"><button type="submit" class="green" id="go" disabled><span><span id="goc">0 selected</span><small id="gop">Tap cards to select</small></span><span>Add to shop &rarr;</span></button></div>
</form>

<script>
var curCat='';
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
  if(isNaN(p)||i.disabled){o.textContent='';r.dataset.profit=0;return;}
  var d=p-c;r.dataset.profit=d;
  o.textContent=(d>=0?'+$':'-$')+Math.abs(d).toFixed(2)+(c>0?' ('+Math.round(d/c*100)+'%)':'');o.className='pf '+(d>0?'paid':'failed');
}
function sumtxt(){
  var m=document.getElementById('rnd'),f=parseFloat(document.getElementById('fix').value)||0;
  document.getElementById('sumtxt').textContent='Markup '+(parseFloat(document.getElementById('pct').value)||0)+'%'+(f?' + $'+f:'')+' · '+(m.value==='five'?'Round 5c':m.value==='x99'?'Ending .99':'No rounding');
}
function recalc(){sumtxt();rows().forEach(function(r){var i=r.querySelector('.pr');if(i.disabled||i.dataset.manual){prof(r);return;}i.value=calc(parseFloat(r.dataset.cost)).toFixed(2);prof(r);});count();}
function resetPrices(){rows().forEach(function(r){delete r.querySelector('.pr').dataset.manual;});recalc();}
function tg(b){b.classList.toggle('on');filt();}
function filt(){
  var q=document.getElementById('q').value.toLowerCase().trim(),sp=document.getElementById('sup').value,
      st=document.getElementById('instock').classList.contains('on'),ha=document.getElementById('hideadded').classList.contains('on'),n=0;
  rows().forEach(function(r){
    var hide=(q&&r.dataset.name.indexOf(q)<0)||(sp&&r.dataset.sid!==sp)||(curCat&&r.dataset.cat!==curCat)||(st&&+r.dataset.stock<=0)||(ha&&r.dataset.added==='1');
    r.hidden=!!hide;if(!hide)n++;
  });
  document.getElementById('shown').textContent=n+' of '+rows().length+' shown';
}
function sortit(){
  var m=document.getElementById('sort').value,l=document.getElementById('list');
  var a=rows().sort(function(x,y){
    if(m==='cheap')return parseFloat(x.dataset.cost)-parseFloat(y.dataset.cost);
    if(m==='profit')return (parseFloat(y.dataset.profit)||0)-(parseFloat(x.dataset.profit)||0);
    if(m==='stock')return (+y.dataset.stock)-(+x.dataset.stock);
    return x.dataset.name<y.dataset.name?-1:1;
  });
  a.forEach(function(r){l.appendChild(r);});
}
function setSel(r,on){var c=r.querySelector('.pick');if(c.disabled)return;c.checked=on;r.classList.toggle('on',on);}
function selShown(on){rows().forEach(function(r){if(!r.hidden)setSel(r,on);});count();}
function count(){
  var sel=rows().filter(function(r){return r.querySelector('.pick').checked;}),n=sel.length,b=document.getElementById('go'),t=0;
  sel.forEach(function(r){t+=parseFloat(r.dataset.profit)||0;});
  b.disabled=!n;
  document.getElementById('goc').textContent=n+' selected';
  document.getElementById('selc').textContent=n;
  document.getElementById('gop').textContent=n?('Profit if each sells once: $'+t.toFixed(2)):'Tap cards to select';
}
rows().forEach(function(r){
  r.addEventListener('click',function(e){
    if(e.target.tagName==='INPUT')return;
    setSel(r,!r.querySelector('.pick').checked);count();
  });
  r.querySelector('.pr').addEventListener('input',function(){this.dataset.manual=1;prof(r);setSel(r,true);count();});
});
document.getElementById('chips').addEventListener('click',function(e){
  var b=e.target.closest('.cg-chip');if(!b)return;
  curCat=b.dataset.c;
  Array.prototype.forEach.call(this.children,function(x){x.classList.toggle('on',x===b);});
  filt();
});
document.getElementById('stk').textContent=rows().filter(function(r){return +r.dataset.stock>0;}).length;
recalc();filt();
document.getElementById('imp').addEventListener('submit',function(e){
  var bad=0,low=0;rows().forEach(function(r){var c=r.querySelector('.pick');if(c.checked){var p=parseFloat(r.querySelector('.pr').value);if(!(p>0))bad++;else if(p<=parseFloat(r.dataset.cost))low++;}});
  if(bad){e.preventDefault();alert('Some selected products have no price.');return;}
  if(low&&!confirm(low+' selected product(s) are priced at or below cost (you would lose money). Add them anyway?'))e.preventDefault();
});
</script>
{% endif %}
{% endblock %}"""


def _norm(name):
    return re.sub(r"\s+", " ", name or "").strip()


@bp.route("/catalog")
def home():
    import web
    db = web.db
    sups, errs = db.catalog_options(max_age=60)
    for e in errs:
        flash(e, "err")
    linked = set()
    for d in db.sources_by_product().values():
        for o in d["offers"]:
            linked.add((o["supplier_id"], o["provider_key"]))
    shop_names = {_norm(p["name"]).casefold() for p in db.list_products()}
    rows = []
    for s in sups:
        for a in s["items"]:
            rows.append({
                "sid": s["id"], "sname": s["name"], "key": a["key"], "name": a["name"],
                "cost": float(a.get("cost") or 0), "stock": int(a.get("stock") or 0),
                "hue": (ord(((a["name"] or "?").strip() or "?")[0].lower()) * 47) % 360,
                "category": (a.get("category") or "").strip()[:60],
                "added": (s["id"], a["key"]) in linked or _norm(a["name"]).casefold() in shop_names,
            })
    rows.sort(key=lambda r: (r["added"], r["name"].lower()))
    cats = sorted({(p["category"] or "").strip() for p in db.list_products() if (p["category"] or "").strip()})
    scats = sorted({r["category"] for r in rows if r["category"]})
    return web.render_str(PAGE, sups=sups, rows=rows, cats=cats, scats=scats, total=len(rows))


@bp.route("/catalog/create", methods=["POST"])
def create():
    import web
    db = web.db
    f = request.form
    category = (f.get("category") or "").strip()[:60] or None
    catalogs = {}

    def cat_of(sid):
        if sid not in catalogs:
            try:
                sup = db.get_supplier(sid)
                catalogs[sid] = {p["key"]: p for p in provider.catalog_for(sup, max_age=300)} if sup else {}
            except Exception:
                catalogs[sid] = {}
        return catalogs[sid]

    names = {_norm(p["name"]).casefold(): p["id"] for p in db.list_products()}
    picks = [p for p in f.getlist("pick") if p.isdigit()][:700]
    ok, problems, below_cost = 0, [], 0
    for i in picks:
        name = _norm(f.get(f"name_{i}", ""))[:120]
        key = f.get(f"key_{i}", "")
        try:
            sid = int(f.get(f"sid_{i}", "0"))
        except ValueError:
            sid = 0
        try:
            price = round(float(f.get(f"price_{i}", "")), 2)
        except ValueError:
            price = 0
        if not name or not key or not sid or price <= 0:
            problems.append(f"{name or key or '#' + i}: name and price are needed")
            continue
        try:
            item = cat_of(sid).get(key, {})
            pid = names.get(name.casefold())
            existed = pid is not None
            if not existed:
                pid = db.add_product(name, price, "auto")
                names[name.casefold()] = pid
            fields = {}
            if not existed:
                fields["price"] = price
                fields["delivery"] = "Instant"
                fields["category"] = category or (item.get("category") or "")[:60] or None
                if item.get("description"):
                    fields["description"] = item["description"][:1000]
            else:
                cur_p = db.get_product_any(pid)
                if cur_p and not (cur_p["category"] or "").strip() and (category or item.get("category")):
                    fields["category"] = category or item["category"][:60]
                if cur_p and not (cur_p["description"] or "").strip() and item.get("description"):
                    fields["description"] = item["description"][:1000]
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
    return redirect(url_for("products") if ok else url_for("catalog_admin.home"))


def register(app):
    app.register_blueprint(bp)
