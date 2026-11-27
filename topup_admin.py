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
<td>{{ '%.2f'|format(r.amount or 0) }}{% if r.bal_before is not none and r.bal_after is not none %}<br><span class="muted">{{ '%.2f'|format(r.bal_before) }} &rarr; {{ '%.2f'|format(r.bal_after) }}</span>{% endif %}</td>
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
                if act in ("wallet_save", "wallet_del", "amounts", "resolve", "withdraw"):
                    a = request.form.get("address", "").strip()
                    bits = [f"{k}={request.form.get(k)}" for k in ("sid", "tid", "result", "amount", "mode", "network", "bot",
                                                                     "min_topup", "fee_buffer", "daily_cap", "low_balance", "target_balance", "auto")
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
                            "fee_buffer": v.get("fee_buffer", 0.5),
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
                              cooldown=os.getenv("TOPUP_WALLET_COOLDOWN_H", str(supplier_topup.WALLET_COOLDOWN_H)),
                              rows=rows, names=names, sup=sup, wallets=wallets, all_sups=sups, networks=supplier_topup.NETWORKS, keys=binance_withdraw.keys_ready(),
                              wd=binance_withdraw.configured(), tg=_tg_ready(),
                              spent=supplier_topup._spent_today(db), cap=supplier_topup.DAILY_CAP, scap=supplier_topup.SUPPLIER_DAILY_CAP,
                              alerts=bool(os.getenv("BOT_TOKEN") and (os.getenv("ALERT_CHAT_ID") or os.getenv("OWNER_ID"))),
                              wd_max=binance_withdraw.cfg("WD_MAX"), coin=binance_withdraw.cfg("WD_COIN"),
                              net=binance_withdraw.cfg("WD_NETWORK"),
                              addr=(addr[:6] + "..." + addr[-6:]) if len(addr) > 14 else "not set")

    @admin_topup.route("/topup/export.csv")
    @_show_errors
    def topup_csv():
        if not tenancy.cur().is_main:
            return redirect(url_for("dashboard"))
        supplier_topup._table(db)
        c = db.conn()
        rows = [dict(r) for r in c.execute("SELECT * FROM topups ORDER BY id DESC").fetchall()]
        c.close()
        cols = ["id", "supplier_id", "amount", "status", "bal_before", "bal_after", "source", "order_ref", "wid", "txid",
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
