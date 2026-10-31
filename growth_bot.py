"""Growth & retention features (Telegram bot side).

Plugged into bot.py: card/quantity/order/wallet/profile screens get extra lines and buttons from here,
`cb_growth` handles every "g:..." button, `growth_text` / `growth_photo` handle typed answers,
`growth_job` runs restock alerts, low-balance reminders, flash-deal announcements and review prompts.
All texts here are English (the rest of the bot keeps its own en/zh/es texts).
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _B():
    import bot          # imported lazily: bot.py imports this module
    return bot


# ---------------------------------------------------------------- small helpers
def _left(end):
    secs = max(0, int((end - datetime.now(timezone.utc)).total_seconds()))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{max(m, 1)}m"


def _mask(name, uid):
    name = (name or "").lstrip("@")
    if len(name) >= 3:
        return name[:2] + "***"
    return f"user{str(uid)[-3:]}"


_flash_cache = {}


def live_flash_ids():
    """Product ids with a live flash deal (cached for a few seconds: used while drawing the product grid)."""
    B = _B()
    key = B.db.path
    hit = _flash_cache.get(key)
    now = time.time()
    if hit and now - hit[0] < 20:
        return hit[1]
    try:
        ids = {f["product_id"] for f in B.db.flash_list(only_live=True)}
    except Exception:
        ids = set()
    _flash_cache[key] = (now, ids)
    return ids


def useful_tiers(p):
    """Bulk levels that really are cheaper than what a customer pays now (a flash deal can undercut them)."""
    db = _B().db
    base = float(p["price"])
    fl = db.flash_for(p["id"])
    if fl:
        base = min(base, float(fl["price"]))
    return [t for t in db.tiers_for(p["id"]) if t["price"] < base]


# ---------------------------------------------------------------- extra lines / buttons used by bot.py views
def card_extras(uid, p):
    """Text added under a product card (flash deal, bulk prices, VIP discount, rating)."""
    B = _B()
    db = B.db
    lines = []
    fl = db.flash_for(p["id"])
    if fl:
        lines.append(f"⚡ <b>FLASH SALE</b> · ends in {_left(fl['end'])}")
    if not B.is_manual(p):
        tiers = useful_tiers(p)
        if tiers:
            lines.append("📦 <b>Bulk prices</b>: " + " · ".join(f"{t['min_qty']}+ → ${B.money(t['price'])}" for t in tiers))
    acct = db.get_account(uid)
    name, pct = db.vip_level(acct["spent"] if acct else 0)
    if pct > 0:
        lines.append(f"🏅 Your {B.esc(name)} discount <b>−{pct:g}%</b> is included in the price")
    if db.get_setting("reviews_enabled", 1):
        avg, n = db.product_rating(p["id"])
        if n:
            lines.append(f"⭐ <b>{avg}</b>/5 · {n} review{'s' if n != 1 else ''}")
    return ("\n\n" + "\n".join(lines)) if lines else ""


def card_rows(uid, p):
    """Extra buttons on a product card: 'notify me' for sold-out stocked products."""
    B = _B()
    if B.is_manual(p) or p["stock"] > 0:
        return []
    if B.db.restock_is_subscribed(uid, p["id"]):
        return [[B.Btn("🔕 Cancel restock alert", callback_data=f"g:nu:{p['id']}")]]
    return [[B.Btn("🔔 Notify me when back in stock", callback_data=f"g:nt:{p['id']}", style="success")]]


def qty_extras(uid, p):
    B = _B()
    tiers = useful_tiers(p)
    if not tiers:
        return ""
    return "\n📦 <b>Bulk prices</b>: " + " · ".join(f"{t['min_qty']}+ → ${B.money(t['price'])}" for t in tiers)


def qty_rows(p):
    """One-tap buttons for bulk quantities that are not among the normal presets."""
    B = _B()
    btns = [B.Btn(f"{t['min_qty']} · ${B.money(t['price'])}", callback_data=f"qty:{p['id']}:{t['min_qty']}", style="success")
            for t in useful_tiers(p) if t["min_qty"] <= p["stock"] and t["min_qty"] not in B.QTY_PRESETS][:4]
    return [btns] if btns else []


def calc_extras(uid, p, qty, info, total):
    """What was applied to this order + upsell to the next bulk level + cashback."""
    B = _B()
    db = B.db
    lines = []
    if info.get("flash"):
        lines.append("⚡ Flash sale price applied")
    if info.get("bulk"):
        lines.append("📦 Bulk price applied")
    if info.get("vip_pct", 0) > 0:
        lines.append(f"🏅 {B.esc(info['vip_name'])} discount −{info['vip_pct']:g}%")
    if info.get("promo_code"):
        lines.append(f"🎟 Promo {B.esc(info['promo_code'])} −{info['promo_pct']:g}%")
    if not info.get("custom"):
        nxt = [t for t in db.tiers_for(p["id"])
               if t["min_qty"] > qty and p["stock"] >= t["min_qty"] and t["price"] < info["unit"]]
        if nxt:
            t = nxt[0]
            lines.append(f"💡 Add {t['min_qty'] - qty} more to pay only ${B.money(t['price'])} each")
    cb = db.get_setting("cashback_percent", 0.0)
    if cb > 0:
        lines.append(f"💸 You get ${B.money(total * cb / 100)} cashback ({cb:g}%) after buying")
    return ("\n" + "\n".join(lines) + "\n") if lines else ""


def wallet_extra_text():
    B = _B()
    levels = B.db.deposit_bonus_levels()
    if not levels:
        return ""
    parts = " · ".join(f"${thr:g}+ → +{pct:g}%" for thr, pct in sorted(levels))
    return f"\n\n🎁 <b>Deposit bonus</b>: {parts}"


def wallet_rows():
    B = _B()
    return [[B.Btn("🎟 Promo code", callback_data="g:promo")]]


def profile_extra(uid):
    """(text, rows) added to the Profile screen."""
    B = _B()
    db = B.db
    acct = db.get_account(uid)
    spent = float(acct["spent"] or 0) if acct else 0.0
    lines, rows = [], []
    if db.vip_levels():
        name, pct = db.vip_level(spent)
        nxt = db.vip_next(spent)
        cur = f"{B.esc(name)} (−{pct:g}%)" if name else "no level yet"
        line = f"\n\n🏅 <b>VIP level</b>: {cur}"
        if nxt:
            line += f"\n➡️ ${B.money(nxt[1])} more to reach <b>{B.esc(nxt[0])}</b> (−{nxt[2]:g}%)"
        lines.append(line)
        rows.append([B.Btn("🏅 VIP levels", callback_data="g:vip")])
    cb = float(acct["cashback_earned"] or 0) if acct and "cashback_earned" in acct.keys() else 0.0
    if cb > 0:
        lines.append(f"\n💸 Cashback earned: <b>${B.money(cb)}</b>")
    rows.append([B.Btn("🎟 Promo code", callback_data="g:promo"), B.Btn("❓ FAQ", callback_data="g:faq")])
    return "".join(lines), rows


def orders_rows(uid):
    """Rate + buy-again buttons under 'My Orders'."""
    B = _B()
    db = B.db
    rows = []
    if db.get_setting("reviews_enabled", 1):
        for o in db.unreviewed_orders(uid, 2):
            rows.append([B.Btn(f"⭐ Rate: {(o['name'] or 'Product')[:28]}", callback_data=f"g:rv:{o['id']}:0")])
    seen = set()
    for o in B.db.user_orders(uid, 8):
        if o["status"] == "paid" and o["product_id"] not in seen and len(seen) < 3:
            seen.add(o["product_id"])
            rows.append([B.Btn(f"🔁 Buy again: {(o['name'] or 'Product')[:26]}", callback_data=f"prod:{o['product_id']}")])
    rows.append([B.Btn("🛠 Claim warranty", callback_data="g:warr")])
    return rows


def warranty_rows():
    B = _B()
    return [[B.Btn("🛠 Claim warranty / replacement", callback_data="g:warr", style="success")],
            [B.Btn("❓ FAQ", callback_data="g:faq")]]


def support_rows():
    B = _B()
    return [[B.Btn("❓ FAQ", callback_data="g:faq")]]


def referral_rows():
    B = _B()
    return [[B.Btn("🏆 Leaderboard", callback_data="g:lb")]]


# ---------------------------------------------------------------- screens
def _back_row(language):
    B = _B()
    return B.main_menu_row(language)


def faq_view(language):
    B = _B()
    return B.db.faq_text(), B.Markup([[B.Btn("🛠 Claim warranty", callback_data="g:warr")], _back_row(language)])


def vip_view(uid, language):
    B = _B()
    db = B.db
    levels = sorted(db.vip_levels())
    acct = db.get_account(uid)
    spent = float(acct["spent"] or 0) if acct else 0.0
    if not levels:
        text = "🏅 VIP levels are not enabled in this shop yet."
    else:
        name, pct = db.vip_level(spent)
        rows = "\n".join(f"{'✅' if spent + 1e-9 >= thr else '🔒'} <b>{B.esc(n)}</b> · spend ${B.money(thr)} · "
                         f"{('−%g%% on every order' % p) if p else 'status only'}" for thr, n, p in levels)
        nxt = db.vip_next(spent)
        text = (f"🏅 <b>VIP levels</b>\n\nTotal spent: <b>${B.money(spent)}</b>\n"
                f"Your level: <b>{B.esc(name) if name else 'none yet'}</b>\n\n{rows}")
        if nxt:
            text += f"\n\n➡️ ${B.money(nxt[1])} more to reach <b>{B.esc(nxt[0])}</b>."
    return text, B.Markup([_back_row(language)])


def leaderboard_view(uid, language):
    B = _B()
    db = B.db
    top = db.referral_leaderboard(days=30, limit=10)
    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
    if top:
        rows = "\n".join(f"{medals[i]} <b>{B.esc(_mask(r['username'], r['uid']))}</b> · {r['n']} invite{'s' if r['n'] != 1 else ''}"
                         for i, r in enumerate(top))
    else:
        rows = "Nobody yet. Be the first!"
    mine = db.referral_count(uid)
    text = (f"🏆 <b>Referral leaderboard</b> (last 30 days)\n\n{rows}\n\n"
            f"You invited <b>{mine}</b> friend{'s' if mine != 1 else ''} in total. "
            f"Share your link from the 🎁 Referral screen to climb up.")
    return text, B.Markup([[B.Btn("🎁 My referral link", callback_data="menu:ref")], _back_row(language)])


def stars_markup(oid):
    B = _B()
    return B.Markup([[B.Btn("⭐" * n, callback_data=f"g:rv:{oid}:{n}") for n in (1, 2, 3)],
                     [B.Btn("⭐" * n, callback_data=f"g:rv:{oid}:{n}") for n in (4, 5)]])


# ---------------------------------------------------------------- callbacks: every "g:..." button
async def cb_growth(update, context, arg):
    B = _B()
    q = update.callback_query
    uid = q.from_user.id
    language = B.lang(uid)
    db = B.db
    action, _, rest = arg.partition(":")

    if action == "promo":
        await q.answer()
        context.user_data["await"] = {"type": "promo"}
        await B.edit(q, "🎟 <b>Promo code</b>\n\nSend your code now.",
                     B.Markup([[B.Btn("✖️ Cancel", callback_data="cx", style="danger")]]))
    elif action == "faq":
        await q.answer()
        await B.edit(q, *faq_view(language))
    elif action == "vip":
        await q.answer()
        await B.edit(q, *vip_view(uid, language))
    elif action == "lb":
        await q.answer()
        await B.edit(q, *leaderboard_view(uid, language))
    elif action in ("nt", "nu"):
        try:
            pid = int(rest)
        except ValueError:
            await q.answer(); return
        p = db.get_product(pid)
        if not p:
            await q.answer(); return
        if action == "nt":
            db.restock_subscribe(uid, pid)
            await q.answer("🔔 Done! We will message you when it is back.", show_alert=True)
        else:
            db.restock_unsubscribe(uid, pid)
            await q.answer("Alert cancelled.")
        text, markup = B.card_view(uid, p, language)
        await B.edit(q, text, markup)
    elif action == "warr":
        await q.answer()
        orders = db.claimable_orders(uid)
        if not orders:
            await B.edit(q, "🛠 You have no paid orders to claim a warranty on yet.", B.Markup([_back_row(language)]))
            return
        rows = [[B.Btn(f"{o['id']} · {o['quantity']}× {(o['name'] or 'Product')[:24]}", callback_data=f"g:wc:{o['id']}")]
                for o in orders]
        rows.append([B.Btn(B.tt(language, "back"), callback_data="menu:warranty", style="primary")])
        await B.edit(q, "🛠 <b>Warranty / replacement</b>\n\nWhich order has a problem?", B.Markup(rows))
    elif action == "wc":
        o = db.get_order(rest)
        if not o or o["user_id"] != uid or o["status"] != "paid":
            await q.answer("Order not found.", show_alert=True)
            return
        await q.answer()
        context.user_data["await"] = {"type": "wclaim", "oid": rest}
        await B.edit(q, f"🛠 Order <code>{B.esc(rest)}</code>\n\nDescribe the problem in one message "
                        f"(you can also send a screenshot with a caption).",
                     B.Markup([[B.Btn("✖️ Cancel", callback_data="cx", style="danger")]]))
    elif action == "rv":
        oid, _, n = rest.rpartition(":")
        try:
            n = int(n)
        except ValueError:
            await q.answer(); return
        if n == 0:
            await q.answer()
            await B.edit(q, "⭐ <b>How was this order?</b>\nTap a rating:", stars_markup(oid))
            return
        res = db.review_add(oid, uid, n)
        if res == "ok":
            await q.answer("Thank you!")
            context.user_data["await"] = {"type": "rvcomment", "oid": oid}
            await B.edit(q, f"{'⭐' * n} <b>Thanks for rating!</b>\n\nWant to add a short comment? Just type it now.",
                         B.Markup([[B.Btn("Skip", callback_data="g:skip")]]))
        elif res == "exists":
            await q.answer("You already rated this order.", show_alert=True)
        else:
            await q.answer("Order not found.", show_alert=True)
    elif action == "skip":
        await q.answer()
        B.clear_await(context)
        await B.edit(q, "✅ Thank you for your feedback!", B.Markup([_back_row(language)]))
    else:
        await q.answer()


# ---------------------------------------------------------------- typed answers / photos
async def growth_text(update, context, st):
    B = _B()
    msg = update.message
    uid = update.effective_user.id
    language = B.lang(uid)
    text = (msg.text or "").strip()
    kind = st["type"]
    if kind == "promo":
        B.clear_await(context)
        ok, info, _k = await asyncio.to_thread(B.db.promo_redeem, uid, text)
        await msg.reply_text(info if ok else f"❌ {info}", parse_mode=B.HTML,
                             reply_markup=B.Markup([[B.Btn("💰 Wallet", callback_data="menu:funds"),
                                                      B.Btn("🛍 Products", callback_data="shop", style="success")]]))
    elif kind == "rvcomment":
        B.clear_await(context)
        B.db.review_comment(st["oid"], uid, text)
        await msg.reply_text("✅ Thank you for your feedback!", reply_markup=B.Markup([_back_row(language)]))
    elif kind == "wclaim":
        await _open_claim(update, context, st["oid"], text, None)


async def growth_photo(update, context, st):
    msg = update.message
    if st["type"] == "wclaim":
        await _open_claim(update, context, st["oid"], (msg.caption or "").strip(), msg.photo[-1].file_id)


async def _open_claim(update, context, oid, text, photo_id):
    B = _B()
    user = update.effective_user
    if not text and not photo_id:
        return
    order = B.db.get_order(oid)
    if not order or order["user_id"] != user.id:
        B.clear_await(context)
        return
    p = B.db.get_product_any(order["product_id"])
    pname = p["name"] if p else "Product"
    B.clear_await(context)
    body = f"Order {oid} · {order['quantity']}× {pname}\n{text or '(photo)'}"
    tid = B.db.create_ticket(user.id, "Warranty", body, photo_id)
    await update.message.reply_text(f"✅ Warranty request sent (ticket <b>#{tid}</b>). We will reply here as soon as possible.",
                                    parse_mode=B.HTML)
    who = f"@{B.esc(user.username)}" if user.username else B.esc(user.full_name)
    await B.notify_admins(
        context,
        f"🛠 <b>Warranty claim · ticket #{tid}</b>\n👤 {who} ({user.id})\n🧾 <code>{B.esc(oid)}</code> · {B.esc(pname)}\n\n"
        f"{B.esc(text or '(photo)')}\n\n<i>Reply to this message to answer the customer.</i>",
        ticket_id=tid, photo_id=photo_id)


# ---------------------------------------------------------------- commands
async def promo_cmd(update, context):
    B = _B()
    uid = update.effective_user.id
    if B.shop_closed_for(uid):
        return
    code = " ".join(context.args or []).strip()
    if not code:
        context.user_data["await"] = {"type": "promo"}
        await update.message.reply_text("🎟 Send your promo code now.")
        return
    B.db.touch_username(uid, update.effective_user.username)
    ok, info, _k = await asyncio.to_thread(B.db.promo_redeem, uid, code)
    await update.message.reply_text(info if ok else f"❌ {info}", parse_mode=B.HTML)


async def faq_cmd(update, context):
    B = _B()
    if B.shop_closed_for(update.effective_user.id):
        return
    text, markup = faq_view(B.lang(update.effective_user.id))
    await update.message.reply_text(text, reply_markup=markup, parse_mode=B.HTML)


async def broadcast_cmd(update, context):
    """/broadcast [all|buyers|nobuy|balance|inactive] your message (HTML formatting is kept)."""
    from growth import AUDIENCES
    B = _B()
    uid = update.effective_user.id
    if not B.is_admin(uid):
        return
    raw = (update.message.text_html or "")
    parts = raw.split(None, 1)
    body = parts[1] if len(parts) > 1 else ""
    audience = "all"
    first = body.split(None, 1)
    if first and first[0].lower() in AUDIENCES:
        audience = first[0].lower()
        body = first[1] if len(first) > 1 else ""
    body = body.strip()
    if not body:
        await update.message.reply_text(
            "📣 <b>Broadcast</b>\n\nUsage: <code>/broadcast your message</code>\n"
            "Audience (optional first word): " + ", ".join(f"<code>{k}</code>" for k in AUDIENCES) +
            "\nExample: <code>/broadcast buyers New stock is live!</code>", parse_mode=B.HTML)
        return
    n = await asyncio.to_thread(B.db.broadcast_queue, body, audience)
    await update.message.reply_text(f"📣 Queued for <b>{n}</b> user(s) ({audience}). Messages go out in the next minutes.",
                                    parse_mode=B.HTML)


# ---------------------------------------------------------------- background job
async def growth_job(context):
    """Runs every ~2 minutes inside each shop."""
    B = _B()
    db = B.db
    for name, fn in (("restock", db.restock_run), ("low balance", db.lowbal_run)):
        try:
            await asyncio.to_thread(fn)
        except Exception:
            log.exception("growth job (%s) failed", name)
    try:
        await _announce_flash(context)
    except Exception:
        log.exception("growth job (flash) failed")
    try:
        await _review_prompts(context)
    except Exception:
        log.exception("growth job (reviews) failed")


async def _announce_flash(context):
    B = _B()
    db = B.db
    for f in db.flash_to_announce():
        end = __import__("growth").parse_ts(f["ends_at"])
        left = _left(end) if end else "soon"
        text = (f"⚡ <b>FLASH SALE</b>\n\n📦 <b>{B.esc(f['product'])}</b>\n"
                f"💰 <s>${B.money(f['base'])}</s> → <b>${B.money(f['price'])}</b>\n⏳ Ends in {left}\n\nGrab it before it is gone!")
        db.flash_mark_announced(f["id"])
        if f["notify_users"]:
            await asyncio.to_thread(db.broadcast_queue, text, "all")
        if B.sales_channel():
            try:
                uname = (getattr(B.tenancy.cur(), "bot_username", "") or "").lstrip("@")
                if not uname:
                    uname = (await context.bot.get_me()).username or ""
                markup = B.Markup([[B.Btn("🛒 Buy Now", url=f"https://t.me/{uname}", style="success")]]) if uname else None
                await context.bot.send_message(B.sales_channel(), text, parse_mode=B.HTML, reply_markup=markup,
                                               disable_web_page_preview=True)
            except Exception:
                log.exception("could not post the flash sale to the sales group")


async def _review_prompts(context):
    B = _B()
    db = B.db
    if not db.get_setting("reviews_enabled", 1):
        return
    for r in await asyncio.to_thread(db.review_prompt_candidates):
        try:
            await context.bot.send_message(
                r["user_id"],
                f"⭐ <b>How was your order?</b>\n📦 {B.esc(r['name'] or 'Product')}\n\nTap a rating, it takes 2 seconds:",
                parse_mode=B.HTML, reply_markup=stars_markup(r["id"]))
        except Exception:
            log.info("review prompt to %s not delivered", r["user_id"])
