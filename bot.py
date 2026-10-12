import asyncio
import html
import logging
import os
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton as _Btn, InlineKeyboardMarkup as Markup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

from db import Database
from binance_pay import (BinancePayClient, PAY_ID, DEPOSIT_ADDRESS, DEPOSIT_NETWORK,
                         BINANCE_ENABLED, BSC_ENABLED)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PAYMENT_TIMEOUT_MINUTES = int(os.getenv("PAYMENT_TIMEOUT_MINUTES", "5"))
DB_PATH = os.getenv("DB_PATH", "shop.db")
SHOP_NAME = os.getenv("SHOP_NAME", "Digital Shop")
HTML = ParseMode.HTML
QTY_PRESETS = (1, 2, 3, 5, 10, 20)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("digital-shop")

db = Database(DB_PATH)
binance = BinancePayClient()

TEXT = {
    "en": {
        "welcome": "🛍 <b>{shop}</b>\n\nChoose your language / 请选择语言 👇",
        "home": "🛍 <b>{shop}</b>\n\n⚡ Instant automatic delivery\n💳 Pay with USDT\n\nChoose a product 👇",
        "no_products": "🛍 <b>{shop}</b>\n\nNo products available right now.",
        "left": "left",
        "sold_out": "Sold out",
        "btn_orders": "🧾 My orders",
        "btn_lang": "🌐 Language",
        "back": "‹ Back",
        "shop": "‹ Shop",
        "product": "📦 <b>{name}</b>\n\n💰 Price: <b>{price}</b> USDT\n📊 Stock: <b>{stock}</b> pcs\n⚡ Delivery: automatic\n\nSelect quantity 👇",
        "other_qty": "✏️ Other quantity",
        "ask_qty": "✏️ Send the quantity you want (a number):",
        "calc": "🧮 <b>Order calculation</b>\n\n📦 Product: <b>{name}</b>\n🔢 Quantity: <b>{qty}</b>\n💵 Price each: {unit} USDT\n💰 Total: <b>{total}</b> USDT\n\nChoose payment method 👇",
        "pay_bsc": "🟨 USDT · BEP-20",
        "pay_bpay": "🟡 Binance Pay (UID)",
        "check": "🔄 Check Payment",
        "cancel_order": "❌ Cancel order",
        "created": "✅ <b>Order created</b>\n\n🧾 Order: <code>{order}</code>\n📦 {name} × {qty}\n💰 Amount: <code>{amount}</code> USDT\n\n⚠️ Send <b>EXACTLY</b> <code>{amount}</code> USDT. A different amount cannot be matched.\n\n{details}\n\n⏳ Expires in <b>{minutes} min</b>. After paying, tap <b>Check Payment</b> (it is also verified automatically).",
        "paid": "🎉 <b>Payment verified!</b>\n\n🧾 Order: <code>{order}</code>\n📦 {name} × {qty}\n\n🔑 <b>Your item(s):</b>\n{items}",
        "expired": "⌛ Order expired. The payment window ended.\n\nUse /start to order again.",
        "cancelled": "❌ Order cancelled.",
        "not_yet": "Payment not confirmed yet. Try again in a minute.",
        "check_down": "Payment check temporarily unavailable.",
        "not_fulfilled": "Order could not be fulfilled. Contact support.",
        "out": "❌ Not enough stock.",
        "invalid": "Please send a valid number.",
        "bpay_down": "Binance Pay is temporarily unavailable. Please choose USDT BEP-20.",
        "pay_down": "Payment service is temporarily unavailable.",
        "not_configured": "Payment is not configured yet.",
        "orders": "🧾 <b>My orders</b>\n\n{rows}",
        "no_orders": "You have no orders yet.",
        "admin_only": "Admin access required.",
        "owner_only": "Owner access required.",
        "link_added": "✅ Item added to stock.",
        "help": "Use /start to open the shop.",
        "bpay_hint": "Binance app → Pay → Send → Pay ID, then send the exact amount in USDT.",
    },
    "zh": {
        "welcome": "🛍 <b>{shop}</b>\n\n请选择语言 / Choose your language 👇",
        "home": "🛍 <b>{shop}</b>\n\n⚡ 自动即时发货\n💳 USDT 付款\n\n请选择商品 👇",
        "no_products": "🛍 <b>{shop}</b>\n\n暂无商品。",
        "left": "件",
        "sold_out": "已售罄",
        "btn_orders": "🧾 我的订单",
        "btn_lang": "🌐 语言",
        "back": "‹ 返回",
        "shop": "‹ 商店",
        "product": "📦 <b>{name}</b>\n\n💰 价格：<b>{price}</b> USDT\n📊 库存：<b>{stock}</b> 件\n⚡ 发货：自动\n\n请选择数量 👇",
        "other_qty": "✏️ 其他数量",
        "ask_qty": "✏️ 请发送您要购买的数量（数字）：",
        "calc": "🧮 <b>订单明细</b>\n\n📦 商品：<b>{name}</b>\n🔢 数量：<b>{qty}</b>\n💵 单价：{unit} USDT\n💰 总计：<b>{total}</b> USDT\n\n请选择支付方式 👇",
        "pay_bsc": "🟨 USDT · BEP-20",
        "pay_bpay": "🟡 Binance Pay (UID)",
        "check": "🔄 检查付款",
        "cancel_order": "❌ 取消订单",
        "created": "✅ <b>订单已创建</b>\n\n🧾 订单：<code>{order}</code>\n📦 {name} × {qty}\n💰 金额：<code>{amount}</code> USDT\n\n⚠️ 请<b>准确</b>转账 <code>{amount}</code> USDT，金额不一致将无法匹配。\n\n{details}\n\n⏳ <b>{minutes} 分钟</b>后过期。付款后点击<b>检查付款</b>（系统也会自动验证）。",
        "paid": "🎉 <b>支付已验证！</b>\n\n🧾 订单：<code>{order}</code>\n📦 {name} × {qty}\n\n🔑 <b>您的商品：</b>\n{items}",
        "expired": "⌛ 订单已过期，支付时间已结束。\n\n请使用 /start 重新下单。",
        "cancelled": "❌ 订单已取消。",
        "not_yet": "尚未确认付款，请稍后再试。",
        "check_down": "付款检查暂时不可用。",
        "not_fulfilled": "订单无法发货，请联系客服。",
        "out": "❌ 库存不足。",
        "invalid": "请发送有效数字。",
        "bpay_down": "Binance Pay 暂时不可用，请选择 USDT BEP-20。",
        "pay_down": "支付服务暂时不可用。",
        "not_configured": "支付尚未配置。",
        "orders": "🧾 <b>我的订单</b>\n\n{rows}",
        "no_orders": "您还没有订单。",
        "admin_only": "需要管理员权限。",
        "owner_only": "需要所有者权限。",
        "link_added": "✅ 商品已加入库存。",
        "help": "使用 /start 打开商店。",
        "bpay_hint": "Binance 应用 → Pay → 发送 → Pay ID，然后转账准确的 USDT 金额。",
    },
}

