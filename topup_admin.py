"""Admin web page: Supplier top-up status, Binance balance, manual withdraw, history.

Main shop owner only. v3: emergency pause, health check, stats, audit log, CSV export, password rate limit. v2: auto-refill settings per supplier, per-supplier daily cap, resolve button for stuck rows. Secrets (Binance key/secret, Telegram session) are NOT editable here: they live
in Railway variables. Supplier wallet addresses CAN be added/changed here, but every change asks for the
admin password again + typing "yes", the address format is checked, and it is logged. Manual withdraw goes
only to the fixed WD_ADDRESS and needs the admin password again.
"""
import csv
import functools
import hmac
import io
import os
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal

from flask import Blueprint, Response, request, redirect, url_for, flash

import binance_withdraw
import supplier_topup
import tenancy

admin_topup = Blueprint("topup_admin", __name__)

PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Supplier top-up</h2>
<p class="muted">When a supplier's API balance is too low, the bot withdraws USDT from Binance to that supplier's wallet,
sends the hash to the supplier's Telegram bot, waits for the credit, then buys. Binance keys are set in Railway variables;
supplier wallets are added below.</p>

<p><a href="{{ url_for('topup_admin.topup_guide') }}"><b>Guide:</b> poora flow, setup aur settings samjho</a></p>
<div class="card"><h3>Status</h3>
<p>Binance withdraw key: <b class="{{ 'paid' if keys else 'failed' }}">{{ 'ready' if keys else 'not set' }}</b> &nbsp;
Fixed wallet (WD_ADDRESS): <b class="{{ 'paid' if wd else 'failed' }}">{{ 'set' if wd else 'not set' }}</b> &nbsp;
Telegram session: <b class="{{ 'paid' if tg else 'failed' }}">{{ 'ready' if tg else 'not set' }}</b> &nbsp;
Alerts: <b class="{{ 'paid' if alerts else 'failed' }}">{{ 'on' if alerts else 'off (needs BOT_TOKEN + OWNER_ID)' }}</b></p>
<p class="muted">Today (UTC): {{ '%.2f'|format(spent) }} / {{ cap }} USDT used &middot; per supplier default {{ scap }} &middot; limit per withdrawal {{ wd_max }} {{ coin }} ({{ net }})</p>
<p>Suppliers with top-up set: {% if not sup %}<span class="muted">none yet</span>{% endif %}</p>
<form method="post"><input type="hidden" name="action" value="balance"><button type="submit" class="green sm">Check Binance balance</button></form>
</div>

<div class="card"><h3>Safety controls</h3>
<p>Top-ups: {% if paused %}<b class="failed">PAUSED</b>{% else %}<b class="paid">running</b>{% endif %}{% if dry %} &middot; <b class="failed">DRY RUN: no real withdrawals</b>{% endif %}</p>
{% if paused %}<form method="post"><input type="hidden" name="action" value="resume">
<input name="pw" type="password" placeholder="admin password" required autocomplete="off" style="width:140px"> <input name="yes" placeholder="type yes" required autocomplete="off" style="width:70px"> <button type="submit" class="green sm">Resume</button></form>
{% else %}<form method="post"><input type="hidden" name="action" value="pause"><button type="submit" class="red sm">Emergency pause</button></form>{% endif %}
<p class="muted">Pause stops NEW withdrawals (orders and auto-refill). A withdrawal already in progress still finishes. If you change a supplier's wallet address, top-ups to it stay locked for {{ cooldown }} h.</p>
<form method="post"><input type="hidden" name="action" value="health"><button type="submit" class="green sm">Run health check</button> <span class="muted">(can take ~30 s)</span></form>
{% if health %}<p class="muted">Last check: {{ health.ts }} UTC</p>{% for h in health.rows %}<p><b class="{{ 'paid' if h.ok else 'failed' }}">{{ 'OK' if h.ok else 'FIX' }}</b> {{ h.name }}{% if h.detail %} <span class="muted">&middot; {{ h.detail }}</span>{% endif %}</p>{% endfor %}{% endif %}
</div>

{% if stats %}<div class="card"><h3>Stats (USDT)</h3>
<table><tr><th>Supplier</th><th>Today</th><th>7 d</th><th>30 d</th><th>OK / failed</th><th>Avg credit</th></tr>
{% for x in stats %}<tr><td>{{ x.name }}</td><td>{{ '%.2f'|format(x.today) }}</td><td>{{ '%.2f'|format(x.d7) }}</td><td>{{ '%.2f'|format(x.d30) }}</td><td>{{ x.ok }} / {{ x.fail }}</td><td>{{ (x.avg_min ~ ' min') if x.avg_min is not none else '-' }}</td></tr>{% endfor %}</table>
<p class="muted"><a href="{{ url_for('topup_admin.topup_csv') }}">Download full history (CSV)</a></p></div>{% endif %}

