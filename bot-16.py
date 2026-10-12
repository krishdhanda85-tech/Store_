import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

from db import Database
from binance_pay import BinancePayClient, PAY_ID, DEPOSIT_ADDRESS, DEPOSIT_NETWORK

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PAYMENT_TIMEOUT_MINUTES = int(os.getenv("PAYMENT_TIMEOUT_MINUTES", "5"))
DB_PATH = os.getenv("DB_PATH", "shop.db")
SHOP_NAME = os.getenv("SHOP_NAME", "Digital Shop")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("digital-shop")

db = Database(DB_PATH)
binance = BinancePayClient(
    api_key=os.getenv("BINANCE_API_KEY", ""),
    secret_key=os.getenv("BINANCE_SECRET_KEY", ""),
    base_url=os.getenv("BINANCE_PAY_BASE_URL", "https://bpay.binanceapi.com"),
)

TEXT = {
    "en": {
        "choose": "Choose language:",
        "products": "Products",
        "stock": "Stock",
        "price": "Price",
        "buy": "Buy",
        "out": "❌ Out of stock.",
        "qty": "Enter quantity:",
        "amount": "Amount: {amount} USDT\n\nPay within {minutes} minutes.",
        "created": "Payment order created.\nOrder: {order}\nAmount: {amount} USDT\n\n⚠️ Send EXACTLY <code>{amount}</code> USDT (a different amount cannot be matched).\n\n{details}\n\nAfter paying, press Check Payment. The order expires in {minutes} minutes.",
        "expired": "❌ Payment failed/expired. The payment window ended.",
        "paid": "✅ Payment verified. Your item(s):",
        "cancelled": "Order cancelled.",
        "invalid": "Please enter a valid quantity.",
        "admin_only": "Admin access required.",
        "owner_only": "Owner access required.",
        "link_added": "✅ Item added to stock.",
        "help": "Use /start to open the shop.",
    },
    "zh": {
        "choose": "请选择语言：",
        "products": "商品",
        "stock": "库存",
        "price": "价格",
        "buy": "购买",
        "out": "❌ 库存不足。",
        "qty": "请输入数量：",
        "amount": "金额：{amount} USDT\n\n请在 {minutes} 分钟内完成付款。",
        "created": "支付订单已创建。\n订单：{order}\n金额：{amount} USDT\n\n⚠️ 请准确转账 <code>{amount}</code> USDT（金额不一致将无法匹配）。\n\n{details}\n\n付款后请点击“Check Payment”。订单 {minutes} 分钟后过期。",
        "expired": "❌ 支付失败/已过期。支付时间已结束。",
        "paid": "✅ 支付已验证。您的商品：",
        "cancelled": "订单已取消。",
        "invalid": "请输入有效数量。",
        "admin_only": "需要管理员权限。",
        "owner_only": "需要所有者权限。",
        "link_added": "✅ 商品已加入库存。",
        "help": "使用 /start 打开商店。",
    },
}

def lang(user_id: int) -> str:
    return db.get_user_language(user_id) or "en"

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def is_admin(user_id: int) -> bool:
    return is_owner(user_id) or db.is_admin(user_id)

def kb_lang():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
        InlineKeyboardButton("🇨🇳 中文", callback_data="lang:zh"),
    ]])

def product_kb(products, language):
    rows = []
    for p in products:
        label = f"{p['name']} | {p['price']:.2f} USDT | {p['stock']} left"
        rows.append([InlineKeyboardButton(label, callback_data=f"product:{p['id']}")])
    return InlineKeyboardMarkup(rows)

def payment_details() -> str:
    lines = []
    if DEPOSIT_ADDRESS:
        net = f" ({DEPOSIT_NETWORK})" if DEPOSIT_NETWORK else ""
        lines.append(f"💳 USDT{net} address:\n<code>{DEPOSIT_ADDRESS}</code>")
    if PAY_ID:
        lines.append(f"🟡 Binance Pay ID / UID:\n<code>{PAY_ID}</code>")
    return "\n\n".join(lines)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.touch_username(update.effective_user.id, update.effective_user.username)
    await update.message.reply_text(f"🛍 {SHOP_NAME}\n\nChoose language / 请选择语言:", reply_markup=kb_lang())