def Btn(text, callback_data=None, style=None):
    """Inline button with optional colour: 'primary' (blue), 'success' (green), 'danger' (red).
    Colours need a Telegram app released after 9 Feb 2026; older apps just show a normal button."""
    if not style:
        return _Btn(text, callback_data=callback_data)
    try:
        return _Btn(text, callback_data=callback_data, style=style)
    except TypeError:  # older python-telegram-bot without the style field
        return _Btn(text, callback_data=callback_data, api_kwargs={"style": style})

STATUS_ICON = {"paid": "✅", "pending": "⏳", "failed": "❌"}

def lang(user_id: int) -> str:
    return db.get_user_language(user_id) or "en"

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def is_admin(user_id: int) -> bool:
    return is_owner(user_id) or db.is_admin(user_id)

def esc(x) -> str:
    return html.escape(str(x))

def money(x) -> str:
    return f"{x:.2f}"

async def edit(q, text, markup=None):
    """Edit the message in place; ignore Telegram's 'message is not modified'."""
    try:
        await q.edit_message_text(text, reply_markup=markup, parse_mode=HTML)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise

# ---------------------------------------------------------------- views
def kb_lang():
    return Markup([[
        Btn("🇬🇧 English", callback_data="lang:en", style="primary"),
        Btn("🇨🇳 中文", callback_data="lang:zh", style="primary"),
    ]])

