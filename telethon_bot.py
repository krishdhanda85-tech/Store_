"""Owner-only /ad command: set up and run the Telethon group ad from inside the Telegram bot.

The owner just sends the ad as a normal Telegram message (premium emojis, bold, italic, links are
kept). It is stored as the Telethon "auto message", so the website and the bot share one ad.
Only the platform owner of the MAIN shop can use it.
"""
import asyncio
import html
import logging

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import tenancy
import telethon_admin as ta

log = logging.getLogger("telethon_bot")
HTML = "HTML"


def _allowed(update):
    import bot
    uid = update.effective_user.id
    t = tenancy.current_or_none()
    return bool(bot.is_owner(uid) and t is not None and getattr(t, "is_main", False))


def to_ad_html(text, entities):
    """Telegram message text + entities -> HTML for the ad (premium emojis always kept)."""
    simple = {"bold": "b", "italic": "i", "underline": "u", "strikethrough": "s", "code": "code",
              "pre": "pre", "spoiler": "tg-spoiler", "blockquote": "blockquote"}
    opens, closes = {}, {}
    for e in sorted(entities or [], key=lambda e: (e.offset, -e.length)):
        t = e.type
        if t == "custom_emoji" and e.custom_emoji_id:
            o, c = f'<tg-emoji emoji-id="{e.custom_emoji_id}">', "</tg-emoji>"
        elif t == "text_link" and e.url:
            o, c = f'<a href="{html.escape(e.url, quote=True)}">', "</a>"
        elif t == "expandable_blockquote":
            o, c = "<blockquote expandable>", "</blockquote>"
        elif t in simple:
            o, c = f"<{simple[t]}>", f"</{simple[t]}>"
        else:
            continue
        opens.setdefault(e.offset, []).append(o)
        closes.setdefault(e.offset + e.length, []).insert(0, c)
    out, pos = [], 0
    for ch in text:
        out.extend(closes.get(pos, []))
        out.extend(opens.get(pos, []))
        out.append(html.escape(ch, quote=False))
        pos += 2 if ord(ch) > 0xFFFF else 1
    out.extend(closes.get(pos, []))
    return "".join(out).strip()


def _panel(db):
    s = ta.bot_status(db)
    lines = ["📢 <b>Telethon Ad</b>", ""]
    for w in s["warnings"][:4]:
        lines.append("⚠️ " + html.escape(w[:220]))
    if s["warnings"]:
        lines.append("")
    lines.append("Account: " + ("✅ connected" if s["connected"] else "❌ not connected (website → Telethon Marketing)"))
    lines.append(f"Groups: <b>{s['groups']}</b>")
    if s["auto_on"]:
        lines.append(f"Auto-post: ✅ ON · every <b>{s['interval']}</b> min · next in ~{s['next_in_min']} min")
    else:
        lines.append(f"Auto-post: ⏸ OFF · every <b>{s['interval']}</b> min")
    lines.append(f"Gap between groups: <b>{s['gap']}</b> s · window: <b>{html.escape(s['hours'])}</b>")
    lines.append("Auto-join new groups: " + ("✅ ON" if s["autojoin"] else "⏸ OFF"))
    if s["paused"]:
        lines.append(f"⏸ Paused groups (errors): <b>{s['paused']}</b>")
    if s["running"]:
        lines.append(f"🚀 Sending now: {s['sent']}/{s['total']}")
    if s["last"]:
        lines.append("Last run: " + html.escape(s["last"][:300]))
    lines.append(f"Ads: <b>{len(s['ads'])}</b>" + (" (sent one after another)" if len(s["ads"]) > 1 else ""))
    lines.append(f"Daily limit: max <b>{s['daily_limit']}</b> post(s) per group in 24h")
    if not s["ads"]:
        lines += ["", "No ad yet. Tap <b>Add ad</b>."]
    rows = [[B("➕ Add ad", callback_data="tm:set"), B(f"📋 My ads ({len(s['ads'])})", callback_data="tm:list")],
            [B("🚀 Send now", callback_data="tm:send"),
             B("⏸ Auto OFF" if s["auto_on"] else "▶️ Auto ON", callback_data="tm:off" if s["auto_on"] else "tm:on")],
             [B("⏱ Interval", callback_data="tm:int"), B("↔️ Gap", callback_data="tm:gap"),
             B("📊 Daily limit", callback_data="tm:lim")],
            [B("🕒 Hours", callback_data="tm:hrs"),
             B("🔗 Auto-join " + ("OFF" if s["autojoin"] else "ON"), callback_data="tm:join")],
            [B("🔍 Check account", callback_data="tm:chk"), B("📄 Last report", callback_data="tm:rep")]]
    if s["paused"]:
        rows.append([B("♻️ Retry paused groups", callback_data="tm:reset")])
    if s["running"]:
        rows.append([B("🛑 Stop sending", callback_data="tm:stop")])
    return "\n".join(lines), M(rows), s


