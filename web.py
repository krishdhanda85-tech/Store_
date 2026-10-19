import html as htmllib
import os
import hmac
import hashlib
import time
from datetime import datetime, timedelta, timezone

import requests

from flask import Flask, request, session, redirect, url_for, render_template, render_template_string, flash
from jinja2 import DictLoader

import provider
import tenancy
import sellers_web
from flask import g

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

tenancy.init_all()
db = tenancy.db                  # always the shop of the logged-in person (see guard() below)

app = Flask(__name__)
from reseller_api import api as reseller_api_blueprint
from reseller_admin import register as register_reseller_admin
app.register_blueprint(reseller_api_blueprint)
register_reseller_admin(app, db)
from suppliers_admin import register as register_suppliers_admin
register_suppliers_admin(app, db)
app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024   # QR uploads
app.secret_key = os.getenv("SECRET_KEY") or hashlib.sha256(
    ("shop-admin:" + ADMIN_PASSWORD).encode()
).hexdigest()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RAILWAY_ENVIRONMENT")),
)

# ---------------------------------------------------------------- templates
TEMPLATES = {
    "base.html": """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f1a24">
<title>{{ shop }} Admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script>try{var t=localStorage.getItem("theme");if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}</script>
<style>
:root{--bg:#f2f4f7;--surface:#fff;--ink:#121b26;--muted:#667085;--line:#e3e7ee;--line2:#cfd6e0;--field:#fff;--accent:#0b7a75;--accent2:#096560;--accent-soft:#e3f3f1;--link:#0b6b67;--paid:#12805c;--pend:#a86a0a;--fail:#c0352b;--errbg:#fdeceb;--grey:#e7ebf1;--greyink:#223042;--side:#0f1a24;--sidetxt:#a9b6c6;--sideon:#ffffff14;--bar:#bfe3df;--shadow:0 1px 2px #10203010,0 4px 14px #10203008}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0b121a;--surface:#131d28;--ink:#e8eef5;--muted:#8b99ab;--line:#213040;--line2:#2c3e52;--field:#0d1620;--accent:#12968c;--accent2:#0f8077;--accent-soft:#12312f;--link:#5fd6cb;--paid:#3ccf91;--pend:#f0b84a;--fail:#ff7b72;--errbg:#3a1d1f;--grey:#223245;--greyink:#dbe5f0;--side:#0a1119;--bar:#1d4a46;--shadow:none}}
:root[data-theme=dark]{--bg:#0b121a;--surface:#131d28;--ink:#e8eef5;--muted:#8b99ab;--line:#213040;--line2:#2c3e52;--field:#0d1620;--accent:#12968c;--accent2:#0f8077;--accent-soft:#12312f;--link:#5fd6cb;--paid:#3ccf91;--pend:#f0b84a;--fail:#ff7b72;--errbg:#3a1d1f;--grey:#223245;--greyink:#dbe5f0;--side:#0a1119;--bar:#1d4a46;--shadow:none}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;font-family:Manrope,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);line-height:1.55;font-size:15px}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
h2{font-size:26px;letter-spacing:-.02em;font-weight:800;margin:0 0 18px;line-height:1.2}
h3{font-size:16px;font-weight:700;margin:0 0 8px;letter-spacing:-.01em}
p{margin:0 0 10px}
code{background:var(--accent-soft);padding:2px 6px;border-radius:6px;font-size:13px;overflow-wrap:anywhere}

/* layout */
body.app{display:grid;grid-template-columns:248px minmax(0,1fr);min-height:100vh}
body.bare{display:grid;place-items:center;min-height:100vh;padding:20px}
main{width:100%;max-width:1120px;margin:0 auto;padding:30px 28px 70px}
body.bare main{max-width:420px;padding:0}
.side{position:sticky;top:0;height:100vh;overflow-y:auto;background:var(--side);padding:18px 12px 16px;display:flex;flex-direction:column}
.brand{display:flex;align-items:center;gap:10px;padding:4px 8px 14px;color:#fff;font-weight:800;font-size:16px;letter-spacing:-.01em}
.mark{width:32px;height:32px;border-radius:9px;background:var(--accent);color:#fff;display:grid;place-items:center;font-weight:800;flex:none}
.grp{margin:16px 10px 6px;font-size:12px;font-weight:600;color:#6f8096}
.side a.nl{display:flex;align-items:center;gap:11px;padding:9px 10px;border-radius:9px;color:var(--sidetxt);font-weight:600;font-size:14px;text-decoration:none}
.side a.nl:hover{background:var(--sideon);color:#fff}
.side a.nl.on{background:var(--sideon);color:#fff;box-shadow:inset 3px 0 0 #3fd0c4}
.side svg,.topbar svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex:none}
.side .badge{margin-left:auto}
.side .out{margin-top:auto;padding-top:14px}
.topbar{display:none}
.scrim{display:none}

/* surfaces */
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:16px;box-shadow:var(--shadow)}
.stats,.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:18px}
@media(min-width:760px){.stats,.grid{grid-template-columns:repeat(5,1fr)}}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;box-shadow:var(--shadow)}
.stat small{display:block;color:var(--muted);font-weight:600;font-size:13px}
.stat span{display:block;margin-top:4px;font-size:26px;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.stat.lead{grid-column:1/-1;background:var(--side);border-color:var(--side);padding:24px 26px;border-radius:16px}
.stat.lead small{color:#9fb0c3}
.stat.lead span{font-size:44px;color:#fff}

/* tables */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12.5px;white-space:nowrap}
td{font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:0}
tr:hover td{background:var(--bg)}
td form{margin:0 0 6px}
td input{min-width:92px;margin:0 0 6px}
td button{width:auto;padding:8px 14px;margin:0}
.paid{color:var(--paid)}.pending,.processing{color:var(--pend)}.failed,.refunded{color:var(--fail)}
td.paid,td.pending,td.processing,td.failed,td.refunded{font-weight:700}
td.paid::before,td.pending::before,td.processing::before,td.failed::before,td.refunded::before{content:'';display:inline-block;width:7px;height:7px;border-radius:50%;background:currentColor;margin-right:7px;vertical-align:1px}

/* forms */
input,textarea,select,button{width:100%;padding:10px 12px;margin:6px 0;border-radius:9px;border:1px solid var(--line2);background:var(--field);color:var(--ink);font:inherit;font-size:15px}
input:focus,textarea:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft);outline:0}
input::placeholder,textarea::placeholder{color:var(--muted);opacity:.8}
label{display:block;color:var(--muted);font-size:13px;font-weight:600;margin-top:10px}
button{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:700;cursor:pointer;transition:background .15s}
button:hover{background:var(--accent2);border-color:var(--accent2)}
button:active{transform:translateY(1px)}
.red{background:var(--fail);border-color:var(--fail)}.red:hover{background:var(--fail);filter:brightness(.92);border-color:var(--fail)}
.green{background:#17845b;border-color:#17845b}.green:hover{background:#126f4d;border-color:#126f4d}
.grey{background:var(--grey);border-color:var(--grey);color:var(--greyink)}.grey:hover{background:var(--line2);border-color:var(--line2)}
.sm{width:auto;padding:7px 12px;font-size:13px;margin:0}
.inline{display:flex;gap:8px;align-items:center}.inline input{margin:0}.inline button{width:auto;margin:0;white-space:nowrap}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.row{display:flex;gap:8px;flex-wrap:wrap}.row button{flex:1;min-width:110px}

/* misc */
.msg{padding:12px 14px;border-radius:10px;background:var(--accent-soft);border:1px solid var(--line);border-left:4px solid var(--accent);margin-bottom:16px;overflow-wrap:anywhere}
.msg.err,.err{background:var(--errbg);border-left-color:var(--fail)}
.muted{color:var(--muted);font-size:13px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all;font-size:13.5px}
.badge{display:inline-block;background:#f0b84a;color:#1b1405;border-radius:99px;padding:1px 8px;font-size:12px;font-weight:800;line-height:1.5}
.login{padding:30px 26px}
.login .mark{width:44px;height:44px;border-radius:12px;font-size:20px;margin-bottom:14px}
.login h2{font-size:22px;margin-bottom:4px}
.login button{margin-top:14px;padding:12px}

.page-head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.page-head h2{margin:0}
.seg{display:inline-flex;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:3px}
.seg a{padding:6px 13px;border-radius:7px;font-size:13px;font-weight:700;color:var(--muted);text-decoration:none}
.seg a:hover{color:var(--ink);text-decoration:none}
.seg a.on{background:var(--side);color:#fff}
.lead2{background:var(--side);color:#fff;border-radius:16px;padding:26px 28px;margin-bottom:12px;display:grid;grid-template-columns:1.2fr 1.2fr 1fr;gap:24px;align-items:start}
.lead2 small{color:#9fb0c3;font-weight:600;font-size:13px;display:block}
.lead2 .big{font-size:42px;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.15;margin-top:2px}
.lead2 .big.profit{color:#4fe0a6}.lead2 .big.loss{color:#ff8a82}
.lead2 .sub{color:#9fb0c3;font-size:13px;margin-top:4px}
.lead2 .mini{display:grid;gap:9px;font-size:14px}
.lead2 .mini div{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #ffffff1a;padding-bottom:9px}
.lead2 .mini div:last-child{border-bottom:0;padding-bottom:0}
.lead2 .mini span{color:#9fb0c3}.lead2 .mini b{font-variant-numeric:tabular-nums}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.chart{display:flex;align-items:flex-end;gap:4px;height:170px;margin-top:16px}
.chart .col{flex:1;min-width:0;height:100%;display:flex;flex-direction:column;justify-content:flex-end}
.chart .bar{background:var(--bar);border-radius:4px 4px 0 0;display:flex;align-items:flex-end;min-height:2px}
.chart .bar i{display:block;width:100%;background:var(--accent);border-radius:4px 4px 0 0}
.xl{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-top:8px}
.dot{display:inline-block;width:9px;height:9px;border-radius:3px;margin:0 5px 0 12px;vertical-align:0}
.dot.rev{background:var(--bar)}.dot.prof{background:var(--accent)}
.hint{font-weight:500;color:var(--muted);font-size:12.5px}
@media(max-width:900px){
body.app{display:block}
.side{position:fixed;top:0;bottom:0;left:0;width:274px;height:auto;transform:translateX(-102%);transition:transform .22s ease;z-index:40}
body.nav-open .side{transform:none}
.scrim{position:fixed;inset:0;background:#0008;z-index:30}
body.nav-open .scrim{display:block}
.topbar{display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:20;background:var(--side);color:#fff;padding:10px 14px;font-weight:800}
.topbar button{width:auto;margin:0;padding:7px 9px;background:transparent;border-color:#ffffff30;line-height:0}
.topbar .mark{width:28px;height:28px;border-radius:8px;font-size:14px}
main{padding:20px 14px 60px}
h2{font-size:22px}
.card{padding:16px;border-radius:12px}
.stat.lead span{font-size:34px}
.lead2{grid-template-columns:1fr;padding:20px;gap:18px}
.lead2 .big{font-size:34px}
.cols{grid-template-columns:1fr}
.chart{height:140px}
.stat span{font-size:22px}
.stats .stat:last-child:nth-child(odd){grid-column:1/-1}
.two{grid-template-columns:1fr}
}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body class="{{ 'app' if session.get('ok') else 'bare' }}">
{% macro ico(n) %}<svg viewBox="0 0 24 24" aria-hidden="true">{% if n=='home' %}<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>{% elif n=='box' %}<path d="M21 8l-9-5-9 5 9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/>{% elif n=='list' %}<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>{% elif n=='clock' %}<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>{% elif n=='card' %}<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18"/>{% elif n=='qr' %}<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M20 20v.01M17 20h-3"/>{% elif n=='users' %}<circle cx="9" cy="8" r="4"/><path d="M2 21c0-4 3-7 7-7s7 3 7 7"/><path d="M17 4a4 4 0 010 8M22 21c0-3-2-5.5-5-6.5"/>{% elif n=='help' %}<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5"/><path d="M5.6 5.6l3.9 3.9M14.5 14.5l3.9 3.9M18.4 5.6l-3.9 3.9M9.5 14.5l-3.9 3.9"/>{% elif n=='tag' %}<path d="M3 12V3h9l9 9-9 9-9-9z"/><circle cx="7.5" cy="7.5" r="1.2"/>{% elif n=='key' %}<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3"/>{% elif n=='shield' %}<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>{% elif n=='sliders' %}<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>{% elif n=='out' %}<path d="M9 21H5V3h4"/><path d="M16 17l5-5-5-5M21 12H9"/>{% elif n=='link' %}<path d="M10 14a5 5 0 007 0l3-3a5 5 0 00-7-7l-1 1"/><path d="M14 10a5 5 0 00-7 0l-3 3a5 5 0 007 7l1-1"/>{% elif n=='menu' %}<path d="M4 6h16M4 12h16M4 18h16"/>{% endif %}</svg>{% endmacro %}
{% macro nl(ep, label, icon, count=0, alt='') %}<a class="nl {{ 'on' if request.endpoint == ep or request.endpoint == alt }}" href="{{ url_for(ep) }}">{{ ico(icon) }}<span>{{ label }}</span>{% if count %}<span class="badge">{{ count }}</span>{% endif %}</a>{% endmacro %}
{% if session.get('ok') %}
<div class="topbar"><button type="button" onclick="document.body.classList.toggle('nav-open')" aria-label="Open menu">{{ ico('menu') }}</button><span class="mark">{{ shop[:1]|upper }}</span><span>{{ shop }}</span></div>
<div class="scrim" onclick="document.body.classList.remove('nav-open')"></div>
<aside class="side">
<div class="brand"><span class="mark">{{ shop[:1]|upper }}</span><span>{{ shop }}</span></div>
{{ nl('dashboard','Dashboard','home') }}
<div class="grp">Sales</div>
{{ nl('orders','Orders','list') }}
{{ nl('manual','Pending orders','clock',pending_count) }}
{{ nl('products','Products','box') }}
<div class="grp">Payments</div>
{{ nl('upipay','UPI payments','card',upi_pending) }}
{{ nl('upi','UPI setup','qr') }}
<div class="grp">Customers</div>
{{ nl('users','Users','users') }}
{{ nl('tickets','Support','help',open_tickets,'ticket') }}
{{ nl('userprices','User prices','tag') }}
<div class="grp">System</div>
{% if is_main %}{{ nl('sellers_admin.sellers','Sellers','users',pending_sellers) }}{% else %}{{ nl('connect','Bot & payments','link') }}{% endif %}
{{ nl('suppliers_admin.suppliers','Suppliers API','link') }}
{{ nl('reseller_admin.resellers','Resellers API','key') }}
{{ nl('admins','Admins','shield') }}
{{ nl('settings','Settings','sliders') }}
<div class="out">{{ nl('logout','Log out','out') }}</div>
</aside>
{% endif %}
<div class="content"><main>
{% with m = get_flashed_messages(with_categories=true) %}
{% for cat, text in m %}<div class="msg {{ 'err' if cat == 'err' else '' }}">{{ text }}</div>{% endfor %}
{% endwith %}
{% if session.get('ok') and not is_main and tstatus == 'pending' %}<div class="msg err"><b>Waiting for approval.</b> The platform owner has to approve your shop before your bot goes live. You can already fill in <a href="{{ url_for('connect') }}">Bot &amp; payments</a>.</div>{% endif %}
{% if session.get('ok') and not is_main and tstatus == 'active' and not bot_ready %}<div class="msg err"><b>Your bot is not connected yet.</b> Open <a href="{{ url_for('connect') }}">Bot &amp; payments</a> and add your bot token and Telegram ID.</div>{% endif %}
{% if session.get('ok') and shop_closed %}<div class="msg err"><b>The shop is CLOSED.</b> Customers cannot use the bot.
<form method="post" action="{{ url_for('shop_toggle') }}" style="margin:8px 0 0"><input type="hidden" name="state" value="open"><input type="hidden" name="back" value="{{ request.path }}"><button type="submit" class="green sm">Open the shop</button></form></div>{% endif %}
{% block body %}{% endblock %}
</main></div><div id="google_translate_element" style="position:fixed;right:14px;bottom:14px;z-index:9999;background:var(--card,#fff);padding:7px 10px;border-radius:12px;box-shadow:0 4px 18px #0002"><span style="font-size:12px;font-weight:700">🌐 Idioma / Language</span><div id="google_translate_inner"></div></div>
<script>
function googleTranslateElementInit(){new google.translate.TranslateElement({pageLanguage:'en',includedLanguages:'en,es',autoDisplay:false},'google_translate_inner');}
</script><script src="https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>
</body></html>""",
    "login.html": """{% extends 'base.html' %}{% block body %}
<div class="card login">
<div class="mark">{{ shop[:1]|upper }}</div>
<h2>{{ shop }}</h2>
<p class="muted">Sign in to manage your store.</p>
<form method="post">
<label for="em">E-mail <span class="muted">(sellers; leave empty if you are the platform owner)</span></label>
<input id="em" type="email" name="email" placeholder="you@example.com" autofocus>
<label for="pw">Password</label>
<input id="pw" type="password" name="password" placeholder="Password" required>
<button type="submit">Sign in</button>
</form>
{% if signup != 'closed' %}<p class="muted" style="margin:14px 0 0"><a href="{{ url_for('register_seller') }}">Want your own shop? Create a seller account</a></p>{% endif %}</div>{% endblock %}""",
    "dashboard.html": """{% extends 'base.html' %}{% block body %}
<div class="page-head"><h2>Dashboard</h2>
<div class="seg">{% for k, lbl in ranges %}<a href="{{ url_for('dashboard', range=k) }}" class="{{ 'on' if k == rng }}">{{ lbl }}</a>{% endfor %}</div></div>
{% if d.nocost %}<div class="msg">{{ d.nocost }} sale{{ '' if d.nocost == 1 else 's' }} in this period {{ 'has' if d.nocost == 1 else 'have' }} no cost price, so the full sale amount is counted as profit (this is why profit looks too high). <a href="{{ url_for('products') }}">Set the cost price</a> on the product and the real profit shows up, old sales included.</div>{% endif %}
<div class="lead2">
<div><small>Revenue (USDT)</small><div class="big">{{ '%.2f'|format(d.rev) }}</div><div class="sub">{{ d.n }} paid order{{ '' if d.n == 1 else 's' }}, {{ d.units }} item{{ '' if d.units == 1 else 's' }} sold</div></div>
<div><small>Profit (USDT)</small><div class="big {{ 'profit' if d.profit >= 0 else 'loss' }}">{{ '%.2f'|format(d.profit) }}</div><div class="sub">{{ '%.1f'|format(d.margin) }}% margin{% if d.net is not none %}, {{ '%.2f'|format(d.net) }} after referral payouts{% endif %}</div></div>
<div class="mini">
<div><span>Cost of goods</span><b>{{ '%.2f'|format(d.cost) }}</b></div>
<div><span>Bot sales</span><b>{{ '%.2f'|format(d.bot.rev) }}</b></div>
<div><span>Reseller API sales</span><b>{{ '%.2f'|format(d.api.rev) }}</b></div>
</div></div>
<div class="stats">
<div class="stat"><small>Paid orders</small><span>{{ d.n }}</span></div>
<div class="stat"><small>Items sold</small><span>{{ d.units }}</span></div>
<div class="stat"><small>To deliver</small><span class="processing">{{ d.processing }}</span></div>
<div class="stat"><small>Waiting for payment</small><span class="pending">{{ d.pending }}</span></div>
<div class="stat"><small>Failed</small><span class="failed">{{ d.failed }}</span></div>
<div class="stat"><small>Customers</small><span>{{ d.users }}</span></div>
<div class="stat"><small>New customers</small><span>{{ d.new_users }}</span></div>
<div class="stat"><small>Wallet balances held</small><span>{{ '%.2f'|format(d.wallets) }}</span></div>
<div class="stat"><small>Deposits received</small><span>{{ '%.2f'|format(d.dep_amt) }}</span></div>
<div class="stat"><small>Referral paid (all time)</small><span>{{ '%.2f'|format(d.ref) }}</span></div>
</div>
<div class="card"><div class="page-head" style="margin-bottom:0"><h3 style="margin:0">Daily sales, last {{ d.days|length }} days</h3>
<span class="muted"><i class="dot rev"></i>Revenue <i class="dot prof"></i>Profit</span></div>
<div class="chart">{% for x in d.days %}<div class="col" title="{{ x.label }}: revenue ${{ '%.2f'|format(x.rev) }}, profit ${{ '%.2f'|format(x.profit) }}"><div class="bar" style="height:{{ x.h }}%"><i style="height:{{ x.ph }}%"></i></div></div>{% endfor %}</div>
<div class="xl"><span>{{ d.days[0].label }}</span><span>{{ d.days[-1].label }}</span></div></div>
<div class="cols">
<div class="card"><h3>Best sellers</h3><div class="scroll"><table>
<tr><th>Product</th><th>Sold</th><th>Revenue</th><th>Profit</th></tr>
{% for t in d.top %}<tr><td>{{ t.name }}</td><td>{{ t.units }}</td><td>{{ '%.2f'|format(t.rev) }}</td><td><span class="{{ 'paid' if t.profit >= 0 else 'failed' }}">{{ '%.2f'|format(t.profit) }}</span></td></tr>
{% else %}<tr><td colspan="4">No sales in this period yet.</td></tr>{% endfor %}
</table></div></div>
<div class="card"><h3>Where sales come from</h3><div class="scroll"><table>
<tr><th>Source</th><th>Orders</th><th>Revenue</th><th>Cost</th><th>Profit</th></tr>
{% for name, r in [('Telegram bot', d.bot), ('Reseller API', d.api)] %}<tr><td>{{ name }}</td><td>{{ r.n }}</td><td>{{ '%.2f'|format(r.rev) }}</td><td>{{ '%.2f'|format(r.cost) }}</td><td><span class="{{ 'paid' if r.profit >= 0 else 'failed' }}">{{ '%.2f'|format(r.profit) }}</span></td></tr>{% endfor %}
</table></div>
{% if d.low %}<h3 style="margin-top:18px">Running low on stock</h3><div class="scroll"><table>
{% for p in d.low %}<tr><td>{{ p.name }}</td><td class="{{ 'failed' if p.stock == 0 else 'pending' }}">{{ 'Out of stock' if p.stock == 0 else (p.stock ~ ' left') }}</td></tr>{% endfor %}
</table></div><p class="muted" style="margin:10px 0 0"><a href="{{ url_for('products') }}">Add stock</a></p>{% endif %}</div>
</div>
<div class="card"><h3>Latest orders</h3>
{% include 'orders_table.html' %}</div>{% endblock %}""",
    "orders.html": """{% extends 'base.html' %}{% block body %}
<h2>Orders</h2>
<div class="card">{% include 'orders_table.html' %}</div>{% endblock %}""",
    "orders_table.html": """<div class="scroll"><table>
<tr><th>Order</th><th>User</th><th>Product</th><th>Qty</th><th>Amount</th><th>Status</th><th>Created</th></tr>
{% for o in rows %}<tr>
<td>{{ o.id }}</td>
<td>{{ ('@' ~ o.username) if o.username else o.user_id }}</td>
<td>{{ o.product or o.product_id }}</td>
<td>{{ o.quantity }}</td>
<td>{{ '%.2f'|format(o.amount) }}</td>
<td class="{{ o.status }}">{{ o.status }}{% if o.failure_reason %} ({{ o.failure_reason }}){% endif %}</td>
<td>{{ (o.created_at or '')[:16].replace('T',' ') }}</td>
</tr>{% else %}<tr><td colspan="7">No orders yet.</td></tr>{% endfor %}
</table></div>""",
    "admins.html": """{% extends 'base.html' %}{% block body %}
<h2>Bot admins</h2>
<div class="card"><div class="scroll"><table>
<tr><th>Telegram ID</th><th>Added</th><th></th></tr>
{% for a in rows %}<tr><td>{{ a.user_id }}</td>
<td>{{ (a.added_at or '')[:16].replace('T',' ') }}</td>
<td><form method="post" action="{{ url_for('remove_admin', uid=a.user_id) }}" onsubmit="return confirm('Remove this admin?')">
<button type="submit" class="red sm">Remove</button></form></td></tr>
{% else %}<tr><td colspan="3">No extra admins. (The owner always has access.)</td></tr>{% endfor %}
</table></div></div>
<div class="card"><h3>Add admin</h3>
<form method="post" action="{{ url_for('add_admin') }}">
<input name="uid" type="number" placeholder="Telegram numeric ID" required>
<button type="submit">Add admin</button></form></div>{% endblock %}""",
    "userprices.html": """{% extends 'base.html' %}{% block body %}
<h2>User-specific prices</h2>
<div class="card"><div class="scroll"><table>
<tr><th>Username</th><th>Product</th><th>Price</th><th></th></tr>
{% for r in rows %}<tr><td>@{{ r.username }}</td><td>{{ r.product or r.product_id }}</td>
<td>{{ '%.2f'|format(r.price) }}</td>
<td><form method="post" action="{{ url_for('delete_user_price') }}">
<input type="hidden" name="username" value="{{ r.username }}">
<input type="hidden" name="pid" value="{{ r.product_id }}">
<button type="submit" class="red sm">Delete</button></form></td></tr>
{% else %}<tr><td colspan="4">No user-specific prices set.</td></tr>{% endfor %}
</table></div></div>
<div class="card"><h3>Set user price</h3>
<form method="post" action="{{ url_for('set_user_price') }}">
<input name="username" placeholder="Telegram username (without @)" required>
<select name="pid" required>
{% for p in products %}<option value="{{ p.id }}">{{ p.id }} - {{ p.name }} (default {{ p.price }})</option>{% endfor %}
</select>
<input name="price" type="number" step="0.01" min="0" placeholder="Price (USDT)" required>
<button type="submit">Save</button></form></div>{% endblock %}""",
    "products.html": """{% extends 'base.html' %}{% block body %}
<style>
[hidden]{display:none!important}
.pbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.pbar input{margin:0;flex:1;min-width:160px}
.sumline{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.sumline span{background:var(--surface);border:1px solid var(--line);border-radius:99px;padding:5px 12px;font-size:13px;font-weight:700;color:var(--muted)}
.sumline span.w{color:var(--pend);border-color:var(--pend)}
.sumline span.b{color:var(--fail);border-color:var(--fail)}
.prod{background:var(--surface);border:1px solid var(--line);border-radius:14px;margin-bottom:10px;box-shadow:var(--shadow)}
.prod>summary{list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:12px;padding:14px 16px;flex-wrap:wrap}
.prod>summary::-webkit-details-marker{display:none}
.prod[open]>summary{border-bottom:1px solid var(--line)}
.pn{min-width:0;flex:1 1 200px}
.pn b{display:block;font-size:15.5px;overflow-wrap:anywhere}
.pn small{color:var(--muted);font-size:12.5px}
.pm{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.pill{border-radius:99px;padding:3px 10px;font-size:12.5px;font-weight:700;background:var(--grey);color:var(--greyink)}
.pill.ok{background:var(--accent-soft);color:var(--paid)}
.pill.low{background:#f0b84a33;color:var(--pend)}
.pill.out{background:var(--errbg);color:var(--fail)}
.pr{font-weight:800;font-variant-numeric:tabular-nums}
.pf{font-weight:800;font-size:13px;border-radius:8px;padding:3px 9px}
.pf.good{background:var(--accent-soft);color:var(--paid)}
.pf.bad{background:var(--errbg);color:var(--fail)}
.pf.warn{background:#f0b84a33;color:var(--pend)}
.pbody{padding:6px 16px 16px}
.sec{margin-top:16px;padding-top:14px;border-top:1px dashed var(--line2)}
.sec:first-child{margin-top:8px;padding-top:0;border-top:0}
.sec h4{margin:0 0 4px;font-size:14px;font-weight:800}
.profit{margin:10px 0 4px;padding:10px 12px;border-radius:10px;background:var(--grey);color:var(--greyink);font-weight:800;font-size:14px}
.profit.good{background:var(--accent-soft);color:var(--paid)}
.profit.bad{background:var(--errbg);color:var(--fail)}
.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:6px 0 2px}
.chips small{color:var(--muted);font-weight:600;font-size:12.5px}
.chip{width:auto;margin:0;padding:5px 11px;font-size:13px;border-radius:99px;background:var(--grey);border-color:var(--grey);color:var(--greyink)}
.chip:hover{background:var(--line2);border-color:var(--line2)}
.seg2{display:flex;gap:8px;margin:6px 0}
.seg2 label{flex:1;margin:0;display:flex;align-items:center;gap:8px;padding:10px 12px;border:1px solid var(--line2);border-radius:10px;cursor:pointer;color:var(--ink);font-size:14px}
.seg2 input{width:auto;margin:0}
.sub{margin-top:12px;border:1px solid var(--line);border-radius:10px;background:var(--bg)}
.sub>summary{cursor:pointer;padding:10px 12px;font-weight:700;font-size:13.5px;color:var(--muted)}
.sub>div{padding:0 12px 12px}
.srow{display:flex;gap:8px;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);padding:6px 0}
.srow .mono{font-size:12.5px;min-width:0;flex:1}
.srow form{margin:0}
.srow button{margin:0}
.note{background:#f0b84a22;border:1px solid #f0b84a77;border-radius:10px;padding:10px 12px;margin-bottom:14px;font-size:14px}
</style>
{% set ns = namespace(out=0, nc=0) %}
{% for p in products %}{% set u0 = p.cost if p.cost is not none else p.api_cost %}{% if u0 is not number %}{% set ns.nc = ns.nc + 1 %}{% endif %}{% if p.kind != 'manual' and p.stock == 0 %}{% set ns.out = ns.out + 1 %}{% endif %}{% endfor %}
{% set cats = products|map(attribute='category')|select|unique|list %}
<datalist id="cats">{% for c in cats %}<option value="{{ c }}">{% endfor %}</datalist>

<div class="page-head"><h2>Products</h2>
<button class="sm" type="button" onclick="var b=document.getElementById('addbox');b.hidden=!b.hidden;if(!b.hidden){b.scrollIntoView({behavior:'smooth'});}">+ Add product</button></div>

<div class="sumline"><span>{{ products|length }} products</span>
{% if ns.out %}<span class="b">{{ ns.out }} out of stock</span>{% endif %}
{% if ns.nc %}<span class="w">{{ ns.nc }} without cost</span>{% endif %}</div>

{% if ns.nc %}<div class="note"><b>{{ ns.nc }} product{{ '' if ns.nc == 1 else 's' }} without a cost price.</b> Their sales are counted as 100% profit on the dashboard. Open the product marked <b>Set cost</b> and enter what you paid.</div>{% endif %}

<div class="card" id="addbox" {% if products %}hidden{% endif %}>
<h3>Add new product</h3>
<form method="post" action="{{ url_for('create_product') }}" data-pf oninput="calc(this)">
<label>Product name</label>
<input name="name" placeholder="e.g. Netflix 1 month" required>
<div class="two">
<div><label>Cost - what you paid (USDT)</label><input name="cost" type="number" step="0.01" min="0" inputmode="decimal" placeholder="0.90" required></div>
<div><label>Selling price (USDT)</label><input name="price" type="number" step="0.01" min="0" inputmode="decimal" placeholder="1.10" required></div>
</div>
<div class="chips"><small>Profit:</small>{% for x in ['0.10','0.20','0.50','1.00'] %}<button type="button" class="chip" data-add="{{ x }}" onclick="chip(this)">+{{ x }}</button>{% endfor %}</div>
<div class="profit">Profit per item: enter cost and price</div>
<label>Type</label>
<div class="seg2">
<label><input type="radio" name="kind" value="auto" checked onchange="document.getElementById('newstock').hidden=false"> Auto delivery</label>
<label><input type="radio" name="kind" value="manual" onchange="document.getElementById('newstock').hidden=true"> Made to order</label>
</div>
<div id="newstock"><label>Stock - one item per line (login / key / link) <span class="hint">optional now, you can add later</span></label>
<textarea name="links" rows="5" placeholder="email:password&#10;email:password"></textarea></div>
<label>Category <span class="hint">optional, same category = one button group in the bot</span></label>
<input name="category" list="cats" placeholder="e.g. Streaming">
<button type="submit">Add product</button>
</form></div>

{% if products|length > 6 %}<div class="pbar"><input type="search" placeholder="Search products..." oninput="filt(this.value)"></div>{% endif %}

{% for p in products %}
{% set u = p.cost if p.cost is not none else p.api_cost %}
{% set its = items.get(p.id, []) %}
<details class="prod" id="p{{ p.id }}" data-name="{{ (p.name ~ ' ' ~ (p.category or ''))|lower }}" {% if open_id == p.id %}open{% endif %}>
<summary>
<div class="pn"><b>{{ p.name }}</b><small>{{ p.category or 'No category' }} · {{ 'Made to order' if p.kind == 'manual' else 'Auto delivery' }}</small></div>
<div class="pm">
{% if p.kind != 'manual' %}<span class="pill {{ 'out' if p.stock == 0 else ('low' if p.stock <= 3 else 'ok') }}">{{ 'Out of stock' if p.stock == 0 else (p.stock ~ ' in stock') }}</span>{% endif %}
<span class="pr">${{ '%.2f'|format(p.price) }}</span>
{% if u is number %}<span class="pf {{ 'good' if p.price - u > 0 else 'bad' }}">{{ '+' if p.price - u >= 0 else '-' }}${{ '%.2f'|format((p.price - u)|abs) }}</span>{% else %}<span class="pf warn">Set cost</span>{% endif %}
</div>
</summary>
<div class="pbody">

<div class="sec">
<h4>Price and cost</h4>
<form method="post" action="{{ url_for('quick_product', pid=p.id) }}" data-pf oninput="calc(this)">
<div class="two">
<div><label>Cost - what you paid (USDT)</label><input name="cost" type="number" step="0.01" min="0" inputmode="decimal" value="{{ p.cost if p.cost is not none else '' }}" placeholder="{{ ('%.2f' % p.api_cost) if p.api_key else '0.90' }}" {{ '' if p.api_key else 'required' }}></div>
<div><label>Selling price (USDT)</label><input name="price" type="number" step="0.01" min="0" inputmode="decimal" value="{{ p.price }}" required></div>
</div>
<div class="chips"><small>Profit:</small>{% for x in ['0.10','0.20','0.50','1.00'] %}<button type="button" class="chip" data-add="{{ x }}" onclick="chip(this)">+{{ x }}</button>{% endfor %}</div>
<div class="profit">Profit per item</div>
<button type="submit">Save price</button>
</form>
</div>

{% if p.kind != 'manual' %}
<div class="sec">
<h4>Add stock</h4>
<form method="post" action="{{ url_for('stock_add', pid=p.id) }}">
<textarea name="links" rows="4" placeholder="One item per line (login / key / link)" required></textarea>
<button type="submit">Add stock</button>
</form>
{% if its %}
<details class="sub"><summary>View or remove stock ({{ p.stock if not p.api_key else (p.stock - p.api_stock) }} own items)</summary><div>
{% for it in its %}<div class="srow"><span class="mono">{{ it.link[:70] }}{{ '…' if it.link|length > 70 }}</span>
<form method="post" action="{{ url_for('stock_delete', pid=p.id, sid=it.id) }}"><button class="red sm" type="submit">Remove</button></form></div>{% endfor %}
<form method="post" action="{{ url_for('stock_clear', pid=p.id) }}" onsubmit="return confirm('Remove ALL unsold stock of this product? Sold items stay in order history.')" style="margin-top:12px">
<button class="red" type="submit">Remove all unsold stock</button></form>
</div></details>
{% endif %}
</div>
{% endif %}

<details class="sub"><summary>More settings (name, category, description, delivery...)</summary><div>
<form method="post" action="{{ url_for('edit_product', pid=p.id) }}">
<input type="hidden" name="price" value="{{ p.price }}"><input type="hidden" name="cost" value="{{ p.cost if p.cost is not none else '' }}">
<label>Name</label><input name="name" value="{{ p.name }}" required>
<label>Old price <span class="hint">optional, shows a % discount in the bot</span></label>
<input name="old_price" type="number" step="0.01" min="0" value="{{ p.old_price or '' }}">
<label>Category <span class="hint">same category = grouped under one button, empty = listed directly</span></label>
<input name="category" list="cats" value="{{ p.category or '' }}">
<label>Type</label>
<select name="kind">
<option value="auto" {{ 'selected' if p.kind != 'manual' }}>Automatic delivery (from stock)</option>
<option value="manual" {{ 'selected' if p.kind == 'manual' }}>Made to order (customer sends an email, you deliver by hand)</option>
</select>
<label>Delivery time <span class="hint">shown to the customer</span></label>
<input name="delivery" value="{{ p.delivery or '' }}">
<label>Warranty <span class="hint">e.g. 90 days, empty shows None</span></label>
<input name="warranty" value="{{ p.warranty or '' }}">
<label>Question asked before buying <span class="hint">made-to-order only</span></label>
<input name="prompt" value="{{ p.prompt or '' }}" placeholder="Send an email that you haven't linked to Spotify before.">
<label>Description <span class="hint">one point per line</span></label>
<textarea name="description" rows="4">{{ p.description or '' }}</textarea>
<button type="submit">Save changes</button>
</form>
{% if p.kind != 'manual' %}
{% set src = sources.get(p.id) %}
<div style="margin-top:14px">
<label>Suppliers (API) {% if p.api_key %}<span class="muted">· active cost ${{ '%.2f' % p.api_cost }} · profit ${{ '%.2f' % (p.price - p.api_cost) }}/item</span>{% endif %}</label>
{% if src %}
<div class="scroll"><table>
<tr><th>Supplier</th><th>Product key</th><th>Cost</th><th>Stock</th><th>Profit</th><th></th></tr>
{% for o in src.offers %}<tr>
<td>{{ o.sname }}{% if src.mode == 'manual' and src.chosen == o.supplier_id %} <span class="muted">(first choice)</span>{% endif %}</td>
<td><code>{{ o.provider_key }}</code></td><td>${{ '%.2f' % o.cost }}</td><td>{{ o.stock }}</td>
<td class="{{ 'paid' if p.price > o.cost else 'failed' }}">${{ '%.2f' % (p.price - o.cost) }}</td>
<td><form method="post" action="{{ url_for('source_edit', pid=p.id) }}"><input type="hidden" name="action" value="remove"><input type="hidden" name="source_id" value="{{ o.id }}"><button class="red sm" type="submit">Remove</button></form></td>
</tr>{% endfor %}
</table></div>
<form method="post" action="{{ url_for('source_edit', pid=p.id) }}" style="margin-top:10px">
<input type="hidden" name="action" value="mode">
<label>Which supplier to buy from</label>
<select name="mode">
<option value="manual" {{ 'selected' if src.mode == 'manual' }}>I choose the supplier</option>
<option value="cheapest" {{ 'selected' if src.mode == 'cheapest' }}>Automatically the cheapest one that has stock</option>
</select>
<label>My choice <span class="hint">used first when "I choose"</span></label>
<select name="chosen">{% for o in src.offers %}<option value="{{ o.supplier_id }}" {{ 'selected' if src.chosen == o.supplier_id }}>{{ o.sname }} · ${{ '%.2f' % o.cost }} · stock {{ o.stock }}</option>{% endfor %}</select>
<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" name="fallback" value="1" style="width:auto" {{ 'checked' if src.fallback }}> If it fails or is out of stock, try the other suppliers</label>
<button type="submit">Save</button></form>
{% endif %}
{% if suppliers_cat %}
<form method="post" action="{{ url_for('source_edit', pid=p.id) }}" style="margin-top:10px">
<input type="hidden" name="action" value="add">
<label>Add a supplier product</label>
<select name="offer" required>
<option value="">- choose -</option>
{% for sp in suppliers_cat %}<optgroup label="{{ sp.name }}">{% for a in sp['items'] %}<option value="{{ sp.id }}|{{ a.key }}">{{ a.name }} · cost ${{ '%.2f' % a.cost }} · stock {{ a.stock }}</option>{% endfor %}</optgroup>{% endfor %}
</select>
<button type="submit">Add</button></form>
{% else %}<p class="muted" style="margin-top:8px">No supplier connected yet. <a href="{{ url_for('suppliers_admin.suppliers') }}">Add a supplier</a>.</p>{% endif %}
</div>
{% endif %}
</div></details>

<form method="post" action="{{ url_for('delete_product', pid=p.id) }}" onsubmit="return confirm('Delete this product? It disappears from the shop. Old orders keep their history.')" style="margin-top:14px">
<button class="red" type="submit">Delete product</button></form>
</div>
</details>
{% else %}<div class="card">No products yet. Use the form above to add your first one.</div>{% endfor %}

<script>
function calc(f){
  var o=f.querySelector('.profit');if(!o)return;
  var c=parseFloat(f.cost.value),p=parseFloat(f.price.value);
  if(isNaN(p)){o.textContent='Profit per item: enter the price';o.className='profit';return;}
  if(isNaN(c)){o.textContent='Profit per item: enter the cost to see it';o.className='profit';return;}
  var d=p-c,m=p>0?Math.round(d/p*100):0;
  o.textContent='Profit per item: '+(d<0?'-':'')+'$'+Math.abs(d).toFixed(2)+' ('+m+'% margin)';
  o.className='profit '+(d>0?'good':(d<0?'bad':''));
}
function chip(b){
  var f=b.form,c=parseFloat(f.cost.value);
  if(isNaN(c)){f.cost.focus();return;}
  f.price.value=(c+parseFloat(b.dataset.add)).toFixed(2);calc(f);
}
function filt(q){
  q=q.toLowerCase();
  document.querySelectorAll('details.prod').forEach(function(d){d.hidden=q&&d.dataset.name.indexOf(q)<0;});
}
document.querySelectorAll('form[data-pf]').forEach(calc);
if(location.hash){var t=document.querySelector(location.hash);if(t&&t.tagName==='DETAILS'){t.open=true;t.scrollIntoView();}}
</script>
{% endblock %}""",
    "manual.html": """{% extends 'base.html' %}{% block body %}
<h2>Pending orders</h2>
<p class="muted">Made-to-order items to deliver by hand. Marking an order done (or refunding it) tells the customer in the bot - once.</p>
{% for o in rows %}
<div class="card">
<b>{{ o.product or o.product_id }}</b> <span class="muted">· ${{ '%.2f'|format(o.amount) }} · {{ o.id }} · {{ (o.created_at or '')[:16].replace('T',' ') }}</span><br>
<span class="muted">Customer: {{ ('@' ~ o.username) if o.username else o.user_id }} ({{ o.user_id }})</span>
<div class="row" style="margin-top:8px;align-items:center">
<span class="mono" id="c{{ loop.index }}" style="flex:1">{{ o.contact }}</span>
<button type="button" class="grey" style="flex:0 0 auto;min-width:80px" onclick="navigator.clipboard.writeText(document.getElementById('c{{ loop.index }}').innerText);this.innerText='Copied'">Copy</button>
</div>
<form method="post">
<input name="note" placeholder="Message to the customer (optional)">
<div class="row">
<button class="green" formaction="{{ url_for('manual_done', oid=o.id) }}">Mark done</button>
<button class="grey" formaction="{{ url_for('manual_note', oid=o.id) }}">Send note only</button>
<button class="red" formaction="{{ url_for('manual_reject', oid=o.id) }}" onclick="return confirm('Refund this order to the customer wallet?')">Refund</button>
</div></form></div>
{% else %}<div class="card">Nothing to deliver right now.</div>{% endfor %}{% endblock %}""",
    "users.html": """{% extends 'base.html' %}{% block body %}
<style>
[hidden]{display:none!important}
.pbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.pbar input{margin:0;flex:1;min-width:160px}
.pbar select{width:auto;margin:0}
.sumline{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.sumline span{background:var(--surface);border:1px solid var(--line);border-radius:99px;padding:5px 12px;font-size:13px;font-weight:700;color:var(--muted)}
.prod{background:var(--surface);border:1px solid var(--line);border-radius:14px;margin-bottom:10px;box-shadow:var(--shadow)}
.prod>summary{list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:12px;padding:14px 16px;flex-wrap:wrap}
.prod>summary::-webkit-details-marker{display:none}
.prod[open]>summary{border-bottom:1px solid var(--line)}
.pn{min-width:0;flex:1 1 200px}
.pn b{display:block;font-size:15.5px;overflow-wrap:anywhere}
.pn small{color:var(--muted);font-size:12.5px}
.pm{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.pill{border-radius:99px;padding:3px 10px;font-size:12.5px;font-weight:700;background:var(--grey);color:var(--greyink)}
.pill.ok{background:var(--accent-soft);color:var(--paid)}
.pill.zero{background:var(--grey);color:var(--muted)}
.pr{font-weight:800;font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
.pbody{padding:6px 16px 16px}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin:10px 0 4px}
.facts div{background:var(--bg);border-radius:10px;padding:9px 12px}
.facts small{display:block;color:var(--muted);font-weight:600;font-size:12px}
.facts b{font-size:16px;font-variant-numeric:tabular-nums}
.sec{margin-top:14px;padding-top:12px;border-top:1px dashed var(--line2)}
.sec h4{margin:0 0 4px;font-size:14px;font-weight:800}
.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:6px 0 2px}
.chip{width:auto;margin:0;padding:5px 11px;font-size:13px;border-radius:99px;background:var(--grey);border-color:var(--grey);color:var(--greyink)}
.chip:hover{background:var(--line2);border-color:var(--line2)}
.btns{display:flex;gap:8px}.btns button{flex:1}
</style>
<div class="page-head"><h2>Users</h2></div>
<div class="sumline"><span>{{ tot.n }} users</span><span>Wallets hold ${{ '%.2f'|format(tot.bal or 0) }}</span><span>Total spent ${{ '%.2f'|format(tot.spent or 0) }}</span></div>
<form method="get" class="pbar">
<input name="q" value="{{ q }}" placeholder="Search @username or Telegram ID" type="search">
<select name="sort" onchange="this.form.submit()">
{% for k, lbl in [('new','Newest'),('spent','Top spenders'),('balance','Highest balance')] %}<option value="{{ k }}" {{ 'selected' if sort == k }}>{{ lbl }}</option>{% endfor %}
</select>
<button type="submit" class="sm">Search</button>
</form>
{% for u in rows %}
<details class="prod" {% if rows|length == 1 %}open{% endif %}>
<summary>
<div class="pn"><b>{{ ('@' ~ u.username) if u.username else 'No username' }}</b><small>ID {{ u.user_id }} · joined {{ (u.created_at or '')[:10] }}</small></div>
<div class="pm"><span class="pill {{ 'ok' if (u.balance or 0) > 0 else 'zero' }}">Balance ${{ '%.2f'|format(u.balance or 0) }}</span><span class="pr">Spent ${{ '%.2f'|format(u.spent or 0) }}</span></div>
</summary>
<div class="pbody">
<div class="facts">
<div><small>Balance</small><b>${{ '%.2f'|format(u.balance or 0) }}</b></div>
<div><small>Total spent</small><b>${{ '%.2f'|format(u.spent or 0) }}</b></div>
<div><small>Paid orders</small><b>{{ u.n_orders }}</b></div>
<div><small>Referral earned</small><b>${{ '%.2f'|format(u.ref_earned or 0) }}</b></div>
</div>
<div class="sec">
<h4>Add or remove wallet money</h4>
<form method="post" action="{{ url_for('topup', uid=u.user_id) }}">
<input name="amount" type="number" step="0.01" min="0.01" inputmode="decimal" placeholder="Amount (USDT)" required>
<div class="chips">{% for x in ['1','5','10','20','50'] %}<button type="button" class="chip" onclick="this.form.amount.value='{{ x }}'">${{ x }}</button>{% endfor %}</div>
<input name="note" placeholder="Note to the customer (optional)">
<div class="btns"><button type="submit" name="dir" value="add" class="green">Add money</button>
<button type="submit" name="dir" value="remove" class="red">Remove money</button></div>
</form>
</div>
<div class="sec"><button type="button" class="chip" onclick="navigator.clipboard&&navigator.clipboard.writeText('{{ u.user_id }}');this.textContent='Copied'">Copy Telegram ID</button></div>
</div>
</details>
{% else %}<div class="card">No users found.</div>{% endfor %}
{% if rows|length >= 50 %}<p class="muted">Showing the first 50. Use search to find someone specific.</p>{% endif %}
{% endblock %}""",
    "tickets.html": """{% extends 'base.html' %}{% block body %}
<style>
.pbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.pbar input{margin:0;flex:1;min-width:160px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.tabs a{padding:7px 14px;border-radius:99px;font-size:13.5px;font-weight:700;color:var(--muted);background:var(--surface);border:1px solid var(--line);text-decoration:none}
.tabs a.on{background:var(--side);border-color:var(--side);color:#fff}
.tabs a b{margin-left:5px;font-variant-numeric:tabular-nums}
.tk{display:block;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin-bottom:10px;box-shadow:var(--shadow);color:var(--ink);text-decoration:none}
.tk:hover{border-color:var(--accent);text-decoration:none}
.tk.wait{border-left:4px solid var(--pend)}
.tk .top{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}
.tk .who{font-weight:800;overflow-wrap:anywhere}
.tk .who span{color:var(--muted);font-weight:600;font-size:12.5px;margin-left:6px}
.tk .prev{color:var(--muted);font-size:14px;margin-top:6px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:anywhere}
.tk .meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;align-items:center}
.pill{border-radius:99px;padding:3px 10px;font-size:12px;font-weight:700;background:var(--grey);color:var(--greyink)}
.pill.wait{background:#f0b84a33;color:var(--pend)}
.pill.ok{background:var(--accent-soft);color:var(--paid)}
.pill.done{background:var(--grey);color:var(--muted)}
</style>
<div class="page-head"><h2>Support</h2></div>
<div class="tabs">
{% for k, lbl, n in [('open','Open',cnt.open),('closed','Closed',cnt.closed),('all','All',cnt.all)] %}<a href="{{ url_for('tickets', status=k) }}" class="{{ 'on' if st == k }}">{{ lbl }}<b>{{ n }}</b></a>{% endfor %}
</div>
{% if cnt.waiting %}<div class="msg"><b>{{ cnt.waiting }}</b> customer{{ ' is' if cnt.waiting == 1 else 's are' }} waiting for your reply. They are listed first.</div>{% endif %}
<form method="get" class="pbar"><input type="hidden" name="status" value="{{ st }}">
<input name="q" value="{{ q }}" type="search" placeholder="Search @username, Telegram ID or ticket number">
<button type="submit" class="sm">Search</button></form>
{% for t in rows %}
<a class="tk {{ 'wait' if t.status == 'open' and t.last_sender == 'user' }}" href="{{ url_for('ticket', tid=t.id) }}">
<div class="top"><div class="who">{{ ('@' ~ t.username) if t.username else t.user_id }}<span>#{{ t.id }} · {{ t.kind or 'Question' }}</span></div>
<span class="muted">{{ (t.last_at or t.created_at or '')[5:16].replace('T',' ') }}</span></div>
<div class="prev">{{ 'You: ' if t.last_sender == 'admin' else '' }}{{ t.last_text or ('Photo' if t.n_photos else '') }}</div>
<div class="meta">
{% if t.status == 'closed' %}<span class="pill done">Closed</span>{% elif t.last_sender == 'user' %}<span class="pill wait">Needs reply</span>{% else %}<span class="pill ok">You replied</span>{% endif %}
<span class="pill">{{ t.n_msgs }} message{{ '' if t.n_msgs == 1 else 's' }}</span>
{% if t.n_photos %}<span class="pill">{{ t.n_photos }} photo{{ '' if t.n_photos == 1 else 's' }}</span>{% endif %}
</div></a>
{% else %}<div class="card">{{ 'No tickets found.' if q else 'No tickets here. All caught up!' }}</div>{% endfor %}
{% endblock %}""",
    "ticket.html": """{% extends 'base.html' %}{% block body %}
<style>
.thead{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.thead h2{margin:0}
.thead .sub{color:var(--muted);font-size:13.5px;margin-top:2px}
.pill{border-radius:99px;padding:3px 10px;font-size:12px;font-weight:700;background:var(--grey);color:var(--greyink);vertical-align:2px;margin-left:6px}
.pill.ok{background:var(--accent-soft);color:var(--paid)}
.chat{display:flex;flex-direction:column;gap:10px;margin-bottom:16px}
.bub{max-width:min(560px,88%);padding:10px 14px;border-radius:16px;overflow-wrap:anywhere;white-space:pre-wrap;line-height:1.5}
.bub small{display:block;font-size:11.5px;font-weight:600;opacity:.7;margin-bottom:3px;white-space:normal}
.bub.u{align-self:flex-start;background:var(--surface);border:1px solid var(--line);border-bottom-left-radius:4px}
.bub.a{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px}
.bub img{max-width:100%;max-height:360px;border-radius:10px;margin-top:8px;display:block}
.qr{display:flex;gap:6px;flex-wrap:wrap;margin:2px 0 8px}
.chip{width:auto;margin:0;padding:6px 12px;font-size:13px;border-radius:99px;background:var(--grey);border-color:var(--grey);color:var(--greyink);text-align:left}
.chip:hover{background:var(--line2);border-color:var(--line2)}
.btns{display:flex;gap:8px;flex-wrap:wrap}.btns button{flex:1;min-width:130px;margin:0}
</style>
<p style="margin:0 0 10px"><a href="{{ url_for('tickets') }}">&larr; All tickets</a></p>
<div class="thead"><div>
<h2>{{ ('@' ~ t.username) if t.username else t.user_id }} <span class="pill {{ 'ok' if t.status == 'open' else '' }}">{{ 'Open' if t.status == 'open' else 'Closed' }}</span></h2>
<div class="sub">Ticket #{{ t.id }} · {{ t.kind or 'Question' }} · ID {{ t.user_id }} · <a href="{{ url_for('users', q=t.user_id) }}">Open customer</a></div></div>
<form method="post" action="{{ url_for('ticket_status', tid=t.id) }}" style="margin:0">
<input type="hidden" name="status" value="{{ 'closed' if t.status == 'open' else 'open' }}">
<button class="grey sm" type="submit">{{ 'Close ticket' if t.status == 'open' else 'Reopen ticket' }}</button></form></div>
<div class="chat">
{% for m in msgs %}<div class="bub {{ 'a' if m.sender == 'admin' else 'u' }}"><small>{{ 'You' if m.sender == 'admin' else 'Customer' }} · {{ (m.created_at or '')[5:16].replace('T',' ') }}</small>{{ m.text or '' }}{% if m.photo_id %}<a href="{{ url_for('ticket_photo', mid=m.id) }}" target="_blank"><img src="{{ url_for('ticket_photo', mid=m.id) }}" alt="photo" loading="lazy"></a>{% endif %}</div>{% endfor %}
</div>
<div class="card" id="reply">
<form method="post" action="{{ url_for('ticket_reply', tid=t.id) }}">
{% if quick %}<div class="qr">{% for r in quick %}<button type="button" class="chip" data-r="{{ r }}" onclick="var a=document.getElementById('rt');a.value=this.dataset.r;a.focus();">{{ r[:38] }}{{ '…' if r|length > 38 }}</button>{% endfor %}</div>{% endif %}
<textarea id="rt" name="text" rows="4" placeholder="Your reply (the customer gets it in the bot)" required></textarea>
<div class="btns"><button type="submit">Send reply</button>
{% if t.status == 'open' %}<button type="submit" name="close" value="1" class="green">Send and close</button>{% endif %}</div>
</form></div>
<script>var c=document.querySelector('.chat');if(c&&c.lastElementChild){c.lastElementChild.scrollIntoView({block:'nearest'});}</script>
{% endblock %}""",
    "settings.html": """{% extends 'base.html' %}{% block body %}
<style>
.sg{margin-bottom:16px}
.sg h3{margin:0 0 2px}
.sg>p{margin:0 0 6px}
.def{color:var(--muted);font-size:12.5px;font-weight:500}
.sw{display:flex;align-items:center;gap:12px;padding:12px 14px;border:1px solid var(--line2);border-radius:10px;cursor:pointer;margin-top:8px;color:var(--ink);font-size:14.5px;font-weight:600}
.sw input{width:20px;height:20px;margin:0;accent-color:var(--accent);flex:none}
.themes{display:flex;gap:8px;margin-top:8px}
.themes button{flex:1;background:var(--grey);border-color:var(--grey);color:var(--greyink);margin:0}
.themes button.on{background:var(--accent);border-color:var(--accent);color:#fff}
.savebar{position:sticky;bottom:0;background:var(--bg);padding:12px 0;display:flex;gap:8px;border-top:1px solid var(--line)}
.savebar button{margin:0}
</style>
<h2>Settings</h2>
<div class="card sg"><h3>Appearance</h3><p class="muted">Only for this admin site, on this device.</p>
<div class="themes"><button type="button" data-t="auto" onclick="setTheme('auto')">Auto</button><button type="button" data-t="light" onclick="setTheme('light')">Light</button><button type="button" data-t="dark" onclick="setTheme('dark')">Dark</button></div></div>

<div class="card sg"><h3>Shop status</h3>
<p class="muted">{% if s.shop_open %}The shop is <b>OPEN</b>.{% else %}The shop is <b>CLOSED</b>. Customers only see your closed message.{% endif %} You and your bot admins can still use the bot while it is closed. Money already being paid is still received.</p>
<form method="post" action="{{ url_for('shop_toggle') }}" style="margin:0"><input type="hidden" name="state" value="{{ 'close' if s.shop_open else 'open' }}">
<button type="submit" class="{{ 'red' if s.shop_open else 'green' }}">{{ 'Close the shop' if s.shop_open else 'Open the shop' }}</button></form></div>

<form method="post" action="{{ url_for('save_settings') }}">
<div class="card sg"><h3>Messages</h3>
<p class="muted">Plain text. Use {shop} for your shop name. Leave empty to use the built-in message.</p>
<label>Welcome message <span class="def">shown after /start, under the bottom menu</span></label>
<textarea name="welcome_text" rows="5" maxlength="1500" placeholder="Welcome to {shop}! Premium subscriptions at the best price.">{{ s.welcome_text }}</textarea>
<label>Shop closed message <span class="def">shown to customers while the shop is closed</span></label>
<textarea name="closed_message" rows="3" maxlength="600" placeholder="We are closed for maintenance. Back in 1 hour.">{{ s.closed_message }}</textarea></div>

<div class="card sg"><h3>Wallet top-ups</h3>
<p class="muted">Limits and time windows for customers adding funds. Changes apply to the next top-up, no restart needed.</p>
<div class="two">
<div><label>Smallest top-up (USDT) <span class="def">default {{ '%g' % d.min_deposit }}</span></label><input name="min_deposit" type="number" step="0.01" min="0.01" value="{{ '%g' % s.min_deposit }}" required></div>
<div><label>Largest top-up (USDT) <span class="def">default {{ '%g' % d.max_deposit }}</span></label><input name="max_deposit" type="number" step="0.01" min="0.01" value="{{ '%g' % s.max_deposit }}" required></div>
</div>
<div class="two">
<div><label>Crypto / Binance payment window (minutes) <span class="def">default {{ d.deposit_timeout_minutes }}</span></label><input name="deposit_timeout_minutes" type="number" step="1" min="1" max="1440" value="{{ s.deposit_timeout_minutes }}" required></div>
<div><label>UPI payment window (minutes) <span class="def">default {{ d.upi_timeout_minutes }}</span></label><input name="upi_timeout_minutes" type="number" step="1" min="1" max="1440" value="{{ s.upi_timeout_minutes }}" required></div>
</div></div>

<div class="card sg"><h3>Referral commission</h3>
<p class="muted">Percent of everything a referred customer buys that the person who invited them earns (added to their wallet). Applies to purchases from now on.</p>
<label>Commission (%) <span class="def">default {{ '%g' % d.referral_percent }}</span></label>
<input name="referral_percent" type="number" step="0.1" min="0" max="100" value="{{ '%g' % s.referral_percent }}" required></div>

<div class="card sg"><h3>Alerts</h3>
<label class="sw"><input type="checkbox" name="manual_alerts" value="1" {{ 'checked' if s.manual_alerts }}> Message me in Telegram about new made-to-order orders</label></div>

<div class="card sg"><h3>Quick replies for support</h3>
<p class="muted">One reply per line. They show as buttons on every ticket, tap one and edit it before sending.</p>
<textarea name="quick_replies" rows="5">{{ s.quick_replies }}</textarea></div>

<div class="savebar"><button type="submit">Save settings</button></div>
</form>
<form method="post" action="{{ url_for('reset_settings') }}" onsubmit="return confirm('Put every setting back to its default value?')">
<button type="submit" class="grey">Reset all to defaults</button></form>
<script>
function setTheme(t){
  try{if(t==='auto'){localStorage.removeItem('theme');}else{localStorage.setItem('theme',t);}}catch(e){}
  if(t==='auto'){document.documentElement.removeAttribute('data-theme');}else{document.documentElement.setAttribute('data-theme',t);}
  mark();
}
function mark(){
  var t='auto';try{t=localStorage.getItem('theme')||'auto';}catch(e){}
  document.querySelectorAll('.themes button').forEach(function(b){b.classList.toggle('on',b.dataset.t===t);});
}
mark();
</script>
{% endblock %}""",
    "upi.html": """{% extends 'base.html' %}{% block body %}
<h2>UPI setup</h2>
<div class="card"><h3>Rate (INR per 1 USDT)</h3>
<p class="muted">Shown next to every USDT price in the bot. Applies to new requests; open ones keep the rate they were created with.</p>
<form method="post" action="{{ url_for('upi_rate') }}"><div class="inline">
<input name="rate" type="number" step="0.01" min="1" value="{{ rate }}" required>
<button type="submit">Save</button></div></form></div>
<div class="card"><h3>Add a UPI account</h3>
<p class="muted">The bot uses your active accounts one after another (rotation). The QR image is optional (PNG or JPG, up to 2 MB).</p>
<form method="post" action="{{ url_for('upi_add') }}" enctype="multipart/form-data">
<label>Name / label (only you see it)</label><input name="label" placeholder="e.g. PhonePe - Rahul">
<label>UPI ID</label><input name="upi_id" placeholder="name@bank" required>
<label>QR image</label><input name="qr" type="file" accept="image/png,image/jpeg">
<button type="submit">Add UPI</button></form></div>
{% for a in rows %}
<div class="card">
<b>{{ a.label or 'UPI' }}</b> <span class="{{ 'paid' if a.active else 'failed' }}">· {{ 'ON' if a.active else 'OFF' }}</span><br>
<span class="mono">{{ a.upi_id }}</span>
{% if a.has_qr %}<div style="margin:10px 0"><img src="{{ url_for('upi_qr', aid=a.id) }}" alt="QR" style="max-width:180px;border-radius:8px;background:#fff"></div>{% endif %}
<form method="post" enctype="multipart/form-data" action="{{ url_for('upi_qr_set', aid=a.id) }}">
<input name="qr" type="file" accept="image/png,image/jpeg" required>
<div class="row"><button class="grey" type="submit">{{ 'Replace QR' if a.has_qr else 'Upload QR' }}</button>
<button class="grey" formaction="{{ url_for('upi_toggle', aid=a.id) }}" formnovalidate>{{ 'Turn OFF' if a.active else 'Turn ON' }}</button>
<button class="red" formaction="{{ url_for('upi_delete', aid=a.id) }}" formnovalidate onclick="return confirm('Delete this UPI account?')">Delete</button></div>
</form></div>
{% else %}<div class="card">No UPI account yet. Add one above - the UPI button appears in the bot as soon as one is ON.</div>{% endfor %}{% endblock %}""",
    "upipay.html": """{% extends 'base.html' %}{% block body %}
<h2>UPI payments</h2>
<div class="card"><form method="get"><div class="inline">
<input name="q" value="{{ q }}" placeholder="Find by UTR, request ID, user ID or @username">
<button type="submit">Find</button></div></form>
<p class="muted">The bot only alerts you about new payments. Check the UTR in your bank app, then approve or reject here.</p>
<p class="muted" style="margin-bottom:0">{% if q %}Search results (all statuses). <a href="{{ url_for('upipay') }}">Back to waiting</a>{% elif view == 'all' %}Latest 100. <a href="{{ url_for('upipay') }}">Only waiting</a>{% else %}Waiting for you to verify. <a href="{{ url_for('upipay', view='all') }}">Show all</a>{% endif %}</p></div>
{% for d in rows %}
<div class="card">
<b>₹{{ '%.2f'|format(d.inr_amount or 0) }}</b> → <b>${{ '%.2f'|format(d.amount) }}</b>
<span class="{{ 'paid' if d.status == 'paid' else ('failed' if d.status in ('rejected','failed') else 'pending') }}">· {{ {'upi_review':'waiting for you','upi_open':'no UTR yet','paid':'approved','rejected':'rejected','failed':'expired/cancelled'}.get(d.status, d.status) }}</span><br>
<span class="muted">{{ d.id }} · {{ (d.created_at or '')[:16].replace('T',' ') }} UTC · rate ₹{{ d.rate }} · UPI {{ d.upi_id }}</span><br>
<span class="muted">Customer: {{ ('@' ~ d.username) if d.username else d.user_id }} ({{ d.user_id }})</span>
<div class="row" style="margin-top:8px;align-items:center"><span class="mono" style="flex:1">UTR: {{ d.utr or '-' }}</span></div>
{% if d.status != 'paid' %}
<form method="post">
<div class="row">
<button class="green" formaction="{{ url_for('upipay_approve', did=d.id) }}" onclick="return confirm('Add ${{ '%.2f'|format(d.amount) }} to the customer wallet?')">Approve & add ${{ '%.2f'|format(d.amount) }}</button>
{% if d.status in ('upi_review','upi_open') %}<button class="red" formaction="{{ url_for('upipay_reject', did=d.id) }}" onclick="return confirm('Reject this payment?')">Reject</button>{% endif %}
</div></form>{% endif %}</div>
{% else %}<div class="card">Nothing here.</div>{% endfor %}{% endblock %}""",
}
TEMPLATES.update(sellers_web.TEMPLATES)
app.jinja_loader = DictLoader(TEMPLATES)


