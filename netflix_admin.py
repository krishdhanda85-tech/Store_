"""Netflix customers (admin site -> Netflix customers).

Every Netflix profile that is sold through the bot is recorded here automatically (who, which profile, when,
when it ends).  The admin can also add a customer by hand, add days when a customer renews, and send a
renewal message (queued in the outbox, the bot delivers it).  "Import old orders" fills the list from
Netflix orders that were sold before this page existed.
"""
import html as _html
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, request, redirect, url_for, flash

from db import NF_DDL, nf_days

bp = Blueprint("netflix_admin", __name__)

DEFAULT_TEXT = ("⏰ <b>Netflix renewal</b>\n"
                "Your Netflix profile <b>{profile}</b> ({mail}) {when}, on <b>{date}</b>.\n"
                "To keep watching, open <b>Products → Netflix</b> and order again.")

PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Netflix customers</h2>
<p class="muted">Every Netflix profile sold by the bot is saved here. Add a customer by hand, add days when someone renews,
and send a renewal message (the bot delivers it to the customer's chat).</p>

<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:0 0 12px">
<a href="?f=all" class="card" style="text-align:center;padding:10px"><div class="muted">All</div><b style="font-size:20px">{{ n_all }}</b></a>
<a href="?f=soon" class="card" style="text-align:center;padding:10px"><div class="muted">Ends in {{ soon }} days</div><b style="font-size:20px">{{ n_soon }}</b></a>
<a href="?f=expired" class="card" style="text-align:center;padding:10px"><div class="muted">Expired</div><b style="font-size:20px">{{ n_exp }}</b></a>
</div>

<div class="card"><h3>Customers {% if f != 'all' %}<span class="muted">({{ f }})</span>{% endif %}</h3>
<div class="scroll"><table>
<tr><th>Customer</th><th>Profile</th><th>Bought</th><th>Ends</th><th>Left</th><th></th></tr>
{% for s in subs %}<tr>
<td>{{ s.who }}<div class="muted" style="font-size:12px">{{ s.source }}</div></td>
<td>{{ s.profile or '-' }}<div class="muted" style="font-size:12px">{{ s.mail or '' }}</div></td>
<td>{{ s.start_date }}</td><td>{{ s.expires_at }}</td>
<td><b style="color:{{ 'var(--text-danger)' if s.left < 0 else ('var(--text-warning)' if s.left <= soon else 'var(--text-success)') }}">{{ s.left }}d</b></td>
<td style="white-space:nowrap">
<form method="post" style="display:inline"><input type="hidden" name="action" value="remind"><input type="hidden" name="id" value="{{ s.id }}"><button type="submit" class="sm" {% if not s.user_id %}title="No Telegram ID: cannot send"{% endif %}>Remind</button></form>
<form method="post" style="display:inline"><input type="hidden" name="action" value="extend"><input type="hidden" name="id" value="{{ s.id }}"><input type="hidden" name="days" value="30"><button type="submit" class="sm green">+30d</button></form>
<form method="post" style="display:inline" onsubmit="return confirm('Delete this record?')"><input type="hidden" name="action" value="delete"><input type="hidden" name="id" value="{{ s.id }}"><button type="submit" class="sm red">Delete</button></form>
</td></tr>
{% else %}<tr><td colspan="6">Nobody here yet.</td></tr>{% endfor %}
</table></div></div>

<div class="card"><h3>Add a customer by hand</h3>
<form method="post"><input type="hidden" name="action" value="add">
<label>Telegram user ID <span class="hint">optional, needed to send messages</span></label><input name="uid" inputmode="numeric">
<label>Customer name / note <span class="hint">optional</span></label><input name="name">
<label>Netflix mail</label><input name="mail" type="email">
<label>Profile</label><input name="profile" placeholder="Mathew">
<label>Start date</label><input name="start" type="date" value="{{ today }}">
<label>Days</label><input name="days" type="number" min="1" value="30">
<button type="submit" class="green">Add</button></form></div>

<div class="card"><h3>Renewal message</h3>
<form method="post"><input type="hidden" name="action" value="save_text">
<label>Text <span class="hint">{profile} {mail} {when} {date} {days} can be used. HTML like &lt;b&gt; works.</span></label>
<textarea name="text" rows="5">{{ text }}</textarea>
<button type="submit">Save text</button></form>
<form method="post" style="margin-top:10px" onsubmit="return confirm('Send to every customer who ends in {{ soon }} days or is already expired?')">
<input type="hidden" name="action" value="remind_all"><button type="submit" class="green">Remind everyone ending soon / expired</button></form></div>

<div class="card"><h3>Old orders</h3><p class="muted">Add Netflix orders sold before this page existed (customer and date only, no profile name).</p>
<form method="post"><input type="hidden" name="action" value="import_old"><button type="submit">Import old Netflix orders</button></form></div>
{% endblock %}"""


def _conn():
    import web
    c = web.db.conn()
    c.execute(NF_DDL)
    return c


def _d(s):
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def _soon():
    import web
    try:
        return max(1, int(web.db.get_setting("nf_soon_days", "3")))
    except ValueError:
        return 3


def _text(db):
    return db.get_setting("nf_renew_text", "") or DEFAULT_TEXT


def _fmt(db, s):
    left = (_d(s["expires_at"]) - date.today()).days
    if left < 0:
        when = f"expired {-left} day(s) ago" if left < -1 else "expired yesterday"
    elif left == 0:
        when = "ends today"
    else:
        when = f"ends in {left} day(s)"
    vals = dict(profile=_html.escape(s["profile"] or "your profile"), mail=_html.escape(s["mail"] or ""),
                when=when, date=s["expires_at"], days=left)
    out = _text(db)
    for k, v in vals.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _remind(db, c, s):
    if not s["user_id"]:
        return False
    db.queue_message(int(s["user_id"]), _fmt(db, s))
    c.execute("UPDATE nf_subs SET notified_at=? WHERE id=?", (date.today().isoformat(), s["id"]))
    return True


@bp.route("/netflix", methods=["GET", "POST"])
def home():
    import web
    db = web.db
    c = _conn()
    try:
        if request.method == "POST":
            f = request.form
            act = f.get("action")
            today = date.today()
            if act == "add":
                try:
                    uid = int(f.get("uid", "").strip()) if f.get("uid", "").strip() else None
                    days = max(1, int(f.get("days") or 30))
                except ValueError:
                    flash("User ID and days must be numbers.", "err")
                    return redirect(url_for("netflix_admin.home"))
                start = _d(f.get("start") or today.isoformat())
                c.execute("INSERT INTO nf_subs(user_id,name,mail,profile,start_date,expires_at,source) VALUES(?,?,?,?,?,?,'manual')",
                          (uid, f.get("name", "").strip()[:80], f.get("mail", "").strip().lower(),
                           f.get("profile", "").strip()[:60], start.isoformat(), (start + timedelta(days=days)).isoformat()))
                c.commit(); flash("Customer added.")
            elif act == "extend":
                s = c.execute("SELECT * FROM nf_subs WHERE id=?", (int(f["id"]),)).fetchone()
                if s:
                    base = max(_d(s["expires_at"]), today)
                    c.execute("UPDATE nf_subs SET expires_at=?, notified_at=NULL WHERE id=?",
                              ((base + timedelta(days=max(1, int(f.get("days") or 30)))).isoformat(), s["id"]))
                    c.commit(); flash("Days added.")
            elif act == "delete":
                c.execute("DELETE FROM nf_subs WHERE id=?", (int(f["id"]),)); c.commit(); flash("Deleted.")
            elif act == "remind":
                s = c.execute("SELECT * FROM nf_subs WHERE id=?", (int(f["id"]),)).fetchone()
                if s and _remind(db, c, s):
                    c.commit(); flash("Renewal message queued, the bot sends it in a few seconds.")
                else:
                    flash("This customer has no Telegram ID, so no message can be sent.", "err")
            elif act == "remind_all":
                n = 0
                lim = (today + timedelta(days=_soon())).isoformat()
                for s in c.execute("SELECT * FROM nf_subs WHERE expires_at<=? AND user_id IS NOT NULL "
                                   "AND COALESCE(notified_at,'')<>?", (lim, today.isoformat())).fetchall():
                    n += 1 if _remind(db, c, s) else 0
                c.commit(); flash(f"{n} message(s) queued.")
            elif act == "save_text":
                db.set_setting("nf_renew_text", (f.get("text") or "").strip()[:1500]); flash("Text saved.")
            elif act == "import_old":
                n = 0
                rows = c.execute("""SELECT o.id, o.user_id, o.created_at, o.paid_at, p.name FROM orders o
                                    JOIN products p ON p.id=o.product_id
                                    WHERE o.status IN ('paid','delivered','done','completed') AND lower(p.name) LIKE '%netflix%'""").fetchall()
                for o in rows:
                    if c.execute("SELECT 1 FROM nf_subs WHERE order_id=?", (o["id"],)).fetchone():
                        continue
                    start = _d(o["paid_at"] or o["created_at"])
                    c.execute("INSERT INTO nf_subs(user_id,mail,profile,start_date,expires_at,order_id,source) VALUES(?,?,?,?,?,?,'old order')",
                              (o["user_id"], "", "", start.isoformat(), (start + timedelta(days=nf_days(o["name"]))).isoformat(), o["id"]))
                    n += 1
                c.commit(); flash(f"{n} old order(s) imported.")
            return redirect(url_for("netflix_admin.home"))

        soon = _soon()
        rows = c.execute("""SELECT s.*, u.username, u.first_name FROM nf_subs s
                            LEFT JOIN users u ON u.user_id=s.user_id ORDER BY s.expires_at""").fetchall()
        subs = []
        for r in rows:
            d = dict(r)
            d["left"] = (_d(d["expires_at"]) - date.today()).days
            if d.get("username"):
                d["who"] = "@" + d["username"]
            elif d.get("first_name"):
                d["who"] = d["first_name"]
            elif d.get("name"):
                d["who"] = d["name"]
            else:
                d["who"] = str(d["user_id"]) if d.get("user_id") else "-"
            if d.get("user_id") and d["who"] != str(d["user_id"]):
                d["who"] += f" ({d['user_id']})"
            subs.append(d)
        flt = request.args.get("f", "all")
        shown = [x for x in subs if flt == "all" or (flt == "soon" and 0 <= x["left"] <= soon)
                 or (flt == "expired" and x["left"] < 0)]
        return web.render_str(PAGE, subs=shown, f=flt, soon=soon, today=date.today().isoformat(), text=_text(db),
                              n_all=len(subs), n_soon=sum(1 for x in subs if 0 <= x["left"] <= soon),
                              n_exp=sum(1 for x in subs if x["left"] < 0))
    finally:
        c.close()


def register_admin(app):
    app.register_blueprint(bp)
