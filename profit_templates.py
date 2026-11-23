"""Templates for the Profit centre and the Replace product page (merged into web.TEMPLATES).

Design: a "profit statement" - the answer (net profit) first, then the lines that add up to it, like a
ledger. Activity is a list of signed lines (readable on a phone), forms open as bottom sheets.
"""

CSS = r"""<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700;800&display=swap" rel="stylesheet">
<style>
.pc,.pcbar,dialog.sheet{--mint:#8ff0c8;--coral:#ffa99a;--amber:#ffd08a;--violet:#cdbdff;--glass:rgba(255,255,255,.09);--edge:rgba(255,255,255,.2);--soft:rgba(255,255,255,.7)}
.pc{padding-bottom:8px}
.pc button,.pc a{-webkit-tap-highlight-color:transparent}
.pc .num,.pc .fig{font-family:'Bricolage Grotesque','Plus Jakarta Sans',system-ui,sans-serif;font-variant-numeric:tabular-nums;letter-spacing:-.03em}
.pc .pos{color:var(--mint)}.pc .neg{color:var(--coral)}.pc .zer{color:var(--soft)}

/* title row */
.pc-top{display:flex;align-items:flex-end;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:18px}
.pc-top h2{margin:0}
.pc-top p{margin:4px 0 0;color:var(--soft);font-size:14px}

/* action bar: inline on desktop, fixed to the bottom on phones */
.pcbar{display:flex;gap:8px;flex-wrap:wrap}
.pcbar:not(.inline){display:none}
.pb{width:auto;margin:0;display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:42px;padding:0 18px;border-radius:14px;font:inherit;font-size:14px;font-weight:700;line-height:1;cursor:pointer;text-decoration:none;border:1px solid var(--edge);background:var(--glass);color:#fff;transition:background .15s,transform .1s}
.pb:hover{background:rgba(255,255,255,.17);color:#fff;border-color:var(--edge);text-decoration:none}
.pb:active{transform:scale(.97)}
.pb.go{background:var(--mint);border-color:var(--mint);color:#06301f}
.pb.go:hover{background:#a6f5d6;border-color:#a6f5d6;color:#06301f}
.pb.out{background:var(--coral);border-color:var(--coral);color:#4a0f05}
.pb.out:hover{background:#ffbcb0;border-color:#ffbcb0;color:#4a0f05}
.pb svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}

/* statement */
.stm{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,6fr);gap:0;background:var(--glass);border:1px solid var(--edge);border-radius:26px;margin-bottom:16px;overflow:hidden;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}
.stm-l{padding:26px 28px}
.stm-r{padding:26px 28px 20px;border-left:1px solid var(--edge);background:rgba(10,6,40,.22);display:flex;flex-direction:column}
.stm-k{font-size:14px;color:var(--soft);margin:0}
.stm-net{display:flex;align-items:baseline;gap:4px;margin:2px 0 4px;line-height:1}
.stm-net .num{font-size:clamp(46px,7vw,68px);font-weight:800}
.stm-net .cur{font-size:26px;font-weight:700;opacity:.7}
.stm-m{color:var(--soft);font-size:14px;margin:0 0 18px}
.stm-lines{margin:0;padding:0;border-top:1px solid var(--edge)}
.stm-lines div{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:11px 0;border-bottom:1px dashed var(--edge);font-size:15px}
.stm-lines dt{color:#fff;font-weight:550}
.stm-lines dt small{color:var(--soft);font-weight:500;margin-left:6px;font-size:13px}
.stm-lines dd{margin:0;font-size:17px;font-weight:700}
.stm-sum{display:flex;justify-content:space-between;align-items:baseline;padding-top:12px;margin-top:-1px;border-top:2px solid var(--edge);font-weight:750}
.stm-sum .num{font-size:20px}
.stm-r h3{margin:0 0 2px;font-size:16px}
.dd svg{width:100%;height:170px;display:block;margin-top:12px;flex:1}
.dd .z{stroke:rgba(255,255,255,.35);stroke-width:1;stroke-dasharray:3 4}
.dd .b{fill:var(--mint)}.dd .l{fill:var(--coral)}
.dd .ax{display:flex;justify-content:space-between;font-size:12.5px;color:var(--soft);margin-top:8px}
.dd .best{font-size:13px;color:var(--soft);margin:0}

/* range + product filters */
.flt{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
.rng{display:flex;gap:4px;padding:4px;border-radius:14px;background:var(--glass);border:1px solid var(--edge);overflow-x:auto;max-width:100%;scrollbar-width:none}
.rng::-webkit-scrollbar{display:none}
.rng a{padding:8px 13px;border-radius:10px;color:var(--soft);font-weight:650;font-size:13.5px;white-space:nowrap;text-decoration:none}
.rng a:hover{color:#fff;text-decoration:none}
.rng a.on{background:#fff;color:#2a1a6e}
.flt select{width:auto;max-width:210px;margin:0;min-height:44px;border-radius:14px}
.flt details{position:relative}
.flt summary{list-style:none;cursor:pointer;min-height:44px;display:inline-flex;align-items:center;padding:0 14px;border-radius:14px;border:1px solid var(--edge);background:var(--glass);font-weight:650;font-size:13.5px}
.flt summary::-webkit-details-marker{display:none}
.flt .pop{position:absolute;z-index:5;top:50px;left:0;display:flex;gap:8px;align-items:center;padding:12px;border-radius:16px;background:#2a1d78;border:1px solid var(--edge);box-shadow:0 18px 50px #0009}
.flt .pop input{margin:0;width:150px}
.flt .pop button{width:auto;margin:0;padding:10px 16px}

/* section blocks */
.blk{background:var(--glass);border:1px solid var(--edge);border-radius:22px;padding:20px 22px;margin-bottom:16px;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}
.blk-h{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.blk-h h3{margin:0;font-size:18px}
.tabs2{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}
.tabs2::-webkit-scrollbar{display:none}
.tabs2 button{width:auto;margin:0;padding:7px 13px;border-radius:10px;border:0;background:transparent;color:var(--soft);font-size:13.5px;font-weight:650;white-space:nowrap}
.tabs2 button:hover{background:rgba(255,255,255,.1);color:#fff}
.tabs2 button.on{background:rgba(255,255,255,.2);color:#fff}

/* product rows with a share bar */
.pr{padding:13px 0;border-bottom:1px solid var(--edge)}
.pr:last-child{border-bottom:0;padding-bottom:2px}
.pr-t{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.pr-t b{font-weight:650}.pr-t .fig{font-size:18px;font-weight:750}
.pr-s{color:var(--soft);font-size:13px;margin-top:2px}
.pr-bar{height:6px;border-radius:9px;background:rgba(255,255,255,.12);margin-top:9px;overflow:hidden}
.pr-bar i{display:block;height:100%;border-radius:9px;background:var(--mint)}
.pr-bar i.l{background:var(--coral)}

/* ledger lines */
.lns{list-style:none;margin:0;padding:0}
.ln{display:grid;grid-template-columns:10px minmax(0,1fr) auto;gap:13px;align-items:center;padding:13px 0;border-bottom:1px solid var(--edge)}
.ln:last-child{border-bottom:0}
.ln .mk{width:10px;height:10px;border-radius:50%;background:#fff}
.ln .k-dm_sale{background:var(--violet)}.ln .k-replacement{background:var(--amber)}.ln .k-add{background:var(--mint)}.ln .k-minus{background:var(--coral)}.ln .k-api{background:#9fb7ff}
.ln-m{min-width:0}
.ln-m b{display:block;font-weight:650;overflow-wrap:anywhere}
.ln-m span{display:block;color:var(--soft);font-size:13px;margin-top:1px;overflow-wrap:anywhere}
.ln-a{text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.ln-a .fig{font-size:18px;font-weight:750}
.undo{all:unset;cursor:pointer;color:var(--soft);font-size:12.5px;text-decoration:underline;text-underline-offset:3px}
.undo:hover{color:#fff}
.none{padding:26px 6px;text-align:center;color:var(--soft)}
.chg{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 12px;padding:10px 0;border-bottom:1px solid var(--edge);font-size:14px}
.chg:last-child{border-bottom:0}.chg span{color:var(--soft);font-size:13px}

/* bottom sheets */
dialog.sheet{border:1px solid var(--edge);border-radius:26px;background:linear-gradient(165deg,#3b2b9a,#1c1457);color:#fff;padding:0;width:min(470px,calc(100vw - 20px));max-height:calc(100vh - 20px);overflow-y:auto;box-shadow:0 40px 100px #000b}
dialog.sheet::backdrop{background:#0009;backdrop-filter:blur(3px)}
dialog.sheet[open]{animation:sheet .22s ease-out}
@keyframes sheet{from{transform:translateY(28px);opacity:0}to{transform:none;opacity:1}}
@media(prefers-reduced-motion:reduce){dialog.sheet[open]{animation:none}}
.sheet form{margin:0;padding:24px 22px 22px}
.sheet h3{margin:0 0 2px;font-size:22px}
.sheet .hint{color:var(--soft);font-size:14px;margin:0 0 6px}
.sheet label{display:block;margin-top:14px;color:rgba(255,255,255,.85);font-size:13.5px;font-weight:650}
.sheet label small{font-weight:500;color:var(--soft)}
.sheet input,.sheet select,.sheet textarea{margin:6px 0 0;min-height:46px;border-radius:13px}
.sheet select option{color:#111;background:#fff}
.sheet .x{position:absolute;right:14px;top:14px;width:36px;height:36px;min-height:0;padding:0;margin:0;border-radius:50%;border:0;background:rgba(255,255,255,.14);color:#fff;font-size:20px;line-height:1}
.sheet .x:hover{background:rgba(255,255,255,.26)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.amtbox{display:flex;align-items:center;gap:6px;margin-top:8px;padding:2px 16px;border-radius:16px;background:rgba(0,0,0,.25);border:1px solid var(--edge)}
.amtbox span{font-size:30px;font-weight:800;white-space:nowrap;flex:none}
.amtbox input{border:0;background:transparent;box-shadow:none;margin:0;font-size:36px;font-weight:800;min-height:68px;padding:0 4px;font-family:'Bricolage Grotesque',system-ui,sans-serif}
.amtbox input:focus{box-shadow:none}
.amtbox.plus span,.amtbox.plus input{color:var(--mint)}.amtbox.minus span,.amtbox.minus input{color:var(--coral)}
.calc{margin:16px 0 4px;padding:13px 15px;border-radius:14px;background:rgba(255,255,255,.1);font-weight:700;font-size:15px}
.calc.good{background:rgba(143,240,200,.17);color:var(--mint)}.calc.bad{background:rgba(255,169,154,.18);color:var(--coral)}
.tick,.sheet label.tick,.it label.tick{display:flex;gap:10px;align-items:center;margin-top:14px;font-size:14.5px;font-weight:600}
.tick input{width:20px;height:20px;min-height:0;margin:0;flex:none}
.tick small{color:var(--soft);font-weight:500}
.sheet .save{width:100%;margin-top:18px;min-height:52px;border-radius:15px;font-size:16px}
.qk{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.qk button{width:auto;margin:0;min-height:0;padding:6px 12px;border-radius:99px;border:1px solid var(--edge);background:rgba(255,255,255,.08);color:#fff;font-size:13px;font-weight:600}
.qk button:hover{background:rgba(255,255,255,.2)}

/* replace page */
.find{display:flex;gap:10px;margin-bottom:16px}
.find input{margin:0;min-height:54px;border-radius:16px;font-size:16px}
.find button{width:auto;margin:0;padding:0 24px;border-radius:16px;min-height:54px}
.tk{background:var(--glass);border:1px solid var(--edge);border-radius:22px;margin-bottom:16px;overflow:hidden}
.tk-h{padding:18px 22px;border-bottom:1px dashed var(--edge)}
.tk-h b{font-size:18px;font-weight:700}
.tk-h p{margin:3px 0 0;color:var(--soft);font-size:13.5px;overflow-wrap:anywhere}
.tk-s{display:inline-block;margin-top:10px;padding:5px 12px;border-radius:99px;font-size:13px;font-weight:650;background:rgba(143,240,200,.16);color:var(--mint)}
.tk-s.low{background:rgba(255,169,154,.18);color:var(--coral)}
.it{padding:18px 22px;border-bottom:1px solid var(--edge)}
.it:last-child{border-bottom:0}
.it-v{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;word-break:break-all;padding:12px 14px;border-radius:12px;background:rgba(0,0,0,.25);margin:8px 0 12px}
.it-n{font-size:13px;color:var(--soft)}.it-n em{font-style:normal;color:var(--amber);margin-left:6px}
.it details{margin-top:12px}
.it summary{cursor:pointer;color:var(--soft);font-size:13.5px;font-weight:650}
.it .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.it label{display:block;margin-top:12px;font-size:13px;font-weight:650;color:rgba(255,255,255,.85)}
.it label small{font-weight:500;color:var(--soft)}
.it input,.it select,.it textarea{margin:6px 0 0;border-radius:12px}
.it .tick{margin-top:12px}
.old{color:var(--soft);text-decoration:line-through}

/* phones */
@media(max-width:760px){
.pc{padding-bottom:84px}
.stm{grid-template-columns:1fr;border-radius:22px}
.stm-l{padding:22px 20px}.stm-r{border-left:0;border-top:1px solid var(--edge);padding:20px 20px 16px}
.blk{padding:16px 16px;border-radius:20px}
.pcbar:not(.inline){display:flex;position:fixed;left:0;right:0;bottom:0;z-index:50;gap:6px;padding:10px 10px calc(10px + env(safe-area-inset-bottom,0px));background:rgba(26,16,82,.88);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border-top:1px solid var(--edge)}
.pcbar .pb{flex:1;padding:0 6px;font-size:13px;min-height:46px}
.pcbar .pb svg{display:none}
.pc-top .pcbar.inline{display:none}
dialog.sheet{margin:auto 0 0 0;width:100%;max-width:none;border-radius:26px 26px 0 0;max-height:92vh}
.flt .pop{left:auto;right:0}
.find{flex-direction:column}.find button{width:100%}
.it .grid2,.two{grid-template-columns:1fr}
.two.keep{grid-template-columns:1fr 1fr}
}
</style>"""

