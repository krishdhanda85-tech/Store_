"""Owner-only Telethon group marketing controls.

Uses a dedicated Telegram user account session. Only groups the owner added (and confirmed
they may promote in) are used. No group discovery, scraping or unsolicited DMs.

Features
  * Unlimited approved groups (add many at once, one per line).
  * Manual send and AUTO-POST: the owner sets the repeat interval, the gap between groups
    and the message from the website.
  * Sending runs in the background, so large group lists never time out the web page.
  * Safety: minimum interval / gap, stops on Telegram flood limits, stops after many failures
    in a row, switches auto-post off if the Telegram session is dead.
"""
import asyncio
import importlib.util
import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timezone

from flask import request, redirect, url_for, flash

import tenancy
from provider import encrypt, decrypt

log = logging.getLogger("telethon_admin")
PREFIX = "telethon_marketing_"

MIN_INTERVAL_MIN = 10      # auto-post: shortest repeat time the owner may set
MIN_GAP_SEC = 5            # shortest pause between two groups
MAX_MSG = 3500
MAX_FAILS_IN_ROW = 8
MAX_ADS = 10               # ads that can rotate
DEFAULT_DAILY = 2          # max posts per group in 24 hours
_TICK = 20                 # scheduler wake-up (seconds)
MAX_JOINS_PER_RUN = 5      # auto-join: new groups joined per run (Telegram limits joins hard)
JOIN_GAP_SEC = 25          # pause after every join
QUARANTINE_FAILS = 3       # a group failing this many runs in a row (permanent error) is paused
QUARANTINE_HOURS = 48      # ... for this long (Retry button clears it)
SEND_TIMEOUT = 60

_pending = {}
_pending_lock = threading.RLock()

_state_lock = threading.RLock()
_state = {"running": False, "source": "", "sent": 0, "total": 0, "failed": [],
          "started": 0.0, "stop": False, "current": ""}

_loop = None
_loop_lock = threading.Lock()
_sched_started = False

_FATAL = {"AuthKeyUnregisteredError", "AuthKeyDuplicatedError", "UserDeactivatedError",
          "UserDeactivatedBanError", "SessionRevokedError", "SessionExpiredError"}


# ------------------------------------------------------------------ settings helpers
def _get(db, key, default=""):
    return db.get_setting(PREFIX + key, default)


def _set(db, key, value):
    db.set_setting(PREFIX + key, value)


def _int_setting(db, key, default, lo, hi):
    try:
        v = int(float(_get(db, key, str(default)) or default))
    except Exception:
        v = default
    return max(lo, min(hi, v))


def _float_setting(db, key, default=0.0):
    try:
        return float(_get(db, key, str(default)) or default)
    except Exception:
        return default


