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
.cgx{background:#15102e;color:#fff;border-radius:22px;padding:14px 12px 12px;margin:-4px -4px 0}
.cgx *{box-sizing:border-box}
.cgx input,.cgx select{background:#221a47;border:1px solid #3a2f6b;color:#fff;border-radius:14px;margin:0}
.cgx input::placeholder{color:#a79fd6}
.cgx label{color:#c9c2ee;font-size:12.5px}
.cgx .hint,.cgx .muted{color:#a79fd6}
.cg-hero{background:linear-gradient(120deg,#7c3aed 0%,#ec4899 55%,#fb923c 100%);border-radius:22px;padding:16px 16px 14px;margin-bottom:12px;position:relative;overflow:hidden}
.cg-hero:before{content:'';position:absolute;right:-26px;top:-26px;width:110px;height:110px;border-radius:50%;background:#ffffff22}
.cg-hero:after{content:'';position:absolute;right:30px;bottom:-34px;width:70px;height:70px;border-radius:50%;background:#ffffff18}
.cg-hero small{font-size:12px;letter-spacing:.06em;text-transform:uppercase;opacity:.9}
.cg-hero b{display:block;font-size:34px;font-weight:700;line-height:1.1;margin-top:4px}
.cg-hero .pills{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.cg-hero .pills span{background:#ffffff2e;border-radius:999px;padding:4px 12px;font-size:12px}
.cg-lbl{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:#a79fd6;margin:2px 2px 6px}
.cg-sups{display:flex;gap:8px;overflow-x:auto;padding:2px 0 10px;scrollbar-width:none}
.cg-sups::-webkit-scrollbar{display:none}
.cg-sup{flex:none;width:auto;min-height:0!important;margin:0;text-align:left;border:0;border-radius:18px;padding:10px 16px;color:#fff;cursor:pointer;background:#221a47;border:2px solid transparent}
.cg-sup b{display:block;font-size:14px;font-weight:700}
.cg-sup span{font-size:11.5px;opacity:.85}
.cg-sup.on{border-color:#fff;box-shadow:0 10px 20px -10px #000}
.cg-search{position:relative}
.cg-search input{padding-left:42px;margin-bottom:10px;background:#221a47}
.cg-search i{position:absolute;left:15px;top:14px;width:14px;height:14px;border:2px solid #a79fd6;border-radius:50%;pointer-events:none}
.cg-search i:after{content:'';position:absolute;width:2px;height:7px;background:#a79fd6;right:-4px;bottom:-6px;transform:rotate(-45deg)}
.cg-chips{display:flex;gap:7px;overflow-x:auto;padding:2px 0 10px;scrollbar-width:none}
.cg-chips::-webkit-scrollbar{display:none}
.cg-chip{flex:none;width:auto;min-height:0!important;margin:0;padding:8px 16px;border-radius:999px;border:0;background:#2f2560;color:#fff;font-size:13px;font-weight:600;cursor:pointer}
.cg-chip.on{background:#fff;color:#15102e}
.cg-row2{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.cg-row2 select{flex:1;min-width:130px}
.cg-set{background:#221a47;border-radius:16px;padding:12px 14px;margin:2px 0 12px}
.cg-set summary{cursor:pointer;display:flex;justify-content:space-between;align-items:center;list-style:none;font-size:14px;font-weight:600}
.cg-set summary::-webkit-details-marker{display:none}
.cg-set summary b{background:linear-gradient(120deg,#7c3aed,#ec4899);color:#fff;font-size:12px;padding:4px 12px;border-radius:999px}
.cg-setbody{margin-top:12px}
.cg-2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.cg-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:2px 0 10px}
.cg-tools button{background:#2f2560;color:#fff;border:0}
.cg-tools .muted{margin-left:auto;font-size:12.5px}
.cg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}
@media(max-width:640px){.cg-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
.cc{position:relative;overflow:hidden;min-height:178px;border-radius:22px;padding:14px;color:#fff;cursor:pointer;display:flex;flex-direction:column;justify-content:flex-end;transition:transform .12s}
.cc:active{transform:scale(.98)}
.cc[hidden]{display:none}
.cc .wm{position:absolute;right:-6px;top:-18px;font-size:110px;font-weight:700;color:#ffffff24;line-height:1;pointer-events:none}
.cc .tick{position:absolute;top:10px;left:10px;width:24px;height:24px;border-radius:50%;border:2px solid #ffffff88;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:800;color:transparent}
.cc.on{box-shadow:0 0 0 3px #fff,0 14px 24px -12px #000}
.cc.on .tick{background:#fff;border-color:#fff;color:#15102e}
.cc.added{opacity:.45;cursor:default}
.cc input.cn{background:transparent;border:1px solid transparent;color:#fff;padding:3px 4px;margin:0 0 2px -4px;min-height:0;font-size:14px;font-weight:700;line-height:1.25;border-radius:8px;text-overflow:ellipsis}
.cc input.cn:focus{border-color:#fff;background:#00000033}
.cc .sell{display:flex;align-items:center;font-size:26px;font-weight:800;margin-top:4px}
.cc .sell input{background:transparent;border:0;color:#fff;width:100%;min-width:0;padding:0 0 0 2px;margin:0;min-height:0;font-size:26px;font-weight:800;box-shadow:none}
.cc .sell input:focus{background:#00000033;border-radius:8px}
.cc .low{display:flex;justify-content:space-between;align-items:center;margin-top:4px;gap:6px}
.cc .pf{background:#ffffff33;border-radius:999px;padding:2px 10px;font-size:12px;font-weight:700;min-height:20px}
.cc .pf:empty{display:none}
.cc .sk{font-size:12px;opacity:.92;white-space:nowrap}
.cc .sp{position:absolute;top:10px;right:10px;background:#00000040;border-radius:999px;padding:2px 9px;font-size:11px;font-weight:700}
.cc .cost{font-size:11.5px;opacity:.8}
.cc .ccat{font-size:11px;opacity:.9}
.cc .ccat:empty{display:none}
.cg-newcat{display:flex;gap:8px;margin:0 0 10px}.cg-newcat[hidden]{display:none}
.cg-newcat input{flex:1;margin:0}.cg-newcat button{width:auto;margin:0;background:#2f2560;color:#fff;border:0}
.cg-hint{font-size:12.5px;color:#a79fd6;margin:-2px 2px 10px}
.cg-own form{margin-top:10px}.cg-own textarea{background:#221a47;border:1px solid #3a2f6b;color:#fff;border-radius:14px;width:100%}
.cg-own select{width:100%}
.cg-own .go{width:100%;background:linear-gradient(120deg,#7c3aed,#ec4899);color:#fff;border:0;border-radius:16px;padding:12px;font-weight:700}
.cg-bar{position:sticky;bottom:10px;z-index:6;margin-top:14px}
.cg-bar .in{background:#fff;color:#15102e;border-radius:22px;padding:8px 8px 8px 18px;display:flex;justify-content:space-between;align-items:center;gap:10px;box-shadow:0 16px 34px -10px #000}
.cg-bar small{display:block;font-size:12px;color:#6b5fa8}
.cg-bar b{font-size:14px}
.cg-bar button{width:auto;margin:0;min-height:0;background:linear-gradient(120deg,#7c3aed,#ec4899);color:#fff;border:0;border-radius:16px;padding:12px 18px;font-size:14px;font-weight:700}
.cg-bar button:disabled{opacity:.45}
</style>

{% if not sups %}<div class="card">No supplier connected yet. <a href="{{ url_for('suppliers_admin.suppliers') }}">Add a supplier</a> first.</div>
{% else %}
<div class="cgx">
<div class="cg-hero"><small>Supplier catalog</small><b>{{ total }} products</b>
<div class="pills"><span><span id="stk">0</span> in stock</span><span>{{ sups|length }} supplier{{ '' if sups|length == 1 else 's' }}</span></div></div>

<details class="cg-set cg-own"><summary><span>+ Add my own product (not from a supplier)</span><b>Open</b></summary>
<form method="post" action="{{ url_for('create_product') }}" id="own" enctype="multipart/form-data">
<input type="hidden" name="next" value="catalog"><input type="hidden" name="kind" value="auto"><input type="hidden" name="category" id="owncat">
<label>Product name</label><input name="name" required maxlength="120" placeholder="e.g. Netflix 1 month">
<div class="cg-2"><div><label>Cost (what you paid)</label><input name="cost" type="number" step="0.01" min="0" inputmode="decimal" required placeholder="0.90"></div>
<div><label>Selling price</label><input name="price" type="number" step="0.01" min="0" inputmode="decimal" required placeholder="1.10"></div></div>
<label>Category</label>
<select id="ownsel"><option value="">No category</option>{% for c in cats %}<option value="{{ c }}">{{ c }}</option>{% endfor %}<option value="__new">+ New category...</option></select>
<input id="ownnew" placeholder="New category name" maxlength="60" hidden style="margin-top:8px">
<label>Stock <span class="hint">optional, one item per line</span></label><textarea name="links" rows="3" placeholder="email:password"></textarea>
<button type="submit" class="go">Add product</button></form></details>

<form method="post" action="{{ url_for('catalog_admin.create') }}" id="imp">

<div class="cg-lbl">Take products from</div>
{% set hues = [265, 205, 155, 35, 330, 0] %}
<div class="cg-sups" id="supbar">
<button type="button" class="cg-sup on" data-s="" style="background:linear-gradient(135deg,#7c3aed,#ec4899)"><b>All suppliers</b><span>{{ total }} products</span></button>
{% for s in sups %}<button type="button" class="cg-sup" data-s="{{ s.id }}" style="background:linear-gradient(135deg,hsl({{ hues[loop.index0 % 6] }} 75% 55%),hsl({{ (hues[loop.index0 % 6] + 30) % 360 }} 70% 32%))"><b>{{ s.name }}</b><span>{{ s['items']|length }} products</span></button>{% endfor %}
</div>

<div class="cg-search"><i></i><input id="q" placeholder="Search product" oninput="filt()" autocomplete="off"></div>

<div class="cg-chips" id="chips">
<button type="button" class="cg-chip on" data-c="">All</button>
{% for c in scats %}<button type="button" class="cg-chip" data-c="{{ c }}">{{ c }}</button>{% endfor %}
</div>

<div class="cg-row2">
<select id="sort" onchange="sortit()"><option value="name">Sort: A to Z</option><option value="cheap">Cheapest first</option><option value="profit">Most profit first</option><option value="stock">Most stock first</option></select>
<button type="button" class="cg-chip on" id="instock" onclick="tg(this)">In stock</button>
<button type="button" class="cg-chip on" id="hideadded" onclick="tg(this)">Hide added</button>
</div>

<div class="cg-lbl">Category for the products you add</div>
<div class="cg-chips" id="catbar"><button type="button" class="cg-chip on" data-cc="">Supplier's own</button>{% for c in cats %}<button type="button" class="cg-chip" data-cc="{{ c }}">{{ c }}</button>{% endfor %}<button type="button" class="cg-chip" id="newcat" style="border:1px dashed #a79fd6;background:transparent">+ New</button></div>
<div class="cg-newcat" id="newcatbox" hidden><input id="newcatin" maxlength="60" placeholder="New category name" autocomplete="off"><button type="button" class="sm" id="newcatok">Add</button></div>
<div class="cg-hint" id="cathint">Tip: pick a category first, then tap tiles. Or tap tiles first, then pick a category.</div>

<details class="cg-set" id="set"><summary><span id="sumtxt">Markup 40% · Round 5c</span><b>Edit</b></summary>
<div class="cg-setbody">
<div class="cg-2">
<div><label>Markup %</label><input id="pct" type="number" step="1" min="0" value="40" oninput="recalc()"></div>
<div><label>+ Fixed ($)</label><input id="fix" type="number" step="0.05" min="0" value="0" oninput="recalc()"></div></div>
<label>Round</label><select id="rnd" onchange="recalc()" style="width:100%"><option value="five" selected>Next 5 cents (1.23 → 1.25)</option><option value="x99">Ending .99</option><option value="none">No rounding</option></select>
<p class="muted" style="margin:6px 0">Prices you type by hand are kept when you change the markup.</p>
<button type="button" class="grey sm" onclick="resetPrices()">Reset prices</button>
</div></details>

<div class="cg-tools">
<button type="button" class="sm" onclick="selShown(true)">Select all shown</button>
<button type="button" class="sm" onclick="selShown(false)">Clear</button>
<span class="muted" id="shown"></span></div>

<div class="cg-grid" id="list">
{% for r in rows %}
<div class="cc imp-row{{ ' added' if r.added }}" style="background:linear-gradient(155deg,hsl({{ r.hue }} 80% 58%),hsl({{ (r.hue + 25) % 360 }} 75% 30%))" data-name="{{ r.name|lower }}" data-sid="{{ r.sid }}" data-cat="{{ r.category }}" data-stock="{{ r.stock }}" data-cost="{{ '%.4f' % r.cost }}" data-added="{{ 1 if r.added else 0 }}">
<input type="checkbox" class="pick" name="pick" value="{{ loop.index0 }}" hidden {{ 'disabled' if r.added }}>
<span class="wm">{{ (r.name[:1] or '?')|upper }}</span><span class="tick">&#10003;</span><span class="sp">{{ r.sname }}</span>
<input class="cn" name="name_{{ loop.index0 }}" value="{{ r.name }}" maxlength="120" title="{{ r.name }}" {{ 'disabled' if r.added }}>
<div class="cost">cost ${{ '%.2f' % r.cost }} <span class="ccat">{{ r.category }}</span></div>
<div class="sell">$<input class="pr" name="price_{{ loop.index0 }}" type="number" step="0.01" min="0.01" aria-label="Sell price" {{ 'disabled' if r.added }}></div>
<div class="low"><span class="pf"></span><span class="sk">{% if r.added %}In shop{% elif r.stock > 0 %}{{ r.stock }} left{% else %}out of stock{% endif %}</span></div>
<input type="hidden" name="cat_{{ loop.index0 }}" value="{{ r.category }}" data-orig="{{ r.category }}"><input type="hidden" name="sid_{{ loop.index0 }}" value="{{ r.sid }}"><input type="hidden" name="key_{{ loop.index0 }}" value="{{ r.key }}">
</div>
{% endfor %}
</div>

<div class="cg-bar"><div class="in"><div><b id="goc">0 selected</b><small id="gop">Tap tiles to select</small></div><button type="submit" id="go" disabled>Add to shop &rarr;</button></div></div>
</form>
</div>

<script>
var curCat='',curSup='';
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
  o.textContent=(d>=0?'+$':'-$')+Math.abs(d).toFixed(2)+(c>0?' ('+Math.round(d/c*100)+'%)':'');
}
function sumtxt(){
  var m=document.getElementById('rnd'),f=parseFloat(document.getElementById('fix').value)||0;
  document.getElementById('sumtxt').textContent='Markup '+(parseFloat(document.getElementById('pct').value)||0)+'%'+(f?' + $'+f:'')+' · '+(m.value==='five'?'Round 5c':m.value==='x99'?'Ending .99':'No rounding');
}
function recalc(){sumtxt();rows().forEach(function(r){var i=r.querySelector('.pr');if(i.disabled||i.dataset.manual){prof(r);return;}i.value=calc(parseFloat(r.dataset.cost)).toFixed(2);prof(r);});count();}
function resetPrices(){rows().forEach(function(r){delete r.querySelector('.pr').dataset.manual;});recalc();}
function tg(b){b.classList.toggle('on');filt();}
function filt(){
  var q=document.getElementById('q').value.toLowerCase().trim(),
      st=document.getElementById('instock').classList.contains('on'),ha=document.getElementById('hideadded').classList.contains('on'),n=0;
  rows().forEach(function(r){
    var hide=(q&&r.dataset.name.indexOf(q)<0)||(curSup&&r.dataset.sid!==curSup)||(curCat&&r.dataset.cat!==curCat)||(st&&+r.dataset.stock<=0)||(ha&&r.dataset.added==='1');
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
var activeCat='';
function catIn(r){return r.querySelector('input[name^=cat_]');}
function setCat(r,v){catIn(r).value=v;r.querySelector('.ccat').textContent=v;}
function setSel(r,on){var c=r.querySelector('.pick');if(c.disabled)return;c.checked=on;r.classList.toggle('on',on);setCat(r,on&&activeCat?activeCat:catIn(r).dataset.orig);}
function pickCat(b){
  activeCat=b.dataset.cc;
  Array.prototype.forEach.call(document.getElementById('catbar').querySelectorAll('.cg-chip[data-cc]'),function(x){x.classList.toggle('on',x===b);});
  var n=0;rows().forEach(function(r){if(r.querySelector('.pick').checked){setCat(r,activeCat||catIn(r).dataset.orig);n++;}});
  document.getElementById('cathint').textContent=activeCat?('Selected and next tapped products go to "'+activeCat+'"'+(n?' ('+n+' updated)':'')):'Each product keeps its supplier\'s own category.';
}
document.getElementById('catbar').addEventListener('click',function(e){
  var b=e.target.closest('.cg-chip');if(!b)return;
  if(b.id==='newcat'){var bx=document.getElementById('newcatbox');bx.hidden=!bx.hidden;if(!bx.hidden)document.getElementById('newcatin').focus();return;}
  pickCat(b);
});
function addNewCat(){
  var i=document.getElementById('newcatin'),v=i.value.replace(/\s+/g,' ').trim();if(!v)return;
  var bar=document.getElementById('catbar'),b=Array.prototype.filter.call(bar.querySelectorAll('.cg-chip[data-cc]'),function(x){return x.dataset.cc.toLowerCase()===v.toLowerCase();})[0];
  if(!b){b=document.createElement('button');b.type='button';b.className='cg-chip';b.dataset.cc=v;b.textContent=v;bar.insertBefore(b,document.getElementById('newcat'));}
  i.value='';document.getElementById('newcatbox').hidden=true;pickCat(b);
}
document.getElementById('newcatok').addEventListener('click',addNewCat);
document.getElementById('newcatin').addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();addNewCat();}});
var os=document.getElementById('ownsel'),on_=document.getElementById('ownnew');
os.addEventListener('change',function(){on_.hidden=os.value!=='__new';if(!on_.hidden)on_.focus();});
document.getElementById('own').addEventListener('submit',function(){document.getElementById('owncat').value=os.value==='__new'?on_.value.trim():os.value;});
function selShown(on){rows().forEach(function(r){if(!r.hidden)setSel(r,on);});count();}
function count(){
  var sel=rows().filter(function(r){return r.querySelector('.pick').checked;}),n=sel.length,b=document.getElementById('go'),t=0;
  sel.forEach(function(r){t+=parseFloat(r.dataset.profit)||0;});
  b.disabled=!n;
  document.getElementById('goc').textContent=n+' selected';
  document.getElementById('gop').textContent=n?('Profit if each sells once: $'+t.toFixed(2)):'Tap tiles to select';
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
document.getElementById('supbar').addEventListener('click',function(e){
  var b=e.target.closest('.cg-sup');if(!b)return;
  curSup=b.dataset.s;
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


def _drop_stale_link(db, sid, key):
    """Remove the supplier link of a deleted (hidden) product so the item can be added to the shop again."""
    c = db.conn()
    try:
        ids = [r[0] for r in c.execute(
            "SELECT ps.product_id FROM product_sources ps JOIN products p ON p.id = ps.product_id "
            "WHERE ps.supplier_id=? AND ps.provider_key=? AND p.active=0", (sid, key)).fetchall()]
        for pid in ids:
            c.execute("DELETE FROM product_sources WHERE product_id=? AND supplier_id=?", (pid, sid))
            c.execute("DELETE FROM source_settings WHERE product_id=?", (pid,))
        if ids:
            c.commit()
    finally:
        c.close()


@bp.route("/catalog")
def home():
    import web
    db = web.db
    sups, errs = db.catalog_options(max_age=60)
    for e in errs:
        flash(e, "err")
    # only products that are really in the shop count: a deleted (hidden) product keeps its supplier link,
    # which used to make its supplier item look "In shop" forever
    live_ids = {p["id"] for p in db.list_products()}
    linked = set()
    for pid, d in db.sources_by_product().items():
        if pid not in live_ids:
            continue
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
    bulk_category = (f.get("category") or "").strip()[:60] or None   # old forms
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
        category = (f.get(f"cat_{i}") or "").strip()[:60] or bulk_category
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
            _drop_stale_link(db, sid, key)
            db.add_source(pid, sid, key)
            ok += 1
            src = db.sources_by_product().get(pid)
            if src and price <= min(o["cost"] for o in src["offers"]):
                below_cost += 1
        except Exception as e:
            problems.append(f"{name}: {str(e)[:120]}")
    if ok:
        flash(f"{ok} product{'s' if ok != 1 else ''} added to your shop and linked to the supplier."
              + (f" {below_cost} priced at or below cost, check the prices." if below_cost else ""))
    for p in problems[:8]:
        flash(p, "err")
    if len(problems) > 8:
        flash(f"... and {len(problems) - 8} more problems.", "err")
    return redirect(url_for("catalog_admin.home"))


def register(app):
    app.register_blueprint(bp)