def products_view(uid: int, language: str):
    t = TEXT[language]
    products = db.list_products()
    if not products:
        rows = [[Btn(t["btn_orders"], callback_data="menu:orders", style="primary"), Btn(t["btn_lang"], callback_data="menu:lang", style="primary")]]
        return t["no_products"].format(shop=esc(SHOP_NAME)), Markup(rows)
    rows = []
    for p in products:
        price = db.get_effective_price(uid, p["id"], p["price"])
        if p["stock"] > 0:
            label, style = f"📦 {p['name']} · {money(price)} USDT · {p['stock']} {t['left']}", "success"
        else:
            label, style = f"❌ {p['name']} · {t['sold_out']}", None
        rows.append([Btn(label, callback_data=f"product:{p['id']}", style=style)])
    rows.append([Btn(t["btn_orders"], callback_data="menu:orders", style="primary"), Btn(t["btn_lang"], callback_data="menu:lang", style="primary")])
    return t["home"].format(shop=esc(SHOP_NAME)), Markup(rows)

def product_view(uid: int, p, language: str):
    t = TEXT[language]
    price = db.get_effective_price(uid, p["id"], p["price"])
    text = t["product"].format(name=esc(p["name"]), price=money(price), stock=p["stock"])
    sizes = [n for n in QTY_PRESETS if n <= p["stock"]]
    rows = [[Btn(str(n), callback_data=f"qty:{p['id']}:{n}", style="primary") for n in sizes[i:i + 3]] for i in range(0, len(sizes), 3)]
    rows.append([Btn(t["other_qty"], callback_data=f"qtyo:{p['id']}", style="success")])
    rows.append([Btn(t["back"], callback_data="back:products", style="primary")])
    return text, Markup(rows)

def calc_view(uid: int, pid: int, qty: int, language: str):
    """Order calculation screen, or None if the product/stock is not available."""
    t = TEXT[language]
    p = db.get_product(pid)
    if not p or qty <= 0 or p["stock"] < qty:
        return None
    unit = db.get_effective_price(uid, pid, p["price"])
    total = round(unit * qty, 2)
    text = t["calc"].format(name=esc(p["name"]), qty=qty, unit=money(unit), total=money(total))
    rows = []
    if BSC_ENABLED:
        rows.append([Btn(t["pay_bsc"], callback_data=f"pay:bsc:{pid}:{qty}", style="success")])
    if BINANCE_ENABLED:
        rows.append([Btn(t["pay_bpay"], callback_data=f"pay:bpay:{pid}:{qty}", style="success")])
    rows.append([Btn(t["back"], callback_data=f"product:{pid}", style="primary")])
    return text, Markup(rows)

def payment_details(method: str, language: str) -> str:
    if method == "bpay":
        return f"🟡 Binance Pay ID / UID:\n<code>{esc(PAY_ID)}</code>\n\n{TEXT[language]['bpay_hint']}"
    net = f" ({esc(DEPOSIT_NETWORK)})" if DEPOSIT_NETWORK else ""
    return f"💳 USDT{net} address:\n<code>{esc(DEPOSIT_ADDRESS)}</code>"

def delivery_text(language: str, order, items) -> str:
    p = db.get_product(order["product_id"])
    name = esc(p["name"]) if p else "Product"
    return TEXT[language]["paid"].format(
        order=esc(order["id"]), name=name, qty=order["quantity"],
        items="\n".join(f"<code>{esc(i)}</code>" for i in items),
    )

# ---------------------------------------------------------------- handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.touch_username(u.id, u.username)
    language = db.get_user_language(u.id)
    if language:
        text, markup = products_view(u.id, language)
        await update.message.reply_text(text, reply_markup=markup, parse_mode=HTML)
    else:
        await update.message.reply_text(TEXT["en"]["welcome"].format(shop=esc(SHOP_NAME)),
                                        reply_markup=kb_lang(), parse_mode=HTML)

async def language_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    language = q.data.split(":")[1]
    db.set_user_language(q.from_user.id, language, q.from_user.username)
    text, markup = products_view(q.from_user.id, language)
    await edit(q, text, markup)

