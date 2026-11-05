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
OTP_DIGITS = max(4, min(8, int(os.getenv("OTP_DIGITS", "4"))))   # Netflix sign-in code length
BTN_LABEL = "🔑 Get OTP"
UNL_COOLDOWN = int(os.getenv("OTP_UNLIMITED_COOLDOWN", "5"))     # seconds between two tries of one unlimited user
_last_try = {}

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
CREATE TABLE IF NOT EXISTS otp_grants(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, mail_id INTEGER NOT NULL, order_id TEXT, line TEXT,
    otp_limit INTEGER NOT NULL DEFAULT 1, used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS otp_unlimited(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, mail_id INTEGER NOT NULL,
    taken INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, mail_id));
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
        best_code = best_link = None
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
            # Netflix login codes are exactly OTP_DIGITS (default 4) digits. Look next to the word "code" in
            # English / Spanish / Portuguese / French / Italian / German ("codigo", "código", "codice", "kod"),
            # then a lone number in the subject (a year like 2026 is ignored)
            d = OTP_DIGITS
            m = (re.search(r"(?:code|c[oó]digo|codigo|codice|kod|cod\.?)[^\d]{0,80}(?<!\d)(\d{%d})(?!\d)" % d, text, re.I)
                 or re.search(r"(?<!\d)(\d{%d})(?!\d)" % d, re.sub(r"\b(19|20)\d\d\b", " ", subject)))
            code = m.group(1) if m else None
            link = None
            for l in re.findall(r"https://www\.netflix\.com/[^\s\"'<>\]\)]+", text):
                if re.search(r"verify|travel|update-primary|households|password", l, re.I):
                    link = l
                    break
            if code and not best_code:
                best_code = code
            if link and not best_link:
                best_link = link
            if best_code and best_link:
                break
        # A sign-in code always wins. A plain "verify email" link is useless next to a code, so it is dropped then.
        if best_code:
            if best_link and re.search(r"verifyemail", best_link, re.I):
                best_link = None
            return {"code": best_code, "link": best_link}
        if best_link:
            return {"code": None, "link": best_link}
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


def _grants(uid):
    c = _conn()
    rows = c.execute("""SELECT g.id, g.otp_limit, g.used, g.line, m.email
                        FROM otp_grants g JOIN otp_mails m ON m.id=g.mail_id
                        WHERE g.user_id=? AND g.used<g.otp_limit ORDER BY g.id""", (uid,)).fetchall()
    c.close()
    return rows


def _profile_hint(line):
    """'mail:pass | Profile 3 | PIN 1234' -> 'Profile 3' (best effort, for the button label)."""
    parts = [x.strip() for x in re.split(r"[|\n]", line or "") if x.strip()]
    return parts[1] if len(parts) > 1 else ""


async def _read_and_send(uid, mail_id, addr, pwd_enc, host, used, limit, count_sql, count_arg):
    pwd = decrypt(pwd_enc)
    if not pwd:
        return "⚠️ This mail is not set up correctly. Please contact the admin."
    try:
        res = await asyncio.to_thread(fetch_netflix_mail, addr, pwd, host)
    except Exception as e:
        log.warning("OTP mail check failed for %s: %s", addr, type(e).__name__)
        _log(uid, mail_id, "error: " + type(e).__name__)
        return "⚠️ Could not check the mail right now. Try again in a minute."
    if not res:
        _log(uid, mail_id, "no mail")
        return (f"📭 No new Netflix mail in the last {MAX_AGE_MIN} min.\n"
                "First open Netflix and ask for the login code, then press the button again.\n"
                f"Used: {used}/{limit} (this try was not counted)")
    c = _conn()
    cur = c.execute(count_sql, (count_arg,))
    c.commit()
    c.close()
    if cur.rowcount == 0:
        return "⛔ Your OTP limit is finished. Please contact the admin."
    _log(uid, mail_id, "sent " + (res["code"] or "link"))
    out = f"✅ Mail: {addr}\n"
    if res["code"]:
        out += f"🔑 Code: {res['code']}\n"
    if res["link"]:
        out += f"🔗 Link: {res['link']}\n"
    return out + f"\nUsed: {used + 1}/{limit}"


async def do_grant(uid: int, gid: int) -> str:
    c = _conn()
    g = c.execute("""SELECT g.id, g.otp_limit, g.used, m.id mid, m.email, m.pwd_enc, m.imap_host
                     FROM otp_grants g JOIN otp_mails m ON m.id=g.mail_id WHERE g.id=? AND g.user_id=?""",
                  (gid, uid)).fetchone()
    c.close()
    if not g:
        return "❌ This profile was not found."
    if g["used"] >= g["otp_limit"]:
        return f"⛔ The OTP for this profile was already used ({g['used']}/{g['otp_limit']})."
    return await _read_and_send(uid, g["mid"], g["email"], g["pwd_enc"], g["imap_host"], g["used"], g["otp_limit"],
                                "UPDATE otp_grants SET used=used+1 WHERE id=? AND used<otp_limit", gid)