PLUS = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>'
MINUS = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg>'
SWAP = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4L3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7"/></svg>'
DL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 11l5 5 5-5M5 20h14"/></svg>'

BAR = (r"""<div class="pcbar">
<button type="button" class="pb go" data-open="dlg-dm">+ DM sale</button>
<button type="button" class="pb go" data-open="dlg-add">+ Profit</button>
<button type="button" class="pb out" data-open="dlg-minus">&minus; Profit</button>
<a class="pb" href="{{ url_for('replace_page') }}">Replace</a>
</div>""")

PROFIT_HTML = r"""{% extends 'base.html' %}{% block body %}
""" + CSS + r"""
<div class="pc">
<div class="pc-top"><div><h2>Profit centre</h2><p>{{ period }}{% if pid %} &middot; one product{% endif %}</p></div>
<div class="pcbar inline">
<button type="button" class="pb go" data-open="dlg-dm">""" + PLUS + r"""DM sale</button>
<button type="button" class="pb go" data-open="dlg-add">""" + PLUS + r"""Add profit</button>
<button type="button" class="pb out" data-open="dlg-minus">""" + MINUS + r"""Minus profit</button>
<a class="pb" href="{{ url_for('replace_page') }}">""" + SWAP + r"""Replace product</a>
<a class="pb" href="{{ url_for('profit_export', range=rng, product=pid, **{'from': dfrom, 'to': dto}) }}" aria-label="Download CSV">""" + DL + r"""CSV</a>
</div></div>

{% if d.nocost %}<div class="msg">{{ d.nocost }} sale{{ '' if d.nocost == 1 else 's' }} here {{ 'has' if d.nocost == 1 else 'have' }} no cost saved, so profit looks too high.
<form method="post" action="{{ url_for('profit_fill') }}" style="display:inline"><button class="pb" style="min-height:34px;padding:0 12px;margin-left:6px">Use today's cost</button></form></div>{% endif %}

<div class="flt">
<nav class="rng" aria-label="Period">{% for k, label in ranges %}<a href="{{ url_for('profit_centre', range=k, product=pid) }}" class="{{ 'on' if k == rng }}">{{ label }}</a>{% endfor %}</nav>
<form method="get" style="display:contents">
<details {{ 'open' if rng == 'custom' }}><summary>{{ 'Dates: ' ~ period if rng == 'custom' else 'Pick dates' }}</summary>
<div class="pop"><input type="date" name="from" value="{{ dfrom }}" aria-label="From"><input type="date" name="to" value="{{ dto }}" aria-label="To"><button class="pb go">Show</button></div></details>
<select name="product" onchange="this.form.submit()" aria-label="Product"><option value="">All products</option>{% for p in prods %}<option value="{{ p.id }}" {{ 'selected' if p.id == pid }}>{{ p.name }}</option>{% endfor %}</select>
{% if rng != 'custom' %}<input type="hidden" name="range" value="{{ rng }}">{% endif %}</form>
</div>

<section class="stm">
<div class="stm-l">
<p class="stm-k">Net profit</p>
<div class="stm-net"><span class="cur {{ 'neg' if d.net_profit < 0 else 'pos' }}">{{ '&minus;'|safe if d.net_profit < 0 }}$</span><span class="num {{ 'neg' if d.net_profit < 0 else 'pos' }}">{{ '%.2f'|format(d.net_profit|abs) }}</span></div>
<p class="stm-m">{{ '%.1f'|format(d.net_margin) }}% margin on ${{ '%.2f'|format(d.total_rev) }} sold &middot; {{ d.n + d.dm.n }} sales</p>
<dl class="stm-lines">
<div><dt>Bot &amp; API sales<small>{{ d.n }}</small></dt><dd class="num {{ 'neg' if d.sales_profit < 0 else 'pos' }}">{{ '%+.2f'|format(d.sales_profit) }}</dd></div>
<div><dt>DM sales<small>{{ d.dm.n }}</small></dt><dd class="num {{ 'neg' if d.dm.profit < 0 else ('pos' if d.dm.profit else 'zer') }}">{{ '%+.2f'|format(d.dm.profit) }}</dd></div>
<div><dt>Replacements<small>{{ d.repl_n }}</small></dt><dd class="num {{ 'neg' if d.repl_loss else 'zer' }}">{{ '%+.2f'|format(-d.repl_loss) }}</dd></div>
<div><dt>Profit you added<small>{{ d.add_n }}</small></dt><dd class="num {{ 'pos' if d.add_total else 'zer' }}">{{ '%+.2f'|format(d.add_total) }}</dd></div>
<div><dt>Profit you removed<small>{{ d.minus_n }}</small></dt><dd class="num {{ 'neg' if d.minus_total else 'zer' }}">{{ '%+.2f'|format(-d.minus_total) }}</dd></div>
</dl>
<div class="stm-sum"><span>Net profit</span><span class="num {{ 'neg' if d.net_profit < 0 else 'pos' }}">{{ '%+.2f'|format(d.net_profit) }}</span></div>
</div>
<div class="stm-r dd">
<h3>Day by day</h3>
{% set best = (d.days|sort(attribute='profit')|last) if d.days else none %}
<p class="best">{% if best and best.profit > 0 %}Best day {{ best.label }}: <b class="pos">{{ '%+.2f'|format(best.profit) }}</b>{% else %}No profit days in this period yet{% endif %}</p>
<svg viewBox="0 0 600 146" preserveAspectRatio="none" role="img" aria-label="Daily net profit">
<line class="z" x1="0" x2="600" y1="{{ d.zero_y }}" y2="{{ d.zero_y }}"/>
{% for x in d.days %}{% if x.profit %}<rect class="{{ 'b' if x.profit > 0 else 'l' }}" x="{{ x.x }}" y="{{ x.y }}" width="{{ x.w }}" height="{{ x.h }}" rx="2"><title>{{ x.label }}: {{ '%+.2f'|format(x.profit) }}</title></rect>{% endif %}{% endfor %}
</svg>
<div class="ax"><span>{{ d.days[0].label if d.days }}</span><span>{{ d.days[-1].label if d.days }}</span></div>
</div>
</section>

<section class="blk">
<div class="blk-h"><h3>Products</h3></div>
{% set mx = (d.by_product|map(attribute='profit')|map('abs')|list + [0.01])|max %}
{% for r in d.by_product %}<div class="pr"><div class="pr-t"><b>{{ r.name }}</b><span class="fig {{ 'neg' if r.profit < 0 else 'pos' }}">{{ '%+.2f'|format(r.profit) }}</span></div>
<div class="pr-s">{{ r.units }} sold &middot; {{ '%.2f'|format(r.rev) }} in, {{ '%.2f'|format(r.cost) }} cost{% if r.dm_units %} &middot; {{ r.dm_units }} via DM{% endif %}{% if r.loss %} &middot; {{ '%.2f'|format(r.loss) }} lost on replacements{% endif %}{% if r.adj %} &middot; {{ '%+.2f'|format(r.adj) }} adjusted{% endif %}</div>
<div class="pr-bar"><i class="{{ 'l' if r.profit < 0 }}" style="width:{{ (r.profit|abs / mx * 100)|round(1) }}%"></i></div></div>
{% else %}<div class="none">No sales in this period.</div>{% endfor %}
</section>

<section class="blk">
<div class="blk-h"><h3>Activity</h3>
<div class="tabs2" id="flt"><button type="button" class="on" data-f="all">All</button><button type="button" data-f="sale">Bot &amp; API</button><button type="button" data-f="dm_sale">DM</button><button type="button" data-f="replacement">Replaced</button><button type="button" data-f="adj">Add / minus</button></div></div>
<ul class="lns" id="acts">
{% for r in d.activity %}<li class="ln" data-k="{{ 'sale' if r.kind in ('sale','api') else ('adj' if r.kind in ('add','minus') else r.kind) }}">
<i class="mk k-{{ r.kind }}"></i>
<div class="ln-m"><b>{{ r.title or 'Entry' }}</b>
<span>{{ {'sale':'Bot','api':'API','dm_sale':'DM sale','add':'Added','minus':'Removed','replacement':'Replaced'}[r.kind] }}{% if r.kind in ('sale','api','dm_sale') and r.qty %} &middot; {{ r.qty }} &times; {{ '%.2f'|format(r.rev / r.qty) }}{% if r.cost %}, cost {{ '%.2f'|format(r.cost / r.qty) }}{% endif %}{% endif %}{% if r.ref %} &middot; {{ r.ref }}{% endif %}{% if r.note %} &middot; {{ r.note }}{% endif %} &middot; {{ r.when }}</span></div>
<div class="ln-a"><span class="fig {{ 'neg' if r.profit < 0 else ('pos' if r.profit else 'zer') }}">{{ '%+.2f'|format(r.profit) }}</span>
{% if r.eid %}<form method="post" action="{{ url_for('profit_entry_delete', eid=r.eid) }}" onsubmit="return confirm('Remove this entry? Your profit goes back to what it was.')" style="margin:0"><button class="undo">Undo</button></form>{% endif %}</div></li>
{% else %}<li class="none">Nothing here yet. Sales show up on their own. Use the buttons for DM sales or to change profit.</li>{% endfor %}
</ul></section>

{% if d.changes %}<section class="blk"><div class="blk-h"><h3>Price and cost changes</h3></div>
{% for r in d.changes %}<div class="chg"><b>{{ r.name }}</b><b class="fig">{{ '%.3f'|format(r.old_value) if r.old_value is not none else '-' }} &rarr; {{ '%.3f'|format(r.new_value) if r.new_value is not none else '-' }}</b><span>{{ r.field }}</span><span style="text-align:right">{{ (r.ts or '')[:16].replace('T',' ') }}</span></div>{% endfor %}</section>{% endif %}
</div>

""" + BAR + r"""

<dialog class="sheet" id="dlg-dm"><form method="post" action="{{ url_for('profit_dm') }}">
<button type="button" class="x" data-close aria-label="Close">&times;</button>
<h3>DM sale</h3><p class="hint">Something you sold outside the bot.</p>
<label>Product<select name="product" id="dm-prod"><option value="" data-price="" data-cost="" data-stock="0">Other (type a name)</option>
{% for p in prods %}<option value="{{ p.id }}" data-price="{{ p.price }}" data-cost="{{ '' if p.cost_now is none else p.cost_now }}" data-stock="{{ p.stock }}">{{ p.name }}</option>{% endfor %}</select></label>
<label id="dm-tw">What did you sell?<input name="title" maxlength="80" placeholder="e.g. Netflix 1 month"></label>
<div class="two keep"><label>Sold for (each)<input name="price" id="dm-price" type="number" step="0.001" min="0" inputmode="decimal" placeholder="0.00" required></label>
<label>It cost you (each)<input name="cost" id="dm-cost" type="number" step="0.001" min="0" inputmode="decimal" placeholder="0.00"></label></div>
<div class="two keep"><label>How many<input name="qty" id="dm-qty" type="number" min="1" value="1" inputmode="numeric" required></label>
<label>Date <small>(today if empty)</small><input name="day" type="date"></label></div>
<label>Customer or note <small>(optional)</small><input name="note" maxlength="300" placeholder="@username, how they paid"></label>
<label class="tick"><input type="checkbox" name="take_stock" value="1" id="dm-stock"><span>Take the item from stock and show it to me <small id="dm-sn"></small></span></label>
<div class="calc" id="dm-calc">Enter the price to see your profit</div>
<button class="pb go save" type="submit">Save DM sale</button></form></dialog>

<dialog class="sheet" id="dlg-add"><form method="post" action="{{ url_for('profit_adjust') }}">
<button type="button" class="x" data-close aria-label="Close">&times;</button>
<input type="hidden" name="kind" value="add">
<h3>Add profit</h3><p class="hint">Extra money that is not a normal sale.</p>
<div class="amtbox plus"><span>+$</span><input name="amount" type="number" step="0.01" min="0.01" inputmode="decimal" placeholder="0.00" required aria-label="Amount"></div>
<label>What is it for?<input name="note" maxlength="300" placeholder="e.g. old balance, bonus" required></label>
<div class="two keep"><label>Product <small>(optional)</small><select name="product"><option value="">None</option>{% for p in prods %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}</select></label>
<label>Date<input name="day" type="date"></label></div>
<button class="pb go save" type="submit">Add profit</button></form></dialog>

<dialog class="sheet" id="dlg-minus"><form method="post" action="{{ url_for('profit_adjust') }}">
<button type="button" class="x" data-close aria-label="Close">&times;</button>
<input type="hidden" name="kind" value="minus">
<h3>Minus profit</h3><p class="hint">A loss or expense you want to take off.</p>
<div class="amtbox minus"><span>&minus;$</span><input name="amount" type="number" step="0.01" min="0.01" inputmode="decimal" placeholder="0.00" required aria-label="Amount"></div>
<label>What is it for?<input name="note" id="mi-note" maxlength="300" placeholder="e.g. refund, ads" required></label>
<div class="qk"><button type="button" data-note="Refund">Refund</button><button type="button" data-note="Dead account">Dead account</button><button type="button" data-note="Ad spend">Ads</button><button type="button" data-note="Bank or network fee">Fee</button></div>
<div class="two keep"><label>Product <small>(optional)</small><select name="product"><option value="">None</option>{% for p in prods %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}</select></label>
<label>Date<input name="day" type="date"></label></div>
<button class="pb out save" type="submit">Take off profit</button></form></dialog>

<script>
(function(){
document.querySelectorAll('[data-open]').forEach(function(b){b.addEventListener('click',function(){var d=document.getElementById(b.dataset.open);if(d&&d.showModal)d.showModal();});});
document.querySelectorAll('dialog.sheet').forEach(function(d){
 d.addEventListener('click',function(e){if(e.target===d)d.close();});
 d.querySelectorAll('[data-close]').forEach(function(b){b.addEventListener('click',function(){d.close();});});});
document.querySelectorAll('.qk button').forEach(function(b){b.addEventListener('click',function(){document.getElementById('mi-note').value=b.dataset.note;});});
var sel=document.getElementById('dm-prod'),pr=document.getElementById('dm-price'),co=document.getElementById('dm-cost'),q=document.getElementById('dm-qty'),pv=document.getElementById('dm-calc'),tw=document.getElementById('dm-tw'),st=document.getElementById('dm-stock'),sn=document.getElementById('dm-sn');
function calc(){var p=parseFloat(pr.value),c=parseFloat(co.value),n=parseInt(q.value)||1;
 if(isNaN(p)){pv.textContent='Enter the price to see your profit';pv.className='calc';return;}
 if(isNaN(c)){c=parseFloat((sel.options[sel.selectedIndex]||{}).dataset.cost);if(isNaN(c))c=0;}
 var t=(p-c)*n;pv.textContent=n+' \u00d7 ('+p.toFixed(2)+' \u2212 '+c.toFixed(2)+') = '+(t>=0?'+':'\u2212')+Math.abs(t).toFixed(2);pv.className='calc '+(t>0?'good':(t<0?'bad':''));}
function pick(){var o=sel.options[sel.selectedIndex];tw.style.display=sel.value?'none':'block';
 if(sel.value){pr.value=o.dataset.price||'';co.value=o.dataset.cost||'';sn.textContent='('+(o.dataset.stock||0)+' in stock)';}else{sn.textContent='';st.checked=false;}
 st.disabled=!sel.value;calc();}
if(sel){sel.addEventListener('change',pick);[pr,co,q].forEach(function(x){x.addEventListener('input',calc);});pick();}
var f=document.getElementById('flt'),rows=document.querySelectorAll('#acts li[data-k]');
if(f)f.addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;
 f.querySelectorAll('button').forEach(function(x){x.classList.toggle('on',x===b);});
 rows.forEach(function(r){r.style.display=(b.dataset.f==='all'||r.dataset.k===b.dataset.f)?'':'none';});});
})();
</script>
{% endblock %}"""