async def language_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    language = q.data.split(":")[1]
    db.set_user_language(q.from_user.id, language, q.from_user.username)
    await show_products(q, language)

async def show_products(target, language):
    t = TEXT[language]
    products = db.list_products()
    if not products:
        await target.edit_message_text(f"{t['products']}\n\nNo products available." if language=="en" else f"{t['products']}\n\n暂无商品。")
        return
    await target.edit_message_text(
        f"🛒 {t['products']}\n\n" +
        "\n".join(f"• {p['name']} — {p['price']:.2f} USDT — {p['stock']} {t['stock']}" for p in products),
        reply_markup=product_kb(products, language)
    )

async def product_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split(":")[1])
    p = db.get_product(pid)
    if not p:
        return
    language = lang(q.from_user.id)
    t = TEXT[language]
    if p["stock"] <= 0:
        await q.answer(t["out"], show_alert=True)
        return
    context.user_data["buy_product"] = pid
    await q.message.reply_text(f"{p['name']}\n{t['stock']}: {p['stock']}\n{t['price']}: {p['price']:.2f} USDT\n\n{t['qty']}")

async def quantity_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = context.user_data.get("buy_product")
    if not pid:
        return
    language = lang(update.effective_user.id)
    t = TEXT[language]
    try:
        qty = int(update.message.text.strip())
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(t["invalid"])
        return

    p = db.get_product(pid)
    if not p or p["stock"] < qty:
        await update.message.reply_text(t["out"])
        return

    unit = db.get_effective_price(update.effective_user.id, pid, p["price"])
    amount = round(unit * qty, 2)

    order_id = db.create_order(
        update.effective_user.id, pid, qty, unit, amount,
        datetime.now(timezone.utc) + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
    )

    try:
        pay = await asyncio.to_thread(
            binance.create_order,
            order_id, amount, f"{SHOP_NAME} order {order_id}"
        )
    except Exception as e:
        log.exception("Binance Pay order creation failed")
        db.mark_order_failed(order_id, str(e))
        await update.message.reply_text("Payment service is temporarily unavailable.")
        return

    db.attach_binance_order(order_id, pay["prepay_id"], pay.get("checkout_url", ""))
    buttons = []
    if pay.get("checkout_url"):
        buttons.append([InlineKeyboardButton("💳 Pay with Binance", url=pay["checkout_url"])])
    buttons.append([InlineKeyboardButton("🔄 Check Payment", callback_data=f"check:{order_id}")])
    await update.message.reply_text(
        t["created"].format(
            order=order_id,
            amount=pay.get("amount_text", f"{amount:.2f}"),
            details=payment_details(),
            minutes=PAYMENT_TIMEOUT_MINUTES,
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    context.user_data.pop("buy_product", None)

async def check_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    order_id = q.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order or order["user_id"] != q.from_user.id:
        return

    language = lang(q.from_user.id)
    t = TEXT[language]

    if order["status"] in ("paid", "failed"):
        if order["status"] == "failed":
            await q.message.reply_text(t["expired"])
        return

    if datetime.now(timezone.utc) >= datetime.fromisoformat(order["expires_at"]):
        db.mark_order_failed(order_id, "timeout")
        await q.message.reply_text(t["expired"])
        return

    try:
        status = await asyncio.to_thread(binance.query_order, order["binance_order_id"])
    except Exception:
        await q.answer("Payment check temporarily unavailable.", show_alert=True)
        return

    if status == "PAID":
        items = db.fulfill_order(order_id)
        if items is None:
            await q.answer("Order could not be fulfilled.", show_alert=True)
            return
        await q.message.reply_text(t["paid"] + "\n\n" + "\n".join(items))
    else:
        await q.answer("Payment not confirmed yet.", show_alert=True)

async def expiry_job(context: ContextTypes.DEFAULT_TYPE):
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
    app.add_handler(CallbackQueryHandler(language_cb, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(product_cb, pattern=r"^product:"))
    app.add_handler(CallbackQueryHandler(check_cb, pattern=r"^check:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_msg))
    app.job_queue.run_repeating(expiry_job, interval=20, first=10)
    app.run_polling()

if __name__ == "__main__":
    main()