async def _show(message, db):
    text, markup, s = _panel(db)
    await message.reply_text(text, parse_mode=HTML, reply_markup=markup)


async def _show_ads(message, db):
    import re
    ads = ta.bot_status(db)["ads"]
    if not ads:
        await message.reply_text("No ads yet. Tap Add ad in /ad.")
        return
    for i, ad in enumerate(ads):
        markup = M([[B(f"🗑 Delete ad {i + 1}", callback_data=f"tm:del:{i}")]])
        try:
            await message.reply_text(f"#{i + 1}\n{ad}", parse_mode=HTML, disable_web_page_preview=True,
                                     reply_markup=markup)
        except BadRequest:
            await message.reply_text(f"#{i + 1}\n" + re.sub(r"<[^>]+>", "", ad), reply_markup=markup,
                                     disable_web_page_preview=True)


async def ad_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        await update.message.reply_text("This command is only for the platform owner.")
        return
    import bot
    bot.clear_await(context)
    await _show(update.message, tenancy.db)


async def ad_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    q = update.callback_query
    if not _allowed(update):
        await q.answer("Owner only.", show_alert=True)
        return
    db = tenancy.db
    act = (q.data or "")[3:]
    await q.answer()
    msg = q.message
    if act == "set":
        context.user_data["await"] = {"type": "tmad"}
        await msg.reply_text(
            "➕ Send the new ad now as ONE message. Each ad you add is sent in turn (rotation, max 10).\n\n"
            "Use your premium emojis, bold, italic, links - they are kept exactly as you send them. "
            "(The Telethon account needs Telegram Premium for the emojis to show as premium.)\n\n/ad to cancel.")
    elif act == "list":
        await _show_ads(msg, db)
    elif act.startswith("del:"):
        ok, info = ta.bot_delete_ad(db, int(act[4:] or 0))
        await msg.reply_text(("🗑 " if ok else "❌ ") + info)
    elif act == "lim":
        context.user_data["await"] = {"type": "tmlim"}
        await msg.reply_text("Send the max posts per group in 24 hours (1-50). Recommended: 1 or 2.")
    elif act in ("int", "gap"):
        context.user_data["await"] = {"type": "tmint" if act == "int" else "tmgap"}
        lim = ta.MIN_INTERVAL_MIN if act == "int" else ta.MIN_GAP_SEC
        await msg.reply_text(f"Send a number ({'minutes' if act == 'int' else 'seconds'}, minimum {lim}).")
    elif act == "send":
        await msg.reply_text(
            "🚀 Send the ad to ALL approved groups now?\nOnly confirm if the ad is allowed in every group.",
            reply_markup=M([[B("✅ Yes, send", callback_data="tm:sendyes"), B("✖️ No", callback_data="tm:no")]]))
    elif act == "sendyes":
        ok, info = ta.bot_send_now(db)
        await msg.reply_text(("✅ " if ok else "❌ ") + info)
    elif act == "on":
        await msg.reply_text(
            "▶️ Turn auto-post ON?\nThe ad will repeat by itself. Confirm it is allowed in every group.",
            reply_markup=M([[B("✅ Yes, turn ON", callback_data="tm:onyes"), B("✖️ No", callback_data="tm:no")]]))
    elif act == "onyes":
        ok, info = ta.bot_set_auto(db, True)
        await msg.reply_text(("✅ " if ok else "❌ ") + info)
    elif act == "off":
        ok, info = ta.bot_set_auto(db, False)
        await msg.reply_text("⏸ " + info)
    elif act == "stop":
        ta.bot_stop()
        await msg.reply_text("🛑 Stopping after the current group.")
    elif act == "no":
        await msg.reply_text("Cancelled. /ad")
    elif act == "hrs":
        context.user_data["await"] = {"type": "tmhrs"}
        await msg.reply_text("Send the hours (UTC) when auto-post may start, like 5-20. Send 0 for all day.")
    elif act == "join":
        on = ta.bot_toggle_join(db)
        await msg.reply_text("🔗 Auto-join is now " + ("ON: new groups are joined automatically (max 5 per run)."
                                                      if on else "OFF: the account must already be in every group."))
    elif act == "reset":
        ta.reset_groups(db)
        await msg.reply_text("♻️ Paused groups will be tried again in the next run.")
    elif act == "chk":
        await msg.reply_text("🔍 Checking the Telegram account...")
        try:
            info = await asyncio.to_thread(ta.check_account, db)
        except Exception as e:
            await msg.reply_text("❌ " + html.escape(str(e))[:300])
            return
        if info.get("ok"):
            await msg.reply_text("✅ " + html.escape(ta.acct_line(info)))
        else:
            await msg.reply_text("❌ " + html.escape(str(info.get("error", "problem")))[:400])
    elif act == "rep":
        rows_ = ta.bot_report(db)
        if not rows_:
            await msg.reply_text("No problems in the last run (or no run yet).")
        else:
            txt = "\n".join(f"• {html.escape(r[0])} - {html.escape(r[1])}: {html.escape(r[2])}" for r in rows_[:25])
            await msg.reply_text("📄 <b>Problem groups, last run</b>\n" + txt[:3800], parse_mode=HTML)