def _targets(db):
    try:
        data = json.loads(_get(db, "targets", "[]"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_targets(db, targets):
    _set(db, "targets", json.dumps(targets))


def _api_credentials(db):
    try:
        api_id = int(decrypt(_get(db, "api_id_enc", "")) or 0)
        api_hash = decrypt(_get(db, "api_hash_enc", "")) or ""
    except Exception:
        api_id, api_hash = 0, ""
    return api_id, api_hash


def _saved_session(db):
    enc = _get(db, "session_enc", "")
    try:
        return decrypt(enc) if enc else ""
    except Exception:
        return ""


_USER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")


def _is_invite(target):
    return target.startswith("https://t.me/+")


def _safe_target(value):
    """Accepts @name, name, t.me/name, https://t.me/name/123, numeric ID, t.me/c/ID/.. and private
    invite links (t.me/+HASH or t.me/joinchat/HASH). Returns the normalised form."""
    raw = (value or "").strip().strip(",;")
    if not raw or len(raw) > 160:
        raise ValueError("Enter a valid Telegram group username, invite link or numeric chat ID.")
    m = re.fullmatch(r"(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(.+)", raw, re.I)
    if m:
        path = m.group(1).split("?")[0].strip("/")
        low = path.lower()
        if path.startswith("+") or low.startswith("joinchat/"):
            h = path[1:] if path.startswith("+") else path.split("/", 1)[1]
            h = h.split("/")[0]
            if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", h):
                raise ValueError("Invalid invite link.")
            return "https://t.me/+" + h
        parts = path.split("/")
        if parts[0].lower() == "c" and len(parts) > 1 and parts[1].isdigit():
            return "-100" + parts[1]
        raw = "@" + parts[0]
    if raw.startswith("@"):
        if not re.fullmatch(r"@[A-Za-z0-9_]{4,32}", raw):
            raise ValueError("Invalid public group username.")
        return raw
    if re.fullmatch(r"-?\d{5,20}", raw):
        return raw
    if _USER_RE.fullmatch(raw):
        return "@" + raw
    raise ValueError("Use @groupname, t.me link, invite link or numeric group ID.")


def _entity_ref(target):
    return int(target) if re.fullmatch(r"-?\d+", target) else target


def _errtext(e):
    return (str(e) or type(e).__name__)[:240]


def _fmt_ts(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ------------------------------------------------------------------ Telethon event loop
# One background event loop owns every Telethon client, so logins that span two web requests
# and the auto-post scheduler all work safely from any thread.
def _get_loop():
    global _loop
    with _loop_lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run():
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            threading.Thread(target=_run, daemon=True, name="telethon-loop").start()
            ready.wait(5)
            _loop = loop
        return _loop


def _call(coro, timeout=90):
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result(timeout)


def _new_client(api_id, api_hash, session_string=""):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    return TelegramClient(StringSession(session_string or ""), api_id, api_hash)


async def _a_send_code(api_id, api_hash, phone):
    client = _new_client(api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except Exception:
        await client.disconnect()
        raise
    return client, sent.phone_code_hash


async def _a_sign_in(client, phone, code, code_hash, password, need_password):
    from telethon.errors import SessionPasswordNeededError
    if not need_password:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
        except SessionPasswordNeededError:
            if not password:
                return "need_password", None
            await client.sign_in(password=password)
    else:
        if not password:
            return "need_password", None
        await client.sign_in(password=password)
    session_string = client.session.save()
    await client.disconnect()
    return "ok", session_string


async def _a_disconnect(client):
    await client.disconnect()


async def _nap(seconds):
    """Sleep in 1-second steps so the Stop button reacts quickly."""
    end = time.time() + seconds
    while time.time() < end:
        with _state_lock:
            if _state["stop"]:
                return
        await asyncio.sleep(min(1.0, max(0.0, end - time.time())))


# ------------------------------------------------------------------ premium (custom) emojis
# In the message write  {e:5368324170671202286}  or  {e:5368324170671202286|🔥}  (the part after | is
# the normal emoji shown to people without Premium). The HTML tag
# <tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji>  works too. Bold/italic/links use Telegram HTML
# (<b>, <i>, <a href="...">) - plain text without tags is sent as it is.
_EMOJI_RE = re.compile(
    r'<tg-emoji\s+emoji-id=["\']?(\d{5,30})["\']?\s*>(.*?)</tg-emoji>|\{e:(\d{5,30})(?:\|([^}]{0,8}))?\}',
    re.S)
_PUA = 0xE000
_DEFAULT_FALLBACK = "\u2b50"      # star, shown when no fallback emoji was given


def _u16(s):
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)


def build_message(message):
    """message text -> (plain_text, entities) with MessageEntityCustomEmoji for every premium emoji."""
    from telethon.extensions import html as tl_html
    from telethon.tl.types import MessageEntityCustomEmoji
    found = []

    def _swap(m):
        eid = int(m.group(1) or m.group(3))
        fb = (m.group(2) if m.group(1) else m.group(4)) or ""
        fb = re.sub(r"<[^>]*>", "", fb).strip() or _DEFAULT_FALLBACK
        found.append((eid, fb))
        return chr(_PUA + len(found) - 1)

    marked = _EMOJI_RE.sub(_swap, message)
    text, entities = tl_html.parse(marked)
    if not found:
        return text, entities
    out, shifts, extra, pos = [], [], [], 0     # shifts: (utf16 position in old text, added length)
    for ch in text:
        idx = ord(ch) - _PUA
        if 0 <= idx < len(found):
            eid, fb = found[idx]
            extra.append((0, pos, eid, fb))
            shifts.append((pos, _u16(fb) - 1))
            out.append(fb)
        else:
            out.append(ch)
        pos += _u16(ch)

    def _new(old):                       # old utf-16 offset -> offset in the final text
        return old + sum(d for at, d in shifts if at < old)

    final = []
    for e in entities:
        start, end = _new(e.offset), _new(e.offset + e.length)
        e.offset, e.length = start, max(0, end - start)
        final.append(e)
    for _, at, eid, fb in extra:
        final.append(MessageEntityCustomEmoji(offset=_new(at), length=_u16(fb), document_id=eid))
    final.sort(key=lambda e: e.offset)
    return "".join(out), final


# ------------------------------------------------------------------ sending engine
# Error classes are matched by NAME so this works with every Telethon 1.x version.
_PERM_ERRORS = {
    "ChatWriteForbiddenError": "account cannot write here (restricted / not allowed)",
    "UserBannedInChannelError": "account is banned in this group",
    "ChannelPrivateError": "private group or account was removed",
    "ChannelBannedError": "group is banned",
    "ChatAdminRequiredError": "only admins can post here",
    "ChatRestrictedError": "posting is restricted here",
    "UsernameNotOccupiedError": "username does not exist",
    "UsernameInvalidError": "invalid username",
    "UserNotParticipantError": "account is not a member",
    "InviteHashExpiredError": "invite link expired",
    "InviteHashInvalidError": "invite link invalid",
    "PeerIdInvalidError": "invalid group ID",
}


def _key(target):
    if re.fullmatch(r"-?\d+", target):
        return int(target)
    return target.lower() if target.startswith("@") else target


async def _dialog_index(client):
    """Every group/channel the account already is in -> no username lookups (they flood quickly)."""
    idx = {}
    try:
        async for d in client.iter_dialogs(limit=None):
            if not (d.is_group or d.is_channel):
                continue
            ent = d.entity
            idx[d.id] = ent
            u = getattr(ent, "username", None)
            if u:
                idx["@" + u.lower()] = ent
    except Exception:
        log.warning("could not read dialogs", exc_info=True)
    return idx


async def _resolve(client, target, idx):
    """-> (entity or None, need_join)."""
    k = _key(target)
    if k in idx:
        return idx[k], False
    if _is_invite(target):
        from telethon.tl.functions.messages import CheckChatInviteRequest
        inv = await client(CheckChatInviteRequest(target.rsplit("+", 1)[1]))
        if type(inv).__name__ == "ChatInviteAlready":
            idx[k] = inv.chat
            return inv.chat, False
        return None, True
    ent = await client.get_entity(k if isinstance(k, int) else target)
    idx[k] = ent
    return ent, True          # not in our chat list -> probably not a member yet


async def _join(client, target, ent, ctx):
    """-> (entity, status, info). status None = joined / already inside."""
    if ctx["joins"] >= MAX_JOINS_PER_RUN:
        return ent, "skip", "join limit for this run - will join in the next run"
    ctx["joins"] += 1
    try:
        if _is_invite(target):
            from telethon.tl.functions.messages import ImportChatInviteRequest
            upd = await client(ImportChatInviteRequest(target.rsplit("+", 1)[1]))
            chats = getattr(upd, "chats", None) or []
            ent = chats[0] if chats else ent
        else:
            from telethon.tl.functions.channels import JoinChannelRequest
            await client(JoinChannelRequest(ent))
    except Exception as e:
        name = type(e).__name__
        if name == "UserAlreadyParticipantError":
            return ent, None, ""
        if name == "InviteRequestSentError":
            return ent, "skip", "join request sent - waiting for admin approval"
        if name == "FloodWaitError":
            ctx["joins"] = MAX_JOINS_PER_RUN
            return ent, "skip", f"join flood-wait {int(getattr(e, 'seconds', 0) or 0)}s - retry later"
        raise
    await _nap(JOIN_GAP_SEC)
    if ent is None:
        return ent, "fail", "joined but the group could not be opened"
    return ent, None, ""


def _plain_fallback(message):
    def _fb(m):
        raw = (m.group(2) if m.group(1) else m.group(4)) or ""
        return re.sub(r"<[^>]*>", "", raw).strip() or _DEFAULT_FALLBACK
    return _EMOJI_RE.sub(_fb, message)


async def _do_send(client, ent, message, built):
    if built:
        text, ents = built
        await asyncio.wait_for(client.send_message(ent, text, formatting_entities=ents, parse_mode=None,
                                                   link_preview=False), SEND_TIMEOUT)
    else:
        await asyncio.wait_for(client.send_message(ent, _plain_fallback(message), parse_mode="html",
                                                   link_preview=False), SEND_TIMEOUT)


async def _send_one(client, target, message, built, idx, ctx, autojoin):
    """-> (status, info, permanent). status: ok | skip | fail | flood | fatal."""
    for attempt in (1, 2):
        try:
            ent, need_join = await _resolve(client, target, idx)
            if need_join:
                if not autojoin:
                    return "fail", f"{target}: account is not a member (auto-join is OFF)", True
                ent, st, info = await _join(client, target, ent, ctx)
                if st:
                    return st, f"{target}: {info}", False
                idx[_key(target)] = ent
            await _do_send(client, ent, message, built)
            return "ok", "", False
        except Exception as e:
            name = type(e).__name__
            if name == "FloodWaitError":
                secs = int(getattr(e, "seconds", 0) or 0)
                if attempt == 1 and secs <= 90:
                    await asyncio.sleep(secs + 1)
                    continue
                return "flood", secs, False
            if name == "PeerFloodError":
                return "flood", 6 * 3600, False
            if name == "SlowModeWaitError":
                return "skip", f"{target}: slow mode {int(getattr(e, 'seconds', 0) or 0)}s - retry next run", False
            if name in _FATAL:
                return "fatal", name, False
            perm = name in _PERM_ERRORS or (name == "ValueError" and "Cannot find" in str(e))
            hint = _PERM_ERRORS.get(name) or ("group not found - the account must be a member" if perm else "")
            detail = f"{name} - {hint}" if hint else f"{name}: {_errtext(e)[:80]}"
            return "fail", f"{target}: {detail}", perm
    return "fail", f"{target}: retry failed", False


async def _a_campaign(api_id, api_hash, session_string, targets, message, gap, autojoin=True):
    client = _new_client(api_id, api_hash, session_string)
    await client.connect()
    res = {"abort": "", "fatal": False, "wait": 0, "ok_targets": [], "results": {}}
    try:
        if not await client.is_user_authorized():
            res.update(abort="Telegram session expired. Reconnect it.", fatal=True)
            return res
        try:
            built = build_message(message)
        except Exception:
            log.warning("premium-emoji build failed, sending plain text", exc_info=True)
            built = None
        idx = await _dialog_index(client)
        ctx = {"joins": 0}
        targets = list(targets)
        random.shuffle(targets)                  # fair order, looks less robotic
        misses = 0
        for i, target in enumerate(targets):
            with _state_lock:
                if _state["stop"]:
                    res["abort"] = "Stopped by owner."
                    break
                _state["current"] = target
            status, info, perm = await _send_one(client, target, message, built, idx, ctx, autojoin)
            with _state_lock:
                if status == "ok":
                    res["ok_targets"].append(target)
                    res["results"][target] = ("ok", "")
                    _state["sent"] += 1
                    misses = 0
                elif status == "skip":
                    res["results"][target] = ("skip", info)
                elif status == "fail":
                    res["results"][target] = ("perm" if perm else "fail", info)
                    _state["failed"].append(info)
                    misses += 1
            if status == "flood":
                res.update(abort=f"Telegram flood limit - asked to wait {info}s. Stopped.", wait=int(info))
                break
            if status == "fatal":
                res.update(abort=f"Telegram account problem ({info}). Auto-post switched off.", fatal=True)
                break
            if misses >= MAX_FAILS_IN_ROW:
                res["abort"] = f"{MAX_FAILS_IN_ROW} groups failed in a row. Stopped."
                break
            if i < len(targets) - 1:
                await _nap(min(gap, 3) if status == "skip" else gap * random.uniform(0.8, 1.4))
        return res
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _a_check(api_id, api_hash, session_string):
    client = _new_client(api_id, api_hash, session_string)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {"ok": False, "error": "Session expired or revoked - reconnect the Telegram account."}
        me = await client.get_me()
        n = 0
        try:
            async for d in client.iter_dialogs(limit=None):
                if d.is_group or d.is_channel:
                    n += 1
        except Exception:
            n = -1
        return {"ok": True, "name": ((me.first_name or "") + " " + (me.last_name or "")).strip(),
                "username": me.username or "", "premium": bool(getattr(me, "premium", False)), "chats": n}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ------------------------------------------------------------------ helpers used by the Telegram bot (/ad)
def bot_status(db):
    """Everything the owner needs to see in the bot: connection, groups, ad, timing, running state."""
    with _state_lock:
        st = dict(_state)
    nxt = _float_setting(db, "next_run", 0.0)
    return {
        "connected": bool(_saved_session(db)),
        "api": bool(all(_api_credentials(db))),
        "groups": len(_targets(db)),
        "ads": _ads(db) or ([_get(db, "auto_message", "")] if _get(db, "auto_message", "").strip() else []),
        "rotating": bool(_ads(db)),
        "daily_limit": _daily_limit(db),
        "auto_on": _get(db, "auto_enabled", "0") == "1",
        "interval": _int_setting(db, "interval_min", 60, MIN_INTERVAL_MIN, 10080),
        "gap": _int_setting(db, "gap_sec", 12, MIN_GAP_SEC, 3600),
        "next_in_min": max(0, int((nxt - time.time()) / 60) + 1) if nxt > time.time() else 0,
        "last": _get(db, "last_run_text", ""),
        "running": st["running"], "sent": st["sent"], "total": st["total"],
        "hours": _hours_text(db), "autojoin": _autojoin(db), "paused": _paused_count(db),
        "warnings": health(db),
    }


def bot_set_ad(db, message):
    """Adds one ad to the rotation."""
    message = (message or "").strip()
    if not message:
        return False, "The ad is empty."
    if len(message) > MAX_MSG:
        return False, f"The ad is too long (max {MAX_MSG} characters incl. emoji tags)."
    ads = _ads(db)
    if not ads and _get(db, "auto_message", "").strip():
        ads = [_get(db, "auto_message", "").strip()]      # keep the ad written on the website
    if len(ads) >= MAX_ADS:
        return False, f"Maximum {MAX_ADS} ads. Delete one first."
    ads.append(message)
    _save_ads(db, ads)
    return True, f"Ad added. You now have {len(ads)} ad(s); they are sent one after another."


def bot_delete_ad(db, i):
    ads = _ads(db)
    if not ads and _get(db, "auto_message", "").strip():
        ads = [_get(db, "auto_message", "").strip()]
    if not 0 <= i < len(ads):
        return False, "That ad does not exist."
    ads.pop(i)
    _save_ads(db, ads)
    _set(db, "auto_message", "")
    return True, f"Ad deleted. {len(ads)} left."


def bot_set_limit(db, n):
    if not 1 <= n <= 50:
        return False, "Send a number from 1 to 50."
    _set(db, "daily_limit", str(int(n)))
    return True, f"Daily limit saved: max {int(n)} post(s) per group in 24 hours."


def bot_set_timing(db, interval=None, gap=None):
    if interval is not None:
        if interval < MIN_INTERVAL_MIN:
            return False, f"Interval must be at least {MIN_INTERVAL_MIN} minutes."
        _set(db, "interval_min", str(min(int(interval), 10080)))
        if _get(db, "auto_enabled", "0") == "1":
            _set(db, "next_run", str(time.time() + int(interval) * 60))
    if gap is not None:
        if gap < MIN_GAP_SEC:
            return False, f"Gap must be at least {MIN_GAP_SEC} seconds."
        _set(db, "gap_sec", str(min(int(gap), 3600)))
    return True, "Saved."


def bot_set_auto(db, enable):
    if enable:
        if not _ads(db) and not _get(db, "auto_message", "").strip():
            return False, "Add an ad first."
        if not _saved_session(db):
            return False, "Connect the Telegram account first (website -> Telethon Marketing)."
        if not _targets(db):
            return False, "Add at least one group first (website -> Telethon Marketing)."
        interval = _int_setting(db, "interval_min", 60, MIN_INTERVAL_MIN, 10080)
        _set(db, "auto_enabled", "1")
        _set(db, "next_run", str(time.time() + interval * 60))
        return True, f"Auto-post is ON: every {interval} min."
    _set(db, "auto_enabled", "0")
    return True, "Auto-post is OFF."


def bot_send_now(db):
    return _start_campaign(db, None, "manual")


def bot_stop():
    with _state_lock:
        if _state["running"]:
            _state["stop"] = True


# ------------------------------------------------------------------ group health, hours, diagnostics
def _gstate(db):
    try:
        v = json.loads(_get(db, "gstate", "{}") or "{}")
    except ValueError:
        v = {}
    return v if isinstance(v, dict) else {}


def _update_gstate(db, results):
    gs, now = _gstate(db), time.time()
    for t, (st, info) in results.items():
        g = gs.setdefault(t, {"fails": 0})
        if st == "ok":
            g.update(fails=0, err="", skip_until=0, ok=now)
        elif st == "perm":
            g["fails"] = int(g.get("fails", 0)) + 1
            g["err"] = info[:160]
            if g["fails"] >= QUARANTINE_FAILS:
                g["skip_until"] = now + QUARANTINE_HOURS * 3600
        else:
            g["err"] = info[:160]
    live = set(_targets(db))
    _set(db, "gstate", json.dumps({k: v for k, v in gs.items() if k in live}, ensure_ascii=False))


def _paused_count(db):
    now = time.time()
    live = set(_targets(db))
    return sum(1 for k, v in _gstate(db).items() if k in live and v.get("skip_until", 0) > now)


def reset_groups(db, target=None):
    """Clears the pause/fail counters (one group or all)."""
    gs = _gstate(db)
    if target:
        gs.pop(target, None)
    else:
        gs = {}
    _set(db, "gstate", json.dumps(gs, ensure_ascii=False))


def _autojoin(db):
    return _get(db, "autojoin", "1") != "0"


def _hours_text(db):
    a, b = _get(db, "hours_from", ""), _get(db, "hours_to", "")
    return f"{a}-{b} UTC" if a != "" and b != "" else "all day"


def _in_hours(db):
    try:
        a, b = int(_get(db, "hours_from", "")), int(_get(db, "hours_to", ""))
    except ValueError:
        return True
    if a == b:
        return True
    h = time.gmtime().tm_hour
    return (a <= h < b) if a < b else (h >= a or h < b)


def _report(db):
    try:
        v = json.loads(_get(db, "last_report", "[]") or "[]")
    except ValueError:
        v = []
    return v if isinstance(v, list) else []


def _acct_info(db):
    try:
        v = json.loads(_get(db, "acct_info", "{}") or "{}")
    except ValueError:
        v = {}
    return v if isinstance(v, dict) else {}


def health(db):
    """Plain-language problems that stop posting (shown on the website and in /ad)."""
    w = []
    if importlib.util.find_spec("telethon") is None:
        w.append("The 'telethon' package is not installed. Add telethon to requirements.txt and redeploy.")
    if _get(db, "session_enc", "") and not _saved_session(db):
        w.append("The saved Telegram session cannot be decrypted (API_KEY_SECRET or BOT_TOKEN changed). "
                 "Set API_KEY_SECRET once, never change it, then reconnect the account.")
    if _get(db, "api_id_enc", "") and not all(_api_credentials(db)):
        w.append("API ID / API Hash cannot be decrypted. Save them again.")
    path = getattr(tenancy, "DB_PATH", "")
    if path and not os.path.isabs(path):
        w.append(f"DB_PATH is '{path}' (not on a volume). On Railway add a Volume and set DB_PATH=/data/shop.db, "
                 "otherwise the Telegram session, groups and ads are deleted on every redeploy.")
    info = _acct_info(db)
    if info and info.get("ok") is False and info.get("error"):
        w.append("Account check: " + str(info["error"])[:160])
    ads_text = " ".join(_ads(db) + [_get(db, "auto_message", "")])
    if info.get("ok") and not info.get("premium") and ("{e:" in ads_text or "tg-emoji" in ads_text):
        w.append("Your ads use premium emojis but the connected account has no Telegram Premium - "
                 "people will only see the normal fallback emoji.")
    n = _paused_count(db)
    if n:
        w.append(f"{n} group(s) are paused because posting kept failing there. Fix them or press Retry.")
    return w


def check_account(db):
    """Blocking: connects with the saved session and reports account name, Premium and group count."""
    api_id, api_hash = _api_credentials(db)
    sess = _saved_session(db)
    with _state_lock:
        busy = _state["running"]
    if busy:
        return {"ok": None, "error": "A campaign is running - check again when it finishes.", "ts": time.time()}
    if not (api_id and api_hash):
        info = {"ok": False, "error": "API ID / Hash missing or cannot be decrypted."}
    elif not sess:
        info = {"ok": False, "error": "No Telegram session saved (or it cannot be decrypted). Reconnect the account."}
    else:
        try:
            info = _call(_a_check(api_id, api_hash, sess), 60)
        except Exception as e:
            info = {"ok": False, "error": _errtext(e)}
    info["ts"] = time.time()
    _set(db, "acct_info", json.dumps(info, ensure_ascii=False))
    return info


def acct_line(info):
    if not info:
        return ""
    if info.get("ok"):
        who = info.get("name") or "account"
        if info.get("username"):
            who += f" (@{info['username']})"
        return (f"{who} - Premium: {'yes' if info.get('premium') else 'NO'} - "
                f"in {info.get('chats', '?')} group/channel chats")
    return "Problem: " + str(info.get("error", "unknown"))


def _notify_owner(tenant, text):
    """Tells the owner in the store bot when auto-post stops by itself."""
    try:
        import requests
        if tenant and getattr(tenant, "bot_token", "") and getattr(tenant, "owner_id", 0):
            requests.post(f"https://api.telegram.org/bot{tenant.bot_token}/sendMessage",
                          json={"chat_id": tenant.owner_id, "text": text[:3500]}, timeout=15)
    except Exception:
        log.warning("could not notify owner", exc_info=True)


def bot_set_hours(db, text):
    t = (text or "").strip().lower()
    if t in ("0", "all", "off", "-", "24"):
        _set(db, "hours_from", "")
        _set(db, "hours_to", "")
        return True, "Posting window removed: auto-post can run all day."
    try:
        a, b = (int(x) for x in t.replace(" ", "").split("-", 1))
    except ValueError:
        return False, "Send hours like 5-20 (UTC), or 0 for all day."
    if not (0 <= a <= 23 and 0 <= b <= 23) or a == b:
        return False, "Hours must be 0-23 and different, e.g. 5-20."
    _set(db, "hours_from", str(a))
    _set(db, "hours_to", str(b))
    return True, f"Auto-post will only start between {a}:00 and {b}:00 UTC."


def bot_toggle_join(db):
    new = not _autojoin(db)
    _set(db, "autojoin", "1" if new else "0")
    return new


def bot_report(db):
    return _report(db)


# ------------------------------------------------------------------ campaigns
def _finish(db, tenant, source, res):
    with _state_lock:
        sent, total = _state["sent"], _state["total"]
        failed = list(_state["failed"])
        _state["running"] = False
        _state["current"] = ""
    abort = res.get("abort", "")
    results = res.get("results", {})
    skipped = sum(1 for st, _ in results.values() if st == "skip")
    text = f"{source}: sent {sent}/{total}, failed {len(failed)}" + (f", skipped {skipped}." if skipped else ".")
    if abort:
        text += " " + abort
    if failed:
        text += " Errors: " + " | ".join(failed[:3])
    try:
        with tenancy.use(tenant):
            _set(db, "last_run_ts", str(time.time()))
            _set(db, "last_run_text", text[:600])
            if res.get("ok_targets"):
                _record_sent(db, res["ok_targets"])
            if results:
                _update_gstate(db, results)
                _set(db, "last_report", json.dumps(
                    [[t, st, info[:140]] for t, (st, info) in results.items() if st != "ok"][:80],
                    ensure_ascii=False))
            if res.get("fatal"):
                _set(db, "auto_enabled", "0")
            if source == "auto" and _get(db, "auto_enabled", "0") == "1":
                interval = _int_setting(db, "interval_min", 60, MIN_INTERVAL_MIN, 10080)
                wait = max(interval * 60, int(res.get("wait", 0)) + 60)
                _set(db, "next_run", str(time.time() + wait))
    except Exception:
        log.exception("could not save Telethon campaign result")
    if source == "auto" and (res.get("fatal") or (total and sent == 0)):
        _notify_owner(tenant, "⚠️ Telethon auto-post problem\n" + text +
                      ("\nAuto-post was switched OFF. Fix it in /ad or the website." if res.get("fatal") else ""))


# ------------------------------------------------------------------ ad rotation + per-group daily limit
def _ads(db):
    try:
        v = json.loads(_get(db, "ads", "[]") or "[]")
    except ValueError:
        v = []
    return [a for a in v if isinstance(a, str) and a.strip()]


def _save_ads(db, ads):
    _set(db, "ads", json.dumps(ads, ensure_ascii=False))


def _pick_ad(db):
    """Next ad of the rotation (or the single auto message). Returns (message, index)."""
    ads = _ads(db)
    if not ads:
        return _get(db, "auto_message", ""), -1
    try:
        i = int(_get(db, "ad_idx", "0") or 0)
    except ValueError:
        i = 0
    return ads[i % len(ads)], i


def _daily_limit(db):
    return _int_setting(db, "daily_limit", DEFAULT_DAILY, 1, 50)


def _sent_log(db):
    try:
        v = json.loads(_get(db, "sent_log", "{}") or "{}")
    except ValueError:
        v = {}
    cut = time.time() - 86400
    return {k: [t for t in ts if t > cut] for k, ts in v.items() if isinstance(ts, list)}


def _within_limit(db, targets):
    log_, lim = _sent_log(db), _daily_limit(db)
    return [t for t in targets if len(log_.get(t, [])) < lim]


def _record_sent(db, ok_targets):
    log_, now = _sent_log(db), time.time()
    for t in ok_targets:
        log_.setdefault(t, []).append(now)
    _set(db, "sent_log", json.dumps({k: v for k, v in log_.items() if v}))


def _start_campaign(db, message, source):
    """Starts a background campaign. Returns (ok, text). Must run with the owner shop active."""
    rotate = message is None
    idx = -1
    if rotate:
        message, idx = _pick_ad(db)
    message = (message or "").strip()
    if not message:
        return False, "Message is empty."
    if len(message) > MAX_MSG:
        return False, f"Message is too long (max {MAX_MSG} characters)."
    api_id, api_hash = _api_credentials(db)
    if not api_id or not api_hash:
        return False, "Save Telegram API ID and API Hash first."
    session_string = _saved_session(db)
    if not session_string:
        return False, "Connect the Telegram account first."
    targets = _targets(db)
    if not targets:
        return False, "Add at least one approved group first."
    gs, now_ = _gstate(db), time.time()
    live = [t for t in targets if gs.get(t, {}).get("skip_until", 0) <= now_]
    paused = len(targets) - len(live)
    if not live:
        return False, f"All {paused} group(s) are paused after repeated errors. Check the group list, then Retry."
    allowed = _within_limit(db, live)
    if not allowed:
        return False, f"All groups already got {_daily_limit(db)} post(s) in the last 24h (daily limit)."
    targets = allowed
    gap = _int_setting(db, "gap_sec", 12, MIN_GAP_SEC, 3600)
    autojoin = _autojoin(db)
    with _state_lock:
        if _state["running"]:
            return False, "A campaign is already running."
        _state.update(running=True, source=source, sent=0, total=len(targets), failed=[],
                      started=time.time(), stop=False, current="")
    if rotate and idx >= 0:
        _set(db, "ad_idx", str(idx + 1))
    tenant = tenancy.get_tenant(tenancy.MAIN_ID)

    async def _job():
        try:
            res = await _a_campaign(api_id, api_hash, session_string, targets, message, gap, autojoin)
        except Exception as e:
            log.exception("Telethon campaign crashed")
            res = {"abort": _errtext(e), "fatal": False, "wait": 0, "ok_targets": []}
        _finish(db, tenant, source, res)

    try:
        asyncio.run_coroutine_threadsafe(_job(), _get_loop())
    except Exception as e:
        with _state_lock:
            _state["running"] = False
        return False, _errtext(e)
    extra = f" ({paused} paused)" if paused else ""
    return True, f"Started: sending to {len(targets)} group(s) in the background{extra}."


def _scheduler(db):
    while True:
        time.sleep(_TICK)
        try:
            tenant = tenancy.get_tenant(tenancy.MAIN_ID)
            with tenancy.use(tenant):
                if _get(db, "auto_enabled", "0") != "1":
                    continue
                with _state_lock:
                    if _state["running"]:
                        continue
                if time.time() < _float_setting(db, "next_run", 0.0):
                    continue
                if not _in_hours(db):
                    continue
                interval = _int_setting(db, "interval_min", 60, MIN_INTERVAL_MIN, 10080)
                ok, info = _start_campaign(db, None, "auto")
                if not ok:
                    _set(db, "next_run", str(time.time() + interval * 60))
                    _set(db, "last_run_ts", str(time.time()))
                    _set(db, "last_run_text", "auto skipped: " + info)
        except Exception:
            log.exception("Telethon scheduler error")


def _ensure_scheduler(db):
    global _sched_started
    if not _sched_started:
        _sched_started = True
        threading.Thread(target=_scheduler, args=(db,), daemon=True, name="telethon-scheduler").start()


# ------------------------------------------------------------------ web pages
def register(app, render_str, db):
    _ensure_scheduler(db)

    def _owner_only():
        from tenancy import current_or_none
        t = current_or_none()
        return bool(t and t.is_main)

    def _deny():
        flash("Owner access required.", "err")
        return redirect(url_for("dashboard"))

    def _home():
        return redirect(url_for("telethon_admin_home"))

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
        auto_on = _get(db, "auto_enabled", "0") == "1"
        nxt = _float_setting(db, "next_run", 0.0)
        now = time.time()
        if nxt > now:
            mins = int((nxt - now) / 60) + 1
            next_text = f"in about {mins} min ({_fmt_ts(nxt)})"
        else:
            next_text = "due now (starts within a minute)"
        with _state_lock:
            state = dict(_state)
        page = r'''{% extends "base.html" %}{% block body %}
<div class="page-head"><div><h2>Telethon Marketing</h2><p class="muted">Connected Telegram account, approved groups and auto-posting.</p></div><span class="badge">Owner only</span></div>

{% if warnings %}<div class="card" style="border:1px solid #c0392b"><h3>&#9888;&#65039; Needs attention</h3><ul>{% for w in warnings %}<li>{{ w }}</li>{% endfor %}</ul></div>{% endif %}

<div class="card"><h3>Auto-post</h3>
<p>{% if auto_on %}<span class="badge" style="background:#13805a;color:white">ON</span> Next run: {{ next_text }}{% else %}<span class="badge">OFF</span> Auto-post is not running.{% endif %}</p>
{% if state.running %}<p><b>Running ({{ state.source }}):</b> sent {{ state.sent }} of {{ state.total }}{% if state.current %} &middot; now: <span class="mono">{{ state.current }}</span>{% endif %}</p>
<form method="post" action="{{ url_for('telethon_stop') }}"><button class="red sm" type="submit">Stop</button></form>
<script>setTimeout(function(){location.reload()},8000)</script>{% endif %}
{% if last_text %}<p class="muted">Last run ({{ last_when }}): {{ last_text }}</p>{% endif %}
{% if report %}<details><summary>Problem groups in the last run ({{ report|length }})</summary><div class="scroll"><table><thead><tr><th>Group</th><th>Result</th><th>Why</th></tr></thead><tbody>{% for r in report %}<tr><td class="mono">{{ r[0] }}</td><td>{{ r[1] }}</td><td>{{ r[2] }}</td></tr>{% endfor %}</tbody></table></div></details>{% endif %}
<form method="post" action="{{ url_for('telethon_auto_save') }}">
<label style="display:flex;gap:8px;align-items:center"><input style="width:auto;margin:0" type="checkbox" name="enabled" value="1" {% if auto_on %}checked{% endif %}> Turn auto-post ON</label>
<label>Repeat every (minutes, minimum {{ min_interval }})</label><input name="interval" type="number" min="{{ min_interval }}" value="{{ interval }}" required>
<label>Gap between groups (seconds, minimum {{ min_gap }})</label><input name="gap" type="number" min="{{ min_gap }}" value="{{ gap }}" required>
<label>Only start auto-posts between these hours (UTC, optional - leave both empty for all day)</label><div style="display:flex;gap:8px"><input name="hours_from" type="number" min="0" max="23" placeholder="from e.g. 5" value="{{ hours_from }}"><input name="hours_to" type="number" min="0" max="23" placeholder="to e.g. 20" value="{{ hours_to }}"></div>
<label style="display:flex;gap:8px;align-items:center"><input style="width:auto;margin:0" type="checkbox" name="autojoin" value="1" {% if autojoin %}checked{% endif %}> Auto-join groups the account is not in yet (max 5 new groups per run)</label>
<p class="muted">Premium emoji: write <span class="mono">{e:EMOJI_ID|🔥}</span> (ID of the premium emoji, 🔥 = fallback). Needs Telegram Premium on the connected account. Bold/italic: <span class="mono">&lt;b&gt;text&lt;/b&gt;</span>.</p><label>Auto message</label><textarea name="message" rows="6" maxlength="3500" placeholder="Message to post automatically...">{{ auto_message }}</textarea>
<label style="display:flex;gap:8px;align-items:center"><input style="width:auto;margin:0" type="checkbox" name="confirm" value="yes"> I confirm this message is allowed in every group below (needed to turn auto-post ON).</label>
<button type="submit">Save auto-post settings</button></form>
<form method="post" action="{{ url_for('telethon_auto_run_now') }}" style="margin-top:8px"><button type="submit" {% if state.running or not connected or not targets %}disabled{% endif %}>Send the auto message now</button></form>
</div>

<div class="card"><h3>Ads (rotation) &middot; {{ ads|length }}/{{ max_ads }}</h3>
<p class="muted">Add different real ads (e.g. Netflix, Spotify, Gemini). Each run sends the next one in turn. While this list has ads, the single "Auto message" above is not used. Premium emoji: <span class="mono">{e:EMOJI_ID|🔥}</span>.</p>
{% if ads %}<div class="scroll"><table><thead><tr><th>#</th><th>Ad</th><th>Action</th></tr></thead><tbody>{% for a in ads %}<tr><td>{{ loop.index }}</td><td style="white-space:pre-wrap">{{ a }}</td><td><form method="post" action="{{ url_for('telethon_ad_delete') }}"><input type="hidden" name="i" value="{{ loop.index0 }}"><button class="red sm" type="submit">Delete</button></form></td></tr>{% endfor %}</tbody></table></div>{% else %}<p class="empty">No rotation ads yet.</p>{% endif %}
<form method="post" action="{{ url_for('telethon_ad_add') }}"><label>New ad</label><textarea name="message" rows="5" maxlength="3500" placeholder="Write one ad..." required></textarea><label style="display:flex;gap:8px;align-items:center"><input style="width:auto;margin:0" type="checkbox" name="confirm" value="yes" required> I confirm this ad is allowed in every group below.</label><button type="submit">Add ad</button></form>
<form method="post" action="{{ url_for('telethon_limit_save') }}" style="margin-top:10px"><label>Daily limit: max posts per group in 24 hours (1-50, recommended 1-2)</label><input name="limit" type="number" min="1" max="50" value="{{ daily_limit }}" required><button type="submit">Save daily limit</button></form>
</div>

<div class="card"><h3>Approved groups ({{ targets|length }})</h3><p class="muted">No limit. Add only groups where you are an admin or promotions are allowed. One group per line.</p>
<form method="post" action="{{ url_for('telethon_add_target') }}"><label>Group usernames or IDs (one per line)</label><textarea name="targets" rows="5" placeholder="@group1&#10;t.me/group2&#10;https://t.me/+InviteLink&#10;-1001234567890" required></textarea><label style="display:flex;gap:8px;align-items:center"><input style="width:auto;margin:0" type="checkbox" name="permission" value="yes" required> I administer these groups or have permission to post promotions.</label><button type="submit">Add groups</button></form>
{% if targets %}<div class="scroll"><table><thead><tr><th>Group</th><th>Status</th><th>Action</th></tr></thead><tbody>{% for target in targets %}{% set g = gs.get(target, {}) %}<tr><td class="mono">{{ target }}</td><td>{% if g.get('skip_until', 0) > now %}<span class="badge" style="background:#c0392b;color:white">paused</span>{% elif g.get('ok') %}<span class="badge" style="background:#13805a;color:white">ok</span>{% else %}<span class="badge">new</span>{% endif %}{% if g.get('err') %} <span class="muted">{{ g.get('err') }}</span>{% endif %}</td><td><form method="post" action="{{ url_for('telethon_remove_target') }}" style="display:inline"><input type="hidden" name="target" value="{{ target }}"><button class="red sm" type="submit">Remove</button></form>{% if g.get('fails') or g.get('skip_until') %} <form method="post" action="{{ url_for('telethon_reset_groups') }}" style="display:inline"><input type="hidden" name="target" value="{{ target }}"><button class="sm" type="submit">Retry</button></form>{% endif %}</td></tr>{% endfor %}</tbody></table></div>
<form method="post" action="{{ url_for('telethon_clear_targets') }}" onsubmit="return confirm('Remove ALL groups?')" style="display:inline"><button class="red sm" type="submit">Remove all groups</button></form> <form method="post" action="{{ url_for('telethon_reset_groups') }}" style="display:inline"><button class="sm" type="submit">Retry all paused</button></form>{% else %}<p class="empty">No groups added yet.</p>{% endif %}</div>

<div class="card"><h3>Send once now</h3><p class="muted">Sends this message to all groups above in the background (with the gap set in Auto-post).</p>
<form method="post" action="{{ url_for('telethon_send_message') }}"><p class="muted">Premium emoji: write <span class="mono">{e:EMOJI_ID|🔥}</span> (ID of the premium emoji, 🔥 = fallback). Needs Telegram Premium on the connected account. Bold/italic: <span class="mono">&lt;b&gt;text&lt;/b&gt;</span>.</p><label>Message</label><textarea name="message" rows="5" maxlength="3500" placeholder="Write your promotion..." required></textarea><label style="display:flex;gap:8px;align-items:center"><input style="width:auto;margin:0" type="checkbox" name="confirm" value="yes" required> I confirm this message is relevant and allowed in every group.</label><button type="submit" {% if state.running or not connected or not targets %}disabled{% endif %}>Send now</button></form></div>

<div class="card"><h3>Connection</h3>
{% if connected %}<p><span class="badge" style="background:#13805a;color:white">Session saved</span> Telegram account is connected.</p>
{% if acct_text %}<p class="muted">{{ acct_text }} ({{ acct_when }})</p>{% endif %}
<form method="post" action="{{ url_for('telethon_check') }}" style="margin-bottom:8px"><button type="submit">&#128269; Check account and groups</button></form>
<form method="post" action="{{ url_for('telethon_disconnect') }}" onsubmit="return confirm('Disconnect the Telegram account? Auto-post will stop working.')"><button class="red sm" type="submit">Disconnect session</button></form>
{% else %}<p class="muted">Save your Telegram API credentials below once, then sign in with a dedicated Telegram account.</p>
{% if api_configured %}<p><span class="badge" style="background:#13805a;color:white">API credentials saved</span></p>{% endif %}
<form method="post" action="{{ url_for('telethon_send_code') }}"><label>Phone number with country code</label><input name="phone" placeholder="+91XXXXXXXXXX" required><button type="submit">Send Telegram login code</button></form>
{% if pending %}<form method="post" action="{{ url_for('telethon_verify_code') }}"><label>Telegram login code</label><input name="code" inputmode="numeric" {% if not need_password %}required{% endif %}><label>2-step verification password (only if Telegram asks)</label><input name="password" type="password" autocomplete="off"><button type="submit">Verify and save session</button></form>{% endif %}{% endif %}</div>

<div class="card"><h3>Telegram API credentials (one-time)</h3><p class="muted">From my.telegram.org &rarr; API development tools. Encrypted in the store database.</p>
<form method="post" action="{{ url_for('telethon_save_api') }}"><label>Telegram API ID</label><input name="api_id" inputmode="numeric" placeholder="{% if api_configured %}Saved - enter new value only to replace{% else %}API ID{% endif %}" required><label>Telegram API Hash</label><input name="api_hash" type="password" autocomplete="new-password" placeholder="{% if api_configured %}Saved - enter new value only to replace{% else %}API Hash{% endif %}" required><button type="submit">Save API credentials</button></form></div>
<p class="muted">Tip: use a dedicated Telegram account, and keep the interval and gap generous. Telegram can limit accounts that post too often.</p>
{% endblock %}'''
        pend = _pending.get("owner")
        return render_str(
            page, connected=connected, targets=targets, api_configured=api_configured,
            pending=bool(pend), need_password=bool(pend and pend.get("need_password")),
            auto_on=auto_on, next_text=next_text, state=state,
            last_text=_get(db, "last_run_text", ""), last_when=_fmt_ts(_float_setting(db, "last_run_ts", 0.0)),
            interval=_int_setting(db, "interval_min", 60, MIN_INTERVAL_MIN, 10080),
            gap=_int_setting(db, "gap_sec", 12, MIN_GAP_SEC, 3600),
            auto_message=_get(db, "auto_message", ""),
            ads=_ads(db), max_ads=MAX_ADS, daily_limit=_daily_limit(db),
            min_interval=MIN_INTERVAL_MIN, min_gap=MIN_GAP_SEC,
            warnings=health(db), report=_report(db), gs=_gstate(db), now=time.time(),
            hours_from=_get(db, "hours_from", ""), hours_to=_get(db, "hours_to", ""), autojoin=_autojoin(db),
            acct_text=acct_line(_acct_info(db)), acct_when=_fmt_ts(_acct_info(db).get("ts", 0)))

    @app.post("/telethon/save-api")
    def telethon_save_api():
        if not _owner_only():
            return _deny()
        try:
            api_id = int((request.form.get("api_id") or "").strip())
            api_hash = (request.form.get("api_hash") or "").strip()
            if api_id <= 0 or not api_hash or len(api_hash) > 128:
                raise ValueError("Enter a valid Telegram API ID and API Hash.")
            _set(db, "api_id_enc", encrypt(str(api_id)))
            _set(db, "api_hash_enc", encrypt(api_hash))
            flash("Telegram API credentials saved securely. No need to edit Railway Variables.")
        except Exception as e:
            flash(_errtext(e), "err")
        return _home()

    @app.post("/telethon/send-code")
    def telethon_send_code():
        if not _owner_only():
            return _deny()
        phone = (request.form.get("phone") or "").strip()
        try:
            if not phone.startswith("+") or len(phone) < 8 or len(phone) > 18:
                raise ValueError("Enter phone number in international format, e.g. +91...")
            api_id, api_hash = _api_credentials(db)
            if not api_id or not api_hash:
                raise RuntimeError("Save Telegram API ID and API Hash once in the owner dashboard first.")
            client, code_hash = _call(_a_send_code(api_id, api_hash, phone))
            with _pending_lock:
                old = _pending.pop("owner", None)
                _pending["owner"] = {"client": client, "phone": phone, "phone_code_hash": code_hash,
                                     "created": time.time(), "need_password": False}
            if old:
                try:
                    _call(_a_disconnect(old["client"]), 20)
                except Exception:
                    pass
            flash("Login code sent in Telegram. Enter it below.")
        except Exception as e:
            flash(_errtext(e), "err")
        return _home()

    @app.post("/telethon/verify-code")
    def telethon_verify_code():
        if not _owner_only():
            return _deny()
        with _pending_lock:
            pending = _pending.get("owner")
        if not pending or time.time() - pending["created"] > 600:
            flash("Login request expired. Send a new code.", "err")
            return _home()
        try:
            code = (request.form.get("code") or "").strip()
            password = request.form.get("password") or ""
            status, session_string = _call(_a_sign_in(
                pending["client"], pending["phone"], code, pending["phone_code_hash"],
                password, pending.get("need_password", False)))
            if status == "need_password":
                pending["need_password"] = True
                flash("Telegram requires your 2-step password. Enter it and submit again.", "err")
                return _home()
            _set(db, "session_enc", encrypt(session_string))
            with _pending_lock:
                _pending.pop("owner", None)
            flash("Telegram account connected. Session encrypted and saved.")
        except Exception as e:
            flash(_errtext(e), "err")
        return _home()

    @app.post("/telethon/disconnect")
    def telethon_disconnect():
        if not _owner_only():
            return _deny()
        db.del_settings([PREFIX + "session_enc"])
        _set(db, "auto_enabled", "0")
        flash("Telegram session disconnected. Auto-post switched off.")
        return _home()

    @app.post("/telethon/targets/add")
    def telethon_add_target():
        if not _owner_only():
            return _deny()
        if request.form.get("permission") != "yes":
            flash("Confirm group promotion permission first.", "err")
            return _home()
        raw = re.split(r"[\s,;]+", request.form.get("targets") or "")
        targets = _targets(db)
        added, skipped, bad = 0, 0, []
        for item in raw:
            if not item:
                continue
            try:
                target = _safe_target(item)
            except Exception:
                bad.append(item[:30])
                continue
            if target in targets:
                skipped += 1
            else:
                targets.append(target)
                added += 1
        _save_targets(db, targets)
        msg = f"{added} group(s) added."
        if skipped:
            msg += f" {skipped} already in the list."
        if bad:
            msg += " Invalid: " + ", ".join(bad[:5])
        flash(msg, "err" if bad and not added else None)
        return _home()

    @app.post("/telethon/targets/remove")
    def telethon_remove_target():
        if not _owner_only():
            return _deny()
        target = request.form.get("target", "")
        _save_targets(db, [x for x in _targets(db) if x != target])
        flash("Group removed.")
        return _home()

    @app.post("/telethon/targets/clear")
    def telethon_clear_targets():
        if not _owner_only():
            return _deny()
        _save_targets(db, [])
        flash("All groups removed.")
        return _home()

    @app.post("/telethon/auto/save")
    def telethon_auto_save():
        if not _owner_only():
            return _deny()
        try:
            interval = int(float(request.form.get("interval") or 0))
            gap = int(float(request.form.get("gap") or 0))
        except ValueError:
            flash("Interval and gap must be numbers.", "err")
            return _home()
        if interval < MIN_INTERVAL_MIN:
            flash(f"Interval must be at least {MIN_INTERVAL_MIN} minutes.", "err")
            return _home()
        if gap < MIN_GAP_SEC:
            flash(f"Gap must be at least {MIN_GAP_SEC} seconds.", "err")
            return _home()
        message = (request.form.get("message") or "").strip()
        if len(message) > MAX_MSG:
            flash(f"Message is too long (max {MAX_MSG} characters).", "err")
            return _home()
        enable = request.form.get("enabled") == "1"
        was_on = _get(db, "auto_enabled", "0") == "1"
        if enable:
            if request.form.get("confirm") != "yes":
                flash("Tick the confirmation box to turn auto-post ON.", "err")
                return _home()
            if not message and not _ads(db):
                flash("Write the auto message (or add an ad in the Ads list) first.", "err")
                return _home()
            if not _saved_session(db):
                flash("Connect the Telegram account first.", "err")
                return _home()
            if not _targets(db):
                flash("Add at least one group first.", "err")
                return _home()
        hf = (request.form.get("hours_from") or "").strip()
        ht = (request.form.get("hours_to") or "").strip()
        if hf or ht:
            try:
                ok_h = 0 <= int(hf) <= 23 and 0 <= int(ht) <= 23 and int(hf) != int(ht)
            except ValueError:
                ok_h = False
            if not ok_h:
                flash("Posting hours: fill both fields with different hours 0-23 (UTC), or leave both empty.", "err")
                return _home()
        _set(db, "hours_from", hf)
        _set(db, "hours_to", ht)
        _set(db, "autojoin", "1" if request.form.get("autojoin") == "1" else "0")
        _set(db, "interval_min", str(min(interval, 10080)))
        _set(db, "gap_sec", str(min(gap, 3600)))
        _set(db, "auto_message", message)
        _set(db, "auto_enabled", "1" if enable else "0")
        now = time.time()
        if enable:
            limit = now + interval * 60
            current = _float_setting(db, "next_run", 0.0)
            if not was_on or current <= 0 or current > limit:
                _set(db, "next_run", str(limit))
            flash(f"Auto-post is ON: every {interval} min, {gap}s between groups.")
        else:
            flash("Auto-post settings saved. Auto-post is OFF.")
        return _home()

    @app.post("/telethon/auto/run-now")
    def telethon_auto_run_now():
        if not _owner_only():
            return _deny()
        ok, info = _start_campaign(db, None, "manual")
        flash(info, None if ok else "err")
        return _home()

    @app.post("/telethon/check")
    def telethon_check():
        if not _owner_only():
            return _deny()
        info = check_account(db)
        if info.get("ok"):
            flash("Account OK: " + acct_line(info))
        else:
            flash("Account check: " + str(info.get("error", "problem")), "err")
        return _home()

    @app.post("/telethon/groups/reset")
    def telethon_reset_groups():
        if not _owner_only():
            return _deny()
        reset_groups(db, (request.form.get("target") or "").strip() or None)
        flash("Group error counters cleared - they will be tried again.")
        return _home()

    @app.post("/telethon/stop")
    def telethon_stop():
        if not _owner_only():
            return _deny()
        with _state_lock:
            if _state["running"]:
                _state["stop"] = True
        flash("Stopping after the current group.")
        return _home()

    @app.post("/telethon/send")
    def telethon_send_message():
        if not _owner_only():
            return _deny()
        if request.form.get("confirm") != "yes":
            flash("Confirm that the message is allowed in every group.", "err")
            return _home()
        ok, info = _start_campaign(db, request.form.get("message"), "manual")
        flash(info, None if ok else "err")
        return _home()

    @app.post("/telethon/ads/add")
    def telethon_ad_add():
        if not _owner_only():
            return _deny()
        if request.form.get("confirm") != "yes":
            flash("Confirm that the ad is allowed in every group.", "err")
            return _home()
        ok, info = bot_set_ad(db, request.form.get("message"))
        flash(info, None if ok else "err")
        return _home()

    @app.post("/telethon/ads/delete")
    def telethon_ad_delete():
        if not _owner_only():
            return _deny()
        try:
            i = int(request.form.get("i") or -1)
        except ValueError:
            i = -1
        ok, info = bot_delete_ad(db, i)
        flash(info, None if ok else "err")
        return _home()

    @app.post("/telethon/limit")
    def telethon_limit_save():
        if not _owner_only():
            return _deny()
        try:
            n = int(float(request.form.get("limit") or 0))
        except ValueError:
            n = 0
        ok, info = bot_set_limit(db, n)
        flash(info, None if ok else "err")
        return _home()
