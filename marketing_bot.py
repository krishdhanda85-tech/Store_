"""
Separate MARKETING BOT (own Telegram token) that auto-posts product promos to your channels/groups.

How it works (all free):
  * Manager agent (agents.manager_pick, plain Python) picks what to promote from your REAL products.
  * Marketing agent (agents.write_hook, Gemini) writes one short hook line.
  * This bot posts it with the real price / stock and a Buy button that opens your store bot.
It only SENDS messages (no polling), so it never conflicts with your store bot.

Railway variables:
  MARKETING_BOT_TOKEN        token of the NEW bot from @BotFather (required)
  MARKETING_CHAT_IDS         where to post, comma separated: channel/group IDs or @channelname
  MARKETING_INTERVAL_MIN     minutes between posts (default 60)
  MARKETING_ACTIVE_HOURS     optional, UTC hours to post in, e.g. 5-20 (default: all day)
  STORE_BOT_USERNAME         optional, store bot username for the Buy button (auto-detected if empty)
  GEMINI_API_KEY             optional, for the AI hook line (see agents.py)
The marketing bot must be ADMIN (with "Post messages") in every channel it posts to,
and a member of every group.
"""
import html
import logging
import os
import random
import threading
import time

import requests

import agents
import tenancy

log = logging.getLogger("marketing_bot")

TOKEN = os.getenv("MARKETING_BOT_TOKEN", "").strip()
CHATS = [c.strip() for c in os.getenv("MARKETING_CHAT_IDS", "").split(",") if c.strip()]
INTERVAL_MIN = max(5, int(os.getenv("MARKETING_INTERVAL_MIN", "60") or 60))
ACTIVE_HOURS = os.getenv("MARKETING_ACTIVE_HOURS", "").strip()
LOW = int(os.getenv("LOW_STOCK_THRESHOLD", "3") or 3)

_TAGLINES = ("🔥 Deal alert!", "⚡ Instant delivery", "💎 Featured product", "✅ Available now", "🎯 Today's pick")
_last = {"pid": None}


def _esc(x):
    return html.escape(str(x))


def _is_manual(p):
    return (p["kind"] or "auto") == "manual"


def _available(p):
    return _is_manual(p) or int(p["stock"] or 0) > 0


def _pct(p):
    try:
        old, price = float(p["old_price"] or 0), float(p["price"])
        if old > 0 and old > price:
            return int(round((old - price) / old * 100))
    except (TypeError, ValueError):
        pass
    return 0


def _in_active_hours():
    if not ACTIVE_HOURS or "-" not in ACTIVE_HOURS:
        return True
    try:
        a, b = (int(x) for x in ACTIVE_HOURS.split("-", 1))
    except ValueError:
        return True
    h = time.gmtime().tm_hour
    return (a <= h < b) if a <= b else (h >= a or h < b)


def _api(method, **payload):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=payload, timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('description', r.status_code)}")
    return data["result"]


def build_post():
    """Returns (text, buy_url) or None when there is nothing to promote."""
    t = tenancy.get_tenant(tenancy.MAIN_ID)
    if t is None:
        return None
    with tenancy.use(t):
        products = [p for p in tenancy.db.list_products() if _available(p)]
        uname = (os.getenv("STORE_BOT_USERNAME") or getattr(t, "bot_username", "") or "").lstrip("@")
    if not products:
        return None
    p, reason = agents.manager_pick(products, _last["pid"], LOW, _pct, _is_manual)
    _last["pid"] = p["id"]
    hook = agents.write_hook(p["name"], reason, None, p["description"] or "")
    price, pct = float(p["price"]), _pct(p)
    if hook:
        h1, _, h2 = hook.partition("\n")
        head = f"<b>{_esc(h1)}</b>" + (f"\n<i>{_esc(h2)}</i>" if h2.strip() else "")
    else:
        head = f"<b>{random.choice(_TAGLINES)}</b>"
    lines = [head, "", f"<b>{_esc(p['name'])}</b>"]
    if pct:
        lines.append(f"💰 <s>${float(p['old_price']):.2f}</s> → <b>${price:.2f}</b>  (🔻{pct}% OFF)")
    else:
        lines.append(f"💰 Price: <b>${price:.2f}</b>")
    if not _is_manual(p):
        lines.append(f"📦 In stock: <b>{int(p['stock'])}</b>")
    lines += ["", "👇 Tap <b>Buy Now</b> to order"]
    return "\n".join(lines), (f"https://t.me/{uname}" if uname else "")


def post_once():
    post = build_post()
    if not post:
        return
    text, url = post
    markup = {"inline_keyboard": [[{"text": "🛒 Buy Now", "url": url}]]} if url else None
    for chat in CHATS:
        try:
            kw = dict(chat_id=chat, text=text, parse_mode="HTML", disable_web_page_preview=True)
            if markup:
                kw["reply_markup"] = markup
            _api("sendMessage", **kw)
        except Exception as e:
            log.error("Marketing post to %s failed (is the bot admin / member there?): %s", chat, str(e)[:200])


def _loop():
    time.sleep(90)                      # let the web + store bot start first
    while True:
        try:
            if _in_active_hours():
                post_once()
        except Exception:
            log.exception("marketing loop error")
        time.sleep(INTERVAL_MIN * 60)


def start():
    """Called from run.py. Does nothing unless MARKETING_BOT_TOKEN and MARKETING_CHAT_IDS are set."""
    if not TOKEN or not CHATS:
        return
    if TOKEN == os.getenv("BOT_TOKEN", ""):
        log.error("MARKETING_BOT_TOKEN must be a DIFFERENT bot than the store bot. Marketing bot not started.")
        return
    threading.Thread(target=_loop, daemon=True, name="marketing-bot").start()
    log.info("Marketing bot started: every %s min to %s chat(s)", INTERVAL_MIN, len(CHATS))