REPLACE_HTML = r"""{% extends 'base.html' %}{% block body %}
""" + CSS + r"""
<div class="pc">
<div class="pc-top"><div><h2>Replace product</h2>
<p>Last 30 days: {{ st.n }} replaced{% if st.cost %}, <b class="neg">&minus;${{ '%.2f'|format(st.cost) }}</b> lost{% endif %}</p></div>
<a class="pb" href="{{ url_for('profit_centre') }}">Profit centre</a></div>

<form method="get" class="find"><input name="q" value="{{ q }}" placeholder="Order ID, @username or Telegram ID" autofocus aria-label="Find an order"><button class="pb go" type="submit">Find order</button></form>

{% if q and not found %}<div class="blk"><div class="none" style="padding:6px">No paid order matches <b>{{ q }}</b>. Check the ID on the Orders page.</div></div>{% endif %}

{% for o in found %}<section class="tk">
<div class="tk-h"><b>{{ o.pname or 'Deleted product' }}</b>
<p>{{ o.id }} &middot; {{ ('@' ~ o.username) if o.username else o.user_id }} &middot; paid ${{ '%.2f'|format(o.amount) }} for {{ o.quantity }} &middot; {{ (o.ts or '')[:10] }}</p>
<span class="tk-s {{ 'low' if not o.stock }}">{% if o.stock %}{{ o.stock }} in stock{% else %}No stock left, paste the new item yourself{% endif %}{% if o.cost_now is not none %} &middot; new item costs ${{ '%.2f'|format(o.cost_now) }}{% endif %}</span></div>
{% for it in o.its %}<form method="post" action="{{ url_for('replace_do') }}" class="it" onsubmit="return confirm('Replace this item and send the new one to the customer?')">
<input type="hidden" name="q" value="{{ q }}"><input type="hidden" name="item_row" value="{{ it.id }}">
<div class="it-n">Item {{ loop.index }}{% if it.swaps %}<em>replaced {{ it.swaps }}&times; before</em>{% endif %}</div>
<div class="it-v">{{ it.item }}</div>
<button class="pb go" type="submit">""" + SWAP + r"""Replace and send</button>
<details><summary>More options</summary>
<div class="grid2"><label>Reason<select name="reason"><option>Not working</option><option>Wrong password</option><option>Expired early</option><option>Banned or blocked</option><option>Other</option></select></label>
<label>Cost of new item <small>(empty = automatic, 0 = free)</small><input name="cost" type="number" step="0.001" min="0" inputmode="decimal" placeholder="automatic"></label></div>
<label>Use my own new item <small>(empty = next one from stock)</small><textarea name="manual" rows="2" placeholder="email:password"></textarea></label>
<label class="tick"><input type="checkbox" name="notify" value="1" checked><span>Send it to the customer on Telegram</span></label></details>
</form>{% else %}<div class="it"><p class="it-n" style="margin:0">This order has no saved items (made to order or API), so there is nothing to swap here. Book the loss with Minus profit in the Profit centre.</p></div>{% endfor %}
</section>{% endfor %}

<section class="blk"><div class="blk-h"><h3>Recent replacements</h3></div>
<ul class="lns">{% for r in recent %}<li class="ln"><i class="mk k-replacement"></i>
<div class="ln-m"><b><span class="old" style="display:inline">{{ (r.old_item or '')[:48] }}</span></b><span>now {{ (r.new_item or '')[:48] }}</span>
<span>{{ r.pname }} &middot; {{ r.order_id }} &middot; {{ ('@' ~ r.username) if r.username else r.user_id }}{% if r.reason %} &middot; {{ r.reason }}{% endif %} &middot; {{ (r.ts or '')[:16].replace('T',' ') }}</span></div>
<div class="ln-a"><span class="fig neg">&minus;{{ '%.2f'|format(r.cost or 0) }}</span></div></li>
{% else %}<li class="none">No replacements yet.</li>{% endfor %}</ul></section>
</div>
<script>
document.querySelectorAll('dialog.sheet').forEach(function(){});
</script>
{% endblock %}"""

TEMPLATES = {"profit.html": PROFIT_HTML, "replace.html": REPLACE_HTML}
