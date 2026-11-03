"""Admin website page: Compare ALL suppliers and import the cheapest in one go.

* Same product sold by several suppliers is grouped into ONE row (matched by name, numbers/durations must match).
* The cheapest supplier that has stock is shown as the best offer; the product is linked to every supplier
  with mode "Cheapest" + fallback ON, so the bot always buys from the cheapest one that has stock.
* Category is guessed (your existing categories first, then brand names such as ChatGPT / Netflix, then the
  supplier's category, then the first word). Description is copied from the supplier.
* Products you already sell (same name) or that are already linked are hidden. You can also hide any product forever.
* You only set the price (pre-filled from a markup %) and, if you want, type an emoji in front of the name.
* AUTO MODE: when switched on, the bot does all of this by itself every few minutes (auto_import()).
"""
import json
import math
import re
from difflib import SequenceMatcher

import provider
from flask import Blueprint, request, redirect, url_for, flash, jsonify

bp = Blueprint("compare_admin", __name__)

IGNORE_KEY = "compare_ignore"

# most specific first (the first match wins)
BRANDS = [
    "ChatGPT", "Gemini", "Claude", "Perplexity", "Grok", "Midjourney", "Copilot", "Cursor", "GitHub", "Sora", "Veo",
    "Canva", "CapCut", "Adobe", "Quillbot", "Grammarly", "Notion", "Duolingo", "LinkedIn", "Coursera", "Udemy",
    "Netflix", "Spotify", "YouTube", "Disney", "Prime Video", "Amazon Prime", "HBO", "Crunchyroll", "Hotstar",
    "NordVPN", "ExpressVPN", "Surfshark", "Telegram", "Discord", "Microsoft", "Office", "Windows", "Apple", "Google",
]
GENERIC_CATS = {"account", "accounts", "key", "keys", "link", "links", "code", "codes", "other", "others", "default",
                "general", "auto", "automatic", "product", "products", "digital", "instant", ""}


DEFAULT_ALIASES = """
Amazon Prime = amazon prime video = prime video = amazon prime = prime vedio = amazon prime vedio = amazon video
ChatGPT = chat gpt = gpt = openai chatgpt = open ai chatgpt = gpt plus
YouTube = youtube premium = you tube = yt premium = yt
Disney+ = disney plus = disneyplus = disney hotstar = hotstar disney = hotstar
Spotify = spotify premium
Gemini = google gemini = gemini ai = gemini advanced
Perplexity = perplexity ai = perplexity pro
Midjourney = mid journey
CapCut = cap cut
Canva = canva pro
Microsoft 365 = office 365 = office365 = m365 = ms 365 = microsoft office
LinkedIn = linked in = linkedin premium
NordVPN = nord vpn
ExpressVPN = express vpn
Crunchyroll = crunchy roll
Telegram Premium = tg premium = telegram prem
"""

# words that never change which product it is
NOISE = {"buy", "account", "accounts", "acc", "subscription", "subs", "sub", "full", "instant", "delivery",
         "warranty", "hw", "the", "for", "with", "new", "your", "get"}