<div class="card"><h3>Approvals &amp; withdrawals check</h3>
{% if approve_above %}<p class="muted">Top-ups above {{ approve_above }} USDT wait for your approval here.</p>{% else %}<p class="muted">Approval is off. Set env TOPUP_APPROVE_ABOVE (e.g. 50) so big top-ups need your OK.</p>{% endif %}
{% for a in approvals %}<p><b>#{{ a.id }}</b> {{ names.get(a.supplier_id, a.supplier_id) }}: <b>{{ '%.2f'|format(a.amount) }} USDT</b> <span class="muted">{{ a.created }} UTC</span></p>
<form method="post" style="display:inline"><input type="hidden" name="action" value="approve"><input type="hidden" name="aid" value="{{ a.id }}"><input name="pw" type="password" placeholder="admin password" required autocomplete="off" style="width:130px"> <input name="yes" placeholder="type yes" required autocomplete="off" style="width:70px"> <button type="submit" class="green sm">Approve</button></form>
<form method="post" style="display:inline"><input type="hidden" name="action" value="reject"><input type="hidden" name="aid" value="{{ a.id }}"><button type="submit" class="red sm">Reject</button></form>
{% else %}<p class="muted">Nothing waiting.</p>{% endfor %}
<form method="post"><input type="hidden" name="action" value="reconcile"><button type="submit" class="green sm">Check Binance withdrawals now</button> <span class="muted">(last 24 h, looks for withdrawals this bot did not make)</span></form>
{% if recon %}<p class="muted">Checked {{ recon.ts }} UTC</p>{% for u in recon.rows %}<p><b class="failed">NOT BY BOT</b> {{ u.amount }} {{ u.coin }} ({{ u.network }}) &rarr; {{ u.address[:8] }}...{{ u.address[-6:] }} <span class="muted">{{ u.time }}</span></p>{% else %}<p><b class="paid">OK</b> every withdrawal was made by this bot.</p>{% endfor %}{% endif %}
</div>

<div class="card"><h3>Active rules</h3>
<table>{% for k, v in rules %}<tr><td>{{ k }}</td><td><b>{{ v }}</b></td></tr>{% endfor %}</table>
<p class="muted">Limits are set in Railway variables. Per-supplier amounts are in each supplier's settings below.</p></div>

<div class="card"><h3>Supplier wallets</h3>
<p class="muted">Add each supplier's deposit wallet. Double-check the address and network: crypto sent to a wrong address can't be recovered. Also add the address to your Binance withdrawal whitelist if it is on.</p>
{% for w in wallets %}<p><b>{{ w.name }}</b> &middot; {% if w.mode == 'invoice' %}<span class="paid">invoice mode</span> {{ w.net_button }}{% if w.addr %} &middot; pinned <code>{{ w.addr }}</code>{% endif %}{% else %}<code>{{ w.addr }}</code>{% endif %} &middot; {{ w.network }} &middot; {{ w.bot }}
{% if w.mode == 'invoice' %}<form method="post" style="display:inline"><input type="hidden" name="action" value="test_invoice"><input type="hidden" name="sid" value="{{ w.id }}"><button type="submit" class="sm green">Test (no money)</button></form>{% endif %}
<form method="post" style="display:inline"><input type="hidden" name="action" value="wallet_del"><input type="hidden" name="sid" value="{{ w.id }}">
<input name="pw" type="password" placeholder="admin password" required autocomplete="off" style="width:140px">
<input name="yes" placeholder="type yes" required autocomplete="off" style="width:70px">
<button type="submit" class="red sm">Delete</button></form></p>{% else %}<p class="muted">No wallets saved on the website yet.</p>{% endfor %}
<form method="post"><input type="hidden" name="action" value="wallet_save">
<label>Supplier</label><select name="sid" required>{% for s in all_sups %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}</select>
<label>Mode <span class="hint">fixed = one deposit wallet; invoice = the supplier bot gives a new address + exact amount each time</span></label>
<select name="mode"><option value="fixed">fixed wallet</option><option value="invoice">invoice (e.g. FatBunny)</option></select>
<label>Wallet address <span class="hint">fixed mode: required. invoice mode: leave empty, or paste to pin it</span></label><input name="address" autocomplete="off">
<label>Invoice mode: menu text <span class="hint">what you tap/send to open the wallet, e.g. Wallet</span></label><input name="menu_text" placeholder="Wallet" autocomplete="off">
<label>Invoice mode: network button <span class="hint">part of the button text, e.g. BEP20</span></label><input name="net_button" placeholder="BEP20" autocomplete="off">
<label>Network</label><select name="network">{% for n in networks %}<option>{{ n }}</option>{% endfor %}</select>
<label>Supplier Telegram bot</label><input name="bot" required placeholder="@supplier_bot" autocomplete="off">
<label>Hash message <span class="hint">use {hash}, e.g. /deposit {hash}</span></label><input name="hash_msg" value="{hash}" required autocomplete="off">
<label>Admin password</label><input name="pw" type="password" required autocomplete="off">
<label>Type <b>yes</b> to confirm</label><input name="yes" required autocomplete="off">
<button type="submit" class="red">Save wallet</button></form>
</div>

{% if sup %}<div class="card"><h3>Top-up settings</h3>
<p class="muted">Withdraw = missing balance + fee buffer, never less than the minimum (1 to {{ wd_max }} {{ coin }}). <b>Auto-refill</b>: when the supplier's API balance drops below <i>Low balance</i>, the bot tops it up to <i>Target balance</i> in the background, so orders never wait. Leave low/target empty to keep top-up only on demand.</p>
{% for s in sup %}<form method="post" style="margin-bottom:14px"><input type="hidden" name="action" value="amounts"><input type="hidden" name="sid" value="{{ s.id }}">
<b>{{ s.name }}</b>
<label>Minimum top-up</label><input name="min_topup" value="{{ s.min_topup }}" inputmode="decimal" required>
<label>Fee buffer</label><input name="fee_buffer" value="{{ s.fee_buffer }}" inputmode="decimal" required>
<label>Daily cap for this supplier (USDT)</label><input name="daily_cap" value="{{ s.daily_cap }}" inputmode="decimal" required>
<label>Low balance (refill below this)</label><input name="low_balance" value="{{ s.low_balance }}" inputmode="decimal" placeholder="e.g. 10">
<label>Target balance (refill up to this)</label><input name="target_balance" value="{{ s.target_balance }}" inputmode="decimal" placeholder="e.g. 30">
<label><input type="checkbox" name="auto" value="1" {{ 'checked' if s.auto }}> Auto-refill on</label>
<button type="submit" class="sm">Save</button></form>{% endfor %}</div>{% endif %}