async def ad_text(update: Update, context: ContextTypes.DEFAULT_TYPE, st):
    import bot
    msg = update.message
    if not _allowed(update):
        bot.clear_await(context)
        return
    db, kind = tenancy.db, st["type"]
    if kind == "tmlim":
        try:
            n = int(float((msg.text or "").strip()))
        except ValueError:
            await msg.reply_text("Please send a number.")
            return
        ok, info = ta.bot_set_limit(db, n)
        if ok:
            bot.clear_await(context)
        await msg.reply_text(("✅ " if ok else "❌ ") + info)
        return
    if kind == "tmhrs":
        ok, info = ta.bot_set_hours(db, msg.text or "")
        if ok:
            bot.clear_await(context)
        await msg.reply_text(("✅ " if ok else "❌ ") + info)
        return
    if kind in ("tmint", "tmgap"):
        try:
            n = int(float((msg.text or "").strip()))
        except ValueError:
            await msg.reply_text("Please send a number.")
            return
        ok, info = ta.bot_set_timing(db, interval=n if kind == "tmint" else None, gap=n if kind == "tmgap" else None)
        if ok:
            bot.clear_await(context)
        await msg.reply_text(("✅ " if ok else "❌ ") + info)
        return
    ad = to_ad_html(msg.text or "", msg.entities)
    ok, info = ta.bot_set_ad(db, ad)
    if not ok:
        await msg.reply_text("❌ " + info)
        return
    bot.clear_await(context)
    n_emoji = sum(1 for e in (msg.entities or []) if e.type == "custom_emoji")
    await msg.reply_text(f"✅ Ad saved ({n_emoji} premium emoji). Ads are sent in turn (rotation).")
    await _show(msg, db)