def _ctx(kw):
    """Values every page needs: the shop name and, for a logged-in person, the menu counters."""
    t = tenancy.current_or_none()
    kw.setdefault("shop", t.name if t else tenancy.PLATFORM_NAME)
    kw.setdefault("signup", tenancy.signup_mode())
    if t and session.get("ok"):
        kw.setdefault("pending_count", len(db.pending_manual()))
        kw.setdefault("open_tickets", len(db.all_tickets("open")))
        kw.setdefault("upi_pending", db.upi_review_count())
        kw.setdefault("shop_closed", not db.get_setting("shop_open", 1))
        kw.setdefault("is_main", t.is_main)
        kw.setdefault("tstatus", t.status)
        kw.setdefault("bot_ready", t.can_run_bot or t.is_main)
        if t.is_main:
            kw.setdefault("pending_sellers", sum(1 for r in tenancy.all_rows() if r["status"] == "pending"))
    return kw


def render_str(source, **kw):
    return render_template_string(source, **_ctx(kw))


def render(name, **kw):
    return render_template(name, **_ctx(kw))


# ------------------------------------------------------------------- auth
_attempts = {}


def _blocked(ip):
    now = time.time()
    _attempts[ip] = [t for t in _attempts.get(ip, []) if now - t < 300]
    return len(_attempts[ip]) >= 5