async def do_otp(uid: int) -> str:
    """Old manual assignment (admin assigned a mail to a user)."""
    c = _conn()
    a = c.execute("""SELECT a.id, a.otp_limit, a.used, m.id mid, m.email, m.pwd_enc, m.imap_host
                     FROM otp_assign a JOIN otp_mails m ON m.id=a.mail_id WHERE a.user_id=?""",
                  (uid,)).fetchone()
    c.close()
    if not a:
        return "❌ No mail is assigned to you. Please contact the admin."
    if a["used"] >= a["otp_limit"]:
        return f"⛔ Your OTP limit is finished ({a['used']}/{a['otp_limit']}). Please contact the admin."
    return await _read_and_send(uid, a["mid"], a["email"], a["pwd_enc"], a["imap_host"], a["used"], a["otp_limit"],
                                "UPDATE otp_assign SET used=used+1 WHERE id=? AND used<otp_limit", a["id"])


def _unlimited(uid):
    c = _conn()
    rows = c.execute("""SELECT u.id, m.email FROM otp_unlimited u JOIN otp_mails m ON m.id=u.mail_id
                        WHERE u.user_id=? ORDER BY u.id""", (uid,)).fetchall()
    c.close()
    return rows


async def do_unlimited(uid: int, rid: int) -> str:
    """Admin-given access: no limit, the try is never counted against anything."""
    c = _conn()
    u = c.execute("""SELECT u.id, m.id mid, m.email, m.pwd_enc, m.imap_host
                     FROM otp_unlimited u JOIN otp_mails m ON m.id=u.mail_id WHERE u.id=? AND u.user_id=?""",
                  (rid, uid)).fetchone()
    c.close()
    if not u:
        return "❌ You do not have access to this mail any more. Please contact the admin."
    now = datetime.now().timestamp()
    wait = UNL_COOLDOWN - (now - _last_try.get(uid, 0))
    if wait > 0:
        return f"⏳ Please wait {int(wait) + 1} second(s) and press again."
    _last_try[uid] = now
    pwd = decrypt(u["pwd_enc"])
    if not pwd:
        return "⚠️ This mail is not set up correctly. Please contact the admin."
    try:
        res = await asyncio.to_thread(fetch_netflix_mail, u["email"], pwd, u["imap_host"])
    except Exception as e:
        log.warning("OTP mail check failed for %s: %s", u["email"], type(e).__name__)
        _log(uid, u["mid"], "unlimited error: " + type(e).__name__)
        return "⚠️ Could not check the mail right now. Try again in a minute."
    if not res:
        _log(uid, u["mid"], "unlimited no mail")
        return (f"📭 No new Netflix mail in the last {MAX_AGE_MIN} min.\n"
                "First open Netflix and ask for the login code, then press the button again.")
    c = _conn()
    c.execute("UPDATE otp_unlimited SET taken=taken+1 WHERE id=?", (u["id"],))
    c.commit()
    c.close()
    _log(uid, u["mid"], "unlimited sent " + (res["code"] or "link"))
    out = f"✅ Mail: {u['email']}\n"
    if res["code"]:
        out += f"🔑 Code: {res['code']}\n"
    if res["link"]:
        out += f"🔗 Link: {res['link']}\n"
    return out + "\n♾ Unlimited access"


async def _start(uid):
    """-> (text, markup or None): one profile -> read it now, several -> let the user pick."""
    ul = _unlimited(uid)
    if len(ul) == 1:
        return await do_unlimited(uid, ul[0]["id"]), None
    if len(ul) > 1:
        rows = [[InlineKeyboardButton(f"♾ {r['email']}", callback_data=f"otpu:{r['id']}")] for r in ul[:20]]
        return "Which mail do you want the login code for?", InlineKeyboardMarkup(rows)
    gs = _grants(uid)
    if not gs:
        c = _conn()
        manual = c.execute("SELECT 1 FROM otp_assign WHERE user_id=?", (uid,)).fetchone()
        c.close()
        if manual:
            return await do_otp(uid), None
        return ("❌ You have no profile waiting for an OTP.\n"
                "Each purchased Netflix profile gets one login code. If you already used it, contact support."), None
    if len(gs) == 1:
        return await do_grant(uid, gs[0]["id"]), None
    rows = []
    for g in gs[:20]:
        hint = _profile_hint(g["line"])
        rows.append([InlineKeyboardButton(f"🔑 {g['email']}" + (f" · {hint}" if hint else ""),
                                          callback_data=f"otpg:{g['id']}")])
    return "Which profile do you want the login code for?", InlineKeyboardMarkup(rows)


async def otp_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wait = await update.message.reply_text("⏳ Checking the mail...")
    text, markup = await _start(update.effective_user.id)
    await wait.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


async def otp_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Checking...")
    text, markup = await _start(q.from_user.id)
    await q.message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)


async def otpg_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Checking...")
    gid = int(q.data.split(":", 1)[1])
    text = await do_grant(q.from_user.id, gid)
    await q.message.reply_text(text, disable_web_page_preview=True)


async def otpu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Checking...")
    rid = int(q.data.split(":", 1)[1])
    text = await do_unlimited(q.from_user.id, rid)
    await q.message.reply_text(text, disable_web_page_preview=True)