<div class="card"><h3>Manual withdraw</h3>
{% if keys and wd %}
<p class="muted">Sends to the fixed wallet <code>{{ addr }}</code> only.</p>
<form method="post"><input type="hidden" name="action" value="withdraw">
<label>Amount ({{ coin }})</label><input name="amount" required inputmode="decimal" placeholder="e.g. 20">
<label>Admin password <span class="hint">asked again for safety</span></label><input name="pw" type="password" required autocomplete="off">
<label>Type <b>yes</b> to confirm</label><input name="yes" required autocomplete="off">
<button type="submit" class="red">Withdraw</button></form>
{% else %}<p class="muted">Set BINANCE_WD_KEY, BINANCE_WD_SECRET and WD_ADDRESS first.</p>{% endif %}
</div>

<div class="card"><h3>History</h3>
<table><tr><th>#</th><th>Supplier</th><th>USDT</th><th>Status</th><th>TXID</th><th>When (UTC)</th></tr>
{% for r in rows %}<tr><td>{{ r.id }}</td><td>{{ names.get(r.supplier_id, 'manual' if not r.supplier_id else r.supplier_id) }}</td>
<td>{{ '%.2f'|format(r.amount or 0) }}{% if r.verified == 'ok' %}<br><span class="paid">on-chain &#10003;</span>{% elif r.verified == 'mismatch' %}<br><span class="failed">on-chain &#10007;</span>{% endif %}{% if r.bal_before is not none and r.bal_after is not none %}<br><span class="muted">{{ '%.2f'|format(r.bal_before) }} &rarr; {{ '%.2f'|format(r.bal_after) }}</span>{% endif %}</td>
<td class="{{ 'paid' if r.status in ('credited','manual') else ('failed' if r.status == 'failed' else '') }}">{{ r.status }}{% if r.error %}<br><span class="muted">{{ r.error[:120] }}</span>{% endif %}{% if r.reply %}<br><span class="muted">bot: {{ r.reply[:80] }}{% if r.attempts and r.attempts > 1 %} ({{ r.attempts }} tries){% endif %}</span>{% endif %}
{% if r.status in ('withdrawing','withdrawn','hash_ready','sent') and r.supplier_id %}<form method="post" style="margin-top:4px"><input type="hidden" name="action" value="resolve"><input type="hidden" name="tid" value="{{ r.id }}">
<select name="result"><option value="failed">money did NOT leave</option><option value="credited">money arrived</option></select>
<input name="pw" type="password" placeholder="admin password" required autocomplete="off" style="width:110px"><input name="yes" placeholder="yes" required autocomplete="off" style="width:45px">
<button type="submit" class="sm red">Close</button></form>{% endif %}</td>
<td><code>{{ (r.txid or '')[:14] }}{{ '...' if r.txid }}</code></td><td>{{ r.updated or r.created }}</td></tr>
{% else %}<tr><td colspan="6" class="muted">No top-ups yet.</td></tr>{% endfor %}</table>
<p class="muted">A row on <b>withdrawing</b> is checked against Binance automatically (by order id) on the next order or refill. Use <b>Close</b> only if it stays stuck and you have checked Binance history yourself.</p>
</div>
<div class="card"><h3>Audit log</h3>
<table><tr><th>When (UTC)</th><th>Action</th><th>Details</th></tr>
{% for a in audit_log %}<tr><td>{{ a.ts }}</td><td>{{ a.action }}</td><td class="muted">{{ a.detail }}</td></tr>{% else %}<tr><td colspan="3" class="muted">Nothing yet.</td></tr>{% endfor %}</table></div>
{% endblock %}"""


_FAILS = {}                                     # ip -> timestamps of wrong passwords
_HEALTH = {"ts": None, "rows": []}              # last health check (per process)
_RECON = {"ts": None, "rows": []}               # last withdrawals check (per process)


def _ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()


def _check_pw():
    """Admin password + typing yes. 5 wrong passwords in 15 min lock this IP out for the rest of the window."""
    ip, now = _ip(), time.time()
    recent = [x for x in _FAILS.get(ip, []) if now - x < 900]
    if len(recent) >= 5:
        raise ValueError("Too many wrong passwords. Try again in 15 minutes.")
    pw, admin_pw = request.form.get("pw", ""), os.getenv("ADMIN_PASSWORD", "")
    if not admin_pw or not hmac.compare_digest(pw, admin_pw):
        recent.append(now)
        _FAILS[ip] = recent
        if len(recent) >= 3:
            supplier_topup.notify(f"⚠️ {len(recent)} wrong admin passwords on the top-up page from {ip}.", key=f"pw-{ip}")
        raise ValueError("Wrong admin password.")
    _FAILS.pop(ip, None)
    if request.form.get("yes", "").strip().lower() != "yes":
        raise ValueError("Type yes to confirm.")


def _tg_ready():
    return not any(supplier_topup._tg(k).startswith("PASTE_") for k in supplier_topup._TG)


def _start_auto_refill():
    """Background auto-refill: runs inside the process that serves this admin site (one thread per process)."""
    try:
        import provider

        def main_db():
            for r in tenancy.all_rows():
                if r.get("is_main"):
                    t = tenancy.get_tenant(r["id"])
                    return t.db if t else None
            return None

        supplier_topup.start_watcher(main_db, lambda d: d.list_suppliers(), lambda d, sid: d.get_supplier(sid),
                                     provider.balance_for)
    except Exception:
        supplier_topup.log.exception("auto-refill watcher could not start")


GUIDE_PAGE = r"""{% extends 'base.html' %}{% block body %}
<style>
.tg-flow{list-style:none;margin:0;padding:0}
.tg-flow li{position:relative;padding:0 0 16px 46px;min-height:36px}
.tg-flow li::before{content:"";position:absolute;left:15px;top:32px;bottom:0;width:2px;background:rgba(128,128,128,.35)}
.tg-flow li:last-child::before{display:none}
.tg-flow .n{position:absolute;left:0;top:0;width:32px;height:32px;border-radius:50%;background:#0b7a85;color:#fff;display:grid;place-items:center;font-weight:600;font-size:14px}
.tg-flow b{display:block}
.tg-t{display:inline-block;margin-top:3px;font-size:13px;padding:1px 8px;border-radius:5px;background:rgba(232,176,74,.22)}
.tg-box{border-left:4px solid #0b7a85;padding:8px 12px;margin:10px 0;background:rgba(128,128,128,.10);border-radius:0 8px 8px 0}
.tg-warn{border-left-color:#d99a1c}
.tg-msg{white-space:pre-line}
.tg-scroll{overflow-x:auto}
.tg-nav a{margin-right:12px;white-space:nowrap}
.tg-formula{font-family:ui-monospace,Menlo,Consolas,monospace;padding:10px 12px;border-radius:8px;background:rgba(11,122,133,.15);overflow-x:auto}
</style>
<p><a href="{{ url_for('topup_admin.topup') }}">&larr; Back to Supplier top-up</a></p>
<p class="muted tg-nav" style="overflow-x:auto;white-space:nowrap"><a href="#flow">Flow</a><a href="#amount">Kitna bhejta hai</a><a href="#setup">Setup</a><a href="#vars">Variables</a><a href="#safety">Safety</a><a href="#msgs">Customer messages</a><a href="#help">Dikkat aaye to</a></p>

<div class="card"><h3>Aapki abhi ki settings</h3>
<div class="tg-scroll"><table>{% for k, v in rules %}<tr><td>{{ k }}</td><td><b>{{ v }}</b></td></tr>{% endfor %}</table></div>
<p class="muted">Limits Railway variables se badalti hain. Har supplier ka minimum top-up, fee buffer, daily cap aur auto-refill neeche Supplier wallets mein hai.</p></div>

<div class="card" id="flow"><h3>Order se delivery tak</h3>
<ol class="tg-flow">
<li><span class="n">1</span><b>Customer order deta hai</b>&ldquo;2 to 6 minutes&rdquo; wala message turant jata hai.</li>
<li><span class="n">2</span><b>Supplier ka balance check</b>Balance kaafi hai to seedha step 7, top-up ki zarurat nahi.</li>
<li><span class="n">3</span><b>Safety checks</b>Pause, wallet lock, ek ghante ki limit, daily cap, approval (agar on ho).</li>
<li><span class="n">4</span><b>Binance se withdraw</b>Har withdraw ka order id hota hai, isliye crash ke baad bhi duplicate nahi banta.<span class="tg-t">TXID: 30 sec &ndash; 3 min</span></li>
<li><span class="n">5</span><b>On-chain check</b>Bot blockchain par dekhta hai ki transaction sahi address par sahi amount le gaya, aur 2 confirmations ka wait karta hai.</li>
<li><span class="n">6</span><b>Hash supplier ke bot ko</b>Reply ok, fail ya unknown judge hota hai. Fail par hash 3 baar tak dobara jata hai.<span class="tg-t">Credit: 1 &ndash; 3 min</span></li>
<li><span class="n">7</span><b>Order complete</b>Product kharida jata hai, customer ko &ldquo;delivered&rdquo; message milta hai.</li>
</ol>
<div class="tg-box"><b>Top-up chahiye to extra time:</b> aam taur par 2 se 6 minute, sabse kharab case mein lagbhag 10 minute. Auto-refill ON ho to customer ko ye wait lagta hi nahi, balance pehle se bhar diya jata hai.</div></div>

<div class="card" id="amount"><h3>Kitna USDT bhejta hai</h3>
<p class="tg-formula">bhejna = max( product amount &minus; supplier ka balance + fee buffer , minimum top-up )</p>
<p>Default mein fee buffer 0 hai aur Binance ki fee upar se nahi judti, yaani exact amount jata hai.</p>
<div class="tg-scroll"><table>
<tr><th>Product</th><th>Supplier balance</th><th>Bot bhejta hai</th><th>Order ke baad</th></tr>
<tr><td>$5</td><td>0</td><td>5.00</td><td>0</td></tr>
<tr><td>$3 (minimum 5)</td><td>0</td><td>5.00</td><td>2 bacha</td></tr>
<tr><td>$7</td><td>2</td><td>5.00 (5 chahiye)</td><td>0</td></tr>
<tr><td>$5</td><td>5 ya zyada</td><td>kuch nahi</td><td>top-up skip</td></tr>
</table></div>
<div class="tg-box tg-warn"><b>Exact amount chahiye to:</b> us supplier ka <b>Minimum top-up = 0</b> aur <b>Fee buffer = 0</b> karo. Pehle supplier se poochh lo ki uska minimum deposit kitna hai.</div>
<div class="tg-box tg-warn"><b>Fee:</b> Binance fee bheje gaye amount mein se kat leta hai. BEP20 par bahut kam hai (bot 0.001 tak ki kami ko credited maanta hai). Supplier ko poora exact amount dena ho to <code>TOPUP_ADD_BINANCE_FEE=1</code> set karo, tab bot amount + fee nikalta hai.</div>
<p class="muted">Binance mein itna USDT hona chahiye jitna nikalna hai. Kam ho to bot withdraw nahi karta aur alert bhejta hai.</p></div>

<div class="card" id="setup"><h3>Setup, ek baar</h3>
<ol>
<li>Purani files ka backup lo, phir <code>binance_withdraw.py</code>, <code>supplier_topup.py</code>, <code>topup_admin.py</code> ek saath replace karo. <code>order_messages.py</code> bhi same folder mein rakho.</li>
<li>Binance API key: sirf Withdraw (aur Reading), trading ya transfer band, trusted IP set.</li>
<li>Railway variables daalo (neeche table). Pehle test ke liye <code>TOPUP_DRY_RUN=1</code>.</li>
<li>Is page par <b>Run health check</b> dabao, jo &ldquo;FIX&rdquo; dikhe theek karo.</li>
<li>Har supplier ka wallet add karo: network, bot username, hash message (jaise <code>/deposit {hash}</code>). Invoice wale supplier mein mode &ldquo;invoice&rdquo; rakho aur <b>Test (no money)</b> chalao.</li>
<li>Dry run mein ek order chalao: Telegram par &ldquo;DRY RUN&rdquo; aaye, Binance se kuch na nikle.</li>
<li><code>TOPUP_DRY_RUN</code> hatao, chhote amount ka ek real top-up karo. History mein &ldquo;credited&rdquo; aur &ldquo;on-chain &#10003;&rdquo; dikhna chahiye.</li>
<li>Har supplier ke liye auto-refill set karo (jaise low 20, target 60, daily cap) aur Auto ON.</li>
</ol>
<div class="tg-box tg-warn"><b>Alag alag address:</b> har supplier ka wallet alag save hota hai. Address badalne par 6 ghante ka lock lagta hai. Binance mein &ldquo;whitelist only&rdquo; ON ho to har supplier ka address wahan bhi add karna hoga, aur invoice mode whitelist ke saath nahi chalta.</div></div>

<div class="card" id="vars"><h3>Railway variables</h3>
<div class="tg-scroll"><table>
<tr><th>Naam</th><th>Kaam</th></tr>
<tr><td><code>BINANCE_WD_KEY</code><br><code>BINANCE_WD_SECRET</code></td><td>Withdraw wali API key (IP restricted)</td></tr>
<tr><td><code>WD_ADDRESS</code></td><td>Aapka apna wallet, sirf manual withdraw ke liye</td></tr>
<tr><td><code>WD_MAX</code></td><td>Ek withdrawal ki max limit (default 60)</td></tr>
<tr><td><code>TG_API_ID</code> <code>TG_API_HASH</code> <code>TG_SESSION</code></td><td>Telethon login jo supplier bots ko message bhejta hai</td></tr>
<tr><td><code>BOT_TOKEN</code> <code>OWNER_ID</code></td><td>Aapko Telegram alerts</td></tr>
<tr><td><code>ADMIN_PASSWORD</code></td><td>Bade kaam ka password</td></tr>
<tr><td><code>TOPUP_DRY_RUN</code></td><td>1 = poora flow, bina asli withdrawal ke</td></tr>
<tr><td><code>TOPUP_APPROVE_ABOVE</code></td><td>Is amount se bade top-up ko aapki approval (0 = off)</td></tr>
<tr><td><code>TOPUP_AUTOPAUSE_ON_UNKNOWN</code></td><td>1 = anjaan withdrawal dikhe to top-ups khud pause</td></tr>
<tr><td><code>TOPUP_WALLET_COOLDOWN_H</code></td><td>Address badalne ke baad lock ke ghante (default 6, 0 = off)</td></tr>
<tr><td><code>TOPUP_ADD_BINANCE_FEE</code></td><td>1 = amount ke upar Binance fee bhi (default 0)</td></tr>
<tr><td><code>TOPUP_REPORT_HOUR</code></td><td>Daily report ka UTC ghanta (default 18, -1 = off)</td></tr>
<tr><td><code>TOPUP_RPC_BSC</code> (ETH, MATIC...)</td><td>On-chain check ke liye apna RPC (optional)</td></tr>
<tr><td><code>ORDER_MSG_RECEIVED</code> <code>_DELAY</code> <code>_DELIVERED</code> <code>_FAILED</code></td><td>Customer message ka text</td></tr>
<tr><td><code>ORDER_DELAY_NOTICE_SEC</code></td><td>Delay message kitni der baad (default 360)</td></tr>
</table></div>
<p class="muted">Secrets sirf Railway variables mein, code mein kabhi nahi.</p></div>

<div class="card" id="safety"><h3>Safety ki parten</h3>
<ul>
<li>Address ka format network ke hisaab se check hota hai.</li>
<li>Ek withdrawal ki limit (<code>WD_MAX</code>), roz ka cap (sab suppliers 100, ek supplier 60 USDT), ek supplier ko ek ghante mein max 4 top-ups.</li>
<li>Address badalne par 6 ghante ka lock aur Telegram alert.</li>
<li>Bade amount par approval, har approval sirf ek top-up ke liye.</li>
<li>Har 30 minute Binance history se anjaan withdrawal ki pehchaan.</li>
<li>Hash bhejne se pehle blockchain par amount aur address ki jaanch.</li>
<li>Galat password 5 baar par 15 minute ka lockout. Wallet badalna, withdraw, approve aur resume mein password + &ldquo;yes&rdquo;.</li>
<li>Auto-refill baar baar fail ho to 5, 10, 20, 40, 60 minute ke gap se try karta hai.</li>
</ul></div>

<div class="card" id="msgs"><h3>Customer ko messages</h3>
<p class="muted"><code>order_messages.py</code> se jate hain. Text badalna ho to <code>ORDER_MSG_*</code> variables use karo.</p>
<div class="tg-box tg-msg"><b>Order lete hi</b>
&#9989; Thank you for your order!
Your order is being processed and will be delivered here within 2 to 6 minutes. Please keep this chat open.
We appreciate your patience.</div>
<div class="tg-box tg-msg"><b>6 minute se zyada lage to</b>
Thank you for waiting. Your order is taking slightly longer than usual, but it is still in progress. We expect to deliver it within the next 5 minutes.</div>
<div class="tg-box tg-msg"><b>Delivery ke baad</b>
&#127881; Your order has been delivered!
Thank you for choosing us. We hope you are happy with your purchase.
If you face any issue, just reply here and we will be glad to help. We would love to see you again. &#128153;</div>
<p class="muted">Order jaldi ho jaye to delay wala message jata hi nahi. Jodne ke liye <code>bot.py</code> / <code>provider.py</code> mein <code>n.start()</code>, <code>n.done()</code>, <code>n.failed()</code> lagte hain.</p></div>

<div class="card" id="help"><h3>Dikkat aaye to</h3>
<p><b>Page par &ldquo;Internal Server Error&rdquo;.</b> Is page par error ka asli text dikhta hai, uski aakhri 10 se 15 lines dekho. Teeno files hamesha ek saath replace karo.</p>
<p><b>&ldquo;Binance balance too low&rdquo;.</b> Binance mein USDT daalo. Bot 30 USDT se neeche jaane par pehle se alert deta hai.</p>
<p><b>&ldquo;Hash sent but balance not credited yet&rdquo;.</b> Paisa safe hai. Row khuli rehti hai aur agle order ya 5 minute ke watcher se wahin se aage badhta hai, dobara withdraw nahi hota. Zyada der lage to history mein supplier bot ka reply dekho.</p>
<p><b>&ldquo;Wallet was changed recently&rdquo;.</b> Address badalne ke baad 6 ghante ka lock. Intezaar karo, ya <code>TOPUP_WALLET_COOLDOWN_H</code> kam karo.</p>
<p><b>&ldquo;NOT made by this bot&rdquo; alert.</b> Aapne Binance app se khud withdraw kiya ho to ignore karo, warna turant pause karo aur API key badlo.</p>
<p><b>Supplier ne order reject kiya.</b> Exact amount par fee ke baad 4.999999 mil sakta hai. Supplier exact balance maange to <code>TOPUP_ADD_BINANCE_FEE=1</code> set karo.</p></div>
<p class="muted">Version v5. Logic nakli Binance, Telegram aur blockchain ke saath test hua hai. Pehle chhote amount ka ek real top-up karke dekho.</p>
{% endblock %}"""


def _show_errors(fn):
    """If the top-up page crashes, show the owner the real error as plain text (and log it) instead of a blank
    'Internal Server Error'. Other users still get the normal error page."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            tb = traceback.format_exc()
            supplier_topup.log.error("top-up page crashed:\n%s", tb)
            try:
                is_main = tenancy.cur().is_main
            except Exception:
                is_main = False
            if is_main:
                return Response("Top-up page error. Send the last 15 lines to your developer:\n\n" + tb, 500, mimetype="text/plain")
            raise
    return wrapper


def register(app, db):
    @admin_topup.route("/topup", methods=["GET", "POST"])
    @_show_errors
    def topup():
        if not tenancy.cur().is_main:               # sellers never see money-moving pages
            return redirect(url_for("dashboard"))
        supplier_topup._table(db)
        supplier_topup._overrides(db, 0)          # makes sure topup_cfg exists
        if request.method == "POST":
            act = request.form.get("action", "")
            try:
                if act == "balance":
                    flash(f"Binance {binance_withdraw.cfg('WD_COIN')} balance: {binance_withdraw.spot_balance()}")
                elif act == "pause":
                    supplier_topup.set_paused(db, True, actor=_ip())
                    flash("Top-ups PAUSED. No new withdrawals until you resume.")
                elif act == "resume":
                    _check_pw()
                    supplier_topup.set_paused(db, False, actor=_ip())
                    flash("Top-ups resumed.")
                elif act == "health":
                    _HEALTH["rows"] = supplier_topup.health(db)
                    _HEALTH["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    bad = sum(1 for h in _HEALTH["rows"] if not h["ok"])
                    flash("Health check: all OK." if not bad else f"Health check: {bad} thing(s) to fix, see Safety controls.")
                elif act == "approve":
                    _check_pw()
                    supplier_topup.approve(db, request.form.get("aid", ""), actor=_ip())
                    flash("Approved. The top-up continues with the next order or the next auto-refill check (within 5 min).")
                elif act == "reject":
                    supplier_topup.reject(db, request.form.get("aid", ""), actor=_ip())
                    flash("Rejected.")
                elif act == "reconcile":
                    _RECON["rows"] = supplier_topup.reconcile(db)
                    _RECON["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    flash("Check done." if not _RECON["rows"] else f"{len(_RECON['rows'])} withdrawal(s) not made by this bot, see below.")
                elif act == "wallet_save":
                    _check_pw()
                    sid = int(request.form.get("sid", "0") or 0)
                    if not db.get_supplier(sid):
                        raise ValueError("Unknown supplier.")
                    w = supplier_topup.check_wallet(request.form.get("address"), request.form.get("network"),
                                                    request.form.get("bot"), request.form.get("hash_msg"),
                                                    mode=request.form.get("mode"), menu_text=request.form.get("menu_text"),
                                                    net_button=request.form.get("net_button"))
                    supplier_topup.save_wallet(db, sid, w)
                    flash("Saved: " + ((w["address"][:6] + "..." + w["address"][-6:]) if w["address"] else "invoice mode") +
                          " (" + w["network"] + "). " + ("Press Test to check the bot steps." if w["mode"] == "invoice" else "Check the address once more."))
                elif act == "test_invoice":
                    sid = int(request.form.get("sid", "0") or 0)
                    sdict = db.get_supplier(sid)
                    if not sdict:
                        raise ValueError("Unknown supplier.")
                    inv = supplier_topup.test_invoice(db, sdict)
                    flash(f"Bot steps OK. It asked for exactly {inv['amount']} USDT to {inv['address'][:8]}...{inv['address'][-6:]}. "
                          "Nothing was sent: do not pay this test invoice.")
                elif act == "wallet_del":
                    _check_pw()
                    supplier_topup.delete_wallet(db, int(request.form.get("sid", "0") or 0))
                    flash("Wallet removed.")
                elif act == "amounts":
                    sid = int(request.form.get("sid", "0") or 0)
                    sdict = db.get_supplier(sid)
                    if not sdict or not supplier_topup.has_config(sdict["name"], sid, db):
                        raise ValueError("This supplier has no top-up set.")
                    num = lambda k: (float(request.form.get(k, "").replace(",", ".")) if request.form.get(k, "").strip() else None)
                    mn, fb, dc, lo, tg = num("min_topup"), num("fee_buffer"), num("daily_cap"), num("low_balance"), num("target_balance")
                    wd_max = float(binance_withdraw.cfg("WD_MAX"))
                    if mn is None or not (1 <= mn <= wd_max and mn <= supplier_topup.DAILY_CAP):
                        raise ValueError(f"Minimum must be between 1 and {binance_withdraw.cfg('WD_MAX')}.")
                    if fb is None or not (0 <= fb <= 5):
                        raise ValueError("Fee buffer must be between 0 and 5.")
                    if dc is None or not (mn <= dc <= supplier_topup.DAILY_CAP):
                        raise ValueError(f"Daily cap must be between the minimum and {supplier_topup.DAILY_CAP}.")
                    auto = 1 if request.form.get("auto") else 0
                    if (lo is None) != (tg is None):
                        raise ValueError("Fill both low and target balance, or leave both empty.")
                    if lo is not None and not (0 < lo < tg):
                        raise ValueError("Target balance must be above low balance.")
                    if auto and lo is None:
                        raise ValueError("Auto-refill needs low and target balance.")
                    supplier_topup.save_settings(db, sid, min_topup=mn, fee_buffer=fb, daily_cap=dc,
                                                 low_balance=lo, target_balance=tg, auto=auto)
                    flash("Saved." + (" Auto-refill is ON." if auto else ""))
                elif act == "resolve":
                    _check_pw()
                    supplier_topup.resolve_row(db, int(request.form.get("tid", "0") or 0), request.form.get("result", ""))
                    flash("Top-up closed.")
                elif act == "withdraw":
                    _check_pw()
                    if supplier_topup.is_paused(db):
                        raise ValueError("Top-ups are paused. Resume first.")
                    if binance_withdraw.dry_run():
                        raise ValueError("Dry-run mode is on (TOPUP_DRY_RUN): no real withdrawals.")
                    if not binance_withdraw.configured():
                        raise ValueError("Binance withdraw is not configured.")
                    amt = binance_withdraw.parse_amount(request.form.get("amount", ""))
                    if supplier_topup._spent_today(db) + float(amt) > supplier_topup.DAILY_CAP:
                        raise ValueError(f"Daily cap {supplier_topup.DAILY_CAP} reached.")
                    c = db.conn()
                    tid = c.execute("INSERT INTO topups(supplier_id,amount,status,created,updated) VALUES(0,?,?,?,?)",
                                    (float(amt), "withdrawing", supplier_topup._now(), supplier_topup._now())).lastrowid
                    c.commit(); c.close()
                    try:
                        c = db.conn(); c.execute("UPDATE topups SET order_ref=? WHERE id=?", (f"tu{tid}", tid)); c.commit(); c.close()
                        wid = binance_withdraw.withdraw(amt, order_id=f"tu{tid}")
                    except ValueError as e:
                        supplier_topup._set(db, tid, "failed", error=str(e)); raise
                    except RuntimeError as e:
                        if str(e).startswith("Binance HTTP 4"):
                            supplier_topup._set(db, tid, "failed", error=str(e)[:300])
                        raise
                    txid = None
                    try:
                        txid = binance_withdraw.wait_txid(wid, tries=3, delay=5)
                    except RuntimeError:
                        pass
                    supplier_topup._set(db, tid, "manual", wid=str(wid), **({"txid": txid} if txid else {}))
                    flash(f"Withdrawal sent (Binance id {wid}). " + (f"TXID: {txid}" if txid else "TXID not ready yet, see Binance history."))
                if act in ("wallet_save", "wallet_del", "amounts", "resolve", "withdraw", "approve", "reject"):
                    a = request.form.get("address", "").strip()
                    bits = [f"{k}={request.form.get(k)}" for k in ("sid", "tid", "result", "amount", "mode", "network", "bot",
                                                                     "min_topup", "fee_buffer", "daily_cap", "low_balance", "target_balance", "auto", "aid")
                            if request.form.get(k)]
                    if a:
                        bits.append("address=" + a[:6] + "..." + a[-6:])
                    supplier_topup.audit(db, act, " ".join(bits) + f" ip={_ip()}", actor=_ip())
            except Exception as e:
                flash(str(e)[:250], "err")
            return redirect(url_for("topup_admin.topup"))
        c = db.conn()
        rows = [dict(r) for r in c.execute("SELECT * FROM topups ORDER BY id DESC LIMIT 50").fetchall()]
        c.close()
        sups = db.list_suppliers()
        names = {s["id"]: s["name"] for s in sups}
        sup = []
        for s in sups:
            base = supplier_topup._pay_cfg(s["name"], s["id"], db)
            if base:
                v = {**base, **supplier_topup._overrides(db, s["id"])}
                sup.append({"id": s["id"], "name": s["name"], "min_topup": v.get("min_topup", 5.0),
                            "fee_buffer": v.get("fee_buffer", 0.0),
                            "daily_cap": v.get("daily_cap", supplier_topup.SUPPLIER_DAILY_CAP),
                            "low_balance": v.get("low_balance", ""), "target_balance": v.get("target_balance", ""),
                            "auto": v.get("auto", 0)})
        wallets = []
        for s in sups:
            w = supplier_topup._db_wallet(db, s["id"])
            if w:
                wallets.append({"id": s["id"], "name": s["name"], "network": w["network"], "bot": w["bot"],
                                "mode": w.get("mode", "fixed"), "net_button": w.get("net_button", ""),
                                "addr": (w["address"][:6] + "..." + w["address"][-6:]) if w["address"] else ""})
        addr = binance_withdraw.cfg("WD_ADDRESS")
        try:
            stats = supplier_topup.stats(db)
        except Exception:
            supplier_topup.log.warning("top-up stats failed", exc_info=True)
            stats = []
        import web
        return web.render_str(PAGE, paused=supplier_topup.is_paused(db), dry=binance_withdraw.dry_run(),
                              health=(_HEALTH if _HEALTH["ts"] else None), stats=stats,
                              audit_log=supplier_topup.audit_rows(db),
                              rules=supplier_topup.effective_config(), approvals=supplier_topup.approvals_pending(db), approve_above=(supplier_topup.approve_above() or None),
                              recon=(_RECON if _RECON["ts"] else None),
                              cooldown=os.getenv("TOPUP_WALLET_COOLDOWN_H", str(supplier_topup.WALLET_COOLDOWN_H)),
                              rows=rows, names=names, sup=sup, wallets=wallets, all_sups=sups, networks=supplier_topup.NETWORKS, keys=binance_withdraw.keys_ready(),
                              wd=binance_withdraw.configured(), tg=_tg_ready(),
                              spent=supplier_topup._spent_today(db), cap=supplier_topup.DAILY_CAP, scap=supplier_topup.SUPPLIER_DAILY_CAP,
                              alerts=bool(os.getenv("BOT_TOKEN") and (os.getenv("ALERT_CHAT_ID") or os.getenv("OWNER_ID"))),
                              wd_max=binance_withdraw.cfg("WD_MAX"), coin=binance_withdraw.cfg("WD_COIN"),
                              net=binance_withdraw.cfg("WD_NETWORK"),
                              addr=(addr[:6] + "..." + addr[-6:]) if len(addr) > 14 else "not set")

    @admin_topup.route("/topup/guide")
    @_show_errors
    def topup_guide():
        if not tenancy.cur().is_main:               # sellers never see money-moving pages
            return redirect(url_for("dashboard"))
        import web
        return web.render_str(GUIDE_PAGE, rules=supplier_topup.effective_config())

    @admin_topup.route("/topup/export.csv")
    @_show_errors
    def topup_csv():
        if not tenancy.cur().is_main:
            return redirect(url_for("dashboard"))
        supplier_topup._table(db)
        c = db.conn()
        rows = [dict(r) for r in c.execute("SELECT * FROM topups ORDER BY id DESC").fetchall()]
        c.close()
        cols = ["id", "supplier_id", "amount", "status", "bal_before", "bal_after", "verified", "to_addr", "source", "order_ref", "wid", "txid",
                "attempts", "error", "reply", "created", "updated"]

        def safe(v):                                # stop spreadsheet formula injection from supplier-bot replies
            v = "" if v is None else str(v)
            return "'" + v if v[:1] in ("=", "+", "-", "@") else v
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([safe(r.get(k)) for k in cols])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=topups.csv"})

    _start_auto_refill()
    app.register_blueprint(admin_topup)
