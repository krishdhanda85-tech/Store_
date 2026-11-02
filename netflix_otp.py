"""Netflix OTP via assigned mail.

Admin (website -> Netflix OTP): add Gmail/IMAP mails, assign a mail to a Telegram user with an
OTP limit, reset/remove, see the log.  Customer (bot): "🔑 Get OTP" button or /otp -> the bot reads
the assigned mailbox, finds the newest Netflix code/link and sends it.  A try only counts when a
code/link was really found.  Works per shop (main shop and every seller have their own mails).

Mail passwords are stored ENCRYPTED (same API_KEY_SECRET as the supplier keys).
"""
import asyncio
import email
import html as _html
import imaplib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from flask import Blueprint, request, redirect, url_for, flash
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import tenancy
from provider import decrypt, encrypt

log = logging.getLogger("netflix_otp")
MAX_AGE_MIN = int(os.getenv("OTP_MAX_AGE_MIN", "20"))
BTN_LABEL = "🔑 Get OTP"

SCHEMA = """
CREATE TABLE IF NOT EXISTS otp_mails(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    pwd_enc TEXT NOT NULL,
    imap_host TEXT NOT NULL DEFAULT 'imap.gmail.com');
CREATE TABLE IF NOT EXISTS otp_assign(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE NOT NULL,
    mail_id INTEGER NOT NULL,
    otp_limit INTEGER NOT NULL DEFAULT 5,
    used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS otp_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, mail_id INTEGER, result TEXT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP);
"""


def _conn():
    c = tenancy.db.conn()
    c.executescript(SCHEMA)
    return c


def enabled():
    """True when this shop has at least one mail (then the bot shows the OTP button)."""
    try:
        c = _conn()
        n = c.execute("SELECT COUNT(*) FROM otp_mails").fetchone()[0]
        c.close()
        return n > 0
    except Exception:
        return False


# ------------------------------------------------------------------ IMAP
def _body_text(msg):
    parts = []
    for p in (msg.walk() if msg.is_multipart() else [msg]):
        if p.get_content_type() in ("text/plain", "text/html"):
            try:
                t = p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "ignore")
            except Exception:
                continue
            if p.get_content_type() == "text/html":
                t = re.sub(r"<(script|style).*?</\1>", " ", t, flags=re.S | re.I)
                links = re.findall(r'href=["\'](https?://[^"\']+)', t)
                t = re.sub(r"<[^>]+>", " ", t) + " " + " ".join(links)
            parts.append(_html.unescape(t))
    return "\n".join(parts)