async def menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":")[1]
    uid = q.from_user.id
    language = lang(uid)
    t = TEXT[language]
    if action == "lang":
        await edit(q, t["welcome"].format(shop=esc(SHOP_NAME)), kb_lang())
    elif action == "orders":
        orders = db.user_orders(uid)
        if orders:
            rows = "\n".join(
                f"{STATUS_ICON.get(o['status'], '•')} <code>{esc(o['id'])}</code> · {o['quantity']} × {esc(o['name'] or 'Product')} · {money(o['amount'])} USDT"
                for o in orders)
        else:
            rows = t["no_orders"]
        await edit(q, t["orders"].format(rows=rows), Markup([[Btn(t["shop"], callback_data="back:products", style="primary")]]))

async def back_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.pop("buy_product", None)
    text, markup = products_view(q.from_user.id, lang(q.from_user.id))
    await edit(q, text, markup)

async def product_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    pid = int(q.data.split(":")[1])
    language = lang(q.from_user.id)
    t = TEXT[language]
    p = db.get_product(pid)
    if not p:
        await q.answer()
        return
    if p["stock"] <= 0:
        await q.answer(t["out"], show_alert=True)
        return
    await q.answer()
    context.user_data.pop("buy_product", None)
    text, markup = product_view(q.from_user.id, p, language)
    await edit(q, text, markup)

async def qty_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, pid, qty = q.data.split(":")
    language = lang(q.from_user.id)
    view = calc_view(q.from_user.id, int(pid), int(qty), language)
    if not view:
        await q.answer(TEXT[language]["out"], show_alert=True)
        return
    await q.answer()
    await edit(q, *view)

async def qty_other_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["buy_product"] = int(q.data.split(":")[1])
    await q.message.reply_text(TEXT[lang(q.from_user.id)]["ask_qty"])

async def quantity_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = context.user_data.get("buy_product")
    if not pid:
        return
    uid = update.effective_user.id
    language = lang(uid)
    t = TEXT[language]
    try:
        qty = int(update.message.text.strip())
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(t["invalid"])
        return
    view = calc_view(uid, pid, qty, language)
    if not view:
        await update.message.reply_text(t["out"])
        return
    context.user_data.pop("buy_product", None)
    await update.message.reply_text(view[0], reply_markup=view[1], parse_mode=HTML)

async def pay_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, method, pid, qty = q.data.split(":")
    pid, qty = int(pid), int(qty)
    uid = q.from_user.id
    language = lang(uid)
    t = TEXT[language]

    if (method == "bsc" and not BSC_ENABLED) or (method == "bpay" and not BINANCE_ENABLED) or method not in ("bsc", "bpay"):
        await q.answer(t["not_configured"], show_alert=True)
        return
    p = db.get_product(pid)
    if not p or qty <= 0 or p["stock"] < qty:
        await q.answer(t["out"], show_alert=True)
        return

    unit = db.get_effective_price(uid, pid, p["price"])
    amount = round(unit * qty, 2)
    order_id = db.create_order(
        uid, pid, qty, unit, amount,
        datetime.now(timezone.utc) + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
    )
    try:
        pay = await asyncio.to_thread(
            binance.create_order, order_id, amount, f"{SHOP_NAME} order {order_id}", method)
    except Exception as e:
        log.exception("Payment order creation failed (%s)", method)
        db.mark_order_failed(order_id, str(e))
        # stay on the calculation screen so the user can pick the other method
        msg = t["bpay_down"] if (method == "bpay" and BSC_ENABLED) else t["pay_down"]
        await q.answer(msg, show_alert=True)
        return

    await q.answer()
    db.attach_binance_order(order_id, pay["prepay_id"], pay.get("checkout_url", ""))
    buttons = Markup([
        [Btn(t["check"], callback_data=f"check:{order_id}", style="success")],
        [Btn(t["cancel_order"], callback_data=f"cancel:{order_id}", style="danger")],
    ])
    await edit(q, t["created"].format(
        order=esc(order_id), name=esc(p["name"]), qty=qty,
        amount=pay.get("amount_text", money(amount)),
        details=payment_details(method, language), minutes=PAYMENT_TIMEOUT_MINUTES,
    ), buttons)

async def cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    order_id = q.data.split(":", 1)[1]
    order = db.get_order(order_id)
    language = lang(q.from_user.id)
    t = TEXT[language]
    if not order or order["user_id"] != q.from_user.id:
        return
    if order["status"] == "pending":
        db.mark_order_failed(order_id, "cancelled")
        binance.release(order_id)
    await edit(q, t["cancelled"], Markup([[Btn(t["shop"], callback_data="back:products", style="primary")]]))

