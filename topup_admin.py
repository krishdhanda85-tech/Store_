"""Admin web page: Supplier top-up status, Binance balance, manual withdraw, history.

Main shop owner only. Secrets (Binance key/secret, Telegram session) are NOT editable here: they live
in Railway variables. Supplier wallet addresses CAN be added/changed here, but every change asks for the
admin password again + typing "yes", the address format is checked, and it is logged. Manual withdraw goes
only to the fixed WD_ADDRESS and needs the admin password again.
"""
import hmac
import os
from decimal import Decimal

from flask import Blueprint, request, redirect, url_for, flash

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
Telegram session: <b class="{{ 'paid' if tg else 'failed' }}">{{ 'ready' if tg else 'not set' }}</b></p>
<p class="muted">Today (UTC): {{ '%.2f'|format(spent) }} / {{ cap }} USDT used &middot; limit per withdrawal {{ wd_max }} {{ coin }} ({{ net }})</p>
<p>Suppliers with top-up set: {% if not sup %}<span class="muted">none yet</span>{% endif %}</p>
<form method="post"><input type="hidden" name="action" value="balance"><button type="submit" class="green sm">Check Binance balance</button></form>
</div>

<div class="card"><h3>Supplier wallets</h3>
<p class="muted">Add each supplier's deposit wallet. Double-check the address and network: crypto sent to a wrong address can't be recovered. Also add the address to your Binance withdrawal whitelist if it is on.</p>
{% for w in wallets %}<p><b>{{ w.name }}</b> &middot; <code>{{ w.addr }}</code> &middot; {{ w.network }} &middot; {{ w.bot }}
<form method="post" style="display:inline"><input type="hidden" name="action" value="wallet_del"><input type="hidden" name="sid" value="{{ w.id }}">
<input name="pw" type="password" placeholder="admin password" required autocomplete="off" style="width:140px">
<input name="yes" placeholder="type yes" required autocomplete="off" style="width:70px">
<button type="submit" class="red sm">Delete</button></form></p>{% else %}<p class="muted">No wallets saved on the website yet.</p>{% endfor %}
<form method="post"><input type="hidden" name="action" value="wallet_save">
<label>Supplier</label><select name="sid" required>{% for s in all_sups %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}</select>
<label>Wallet address</label><input name="address" required autocomplete="off">
<label>Network</label><select name="network">{% for n in networks %}<option>{{ n }}</option>{% endfor %}</select>
<label>Supplier Telegram bot</label><input name="bot" required placeholder="@supplier_bot" autocomplete="off">
<label>Hash message <span class="hint">use {hash}, e.g. /deposit {hash}</span></label><input name="hash_msg" value="{hash}" required autocomplete="off">
<label>Admin password</label><input name="pw" type="password" required autocomplete="off">
<label>Type <b>yes</b> to confirm</label><input name="yes" required autocomplete="off">
<button type="submit" class="red">Save wallet</button></form>
</div>