def fetch_netflix_mail(addr, pwd, host):
    """Newest recent Netflix mail as {'code','link'} or None (blocking - run in a thread)."""
    M = imaplib.IMAP4_SSL(host, timeout=20)
    try:
        M.login(addr, pwd)
        M.select("INBOX", readonly=True)
        _, data = M.search(None, '(FROM "netflix.com")')
        ids = data[0].split()[-6:]
        oldest = datetime.now(timezone.utc) - timedelta(minutes=MAX_AGE_MIN)
        for i in reversed(ids):
            _, d = M.fetch(i, "(RFC822)")
            msg = email.message_from_bytes(d[0][1])
            try:
                dt = parsedate_to_datetime(msg["Date"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt < oldest:
                continue
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            text = _body_text(msg)
            m = (re.search(r"(?<!\d)(\d{4,6})(?!\d)", subject)
                 or re.search(r"code[^\d]{0,40}(\d{4,6})(?!\d)", text, re.I))
            code = m.group(1) if m else None
            link = None
            for l in re.findall(r"https://www\.netflix\.com/[^\s\"'<>\]\)]+", text):
                if re.search(r"verify|travel|update-primary|households|password", l, re.I):
                    link = l
                    break
            if code or link:
                return {"code": code, "link": link}
        return None
    finally:
        try:
            M.logout()
        except Exception:
            pass


# ------------------------------------------------------------------ bot
def _log(uid, mid, result):
    c = _conn()
    c.execute("INSERT INTO otp_log(user_id,mail_id,result) VALUES(?,?,?)", (uid, mid, result))
    c.commit()
    c.close()


async def do_otp(uid: int) -> str:
    c = _conn()
    a = c.execute("""SELECT a.id, a.otp_limit, a.used, m.id mid, m.email, m.pwd_enc, m.imap_host
                     FROM otp_assign a JOIN otp_mails m ON m.id=a.mail_id WHERE a.user_id=?""",
                  (uid,)).fetchone()
    c.close()
    if not a:
        return "❌ No mail is assigned to you. Please contact the admin."
    if a["used"] >= a["otp_limit"]:
        return f"⛔ Your OTP limit is finished ({a['used']}/{a['otp_limit']}). Please contact the admin."
    pwd = decrypt(a["pwd_enc"])
    if not pwd:
        return "⚠️ This mail is not set up correctly. Please contact the admin."
    try:
        res = await asyncio.to_thread(fetch_netflix_mail, a["email"], pwd, a["imap_host"])
    except Exception as e:
        log.warning("OTP mail check failed for %s: %s", a["email"], type(e).__name__)
        _log(uid, a["mid"], "error: " + type(e).__name__)
        return "⚠️ Could not check the mail right now. Try again in a minute."
    if not res:
        _log(uid, a["mid"], "no mail")
        return (f"📭 No new Netflix mail in the last {MAX_AGE_MIN} min.\n"
                "First ask Netflix to send the code/link, then press the button again.\n"
                f"Used: {a['used']}/{a['otp_limit']} (this try was not counted)")
    c = _conn()
    cur = c.execute("UPDATE otp_assign SET used=used+1 WHERE id=? AND used<otp_limit", (a["id"],))
    c.commit()
    c.close()
    if cur.rowcount == 0:
        return "⛔ Your OTP limit is finished. Please contact the admin."
    _log(uid, a["mid"], "sent " + (res["code"] or "link"))
    out = f"✅ Mail: {a['email']}\n"
    if res["code"]:
        out += f"🔑 Code: {res['code']}\n"
    if res["link"]:
        out += f"🔗 Link: {res['link']}\n"
    return out + f"\nUsed: {a['used'] + 1}/{a['otp_limit']}"


async def otp_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wait = await update.message.reply_text("⏳ Checking the mail...")
    text = await do_otp(update.effective_user.id)
    await wait.edit_text(text, disable_web_page_preview=True)


async def otp_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Checking...")
    await q.message.reply_text(await do_otp(q.from_user.id), disable_web_page_preview=True)


def register_bot(app):
    """Call in build_application BEFORE the catch-all callback / text handlers."""
    app.add_handler(CommandHandler("otp", otp_msg))
    app.add_handler(MessageHandler(filters.Regex("^" + re.escape(BTN_LABEL) + "$"), otp_msg))
    app.add_handler(CallbackQueryHandler(otp_cb, pattern=r"^otp$"))


# ------------------------------------------------------------------ admin website
bp = Blueprint("otp_admin", __name__)

PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>Netflix OTP</h2>
<p class="muted">Add the Netflix mails, assign one to a customer with an OTP limit. The customer presses
<b>🔑 Get OTP</b> (or sends /otp) and the bot reads that mailbox and sends the newest Netflix code/link.
A try counts only when a code/link was really found. Gmail needs an <b>App Password</b> (2-step verification on).</p>

<div class="card"><h3>Mails</h3>
<form method="post"><input type="hidden" name="action" value="addmail">
<label>Email</label><input name="email" type="email" required>
<label>App password <span class="hint">stored encrypted</span></label><input name="pwd" required autocomplete="off">
<label>IMAP host</label><input name="host" value="imap.gmail.com">
<button type="submit">Add / update mail</button></form>
<div class="scroll"><table><tr><th>Mail</th><th>Host</th><th>Customers</th><th></th></tr>
{% for m in mails %}<tr><td>{{ m.email }}</td><td>{{ m.imap_host }}</td><td>{{ m.n }}</td>
<td><form method="post" style="display:inline" onsubmit="return confirm('Delete this mail and its assignments?')">
<input type="hidden" name="action" value="delmail"><input type="hidden" name="id" value="{{ m.id }}"><button type="submit" class="red">Delete</button></form></td></tr>
{% else %}<tr><td colspan="4">No mail yet.</td></tr>{% endfor %}</table></div></div>

<div class="card"><h3>Assign to a customer</h3>
<form method="post"><input type="hidden" name="action" value="assign">
<label>Telegram user ID</label><input name="uid" inputmode="numeric" required>
<label>Mail</label><select name="mail_id">{% for m in mails %}<option value="{{ m.id }}">{{ m.email }}</option>{% endfor %}</select>
<label>OTP limit</label><input name="limit" type="number" min="1" value="5">
<button type="submit" class="green">Assign / update</button></form>
<div class="scroll"><table><tr><th>User</th><th>Mail</th><th>Used</th><th>Limit</th><th></th></tr>
{% for a in assigns %}<tr><td>{{ a.user_id }}</td><td>{{ a.email }}</td><td>{{ a.used }}</td><td>{{ a.otp_limit }}</td>
<td style="white-space:nowrap">
<form method="post" style="display:inline"><input type="hidden" name="action" value="reset"><input type="hidden" name="id" value="{{ a.id }}"><button type="submit">Reset used</button></form>
<form method="post" style="display:inline" onsubmit="return confirm('Remove?')"><input type="hidden" name="action" value="unassign"><input type="hidden" name="id" value="{{ a.id }}"><button type="submit" class="red">Remove</button></form></td></tr>
{% else %}<tr><td colspan="5">Nobody assigned yet.</td></tr>{% endfor %}</table></div></div>

<div class="card"><h3>Last 50 tries</h3><div class="scroll"><table><tr><th>Time (UTC)</th><th>User</th><th>Mail</th><th>Result</th></tr>
{% for l in logs %}<tr><td>{{ l.ts }}</td><td>{{ l.user_id }}</td><td>{{ l.email or '-' }}</td><td>{{ l.result }}</td></tr>
{% else %}<tr><td colspan="4">No tries yet.</td></tr>{% endfor %}</table></div></div>
{% endblock %}"""


@bp.route("/netflix-otp", methods=["GET", "POST"])
def home():
    import web
    c = _conn()
    try:
        if request.method == "POST":
            f = request.form
            act = f.get("action")
            if act == "addmail":
                em = f.get("email", "").strip().lower()
                pw = f.get("pwd", "").replace(" ", "")
                if not em or not pw:
                    flash("Email and app password are required.", "err")
                else:
                    try:
                        c.execute("""INSERT INTO otp_mails(email,pwd_enc,imap_host) VALUES(?,?,?)
                                     ON CONFLICT(email) DO UPDATE SET pwd_enc=excluded.pwd_enc, imap_host=excluded.imap_host""",
                                  (em, encrypt(pw), f.get("host", "").strip() or "imap.gmail.com"))
                        c.commit()
                        flash("Mail saved.")
                    except ValueError as e:
                        flash(str(e), "err")
            elif act == "delmail":
                c.execute("DELETE FROM otp_assign WHERE mail_id=?", (int(f["id"]),))
                c.execute("DELETE FROM otp_mails WHERE id=?", (int(f["id"]),))
                c.commit()
                flash("Mail deleted.")
            elif act == "assign":
                try:
                    uid, mid, lim = int(f["uid"].strip()), int(f["mail_id"]), max(1, int(f.get("limit") or 5))
                except (ValueError, KeyError):
                    flash("User ID, mail and limit must be valid numbers.", "err")
                    return redirect(url_for("otp_admin.home"))
                c.execute("""INSERT INTO otp_assign(user_id,mail_id,otp_limit) VALUES(?,?,?)
                             ON CONFLICT(user_id) DO UPDATE SET mail_id=excluded.mail_id, otp_limit=excluded.otp_limit""",
                          (uid, mid, lim))
                c.commit()
                flash("Assigned.")
            elif act == "reset":
                c.execute("UPDATE otp_assign SET used=0 WHERE id=?", (int(f["id"]),))
                c.commit()
                flash("Used count reset.")
            elif act == "unassign":
                c.execute("DELETE FROM otp_assign WHERE id=?", (int(f["id"]),))
                c.commit()
                flash("Removed.")
            return redirect(url_for("otp_admin.home"))
        mails = c.execute("""SELECT m.*, (SELECT COUNT(*) FROM otp_assign a WHERE a.mail_id=m.id) n
                             FROM otp_mails m ORDER BY m.id""").fetchall()
        assigns = c.execute("""SELECT a.*, m.email FROM otp_assign a
                               JOIN otp_mails m ON m.id=a.mail_id ORDER BY a.id DESC""").fetchall()
        logs = c.execute("""SELECT l.*, m.email FROM otp_log l LEFT JOIN otp_mails m ON m.id=l.mail_id
                            ORDER BY l.id DESC LIMIT 50""").fetchall()
    finally:
        c.close()
    return web.render_str(PAGE, mails=mails, assigns=assigns, logs=logs)


def register_admin(app):
    app.register_blueprint(bp)