async def check_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    order_id = q.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order or order["user_id"] != q.from_user.id:
        await q.answer()
        return

    language = lang(q.from_user.id)
    t = TEXT[language]

    if order["status"] == "paid":
        await q.answer()
        return
    if order["status"] == "failed":
        await q.answer()
        await q.message.reply_text(t["expired"])
        return

    if datetime.now(timezone.utc) >= datetime.fromisoformat(order["expires_at"]):
        db.mark_order_failed(order_id, "timeout")
        await q.answer()
        await edit(q, t["expired"])
        return

    try:
        status = await asyncio.to_thread(binance.query_order, order["binance_order_id"])
    except Exception:
        await q.answer(t["check_down"], show_alert=True)
        return

    if status == "PAID":
        items = db.fulfill_order(order_id)
        if items is None:
            await q.answer(t["not_fulfilled"], show_alert=True)
            return
        await q.answer()
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except BadRequest:
            pass
        await q.message.reply_text(delivery_text(language, order, items), parse_mode=HTML)
    else:
        await q.answer(t["not_yet"], show_alert=True)

async def poll_payments(context: ContextTypes.DEFAULT_TYPE):
    """Auto-verify pending orders so the user does not have to press Check Payment."""
    c = db.conn()
    rows = c.execute(
        "SELECT id, user_id, binance_order_id FROM orders "
        "WHERE status='pending' AND binance_order_id IS NOT NULL"
    ).fetchall()
    c.close()
    for o in rows:
        try:
            status = await asyncio.to_thread(binance.query_order, o["binance_order_id"])
        except Exception:
            log.exception("auto payment check failed for %s", o["id"])
            continue
        if status != "PAID":
            continue
        items = db.fulfill_order(o["id"])
        if items is None:
            continue
        language = lang(o["user_id"])
        try:
            await context.bot.send_message(
                o["user_id"], delivery_text(language, db.get_order(o["id"]), items), parse_mode=HTML)
        except Exception:
            log.exception("could not deliver order %s", o["id"])

async def expiry_job(context: ContextTypes.DEFAULT_TYPE):
    await poll_payments(context)
    expired = db.expire_orders(datetime.now(timezone.utc))
    for order in expired:
        try:
            await context.bot.send_message(order["user_id"], TEXT[order["language"] or "en"]["expired"])
        except Exception:
            pass

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(TEXT[lang(update.effective_user.id)]["admin_only"])
        return
    await update.message.reply_text(
        "ADMIN\n\n"
        "/addlink <product> <price> <item_link_or_code>\n"
        "/products\n"
        "Admins can only add/manage stock items.\n\n"
        "OWNER ONLY\n"
        "/addadmin <telegram_id>\n"
        "/removeadmin <telegram_id>\n"
        "/setprice <product_id> <price>\n"
        "/setuserprice <username> <product_id> <price>\n"
        "/paymentcenter"
    )