PUBLIC_ENDPOINTS = ("login", "static", "register_seller")


@app.before_request
def guard():
    """Decides who is logged in and points `db` / `binance` at THEIR shop for this request."""
    if request.path.startswith("/api"):
        return None                       # the API picks the shop from the API key
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    tid = session.get("tid")
    if not session.get("ok") or not tid:
        session.clear()
        return redirect(url_for("login"))
    t = tenancy.get_tenant(tid)
    if t is None or t.status == "suspended":
        session.clear()
        flash("This account is not available. Contact the platform owner.", "err")
        return redirect(url_for("login"))
    ep = request.endpoint or ""
    if not t.is_main and ep.startswith("sellers_admin."):
        return redirect(url_for("dashboard"))
    g._tenant_tok = tenancy.set_current(t)
    return None


@app.teardown_request
def release_shop(exc):
    tok = g.pop("_tenant_tok", None)
    if tok is not None:
        try:
            tenancy.reset_current(tok)
        except ValueError:
            pass


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password", "")
        if _blocked(ip):
            flash("Too many attempts. Try again in 5 minutes.", "err")
            return render("login.html")
        tid, err = None, None
        if email:                                   # a seller
            tid = tenancy.authenticate(email, password)
        elif not ADMIN_PASSWORD:                    # the platform owner
            err = "ADMIN_PASSWORD is not set on the server."
        elif hmac.compare_digest(password, ADMIN_PASSWORD):
            tid = tenancy.MAIN_ID
        if tid:
            t = tenancy.get_tenant(tid)
            if t and t.status == "suspended":
                err = "This account is suspended. Contact the platform owner."
            elif t:
                session.clear()
                session["ok"] = True
                session["tid"] = tid
                ready = tid == tenancy.MAIN_ID or t.can_run_bot
                return redirect(url_for("dashboard") if ready else url_for("connect"))
        elif err is None:
            _attempts.setdefault(ip, []).append(time.time())
            err = "Wrong e-mail or password."
        flash(err or "Could not log in.", "err")
    return render("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


sellers_web.register(app, render, render_str)


# ------------------------------------------------------------------ pages
ORDER_SQL = """SELECT o.*, p.name AS product, u.username AS username
FROM orders o
LEFT JOIN products p ON p.id = o.product_id
LEFT JOIN users u ON u.user_id = o.user_id
ORDER BY o.created_at DESC"""


def fetch_orders(limit):
    c = db.conn()
    rows = c.execute(ORDER_SQL + " LIMIT ?", (limit,)).fetchall()
    c.close()
    return rows


RANGES = [("today", "Today"), ("7d", "7 days"), ("30d", "30 days"), ("all", "All time")]

# paid sales from the bot and from the reseller API, with the cost per item
# (product cost price, else the provider cost for API-linked products)
_SALES = """WITH s AS (
SELECT product_id, quantity q, amount a, COALESCE(paid_at, created_at) ts, 'bot' src FROM orders WHERE status='paid'
UNION ALL
SELECT product_id, quantity, amount, created_at, 'api' FROM api_reseller_orders WHERE status='paid'
), x AS (
SELECT s.*, p.name pname, COALESCE(p.cost, pm.cost) ucost FROM s
LEFT JOIN products p ON p.id = s.product_id
LEFT JOIN provider_map pm ON pm.product_id = s.product_id
) """


def _since(rng):
    now = datetime.now(timezone.utc)
    if rng == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if rng == "7d":
        return (now - timedelta(days=7)).isoformat()
    if rng == "30d":
        return (now - timedelta(days=30)).isoformat()
    return None


def dashboard_data(rng):
    since = _since(rng)
    w = " WHERE ts >= ?" if since else ""
    a = (since,) if since else ()
    now = datetime.now(timezone.utc)
    c = db.conn()
    try:
        d = {}
        src = {r["src"]: dict(r) for r in c.execute(
            _SALES + "SELECT src, COUNT(*) n, COALESCE(SUM(q),0) units, COALESCE(SUM(a),0) rev, "
            "COALESCE(SUM(q*COALESCE(ucost,0)),0) cost, "
            "COALESCE(SUM(CASE WHEN ucost IS NULL THEN 1 ELSE 0 END),0) nocost FROM x" + w + " GROUP BY src", a)}
        for k in ("bot", "api"):
            r = src.get(k) or dict(n=0, units=0, rev=0.0, cost=0.0, nocost=0)
            r["profit"] = r["rev"] - r["cost"]
            d[k] = r
        d["n"] = d["bot"]["n"] + d["api"]["n"]
        d["units"] = d["bot"]["units"] + d["api"]["units"]
        d["rev"] = d["bot"]["rev"] + d["api"]["rev"]
        d["cost"] = d["bot"]["cost"] + d["api"]["cost"]
        d["nocost"] = d["bot"]["nocost"] + d["api"]["nocost"]
        d["profit"] = d["rev"] - d["cost"]
        d["margin"] = (d["profit"] / d["rev"] * 100) if d["rev"] else 0.0

        d["top"] = [dict(r) for r in c.execute(
            _SALES + "SELECT product_id, COALESCE(MAX(pname), 'Deleted product') name, COALESCE(SUM(q),0) units, "
            "SUM(a) rev, SUM(a - q*COALESCE(ucost,0)) profit FROM x" + w +
            " GROUP BY product_id ORDER BY rev DESC LIMIT 5", a)]

        ndays = 7 if rng in ("today", "7d") else 30
        start = (now - timedelta(days=ndays - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        got = {r["d"]: r for r in c.execute(
            _SALES + "SELECT substr(ts,1,10) d, SUM(a) rev, SUM(a - q*COALESCE(ucost,0)) profit FROM x "
            "WHERE ts >= ? GROUP BY d", (start.isoformat(),))}
        days = []
        for i in range(ndays):
            day = start + timedelta(days=i)
            r = got.get(day.strftime("%Y-%m-%d"))
            days.append(dict(label=day.strftime("%d %b"), rev=(r["rev"] if r else 0.0),
                             profit=(r["profit"] if r else 0.0)))
        mx = max([x["rev"] for x in days] + [0.01])
        for x in days:
            x["h"] = round(x["rev"] / mx * 100, 1)
            x["ph"] = round(max(0.0, min(100.0, x["profit"] / x["rev"] * 100)), 1) if x["rev"] > 0 else 0
        d["days"] = days

        o = c.execute("SELECT COALESCE(SUM(status='pending'),0) pending, COALESCE(SUM(status='processing'),0) processing FROM orders").fetchone()
        d["pending"], d["processing"] = o["pending"], o["processing"]
        f = "SELECT COUNT(*) n FROM orders WHERE status='failed'" + (" AND created_at >= ?" if since else "")
        d["failed"] = c.execute(f, a).fetchone()["n"]

        u = c.execute("SELECT COUNT(*) n, COALESCE(SUM(balance),0) bal, COALESCE(SUM(ref_earned),0) ref FROM users").fetchone()
        d["users"], d["wallets"], d["ref"] = u["n"], u["bal"], u["ref"]
        nu = "SELECT COUNT(*) n FROM users" + (" WHERE created_at >= ?" if since else "")
        d["new_users"] = c.execute(nu, a).fetchone()["n"]

        dep = ("SELECT COALESCE(SUM(amount),0) amt FROM deposits WHERE status='paid' AND COALESCE(method,'') != 'admin'"
               + (" AND COALESCE(paid_at, created_at) >= ?" if since else ""))
        d["dep_amt"] = c.execute(dep, a).fetchone()["amt"]
    finally:
        c.close()
    d["net"] = (d["profit"] - d["ref"]) if rng == "all" else None
    d["low"] = [dict(name=p["name"], stock=int(p["stock"] or 0)) for p in db.list_products()
                if p["kind"] != "manual" and int(p["stock"] or 0) <= 3][:8]
    return d


@app.route("/")
def dashboard():
    rng = request.args.get("range", "30d")
    if rng not in dict(RANGES):
        rng = "30d"
    return render("dashboard.html", d=dashboard_data(rng), rng=rng, ranges=RANGES, rows=fetch_orders(10))


@app.route("/orders")
def orders():
    return render("orders.html", rows=fetch_orders(200))


@app.route("/products")
def products():
    cat, errs = db.catalog_options(max_age=60)
    for e in errs:
        flash(e, "err")
    prods = db.list_products()
    items = {p["id"]: db.stock_items(p["id"], 50) for p in prods if p["kind"] != "manual"}
    return render("products.html", products=prods, items=items, suppliers_cat=cat,
                  sources=db.sources_by_product(), open_id=request.args.get("open", type=int))


@app.route("/products/<int:pid>/sources", methods=["POST"])
def source_edit(pid):
    act = request.form.get("action", "")
    try:
        if act == "add":
            sid, _, key = request.form.get("offer", "").partition("|")
            db.add_source(pid, int(sid), key)
            flash("Supplier product added.")
        elif act == "remove":
            db.remove_source(pid, int(request.form.get("source_id", "0")))
            flash("Supplier product removed.")
        elif act == "mode":
            ch = request.form.get("chosen", "")
            db.set_source_mode(pid, request.form.get("mode", "manual"), int(ch) if ch.isdigit() else None,
                               bool(request.form.get("fallback")))
            flash("Supplier choice saved.")
    except Exception as e:
        flash(str(e)[:200], "err")
    return redirect(url_for("products", open=pid))


@app.route("/products/add", methods=["POST"])
def add_stock():
    name = request.form.get("name", "").strip()
    links = [x.strip() for x in request.form.get("links", "").splitlines() if x.strip()]
    try:
        price = float(request.form.get("price", ""))
    except ValueError:
        price = -1
    if not name or not links or price < 0:
        flash("Name, valid price and at least one item are required.", "err")
        return redirect(url_for("products"))
    for link in links:
        db.add_product_link(name, price, link, 0)
    flash(f"Added {len(links)} item(s) to {name}.")
    return redirect(url_for("products"))


@app.route("/products/<int:pid>/price", methods=["POST"])
def set_price(pid):
    try:
        price = float(request.form.get("price", ""))
        if price < 0:
            raise ValueError
    except ValueError:
        flash("Invalid price.", "err")
        return redirect(url_for("products"))
    db.set_product_price(pid, price)
    flash("Price updated.")
    return redirect(url_for("products"))


@app.route("/admins")
def admins():
    c = db.conn()
    rows = c.execute("SELECT * FROM admins ORDER BY added_at DESC").fetchall()
    c.close()
    return render("admins.html", rows=rows)


@app.route("/admins/add", methods=["POST"])
def add_admin():
    try:
        uid = int(request.form.get("uid", ""))
    except ValueError:
        flash("Invalid Telegram ID.", "err")
        return redirect(url_for("admins"))
    db.add_admin(uid, 0)
    flash(f"Admin {uid} added.")
    return redirect(url_for("admins"))


@app.route("/admins/<int:uid>/remove", methods=["POST"])
def remove_admin(uid):
    db.remove_admin(uid)
    flash(f"Admin {uid} removed.")
    return redirect(url_for("admins"))


@app.route("/userprices")
def userprices():
    c = db.conn()
    rows = c.execute(
        """SELECT up.username, up.product_id, up.price, p.name AS product
        FROM user_prices up LEFT JOIN products p ON p.id = up.product_id
        ORDER BY up.username"""
    ).fetchall()
    c.close()
    return render("userprices.html", rows=rows, products=db.list_products())


@app.route("/userprices/set", methods=["POST"])
def set_user_price():
    username = request.form.get("username", "").strip().lstrip("@")
    try:
        pid = int(request.form.get("pid", ""))
        price = float(request.form.get("price", ""))
        if price < 0 or not username:
            raise ValueError
    except ValueError:
        flash("Valid username, product and price required.", "err")
        return redirect(url_for("userprices"))
    db.set_user_price(username, pid, price)
    flash(f"Price set for @{username}.")
    return redirect(url_for("userprices"))


@app.route("/userprices/delete", methods=["POST"])
def delete_user_price():
    c = db.conn()
    c.execute(
        "DELETE FROM user_prices WHERE username=? AND product_id=?",
        (request.form.get("username", ""), request.form.get("pid", 0)),
    )
    c.commit()
    c.close()
    flash("User price deleted.")
    return redirect(url_for("userprices"))


# ------------------------------------------------- product editing
def _num(value, allow_empty=False):
    value = (value or "").strip()
    if not value and allow_empty:
        return None
    number = float(value)
    if number < 0:
        raise ValueError
    return number


@app.route("/products/new", methods=["POST"])
def add_product():
    name = request.form.get("name", "").strip()
    kind = request.form.get("kind", "auto")
    try:
        price = _num(request.form.get("price"))
    except ValueError:
        price = None
    if not name or price is None or kind not in ("auto", "manual"):
        flash("Name and a valid price are required.", "err")
        return redirect(url_for("products"))
    db.add_product(name, price, kind)
    flash(f"Product '{name}' added." + (" Now add stock below." if kind == "auto" else ""))
    return redirect(url_for("products"))


@app.route("/products/<int:pid>/edit", methods=["POST"])
def edit_product(pid):
    f = request.form
    try:
        fields = dict(
            name=f.get("name", "").strip(),
            price=_num(f.get("price")),
            old_price=_num(f.get("old_price"), allow_empty=True),
            cost=_num(f.get("cost"), allow_empty=True),
            category=(f.get("category", "").strip() or None),
            kind=f.get("kind", "auto"),
            delivery=(f.get("delivery", "").strip() or None),
            warranty=(f.get("warranty", "").strip() or None),
            prompt=(f.get("prompt", "").strip() or None),
            description=(f.get("description", "").strip() or None),
        )
        if not fields["name"] or fields["kind"] not in ("auto", "manual"):
            raise ValueError
    except ValueError:
        flash("Check the name, price and old price.", "err")
        return redirect(url_for("products"))
    db.update_product(pid, **fields)
    flash("Product saved.")
    return redirect(url_for("products"))


def _profit_text(price, cost):
    d = price - cost
    if d < -0.004:
        return f"LOSS of ${-d:.2f} per item (selling below cost!)"
    return f"profit ${d:.2f} per item"


def _back(pid=None):
    if pid:
        return redirect(url_for("products", open=pid) + f"#p{pid}")
    return redirect(url_for("products"))


def _lines(text):
    return [x.strip() for x in (text or "").splitlines() if x.strip()]


@app.route("/products/create", methods=["POST"])
def create_product():
    """One step: product + cost + price + (optional) stock."""
    f = request.form
    name = f.get("name", "").strip()
    kind = f.get("kind", "auto")
    try:
        price = _num(f.get("price"))
        cost = _num(f.get("cost"))
    except ValueError:
        flash("Enter a valid cost and selling price.", "err")
        return _back()
    if not name or kind not in ("auto", "manual"):
        flash("Product name is required.", "err")
        return _back()
    existed = any(p["name"] == name for p in db.list_products())
    pid = db.add_product(name, price, kind)
    if existed:
        msg = f"'{name}' already exists, so its price and cost were not changed."
    else:
        db.update_product(pid, cost=cost, category=(f.get("category", "").strip() or None))
        msg = f"'{name}' added. Cost ${cost:.2f}, price ${price:.2f}, {_profit_text(price, cost)}."
    lines = _lines(f.get("links")) if kind == "auto" else []
    if lines:
        db.add_stock_lines(pid, lines)
        msg += f" {len(lines)} stock item(s) added."
    elif kind == "auto":
        msg += " No stock yet - open the product and use Add stock."
    flash(msg)
    return _back(pid)


@app.route("/products/<int:pid>/quick", methods=["POST"])
def quick_product(pid):
    f = request.form
    try:
        price = _num(f.get("price"))
        cost = _num(f.get("cost"), allow_empty=True)
    except ValueError:
        flash("Enter a valid cost and selling price.", "err")
        return _back(pid)
    db.update_product(pid, price=price, cost=cost)
    if cost is None:
        flash(f"Price saved: ${price:.2f}. No cost set, so profit is not tracked for this product.")
    else:
        flash(f"Saved. Price ${price:.2f}, cost ${cost:.2f}, {_profit_text(price, cost)}.")
    return _back(pid)


@app.route("/products/<int:pid>/stock/add", methods=["POST"])
def stock_add(pid):
    lines = _lines(request.form.get("links"))
    if not lines:
        flash("Paste at least one item.", "err")
    else:
        db.add_stock_lines(pid, lines)
        flash(f"{len(lines)} item(s) added to stock.")
    return _back(pid)


@app.route("/products/<int:pid>/stock/<int:sid>/delete", methods=["POST"])
def stock_delete(pid, sid):
    flash("Stock item removed." if db.delete_stock_item(pid, sid) else "That item was already sold or removed.", "")
    return _back(pid)


@app.route("/products/<int:pid>/stock/clear", methods=["POST"])
def stock_clear(pid):
    n = db.clear_stock(pid)
    flash(f"Removed {n} unsold item(s).")
    return _back(pid)


@app.route("/products/<int:pid>/delete", methods=["POST"])
def delete_product(pid):
    result = db.delete_product(pid)
    if result == "ok":
        flash("Product deleted (hidden from the shop).")
    elif result == "waiting":
        flash("This product still has made-to-order orders waiting. Finish or refund them on the Pending orders page first.", "err")
    else:
        flash("Product not found.", "err")
    return redirect(url_for("products"))


# ------------------------------------------------- messages to customers (sent once by the bot)
def _note(note):
    return f"\n\n💬 {htmllib.escape(note)}" if note else ""


def _msg(uid, en, zh):
    db.queue_message(uid, zh if db.get_user_language(uid) == "zh" else en)


# ------------------------------------------------- made-to-order orders
@app.route("/manual")
def manual():
    return render("manual.html", rows=db.pending_manual())


@app.route("/manual/<oid>/done", methods=["POST"])
def manual_done(oid):
    note = request.form.get("note", "").strip()
    o = db.complete_manual(oid, note or None)
    if not o:
        flash("That order is not waiting any more.", "err")
        return redirect(url_for("manual"))
    p = db.get_product_any(o["product_id"])
    name = htmllib.escape(p["name"]) if p else "your product"
    _msg(o["user_id"],
         f"✅ <b>Your order is done</b>\n\n🧾 <code>{o['id']}</code>\n📦 {name}{_note(note)}",
         f"✅ <b>您的订单已完成</b>\n\n🧾 <code>{o['id']}</code>\n📦 {name}{_note(note)}")
    flash(f"Order {oid} marked done - the customer is being notified.")
    return redirect(url_for("manual"))


@app.route("/manual/<oid>/reject", methods=["POST"])
def manual_reject(oid):
    note = request.form.get("note", "").strip()
    o = db.reject_manual(oid, note or None)
    if not o:
        flash("That order is not waiting any more.", "err")
        return redirect(url_for("manual"))
    p = db.get_product_any(o["product_id"])
    name = htmllib.escape(p["name"]) if p else "your product"
    _msg(o["user_id"],
         f"↩️ <b>Order refunded</b>\n\n🧾 <code>{o['id']}</code>\n📦 {name}\n💰 ${o['amount']:.2f} was returned to your balance.{_note(note)}",
         f"↩️ <b>订单已退款</b>\n\n🧾 <code>{o['id']}</code>\n📦 {name}\n💰 ${o['amount']:.2f} 已退回您的余额。{_note(note)}")
    flash(f"Order {oid} refunded to the customer's wallet.")
    return redirect(url_for("manual"))


@app.route("/manual/<oid>/note", methods=["POST"])
def manual_note(oid):
    note = request.form.get("note", "").strip()
    o = db.get_order(oid)
    if not o or not note:
        flash("Write a note first.", "err")
        return redirect(url_for("manual"))
    _msg(o["user_id"],
         f"💬 <b>Update on your order</b> <code>{o['id']}</code>\n\n{htmllib.escape(note)}",
         f"💬 <b>订单更新</b> <code>{o['id']}</code>\n\n{htmllib.escape(note)}")
    flash("Note sent to the customer.")
    return redirect(url_for("manual"))


# ------------------------------------------------- users / wallet
@app.route("/users")
def users():
    q = request.args.get("q", "")
    sort = request.args.get("sort", "new")
    return render("users.html", rows=db.users_list(q, sort), q=q, sort=sort, tot=db.user_totals())


@app.route("/users/<int:uid>/topup", methods=["POST"])
def topup(uid):
    try:
        amount = round(float(request.form.get("amount", "")), 2)
        if request.form.get("dir") == "remove":
            amount = -abs(amount)
        elif request.form.get("dir") == "add":
            amount = abs(amount)
        if amount == 0 or abs(amount) > 100000:
            raise ValueError
    except ValueError:
        flash("Enter a non-zero amount.", "err")
        return redirect(url_for("users", q=str(uid)))
    note = request.form.get("note", "").strip()
    db.admin_topup(uid, amount)
    if amount > 0:
        _msg(uid, f"➕ <b>${amount:.2f} added to your balance.</b>{_note(note)}",
             f"➕ <b>${amount:.2f} 已充值到您的余额。</b>{_note(note)}")
    else:
        _msg(uid, f"➖ <b>${-amount:.2f} was removed from your balance.</b>{_note(note)}",
             f"➖ <b>已从您的余额扣除 ${-amount:.2f}。</b>{_note(note)}")
    flash(f"Balance changed by {amount:+.2f} for {uid}.")
    return redirect(url_for("users", q=str(uid)))


# ------------------------------------------------- support
@app.route("/tickets")
def tickets():
    st = request.args.get("status", "open")
    if st not in ("open", "closed", "all"):
        st = "open"
    q = request.args.get("q", "").strip()
    return render("tickets.html", rows=db.tickets_overview(st, q), st=st, q=q, cnt=db.ticket_counts())


@app.route("/tickets/<int:tid>")
def ticket(tid):
    t = db.get_ticket(tid)
    if not t:
        return redirect(url_for("tickets"))
    c = db.conn()
    u = c.execute("SELECT username FROM users WHERE user_id=?", (t["user_id"],)).fetchone()
    c.close()
    info = dict(t)
    info["username"] = u["username"] if u else None
    return render("ticket.html", t=info, msgs=db.ticket_msgs(tid), quick=_quick_replies())


@app.route("/tickets/photo/<int:mid>")
def ticket_photo(mid):
    """Show a photo the customer attached to a ticket (fetched from Telegram with the bot token)."""
    c = db.conn()
    row = c.execute("SELECT photo_id FROM ticket_msgs WHERE id=?", (mid,)).fetchone()
    c.close()
    BOT_TOKEN = tenancy.cur().bot_token
    if not row or not row["photo_id"] or not BOT_TOKEN:
        return ("", 404)
    try:
        info = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                            params={"file_id": row["photo_id"]}, timeout=10).json()
        path = info["result"]["file_path"]
        data = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}", timeout=20).content
    except Exception:
        return ("Could not load the photo from Telegram.", 502)
    mime = _image_mime(data) or "application/octet-stream"
    resp = app.response_class(data, mimetype=mime)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.route("/tickets/<int:tid>/reply", methods=["POST"])
def ticket_reply(tid):
    t = db.get_ticket(tid)
    text = request.form.get("text", "").strip()
    if not t or not text:
        flash("Write a reply first.", "err")
        return redirect(url_for("ticket", tid=tid) if t else url_for("tickets"))
    db.add_ticket_msg(tid, "admin", text)
    _msg(t["user_id"],
         f"💬 <b>Support reply</b> · ticket #{tid}\n\n{htmllib.escape(text)}",
         f"💬 <b>客服回复</b> · 工单 #{tid}\n\n{htmllib.escape(text)}")
    if request.form.get("close"):
        db.set_ticket_status(tid, "closed")
        flash("Reply sent and ticket closed.")
        return redirect(url_for("tickets"))
    flash("Reply sent to the customer.")
    return redirect(url_for("ticket", tid=tid) + "#reply")


@app.route("/tickets/<int:tid>/status", methods=["POST"])
def ticket_status(tid):
    status = request.form.get("status", "closed")
    db.set_ticket_status(tid, "open" if status == "open" else "closed")
    return redirect(url_for("ticket", tid=tid))


# ------------------------------------------------- settings
DEFAULT_QUICK_REPLIES = """Done ✅ Please check your order and tell me if anything is wrong.
Please send a screenshot of the problem.
We are checking this now, please wait a little.
Your money was refunded to your wallet."""


def _quick_replies():
    raw = db.get_setting("quick_replies", DEFAULT_QUICK_REPLIES)
    return [x.strip() for x in str(raw).splitlines() if x.strip()][:12]


SETTING_DEFAULTS = dict(
    min_deposit=float(os.getenv("MIN_DEPOSIT", "0.1")),
    max_deposit=float(os.getenv("MAX_DEPOSIT", "100")),
    deposit_timeout_minutes=int(os.getenv("DEPOSIT_TIMEOUT_MINUTES", os.getenv("PAYMENT_TIMEOUT_MINUTES", "15"))),
    upi_timeout_minutes=int(os.getenv("UPI_TIMEOUT_MINUTES", "30")),
    manual_alerts=0 if os.getenv("MANUAL_ALERTS", "1") == "0" else 1,
)


@app.route("/settings")
def settings():
    d = dict(SETTING_DEFAULTS, referral_percent=float(os.getenv("REFERRAL_PERCENT", "3")))
    s = {k: db.get_setting(k, v) for k, v in SETTING_DEFAULTS.items()}
    s["referral_percent"] = db.referral_percent()
    s["shop_open"] = db.get_setting("shop_open", 1)
    s["welcome_text"] = db.get_setting("welcome_text", "")
    s["closed_message"] = db.get_setting("closed_message", "")
    s["quick_replies"] = "\n".join(_quick_replies())
    return render("settings.html", s=s, d=d)


@app.route("/settings/shop", methods=["POST"])
def shop_toggle():
    opening = request.form.get("state") == "open"
    db.set_setting("shop_open", 1 if opening else 0)
    flash("Shop is OPEN. Customers can use the bot." if opening else
          "Shop is CLOSED. Customers see your closed message; you and your admins can still test it.")
    back = request.form.get("back", "")
    if not back.startswith("/") or back.startswith("//"):
        back = url_for("settings")
    return redirect(back)


@app.route("/settings/save", methods=["POST"])
def save_settings():
    f = request.form
    try:
        vals = dict(
            min_deposit=float(f.get("min_deposit", SETTING_DEFAULTS["min_deposit"])),
            max_deposit=float(f.get("max_deposit", SETTING_DEFAULTS["max_deposit"])),
            deposit_timeout_minutes=int(f.get("deposit_timeout_minutes", SETTING_DEFAULTS["deposit_timeout_minutes"])),
            upi_timeout_minutes=int(f.get("upi_timeout_minutes", SETTING_DEFAULTS["upi_timeout_minutes"])),
            referral_percent=float(f.get("referral_percent", db.referral_percent())),
        )
    except ValueError:
        flash("Please enter valid numbers.", "err")
        return redirect(url_for("settings"))
    if not (0 < vals["min_deposit"] <= vals["max_deposit"]):
        flash("Smallest top-up must be above 0 and not bigger than the largest top-up.", "err")
        return redirect(url_for("settings"))
    if not (1 <= vals["deposit_timeout_minutes"] <= 1440 and 1 <= vals["upi_timeout_minutes"] <= 1440):
        flash("Payment windows must be between 1 and 1440 minutes.", "err")
        return redirect(url_for("settings"))
    if not (0 <= vals["referral_percent"] <= 100):
        flash("Enter a referral percentage between 0 and 100.", "err")
        return redirect(url_for("settings"))
    for k, v in vals.items():
        db.set_setting(k, v)
    db.set_setting("manual_alerts", 1 if f.get("manual_alerts") else 0)
    if "welcome_text" in f:
        db.set_setting("welcome_text", f.get("welcome_text", "").strip()[:1500])
        db.set_setting("closed_message", f.get("closed_message", "").strip()[:600])
        replies = "\n".join(x.strip() for x in f.get("quick_replies", "").splitlines() if x.strip())
        db.set_setting("quick_replies", replies[:3000] if replies else DEFAULT_QUICK_REPLIES)
    flash("Settings saved. They are live in the bot now.")
    return redirect(url_for("settings"))


@app.route("/settings/reset", methods=["POST"])
def reset_settings():
    db.del_settings(list(SETTING_DEFAULTS) + ["referral_percent", "welcome_text", "closed_message", "quick_replies"])
    flash("All settings are back to their defaults.")
    return redirect(url_for("settings"))


# ------------------------------------------------- UPI (accounts, QR, rate, payments to verify)
MAX_QR_BYTES = 2 * 1024 * 1024


def _image_mime(data: bytes):
    """Detect PNG / JPEG from the file itself (never trust the browser's content type)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


def _read_qr(field="qr"):
    """Returns (bytes, mime) of an uploaded QR, (None, None) if nothing was chosen, or raises ValueError."""
    f = request.files.get(field)
    if not f or not f.filename:
        return None, None
    data = f.read(MAX_QR_BYTES + 1)
    if len(data) > MAX_QR_BYTES:
        raise ValueError("The QR image is too big (max 2 MB).")
    mime = _image_mime(data)
    if not mime:
        raise ValueError("The QR must be a PNG or JPG image.")
    return data, mime


@app.route("/upi")
def upi():
    return render("upi.html", rows=db.upi_list(), rate=f"{db.inr_rate():g}")


@app.route("/upi/rate", methods=["POST"])
def upi_rate():
    try:
        rate = float(request.form.get("rate", "").replace(",", "."))
        if not (1 <= rate <= 100000):
            raise ValueError
    except ValueError:
        flash("Enter a rate between 1 and 100000.", "err")
        return redirect(url_for("upi"))
    db.set_inr_rate(rate)
    flash(f"Rate saved: 1 USDT = ₹{rate:g}.")
    return redirect(url_for("upi"))


@app.route("/upi/add", methods=["POST"])
def upi_add():
    upi_id = request.form.get("upi_id", "").strip()
    label = request.form.get("label", "").strip()[:60]
    if not upi_id or len(upi_id) > 100 or " " in upi_id:
        flash("Enter a valid UPI ID (like name@bank).", "err")
        return redirect(url_for("upi"))
    try:
        qr, mime = _read_qr()
    except ValueError as e:
        flash(str(e), "err")
        return redirect(url_for("upi"))
    db.upi_add(label or upi_id, upi_id, qr, mime)
    flash("UPI account added. It is ON now.")
    return redirect(url_for("upi"))


@app.route("/upi/<int:aid>/qr", methods=["GET"])
def upi_qr(aid):
    a = db.upi_get(aid)
    if not a or not a["qr"]:
        return ("", 404)
    resp = app.response_class(bytes(a["qr"]), mimetype=a["qr_mime"] or "image/png")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


@app.route("/upi/<int:aid>/qr", methods=["POST"])
def upi_qr_set(aid):
    try:
        qr, mime = _read_qr()
    except ValueError as e:
        flash(str(e), "err")
        return redirect(url_for("upi"))
    if not qr or not db.upi_get(aid):
        flash("Choose a QR image first.", "err")
        return redirect(url_for("upi"))
    db.upi_set_qr(aid, qr, mime)
    flash("QR saved.")
    return redirect(url_for("upi"))


@app.route("/upi/<int:aid>/toggle", methods=["POST"])
def upi_toggle(aid):
    db.upi_toggle(aid)
    return redirect(url_for("upi"))


@app.route("/upi/<int:aid>/delete", methods=["POST"])
def upi_delete(aid):
    db.upi_delete(aid)
    flash("UPI account deleted.")
    return redirect(url_for("upi"))


@app.route("/upipay")
def upipay():
    q = request.args.get("q", "").strip()
    view = request.args.get("view", "review")
    if q:
        rows = db.upi_payments(q)
    elif view == "all":
        rows = db.upi_payments()
    else:
        rows = db.upi_payments(status="upi_review")
    return render("upipay.html", rows=rows, q=q, view=view)


@app.route("/upipay/<did>/approve", methods=["POST"])
def upipay_approve(did):
    d = db.approve_upi(did, "web")
    if not d:
        flash("That payment was already handled.", "err")
        return redirect(url_for("upipay"))
    bal = db.get_account(d["user_id"])["balance"] or 0
    _msg(d["user_id"],
         f"🎉 <b>Deposit received</b>\n\n➕ ${d['amount']:.2f}\n👛 Balance: <b>${bal:.2f}</b>",
         f"🎉 <b>充值已到账</b>\n\n➕ ${d['amount']:.2f}\n👛 余额：<b>${bal:.2f}</b>")
    flash(f"Approved - ${d['amount']:.2f} added to {d['user_id']}'s wallet and the customer is being told.")
    return redirect(request.referrer or url_for("upipay"))


@app.route("/upipay/<did>/reject", methods=["POST"])
def upipay_reject(did):
    d = db.reject_upi(did, "web")
    if not d:
        flash("That payment was already handled.", "err")
        return redirect(url_for("upipay"))
    utr = htmllib.escape(d["utr"] or "-")
    _msg(d["user_id"],
         f"❌ <b>UPI payment not verified</b>\n\n🧾 <code>{d['id']}</code>\n🔢 UTR: <code>{utr}</code>\n\nIf you did pay, open Support and send a screenshot.",
         f"❌ <b>UPI 付款未核实</b>\n\n🧾 <code>{d['id']}</code>\n🔢 UTR: <code>{utr}</code>\n\n如果您已付款，请联系客服并发送截图。")
    flash("Payment rejected and the customer was told.")
    return redirect(request.referrer or url_for("upipay"))
