"""Owner-only Telethon group marketing controls.

Uses a dedicated Telegram user account session. Only explicitly configured groups are
used; the owner must confirm they administer the group or have permission to promote.
No group discovery, scraping, unsolicited DMs, or random bulk messaging.
"""
import json
import logging
import os
import re
import threading
import time

from flask import request, redirect, url_for, flash
from provider import encrypt, decrypt

log = logging.getLogger("telethon_admin")
PREFIX = "telethon_marketing_"
_pending = {}
_pending_lock = threading.RLock()


def _get(db, key, default=""):
    return db.get_setting(PREFIX + key, default)


def _set(db, key, value):
    db.set_setting(PREFIX + key, value)


def _targets(db):
    try:
        data = json.loads(_get(db, "targets", "[]"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _api_credentials(db):
    try:
        api_id = int(decrypt(_get(db, "api_id_enc", "")) or 0)
        api_hash = decrypt(_get(db, "api_hash_enc", "")) or ""
    except Exception:
        api_id, api_hash = 0, ""
    return api_id, api_hash


def _client(db, session_string=None):
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    api_id, api_hash = _api_credentials(db)
    if not api_id or not api_hash:
        raise RuntimeError("Save Telegram API ID and API Hash once in the owner dashboard first.")
    return TelegramClient(StringSession(session_string or ""), api_id, api_hash)


def _saved_session(db):
    enc = _get(db, "session_enc", "")
    return decrypt(enc) if enc else ""


def _safe_target(value):
    value = (value or "").strip()
    if not value or len(value) > 120:
        raise ValueError("Enter a valid Telegram group username or numeric chat ID.")
    if value.startswith("https://t.me/"):
        value = "@" + value.rsplit("/", 1)[-1].lstrip("@")
    if value.startswith("@"):
        if not re.fullmatch(r"@[A-Za-z0-9_]{4,}", value):
            raise ValueError("Invalid public group username.")
    elif not re.fullmatch(r"-?\d{5,20}", value):
        raise ValueError("Use @publicgroupname or a numeric group ID.")
    return value


def register(app, render, db):
    @app.get("/telethon")
    def telethon_admin_home():
        from tenancy import current_or_none
        t = current_or_none()
        if not t or not t.is_main:
            flash("Telethon marketing is available only to the platform owner.", "err")
            return redirect(url_for("dashboard"))
        connected = bool(_saved_session(db))
        targets = _targets(db)
        api_configured = bool(all(_api_credentials(db)))
        page = r'''{% extends "base.html" %}{% block body %}
<div class="page-head"><div><h2>Telethon Marketing</h2><p class="muted">Manage your connected Telegram account and approved promotion groups.</p></div><span class="badge">Owner only</span></div>
<div class="card"><h3>Connection</h3>
{% if connected %}<p><span class="badge" style="background:#13805a;color:white">Session saved</span> Telegram user session is configured.</p>
<form method="post" action="{{ url_for('telethon_disconnect') }}" onsubmit="return confirm('Disconnect the Telegram user session?')"><button class="red sm" type="submit">Disconnect session</button></form>
{% else %}<p class="muted">Save your Telegram API credentials below once, then sign in with a dedicated Telegram account.</p>
{% if api_configured %}<p><span class="badge" style="background:#13805a;color:white">API credentials saved</span></p>{% endif %}
<form method="post" action="{{ url_for('telethon_send_code') }}"><label>Phone number with country code</label><input name="phone" placeholder="+91XXXXXXXXXX" required><button type="submit">Send Telegram login code</button></form>
{% if pending %}<form method="post" action="{{ url_for('telethon_verify_code') }}"><label>Telegram login code</label><input name="code" inputmode="numeric" required><label>2-step verification password (only if Telegram asks)</label><input name="password" type="password" autocomplete="off"><button type="submit">Verify and save session</button></form>{% endif %}{% endif %}</div>
<div class="card"><h3>Telegram API credentials (one-time)</h3><p class="muted">Get these from my.telegram.org → API development tools. Save them here once; no Railway Variables edits are needed. Credentials are encrypted in the store database.</p>
<form method="post" action="{{ url_for('telethon_save_api') }}"><label>Telegram API ID</label><input name="api_id" inputmode="numeric" placeholder="{% if api_configured %}Saved — enter new value only to replace{% else %}API ID{% endif %}" required><label>Telegram API Hash</label><input name="api_hash" type="password" autocomplete="new-password" placeholder="{% if api_configured %}Saved — enter new value only to replace{% else %}API Hash{% endif %}" required><button type="submit">Save API credentials</button></form></div>
<div class="card"><h3>Approved groups</h3><p class="muted">Add only groups where you are an admin or the owner has explicitly allowed promotional posts. The tool will not search random groups or message members privately.</p>
<form method="post" action="{{ url_for('telethon_add_target') }}"><label>Group username or ID</label><input name="target" placeholder="@yourgroup or -1001234567890" required><label style="display:flex;gap:8px;align-items:center"><input style="width:auto;margin:0" type="checkbox" name="permission" value="yes" required> I administer this group or have permission to post promotions.</label><button type="submit">Add approved group</button></form>
{% if targets %}<div class="scroll"><table><thead><tr><th>Group</th><th>Action</th></tr></thead><tbody>{% for target in targets %}<tr><td class="mono">{{ target }}</td><td><form method="post" action="{{ url_for('telethon_remove_target') }}"><input type="hidden" name="target" value="{{ target }}"><button class="red sm" type="submit">Remove</button></form></td></tr>{% endfor %}</tbody></table></div>{% else %}<p class="empty">No groups added yet.</p>{% endif %}</div>
<div class="card"><h3>Compose promotion</h3><p class="muted">Messages are sent only to the groups listed above. Maximum 5 groups per send, with a delay between sends.</p>
<form method="post" action="{{ url_for('telethon_send_message') }}"><label>Message</label><textarea name="message" rows="6" maxlength="3500" placeholder="Write your Okiemyshop promotion..." required></textarea><label style="display:flex;gap:8px;align-items:center"><input style="width:auto;margin:0" type="checkbox" name="confirm" value="yes" required> I confirm this message is relevant and allowed in every selected group.</label><button type="submit" {% if not connected or not targets %}disabled{% endif %}>Send to approved groups</button></form></div>
<p class="muted">API credentials and Telegram session are encrypted in the store database. Keep your existing bot token and Telegram login details private.</p>
{% endblock %}'''
        return render_str(page, connected=connected, targets=targets, pending=bool(_pending.get("owner")), api_configured=api_configured)

    def _owner_only():
        from tenancy import current_or_none
        t = current_or_none()
        return bool(t and t.is_main)

    @app.post("/telethon/save-api")
    def telethon_save_api():
        if not _owner_only():
            flash("Owner access required.", "err"); return redirect(url_for("dashboard"))
        try:
            api_id = int((request.form.get("api_id") or "").strip())
            api_hash = (request.form.get("api_hash") or "").strip()
            if api_id <= 0 or not api_hash or len(api_hash) > 128:
                raise ValueError("Enter a valid Telegram API ID and API Hash.")
            _set(db, "api_id_enc", encrypt(str(api_id)))
            _set(db, "api_hash_enc", encrypt(api_hash))
            flash("Telegram API credentials saved securely. No need to edit Railway Variables.")
        except Exception as e:
            flash(str(e)[:240], "err")
        return redirect(url_for("telethon_admin_home"))

    @app.post("/telethon/send-code")
    def telethon_send_code():
        if not _owner_only():
            flash("Owner access required.", "err"); return redirect(url_for("dashboard"))
        phone = (request.form.get("phone") or "").strip()
        try:
            if not phone.startswith("+") or len(phone) < 8 or len(phone) > 18:
                raise ValueError("Enter phone number in international format, e.g. +91...")
            client = _client(db)
            client.connect()
            sent = client.send_code_request(phone)
            with _pending_lock:
                old = _pending.pop("owner", None)
                if old:
                    try: old["client"].disconnect()
                    except Exception: pass
                _pending["owner"] = {"client": client, "phone": phone, "phone_code_hash": sent.phone_code_hash, "created": time.time()}
            flash("Login code sent in Telegram. Enter it below.")
        except Exception as e:
            flash(str(e)[:240], "err")
        return redirect(url_for("telethon_admin_home"))

    @app.post("/telethon/verify-code")
    def telethon_verify_code():
        if not _owner_only():
            flash("Owner access required.", "err"); return redirect(url_for("dashboard"))
        with _pending_lock:
            pending = _pending.get("owner")
        if not pending or time.time() - pending["created"] > 600:
            flash("Login request expired. Send a new code.", "err"); return redirect(url_for("telethon_admin_home"))
        try:
            from telethon.errors import SessionPasswordNeededError
            try:
                pending["client"].sign_in(phone=pending["phone"], code=(request.form.get("code") or "").strip(), phone_code_hash=pending["phone_code_hash"])
            except SessionPasswordNeededError:
                password = request.form.get("password") or ""
                if not password:
                    flash("Telegram requires your 2-step password. Enter it and submit again.", "err")
                    return redirect(url_for("telethon_admin_home"))
                pending["client"].sign_in(password=password)
            session_string = pending["client"].session.save()
            _set(db, "session_enc", encrypt(session_string))
            try: pending["client"].disconnect()
            except Exception: pass
            with _pending_lock: _pending.pop("owner", None)
            flash("Telegram account connected. Session encrypted and saved.")
        except Exception as e:
            flash(str(e)[:240], "err")
        return redirect(url_for("telethon_admin_home"))

    @app.post("/telethon/disconnect")
    def telethon_disconnect():
        if not _owner_only():
            flash("Owner access required.", "err"); return redirect(url_for("dashboard"))
        db.del_settings([PREFIX + "session_enc"])
        flash("Telegram session disconnected.")
        return redirect(url_for("telethon_admin_home"))

    @app.post("/telethon/targets/add")
    def telethon_add_target():
        if not _owner_only():
            flash("Owner access required.", "err"); return redirect(url_for("dashboard"))
        try:
            if request.form.get("permission") != "yes":
                raise ValueError("Confirm group promotion permission first.")
            target = _safe_target(request.form.get("target"))
            targets = _targets(db)
            if target not in targets:
                if len(targets) >= 5: raise ValueError("Maximum 5 approved groups for this first version.")
                targets.append(target); _set(db, "targets", json.dumps(targets))
            flash("Approved group added.")
        except Exception as e: flash(str(e), "err")
        return redirect(url_for("telethon_admin_home"))

    @app.post("/telethon/targets/remove")
    def telethon_remove_target():
        if not _owner_only():
            flash("Owner access required.", "err"); return redirect(url_for("dashboard"))
        target = request.form.get("target", "")
        targets = [x for x in _targets(db) if x != target]
        _set(db, "targets", json.dumps(targets))
        flash("Group removed.")
        return redirect(url_for("telethon_admin_home"))

    @app.post("/telethon/send")
    def telethon_send_message():
        if not _owner_only():
            flash("Owner access required.", "err"); return redirect(url_for("dashboard"))
        message = (request.form.get("message") or "").strip()
        if request.form.get("confirm") != "yes" or not message:
            flash("Message and group permission confirmation are required.", "err"); return redirect(url_for("telethon_admin_home"))
        if len(message) > 3500:
            flash("Message is too long (max 3500 characters).", "err"); return redirect(url_for("telethon_admin_home"))
        targets = _targets(db)
        session_string = _saved_session(db)
        if not session_string or not targets:
            flash("Connect Telegram and add approved groups first.", "err"); return redirect(url_for("telethon_admin_home"))
        client = None
        sent, failed = 0, []
        try:
            client = _client(db, session_string)
            client.connect()
            if not client.is_user_authorized(): raise RuntimeError("Telegram session expired. Reconnect it.")
            for target in targets:
                try:
                    entity = client.get_entity(target)
                    awaitable = client.send_message(entity, message, link_preview=False)
                    # telethon.sync returns completed result in sync context.
                    if hasattr(awaitable, "__await__"):
                        import asyncio
                        asyncio.get_event_loop().run_until_complete(awaitable)
                    sent += 1
                    time.sleep(12)
                except Exception as e:
                    failed.append(f"{target}: {str(e)[:100]}")
            flash(f"Campaign finished: sent to {sent} group(s); {len(failed)} failed." + (" Details: " + " | ".join(failed[:3]) if failed else ""), "err" if failed else None)
        except Exception as e:
            flash(str(e)[:240], "err")
        finally:
            if client:
                try: client.disconnect()
                except Exception: pass
        return redirect(url_for("telethon_admin_home"))