async def addlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(TEXT[lang(update.effective_user.id)]["admin_only"])
        return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /addlink <product> <price> <item_link_or_code>")
        return
    try:
        price = float(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid price.")
        return
    name = context.args[0]
    link = context.args[2]
    db.add_product_link(name, price, link, update.effective_user.id)
    await update.message.reply_text(TEXT[lang(update.effective_user.id)]["link_added"])

async def products_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    products = db.list_products()
    await update.message.reply_text("\n".join(
        f"#{p['id']} {p['name']} | {p['price']:.2f} USDT | stock {p['stock']}"
        for p in products
    ) or "No products.")

async def owner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(TEXT["en"]["owner_only"])
        return
    await update.message.reply_text(
        "OWNER COMMANDS\n\n"
        "/addadmin <telegram_id>\n/removeadmin <telegram_id>\n"
        "/setprice <product_id> <price>\n"
        "/setuserprice <username> <product_id> <price>\n"
        "/paymentcenter"
    )

async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or not context.args:
        return
    db.add_admin(int(context.args[0]), update.effective_user.id)
    await update.message.reply_text("✅ Admin added.")

async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or not context.args:
        return
    db.remove_admin(int(context.args[0]))
    await update.message.reply_text("✅ Admin removed.")

async def setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or len(context.args) != 2:
        return
    db.set_product_price(int(context.args[0]), float(context.args[1]))
    await update.message.reply_text("✅ Price changed.")

async def setuserprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or len(context.args) != 3:
        return
    db.set_user_price(context.args[0].lstrip("@"), int(context.args[1]), float(context.args[2]))
    await update.message.reply_text("✅ User-specific price set.")

async def paymentcenter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    stats = db.payment_stats()
    await update.message.reply_text(
        f"💳 PAYMENT CENTRE\n\n"
        f"Total orders: {stats['total']}\n"
        f"Paid: {stats['paid']}\n"
        f"Pending: {stats['pending']}\n"
        f"Failed: {stats['failed']}\n"
        f"Revenue: {stats['revenue']:.2f} USDT"
    )

async def reopen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /reopen <order_id> - re-activate an expired order whose payment arrived late."""
    if not is_owner(update.effective_user.id) or not context.args:
        return
    order_id = context.args[0].strip()
    order = db.get_order(order_id)
    if not order:
        await update.message.reply_text("Order not found.")
        return
    if order["status"] == "paid":
        await update.message.reply_text("Order is already paid.")
        return
    new_exp = datetime.now(timezone.utc) + timedelta(minutes=15)
    c = db.conn()
    c.execute(
        "UPDATE orders SET status='pending', failure_reason=NULL, expires_at=? WHERE id=?",
        (new_exp.isoformat(), order_id),
    )
    c.commit(); c.close()
    await update.message.reply_text(
        f"✅ {order_id} reopened for 15 minutes. The bot will verify the payment automatically."
    )

async def apitest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /apitest - check Binance Pay API and BSC RPC connectivity."""
    if not is_owner(update.effective_user.id):
        return
    out = [f"BINANCE_ENABLED={BINANCE_ENABLED}  BSC_ENABLED={BSC_ENABLED}"]
    if BINANCE_ENABLED:
        try:
            rows = await asyncio.to_thread(binance._bpay_history, True)
            out.append(f"Binance Pay history OK: {len(rows)} rows (last 1h)")
            for t in rows[:5]:
                out.append(f"  {t.get('orderType')} {t.get('currency')} {t.get('amount')} @ {t.get('transactionTime')}")
        except Exception as e:
            out.append(f"Binance Pay ERROR: {str(e)[:300]}")
    if BSC_ENABLED:
        try:
            n = int(await asyncio.to_thread(binance._rpc, "eth_blockNumber", []), 16)
            out.append(f"BSC RPC OK, latest block {n}")
        except Exception as e:
            out.append(f"BSC RPC ERROR: {str(e)[:300]}")
    await update.message.reply_text("\n".join(out))

def main():
    if not BOT_TOKEN or not OWNER_ID:
        raise RuntimeError("Set BOT_TOKEN and OWNER_ID environment variables.")
    db.init()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("owner", owner_cmd))
    app.add_handler(CommandHandler("addlink", addlink))
    app.add_handler(CommandHandler("products", products_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("removeadmin", removeadmin))
    app.add_handler(CommandHandler("setprice", setprice))
    app.add_handler(CommandHandler("setuserprice", setuserprice))
    app.add_handler(CommandHandler("paymentcenter", paymentcenter))
    app.add_handler(CommandHandler("reopen", reopen))
    app.add_handler(CommandHandler("apitest", apitest))
    app.add_handler(CallbackQueryHandler(language_cb, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(menu_cb, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(back_cb, pattern=r"^back:"))
    app.add_handler(CallbackQueryHandler(product_cb, pattern=r"^product:"))
    app.add_handler(CallbackQueryHandler(qty_cb, pattern=r"^qty:"))
    app.add_handler(CallbackQueryHandler(qty_other_cb, pattern=r"^qtyo:"))
    app.add_handler(CallbackQueryHandler(pay_cb, pattern=r"^pay:"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern=r"^cancel:"))
    app.add_handler(CallbackQueryHandler(check_cb, pattern=r"^check:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_msg))
    app.job_queue.run_repeating(expiry_job, interval=20, first=10)
    app.run_polling()

if __name__ == "__main__":
    main()