{% if sup %}<div class="card"><h3>Top-up amounts</h3>
<p class="muted">Withdraw = missing balance + fee buffer, but never less than the minimum. Must stay between 1 and {{ wd_max }} {{ coin }}. Check the supplier's minimum deposit and Binance's withdrawal fee first.</p>
{% for s in sup %}<form method="post" style="margin-bottom:12px"><input type="hidden" name="action" value="amounts"><input type="hidden" name="sid" value="{{ s.id }}">
<b>{{ s.name }}</b><label>Minimum top-up</label><input name="min_topup" value="{{ s.min_topup }}" inputmode="decimal" required>
<label>Fee buffer</label><input name="fee_buffer" value="{{ s.fee_buffer }}" inputmode="decimal" required>
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
<td>{{ '%.2f'|format(r.amount or 0) }}</td>
<td class="{{ 'paid' if r.status in ('credited','manual') else ('failed' if r.status == 'failed' else '') }}">{{ r.status }}{% if r.error %}<br><span class="muted">{{ r.error[:120] }}</span>{% endif %}</td>
<td><code>{{ (r.txid or '')[:14] }}{{ '...' if r.txid }}</code></td><td>{{ r.updated or r.created }}</td></tr>
{% else %}<tr><td colspan="6" class="muted">No top-ups yet.</td></tr>{% endfor %}</table>
<p class="muted">A row stuck on <b>withdrawing</b> means the result is unknown: check Binance history before doing anything.</p>
</div>
{% endblock %}"""


def _check_pw():
    pw = request.form.get("pw", "")
    admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if not admin_pw or not hmac.compare_digest(pw, admin_pw):
        raise ValueError("Wrong admin password.")
    if request.form.get("yes", "").strip().lower() != "yes":
        raise ValueError("Type yes to confirm.")


def _tg_ready():
    return not any(supplier_topup._tg(k).startswith("PASTE_") for k in supplier_topup._TG)


def register(app, db):
    @admin_topup.route("/topup", methods=["GET", "POST"])
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
                elif act == "wallet_save":
                    _check_pw()
                    sid = int(request.form.get("sid", "0") or 0)
                    if not db.get_supplier(sid):
                        raise ValueError("Unknown supplier.")
                    w = supplier_topup.check_wallet(request.form.get("address"), request.form.get("network"),
                                                    request.form.get("bot"), request.form.get("hash_msg"))
                    supplier_topup.save_wallet(db, sid, w)
                    flash("Wallet saved: " + w["address"][:6] + "..." + w["address"][-6:] + " (" + w["network"] + "). Check it once more.")
                elif act == "wallet_del":
                    _check_pw()
                    supplier_topup.delete_wallet(db, int(request.form.get("sid", "0") or 0))
                    flash("Wallet removed.")
                elif act == "amounts":
                    sid = int(request.form.get("sid", "0") or 0)
                    sdict = db.get_supplier(sid)
                    if not sdict or not supplier_topup.has_config(sdict["name"], sid, db):
                        raise ValueError("This supplier has no top-up set.")
                    mn = float(request.form.get("min_topup", "").replace(",", "."))
                    fb = float(request.form.get("fee_buffer", "").replace(",", "."))
                    if not (1 <= mn <= float(binance_withdraw.cfg("WD_MAX")) and mn <= supplier_topup.DAILY_CAP):
                        raise ValueError(f"Minimum must be between 1 and {binance_withdraw.cfg('WD_MAX')}.")
                    if not (0 <= fb <= 5):
                        raise ValueError("Fee buffer must be between 0 and 5.")
                    c = db.conn()
                    c.execute("INSERT INTO topup_cfg(supplier_id,min_topup,fee_buffer) VALUES(?,?,?) "
                              "ON CONFLICT(supplier_id) DO UPDATE SET min_topup=excluded.min_topup, fee_buffer=excluded.fee_buffer",
                              (sid, mn, fb))
                    c.commit(); c.close()
                    flash("Saved.")
                elif act == "withdraw":
                    pw = request.form.get("pw", "")
                    admin_pw = os.getenv("ADMIN_PASSWORD", "")
                    if not admin_pw or not hmac.compare_digest(pw, admin_pw):
                        raise ValueError("Wrong admin password.")
                    if request.form.get("yes", "").strip().lower() != "yes":
                        raise ValueError("Type yes to confirm.")
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
                        wid = binance_withdraw.withdraw(amt)
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
                            "fee_buffer": v.get("fee_buffer", 0.5)})
        wallets = []
        for s in sups:
            w = supplier_topup._db_wallet(db, s["id"])
            if w:
                wallets.append({"id": s["id"], "name": s["name"], "network": w["network"], "bot": w["bot"],
                                "addr": w["address"][:6] + "..." + w["address"][-6:]})
        addr = binance_withdraw.cfg("WD_ADDRESS")
        import web
        return web.render_str(PAGE, rows=rows, names=names, sup=sup, wallets=wallets, all_sups=sups, networks=supplier_topup.NETWORKS, keys=binance_withdraw.keys_ready(),
                              wd=binance_withdraw.configured(), tg=_tg_ready(),
                              spent=supplier_topup._spent_today(db), cap=supplier_topup.DAILY_CAP,
                              wd_max=binance_withdraw.cfg("WD_MAX"), coin=binance_withdraw.cfg("WD_COIN"),
                              net=binance_withdraw.cfg("WD_NETWORK"),
                              addr=(addr[:6] + "..." + addr[-6:]) if len(addr) > 14 else "not set")

    app.register_blueprint(admin_topup)