def _squash(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").casefold())


def make_rules(custom=""):
    """Alias rules: 'Label = phrase = phrase'. Your own lines win over the built-in ones."""
    seen, rules = set(), []
    for line in ((custom or "") + "\n" + DEFAULT_ALIASES).splitlines():
        parts = [p.strip() for p in line.split("=") if p.strip()]
        if len(parts) < 2:
            continue
        label = parts[0]
        canon = _squash(label)
        if not canon:
            continue
        for ph in parts:
            key = (ph.casefold(), canon)
            if key in seen:
                continue
            seen.add(key)
            words = [re.escape(w) for w in re.split(r"[\s\-_]+", ph.casefold()) if w]
            if not words:
                continue
            rx = re.compile(r"(?<![a-z0-9])" + r"[\s\-_]*".join(words) + r"(?![a-z0-9])")
            rules.append({"canon": canon, "label": label, "phrase": ph, "rx": rx})
    rules.sort(key=lambda r: -len(r["phrase"]))
    return rules


_DEFAULT_RULES = None


def _rules(rules):
    global _DEFAULT_RULES
    if rules is not None:
        return rules
    if _DEFAULT_RULES is None:
        _DEFAULT_RULES = make_rules("")
    return _DEFAULT_RULES


def _apply_aliases(text, rules):
    t = (text or "").casefold()
    for r in _rules(rules):
        t = r["rx"].sub(" " + r["canon"] + " ", t)
    return t


def _norm(name, rules=None):
    """-> (numbers tuple, alpha string): two names with the same value are the same product."""
    s = _apply_aliases(name, rules)
    s = re.sub(r"\(?\b\d+\s*(?:hw|hrs?|hours?)\b\)?", " ", s)          # "(24HW)" warranty hours are not a duration
    s = re.sub(r"(\d+)\s*(months?|mos?|m)\b", r"\1 month", s)
    s = re.sub(r"(\d+)\s*(days?|d)\b", r"\1 day", s)
    s = re.sub(r"(\d+)\s*(years?|yrs?|y)\b", r"\1 year", s)
    nums = tuple(sorted(re.findall(r"\d+", s)))
    alpha = " ".join(sorted(w for w in re.findall(r"[a-z]+", s) if w not in NOISE))
    return nums, alpha


def _gkey(n):
    return f"{','.join(n[0])}|{n[1]}"


def guess_category(name, sup_cat, existing_cats, rules=None):
    rs = _rules(rules)
    t = _apply_aliases(name, rs)
    words = set(re.findall(r"[a-z0-9]+", t))
    found = [r for r in rs if r["canon"] in words]
    # 1) one of YOUR categories that means the same brand (e.g. "Amazon prime" for a "Prime Video" product)
    for c in existing_cats:
        cw = set(re.findall(r"[a-z0-9]+", _apply_aliases(c, rs)))
        if found and any(r["canon"] in cw for r in found):
            return c
    sq = _squash(name)
    for c in sorted(existing_cats, key=lambda x: -len(x)):
        if len(_squash(c)) >= 3 and _squash(c) in sq:
            return c
    # 2) the brand label from the alias table
    if found:
        return found[0]["label"][:60]
    for b in BRANDS:
        if _squash(b) in sq:
            return b
    sc = (sup_cat or "").strip()
    if sc.casefold() not in GENERIC_CATS:
        return sc[:60]
    ws = re.findall(r"[A-Za-z0-9]+", name or "")
    return (ws[0].title() if ws else "")[:60]


def clean_name(n):
    n = re.sub(r"^\s*buy\s+", "", n or "", flags=re.I)
    n = re.sub(r"\s+", " ", n).strip().rstrip(".").strip()
    return n or (n or "")


def build_groups(sups, existing_cats, rules=None):
    groups = []   # [{nums, alpha, offers:[...]}]
    for s in sups:
        for it in s["items"]:
            nums, alpha = _norm(it["name"], rules)
            if not alpha:
                continue
            offer = {"sid": s["id"], "sname": s["name"], "key": it["key"], "name": it["name"],
                     "cost": float(it["cost"]), "stock": int(it["stock"]),
                     "description": it.get("description") or "", "category": it.get("category") or ""}
            hit = None
            for g in groups:
                if g["nums"] != nums:
                    continue
                if g["alpha"] == alpha or SequenceMatcher(None, g["alpha"], alpha).ratio() >= 0.9:
                    hit = g
                    break
            if hit is None:
                hit = {"nums": nums, "alpha": alpha, "offers": []}
                groups.append(hit)
            same = [o for o in hit["offers"] if o["sid"] == s["id"]]
            if same:
                if offer["cost"] < same[0]["cost"] and offer["stock"] > 0:
                    hit["offers"].remove(same[0])
                    hit["offers"].append(offer)
            else:
                hit["offers"].append(offer)
    out = []
    for g in groups:
        offs = sorted(g["offers"], key=lambda o: o["cost"])
        instock = [o for o in offs if o["stock"] > 0]
        best = (instock or offs)[0]
        desc = next((o["description"] for o in ([best] + offs) if o["description"]), "")
        out.append({
            "gkey": _gkey((g["nums"], g["alpha"])), "name": clean_name(best["name"]), "offers": offs, "best": best,
            "stock": sum(o["stock"] for o in offs), "instock": bool(instock), "description": desc,
            "category": guess_category(best["name"], next((o["category"] for o in offs if o["category"]), ""),
                                       existing_cats, rules),
        })
    out.sort(key=lambda g: (g["category"].casefold(), g["name"].casefold()))
    return out


PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Compare &amp; import</h2>
<p class="muted">All your suppliers are compared. The cheapest one that has stock is used automatically. Category and description are copied. You only set the price (and add an emoji if you like).</p>


<div class="card"><h3>Auto mode <span class="{{ 'paid' if auto.on else 'failed' }}">{{ 'ON' if auto.on else 'OFF' }}</span></h3>
<p class="muted" style="margin:0 0 8px">When ON, the bot compares all suppliers by itself every 10 minutes and adds every new product with the cheapest supplier, category, description and your markup price. Products you already have, already linked or hidden are never touched. You get a message in the bot when new products are added.</p>
<form method="post" action="{{ url_for('compare_admin.auto_save') }}">
<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" name="on" value="1" style="width:auto" {{ 'checked' if auto.on }}> Auto mode ON</label>
<label>Markup % on the cheapest cost</label><input name="pct" type="number" step="1" min="0" value="{{ auto.pct }}">
<label>Plus a fixed amount ($)</label><input name="fix" type="number" step="0.05" min="0" value="{{ auto.fix }}">
<label>Round the price</label>
<select name="rnd"><option value="five" {{ 'selected' if auto.rnd == 'five' }}>Up to the next 5 cents</option><option value="x99" {{ 'selected' if auto.rnd == 'x99' }}>Ending .99</option><option value="none" {{ 'selected' if auto.rnd == 'none' }}>No rounding</option></select>
<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" name="instock" value="1" style="width:auto" {{ 'checked' if auto.instock }}> Only add products that are in stock</label>
<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap"><button type="submit">Save</button></div></form>
<form method="post" action="{{ url_for('compare_admin.auto_run') }}" style="margin-top:8px"><button type="submit" class="green sm">Run now</button></form></div>

<div class="card"><h3>Smart matching</h3>
<p class="muted" style="margin:0 0 8px">The bot already knows that Prime Video = Amazon Prime, Chat GPT = ChatGPT, 1M = 1 Month, and ignores words like "Buy", "Account" or "(24HW)". If it misses one, add your own line: <b>Name = other name = another name</b> (one per line).</p>
<form method="post" action="{{ url_for('compare_admin.aliases_save') }}">
<textarea name="aliases" rows="5" spellcheck="false" placeholder="Amazon Prime = prime video = prime vedio&#10;Gemini = google ai pro" style="width:100%">{{ aliases_text }}</textarea>
<button type="submit" class="sm" style="margin-top:6px">Save</button></form></div>

{% if not sups %}<div class="card">No supplier connected yet. <a href="{{ url_for('suppliers_admin.suppliers') }}">Add a supplier</a> first.</div>
{% else %}
<form method="post" action="{{ url_for('compare_admin.create') }}" id="imp">

<div class="card"><h3>1. Your profit</h3>
<label>Markup % on the cheapest cost</label><input id="pct" type="number" step="1" min="0" value="40" oninput="recalc()">
<label>Plus a fixed amount ($)</label><input id="fix" type="number" step="0.05" min="0" value="0" oninput="recalc()">
<label>Round the price</label>
<select id="rnd" onchange="recalc()"><option value="five" selected>Up to the next 5 cents (1.23 → 1.25)</option><option value="x99">Ending .99 (1.23 → 1.99)</option><option value="none">No rounding</option></select>
<p class="muted" style="margin:6px 0 0">Prices you type by hand are kept when you change the markup. "Reset prices" overwrites them.</p>
<button type="button" class="grey sm" onclick="resetPrices()">Reset prices</button></div>

<div class="card"><h3>2. Choose products</h3>
<input id="q" placeholder="Search... (e.g. chatgpt)" oninput="filt()" autocomplete="off">
<label>Category</label>
<select id="catf" onchange="filt()"><option value="">All categories</option>{% for c in cat_list %}<option value="{{ c|lower }}">{{ c }}</option>{% endfor %}</select>
<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="instock" checked style="width:auto" onchange="filt()"> Only in stock</label>
<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="multi" style="width:auto" onchange="filt()"> Only products sold by 2+ suppliers</label>
<div style="display:flex;gap:8px;margin:8px 0;flex-wrap:wrap"><button type="button" class="grey sm" onclick="selShown(true)">Select all shown</button><button type="button" class="grey sm" onclick="selShown(false)">Clear</button>
<a href="{{ url_for('compare_admin.home', hidden=(0 if show_hidden else 1)) }}" class="muted" style="align-self:center">{{ 'Back to normal list' if show_hidden else 'Show hidden (' ~ hidden_n ~ ')' }}</a></div>
<datalist id="cats2">{% for c in cat_list %}<option value="{{ c }}">{% endfor %}</datalist>
<p class="muted" style="margin:0 0 8px">{{ groups|length }} product{{ '' if groups|length == 1 else 's' }} to choose from ({{ skipped }} hidden because you already have them).</p>
<div id="list">
{% for g in groups %}
<div class="sub imp-row" data-name="{{ g.name|lower }}" data-cat="{{ g.category|lower }}" data-stock="{{ g.stock }}" data-cost="{{ '%.4f' % g.best.cost }}" data-n="{{ g.offers|length }}" style="padding:10px;margin:6px 0">
<label style="display:flex;gap:10px;align-items:flex-start;margin:0"><input type="checkbox" class="pick" name="pick" value="{{ loop.index0 }}" style="width:auto;margin-top:6px" onchange="count()">
<span style="flex:1"><input name="name_{{ loop.index0 }}" value="{{ g.name }}" maxlength="120" placeholder="Name (you can add an emoji)">
<span class="muted" style="font-size:13px;display:block;margin-top:4px">
{% for o in g.offers %}<span {% if o.sid == g.best.sid and o.key == g.best.key %}class="paid"{% endif %}>{{ o.sname }} ${{ '%.2f' % o.cost }}{% if o.stock <= 0 %} (out){% else %} ({{ o.stock }}){% endif %}</span>{% if not loop.last %} · {% endif %}{% endfor %}
</span></span></label>
<input type="hidden" name="offers_{{ loop.index0 }}" value="{{ g.offers_json }}">
<div style="display:flex;gap:8px;align-items:center;margin-top:6px;padding-left:30px;flex-wrap:wrap">
<span class="muted">Category</span><input name="cat_{{ loop.index0 }}" list="cats2" value="{{ g.category }}" maxlength="60" style="max-width:160px">
<span class="muted">Sell for $</span><input class="pr" name="price_{{ loop.index0 }}" type="number" step="0.01" min="0.01" style="max-width:100px" oninput="this.dataset.manual=1;prof(this.closest('.imp-row'))"><span class="pf muted"></span>
<button type="button" class="grey sm" onclick="hideRow(this,'{{ g.gkey }}','{{ 'remove' if show_hidden else 'add' }}')">{{ 'Unhide' if show_hidden else 'Hide' }}</button></div>
</div>
{% else %}<p class="muted">Nothing new to import. Every supplier product is already in your shop or hidden.</p>{% endfor %}
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
function recalc(){rows().forEach(function(r){var i=r.querySelector('.pr');if(i.dataset.manual)return;i.value=calc(parseFloat(r.dataset.cost)).toFixed(2);prof(r);});}
function resetPrices(){rows().forEach(function(r){delete r.querySelector('.pr').dataset.manual;});recalc();}
function filt(){
  var q=document.getElementById('q').value.toLowerCase().trim(),st=document.getElementById('instock').checked,
      mu=document.getElementById('multi').checked,cf=document.getElementById('catf').value;
  rows().forEach(function(r){
    r.hidden=(q&&r.dataset.name.indexOf(q)<0)||(st&&+r.dataset.stock<=0)||(mu&&+r.dataset.n<2)||(cf&&r.dataset.cat!==cf);
  });
}
function selShown(on){rows().forEach(function(r){var c=r.querySelector('.pick');if(!r.hidden)c.checked=on;});count();}
function count(){var n=document.querySelectorAll('.pick:checked').length,b=document.getElementById('go');b.disabled=!n;b.textContent='Add '+n+' product'+(n==1?'':'s');}
function hideRow(btn,gkey,act){
  var fd=new FormData();fd.append('gkey',gkey);fd.append('act',act);
  fetch('{{ url_for("compare_admin.ignore") }}',{method:'POST',body:fd}).then(function(){var r=btn.closest('.imp-row');r.remove();count();});
}
recalc();filt();count();
document.getElementById('imp').addEventListener('submit',function(e){
  var bad=0;rows().forEach(function(r){var c=r.querySelector('.pick');if(c.checked){var p=parseFloat(r.querySelector('.pr').value);if(!(p>0))bad++;else if(p<=parseFloat(r.dataset.cost))bad+=0.001;}});
  if(bad>=1){e.preventDefault();alert('Some selected products have no price.');return;}
  if(bad>0&&!confirm('Some selected products are priced at or below their cost (you would lose money). Add them anyway?'))e.preventDefault();
});
</script>
{% endif %}
{% endblock %}"""


def _ignored(db):
    try:
        v = json.loads(db.get_setting(IGNORE_KEY, "[]") or "[]")
        return set(v) if isinstance(v, list) else set()
    except (ValueError, TypeError):
        return set()


def auto_settings(db):
    def num(k, d):
        try:
            return float(db.get_setting(k, d))
        except (TypeError, ValueError):
            return d
    return {"on": str(db.get_setting("auto_import_on", "0")) == "1",
            "pct": num("auto_import_pct", 40.0), "fix": num("auto_import_fix", 0.0),
            "rnd": str(db.get_setting("auto_import_rnd", "five")),
            "instock": str(db.get_setting("auto_import_instock", "1")) == "1"}


def price_for(cost, pct, fix, rnd):
    p = cost * (1 + pct / 100.0) + fix
    if rnd == "five":
        p = math.ceil(p / 0.05 - 1e-9) * 0.05
    elif rnd == "x99":
        c = math.ceil(p - 1e-9)
        p = (c - 0.01) if (c - 0.01 >= p - 1e-9) else (c + 0.99)
    return round(max(p, cost + 0.01), 2)


def pending_groups(db, show_hidden=False, max_age=60):
    """Groups that are not in the shop yet. -> (sups, errs, groups, skipped, hidden_n, existing_cats)"""
    sups, errs = db.catalog_options(max_age=max_age)
    rules = make_rules(db.get_setting("compare_aliases", ""))
    linked = set()
    for d in db.sources_by_product().values():
        for o in d["offers"]:
            linked.add((o["supplier_id"], o["provider_key"]))
    products = db.list_products()
    existing_cats = sorted({(p["category"] or "").strip() for p in products if (p["category"] or "").strip()})
    have = set()
    for p in products:
        n = _norm(p["name"], rules)
        if n[1]:
            have.add(_gkey(n))
    ignored = _ignored(db)
    groups, skipped, hidden_n = [], 0, 0
    for g in build_groups(sups, existing_cats, rules):
        is_linked = any((o["sid"], o["key"]) in linked for o in g["offers"])
        if is_linked or g["gkey"] in have:
            skipped += 1
            continue
        if g["gkey"] in ignored:
            hidden_n += 1
            if not show_hidden:
                continue
        elif show_hidden:
            continue
        g["offers_json"] = json.dumps([{"sid": o["sid"], "key": o["key"]} for o in g["offers"]])
        groups.append(g)
    return sups, errs, groups, skipped, hidden_n, existing_cats


def import_one(db, name, price, category, offers, catalogs, names):
    """Create (or extend) one shop product from a group of supplier offers [(sid, key)].
    -> (added: bool, below_cost: bool, problems: [str])"""
    problems = []
    items = []
    for sid, key in offers:
        if sid not in catalogs:
            sup = db.get_supplier(sid)
            try:
                catalogs[sid] = {p["key"]: p for p in provider.catalog_for(sup, max_age=300)} if sup else {}
            except Exception:
                catalogs[sid] = {}
        it = catalogs[sid].get(key)
        if it:
            items.append((sid, key, it))
    if not items:
        return False, False, [f"{name}: supplier products not found any more"]
    items.sort(key=lambda t: (t[2]["stock"] <= 0, t[2]["cost"]))
    best_sid = items[0][0]
    desc = next(((t[2].get("description") or "") for t in items if t[2].get("description")), "")
    pid = names.get(name.casefold())
    existed = pid is not None
    if pid is None:
        pid = db.add_product(name, price, "auto")
        names[name.casefold()] = pid
    fields = {}
    if not existed:
        fields["price"] = price
        fields["delivery"] = "Instant"
        if category:
            fields["category"] = category
        if desc:
            fields["description"] = desc[:1000]
    if fields:
        db.update_product(pid, **fields)
    linked_any = False
    for sid, key, it in items:
        try:
            db.add_source(pid, sid, key)
            linked_any = True
        except Exception as e:
            problems.append(f"{name}: {str(e)[:100]}")
    if not linked_any:
        return False, False, problems
    db.set_source_mode(pid, "cheapest", best_sid, True)
    return True, price <= min(t[2]["cost"] for t in items), problems


def auto_import(db, limit=100):
    """The bot's own job: compare every supplier and add every new product. -> summary dict"""
    st = auto_settings(db)
    sups, errs, groups, _s, _h, _c = pending_groups(db, max_age=60)
    names = {re.sub(r"\s+", " ", p["name"]).strip().casefold(): p["id"] for p in db.list_products()}
    catalogs, added, below, problems, added_names = {}, 0, 0, [], []
    for g in groups:
        if added >= limit:
            break
        if st["instock"] and not g["instock"]:
            continue
        cost = g["best"]["cost"]
        if cost <= 0:
            continue
        price = price_for(cost, st["pct"], st["fix"], st["rnd"])
        try:
            ok, bc, pr = import_one(db, g["name"][:120], price, g["category"] or None,
                                    [(o["sid"], o["key"]) for o in g["offers"]], catalogs, names)
        except Exception as e:
            problems.append(f"{g['name']}: {str(e)[:100]}")
            continue
        problems += pr
        if ok:
            added += 1
            below += 1 if bc else 0
            added_names.append(g["name"])
    if added or problems:
        try:
            import alerts_admin
            shown = ", ".join(added_names[:6]) + (f" +{len(added_names) - 6} more" if len(added_names) > 6 else "")
            if added:
                alerts_admin.log_event(db, f"Auto mode added {added} product(s): {shown}"
                                           + (f" ({below} at or below cost)" if below else ""))
            for p in problems[:3]:
                alerts_admin.log_event(db, f"Auto mode problem: {p}")
        except Exception:
            pass
    return {"added": added, "below_cost": below, "problems": problems, "names": added_names, "errs": errs}


@bp.route("/compare")
def home():
    import web
    db = web.db
    show_hidden = request.args.get("hidden") == "1"
    sups, errs, groups, skipped, hidden_n, existing_cats = pending_groups(db, show_hidden)
    for e in errs:
        flash(e, "err")
    cat_list = sorted({g["category"] for g in groups if g["category"]} | set(existing_cats), key=str.casefold)
    return web.render_str(PAGE, sups=sups, groups=groups, cat_list=cat_list, skipped=skipped,
                          hidden_n=hidden_n, show_hidden=show_hidden, auto=auto_settings(db),
                          aliases_text=db.get_setting("compare_aliases", ""))


@bp.route("/compare/aliases", methods=["POST"])
def aliases_save():
    import web
    txt = (request.form.get("aliases") or "")[:4000]
    web.db.set_setting("compare_aliases", txt)
    flash("Smart matching saved.")
    return redirect(url_for("compare_admin.home"))


@bp.route("/compare/auto", methods=["POST"])
def auto_save():
    import web
    db = web.db
    f = request.form
    def fnum(k, d):
        try:
            return max(0.0, float(f.get(k, d)))
        except ValueError:
            return d
    db.set_setting("auto_import_on", "1" if f.get("on") else "0")
    db.set_setting("auto_import_pct", fnum("pct", 40.0))
    db.set_setting("auto_import_fix", fnum("fix", 0.0))
    rnd = f.get("rnd", "five")
    db.set_setting("auto_import_rnd", rnd if rnd in ("five", "x99", "none") else "five")
    db.set_setting("auto_import_instock", "1" if f.get("instock") else "0")
    flash("Auto mode saved. The bot will " + ("add new products by itself every 10 minutes." if f.get("on") else "not add products by itself."))
    return redirect(url_for("compare_admin.home"))


@bp.route("/compare/auto/run", methods=["POST"])
def auto_run():
    import web
    r = auto_import(web.db)
    flash(f"{r['added']} product{'s' if r['added'] != 1 else ''} added."
          + (f" {r['below_cost']} at or below cost." if r["below_cost"] else ""))
    for p in r["problems"][:6]:
        flash(p, "err")
    return redirect(url_for("products") if r["added"] else url_for("compare_admin.home"))


@bp.route("/compare/ignore", methods=["POST"])
def ignore():
    import web
    db = web.db
    gkey = (request.form.get("gkey") or "")[:300]
    cur = _ignored(db)
    if request.form.get("act") == "remove":
        cur.discard(gkey)
    elif gkey:
        cur.add(gkey)
    db.set_setting(IGNORE_KEY, json.dumps(sorted(cur)))
    return jsonify(ok=True)


@bp.route("/compare/create", methods=["POST"])
def create():
    import web
    db = web.db
    f = request.form
    names = {re.sub(r"\s+", " ", p["name"]).strip().casefold(): p["id"] for p in db.list_products()}
    catalogs = {}
    ok, problems, below_cost = 0, [], 0
    picks = [p for p in f.getlist("pick") if p.isdigit()][:300]
    for i in picks:
        name = re.sub(r"\s+", " ", f.get(f"name_{i}", "")).strip()[:120]
        category = (f.get(f"cat_{i}") or "").strip()[:60] or None
        try:
            price = round(float(f.get(f"price_{i}", "")), 2)
        except ValueError:
            price = 0
        try:
            raw = json.loads(f.get(f"offers_{i}", "[]"))
            offers = [(int(o["sid"]), str(o["key"])) for o in raw]
        except (ValueError, TypeError, KeyError):
            offers = []
        if not name or price <= 0 or not offers:
            problems.append(f"{name or '#' + i}: name and price are needed")
            continue
        try:
            added, bc, pr = import_one(db, name, price, category, offers, catalogs, names)
        except Exception as e:
            problems.append(f"{name}: {str(e)[:120]}")
            continue
        problems += pr
        if added:
            ok += 1
            below_cost += 1 if bc else 0
    if ok:
        flash(f"{ok} product{'s' if ok != 1 else ''} added. The cheapest supplier with stock is used automatically."
              + (f" {below_cost} priced at or below cost, check the prices." if below_cost else ""))
    for p in problems[:8]:
        flash(p, "err")
    if len(problems) > 8:
        flash(f"... and {len(problems) - 8} more problems.", "err")
    return redirect(url_for("products") if ok else url_for("compare_admin.home"))


def register(app):
    app.register_blueprint(bp)