def register_bot(app):
    """Call in build_application BEFORE the catch-all callback / text handlers."""
    app.add_handler(CommandHandler("otp", otp_msg))
    app.add_handler(MessageHandler(filters.Regex("^" + re.escape(BTN_LABEL) + "$"), otp_msg))
    app.add_handler(CallbackQueryHandler(otp_cb, pattern=r"^otp$"))
    app.add_handler(CallbackQueryHandler(otpg_cb, pattern=r"^otpg:\d+$"))
    app.add_handler(CallbackQueryHandler(otpu_cb, pattern=r"^otpu:\d+$"))


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

<div class="card"><h3>♾ Unlimited OTP access</h3>
<p class="muted">Give any Telegram user unlimited OTPs for a mail. No limit, nothing is counted against them.
They press <b>🔑 Get OTP</b> as many times as they want until you remove them.</p>
<form method="post"><input type="hidden" name="action" value="unl_add">
<label>Telegram user ID</label><input name="uid" inputmode="numeric" required>
<label>Mail</label><select name="mail_id">{% for m in mails %}<option value="{{ m.id }}">{{ m.email }}</option>{% endfor %}<option value="all">All mails</option></select>
<label><input type="checkbox" name="notify" value="1" checked style="width:auto"> Tell the user on Telegram</label>
<button type="submit" class="green">Give unlimited access</button></form>
<div class="scroll"><table><tr><th>User</th><th>Mail</th><th>OTPs taken</th><th></th></tr>
{% for u in unl %}<tr><td>{{ u.user_id }}</td><td>{{ u.email }}</td><td>{{ u.taken }}</td>
<td style="white-space:nowrap">
<form method="post" style="display:inline"><input type="hidden" name="action" value="unl_reset"><input type="hidden" name="id" value="{{ u.id }}"><button type="submit">Reset count</button></form>
<form method="post" style="display:inline" onsubmit="return confirm('Remove this access?')"><input type="hidden" name="action" value="unl_del"><input type="hidden" name="id" value="{{ u.id }}"><button type="submit" class="red">Remove</button></form></td></tr>
{% else %}<tr><td colspan="4">Nobody has unlimited access yet.</td></tr>{% endfor %}</table></div></div>

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
                c.execute("DELETE FROM otp_grants WHERE mail_id=?", (int(f["id"]),))
                c.execute("DELETE FROM otp_unlimited WHERE mail_id=?", (int(f["id"]),))
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
            elif act == "unl_add":
                try:
                    uid = int(f["uid"].strip())
                    mail_ids = ([r["id"] for r in c.execute("SELECT id FROM otp_mails").fetchall()]
                                if f.get("mail_id") == "all" else [int(f["mail_id"])])
                except (ValueError, KeyError):
                    flash("User ID and mail must be valid.", "err")
                    return redirect(url_for("otp_admin.home"))
                if not mail_ids:
                    flash("Add a mail first.", "err")
                    return redirect(url_for("otp_admin.home"))
                for mid in mail_ids:
                    c.execute("INSERT OR IGNORE INTO otp_unlimited(user_id,mail_id) VALUES(?,?)", (uid, mid))
                c.commit()
                if f.get("notify"):
                    try:
                        web.db.queue_message(uid, "♾ <b>Unlimited OTP access</b> was given to you.\n"
                                                  "Press <b>🔑 Get OTP</b> (or send /otp) any time you need a Netflix login code.")
                    except Exception:
                        pass
                flash("Unlimited access given.")
            elif act == "unl_reset":
                c.execute("UPDATE otp_unlimited SET taken=0 WHERE id=?", (int(f["id"]),))
                c.commit()
                flash("Count reset.")
            elif act == "unl_del":
                c.execute("DELETE FROM otp_unlimited WHERE id=?", (int(f["id"]),))
                c.commit()
                flash("Access removed.")
            elif act == "unassign":
                c.execute("DELETE FROM otp_assign WHERE id=?", (int(f["id"]),))
                c.commit()
                flash("Removed.")
            return redirect(url_for("otp_admin.home"))
        mails = c.execute("""SELECT m.*, (SELECT COUNT(*) FROM otp_assign a WHERE a.mail_id=m.id) n
                             FROM otp_mails m ORDER BY m.id""").fetchall()
        assigns = c.execute("""SELECT a.*, m.email FROM otp_assign a
                               JOIN otp_mails m ON m.id=a.mail_id ORDER BY a.id DESC""").fetchall()
        unl = c.execute("""SELECT u.*, m.email FROM otp_unlimited u
                           JOIN otp_mails m ON m.id=u.mail_id ORDER BY u.id DESC""").fetchall()
        logs = c.execute("""SELECT l.*, m.email FROM otp_log l LEFT JOIN otp_mails m ON m.id=l.mail_id
                            ORDER BY l.id DESC LIMIT 50""").fetchall()
    finally:
        c.close()
    return web.render_str(PAGE, mails=mails, assigns=assigns, unl=unl, logs=logs)


def register_admin(app):
    app.register_blueprint(bp)
