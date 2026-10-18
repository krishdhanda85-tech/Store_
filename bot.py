import asyncio
import html
import logging
import io
import os
import re
import zlib
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from telegram import (Update, InlineKeyboardButton as _Btn, InlineKeyboardMarkup as Markup,
                      ReplyKeyboardMarkup)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

from db import Database
import provider
import reseller_api
from provider import ShopDB
from binance_pay import (BinancePayClient, PAY_ID, DEPOSIT_ADDRESS, DEPOSIT_NETWORK,
                         BINANCE_ENABLED, BSC_ENABLED, public_url)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DEPOSIT_TIMEOUT_MINUTES = int(os.getenv("DEPOSIT_TIMEOUT_MINUTES", os.getenv("PAYMENT_TIMEOUT_MINUTES", "15")))
UPI_TIMEOUT_MINUTES = int(os.getenv("UPI_TIMEOUT_MINUTES", "30"))   # time to pay + send the UTR
UTR_RE = re.compile(r"^\d{12}$")
MIN_DEPOSIT = float(os.getenv("MIN_DEPOSIT", "0.1"))
MAX_DEPOSIT = float(os.getenv("MAX_DEPOSIT", "100"))
DB_PATH = os.getenv("DB_PATH", "shop.db")
SHOP_NAME = os.getenv("SHOP_NAME", "Digital Shop")
# Public link of the admin/API website, shown in the bot's "🔑 API" screen.
API_BASE_URL = (os.getenv("API_BASE_URL") or os.getenv("PUBLIC_URL")
                or (("https://" + os.getenv("RAILWAY_PUBLIC_DOMAIN")) if os.getenv("RAILWAY_PUBLIC_DOMAIN") else "")).strip().rstrip("/")
# Optional mandatory channel join + order announcements. Bot must be admin in the channel.
REQUIRED_CHANNEL_ID = os.getenv("REQUIRED_CHANNEL_ID", "").strip()
REQUIRED_CHANNEL_URL = os.getenv("REQUIRED_CHANNEL_URL", "").strip()
SALES_CHANNEL_ID = os.getenv("SALES_CHANNEL_ID", REQUIRED_CHANNEL_ID).strip()
MANUAL_ALERTS = os.getenv("MANUAL_ALERTS", "1") != "0"   # tell the owner about new made-to-order orders
TIERS_ENV = os.getenv("TIERS", "")                        # e.g. "10:Bronze,50:Silver,200:Gold"; empty = no tiers
HTML = ParseMode.HTML
QTY_PRESETS = (1, 5, 10, 20, 30, 50, 100)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("digital-shop")

db = ShopDB(DB_PATH)
binance = BinancePayClient()

# ---------------------------------------------------------------- texts
# Every key exists in English; the Chinese table only needs the keys it translates
# (anything missing falls back to English).
TEXT = {
    "en": {
        "welcome": "👋 <b>Welcome to {shop}!</b>\n\n✨ Premium digital products at great prices.\n⚡ Fast delivery • 🔒 Secure checkout • 🎧 Friendly support\n\n🌐 Choose your language / 请选择语言 👇",
        "menu_ready": "👋 <b>Welcome to {shop}!</b>\n\n✨ Your digital shop for premium subscriptions and services.\n⚡ Quick delivery • 💳 Easy wallet top-ups • 🎧 Support when you need it.\n\n👇 <b>Choose an option below to get started!</b>",
        "lang_set": "✅ Language saved.",
        "shop_head": "🛍️ <b>Welcome to the Shop</b>\n\n<blockquote>🟢 Available products: <b>{avail}</b>\n📦 Total products: <b>{total}</b>\n⚡ Fast digital delivery</blockquote>\n\n🔷 Choose a category or browse products below ⬇️",
        "no_products": "🛍 <b>{shop}</b>\n\nNo products available right now.",
        "cat_head": "📂 <b>{cat}</b>\n\n🟢 Select your product below ⬇️",
        "btn_search": "🔎 Search",
        "search_ask": "🔎 <b>Search</b>\n\nType the product name:",
        "search_head": "🔎 <b>Results for “{q}”</b>",
        "search_none": "🔎 Nothing found for “{q}”.",
        "sold_out": "Sold out",
        "in_stock": "In stock",
        "made_to_order": "Made to order",
        "card": "📦 <b>{name}</b>\n\n💰 Price: <b>${price}</b> (₹{inr}){disc}\n📦 Stock: <b>{stock}</b>{delivery}\n🛡 Warranty: <b>{warranty}</b>{desc}",
        "card_delivery": "\n⏳ Delivery: <b>{delivery}</b>",
        "card_desc": "\n\n📝 <b>Description</b>\n<blockquote>{desc}</blockquote>",
        "default_manual_delivery": "by hand — within 24 hours",
        "none": "None",
        "btn_buy": "Buy — ${price}",
        "back": "‹ Back",
        "shop": "‹ Shop",
        "main_menu": "Main menu",
        "browse": "Browse the shop",
        "other_qty": "✏️ Other quantity",
        "qty_head": "🛒 <b>{name}</b>\n\n🔢 <b>Choose quantity</b>\n💵 Price each: <b>${unit}</b> (₹{inr})\n👛 Wallet balance: <b>${bal}</b>\n\n📦 Available stock is checked automatically.",
        "ask_qty": "✏️ Send the quantity you want (a number):",
        "calc": "🧮 <b>Order summary</b>\n\n📦 {name}\n🔢 Quantity: <b>{qty}</b>\n💵 Price each: ${unit}\n💰 Total: <b>${total}</b> (₹{inr})\n👛 Balance: ${bal}",
        "btn_pay": "💰 Pay ${total} from balance",
        "btn_addfunds": "💳 Add Funds",
        "need_more": "\n\n⚠️ Not enough balance — you need <b>${need}</b> more.",
        "bought": "🎉 <b>Order complete!</b>\n\n🧾 Order: <code>{order}</code>\n📦 {name} × {qty}\n\n🔑 <b>Your item(s):</b>\n{items}\n\n👛 Balance: ${bal}",
        "email_prompt_default": "Send the email for this order.",
        "email_ask": "✏️ <b>{prompt}</b>\n\n<i>Send it in one message.</i>",
        "email_invalid": "That doesn't look like an email address. Please send it again.",
        "confirm": "🧾 <b>Confirm order</b>\n\n📦 {name}\n📧 {contact}\n💰 Price: <b>${price}</b>\n⏳ Delivery: {delivery}\n\nThe amount is held from your balance. If we can't complete the order it is refunded.",
        "btn_confirm": "✅ Confirm — ${price}",
        "btn_cancel": "❌ Cancel",
        "placed": "✅ <b>Order placed</b>\n\n🧾 Order: <code>{order}</code>\n📦 {name}\n📧 {contact}\n⏳ Delivery: {delivery}\n\nWe'll message you here as soon as it's done.",
        "cancelled": "❌ Cancelled.",
        "wallet": "💳 <b>Wallet</b>\n\n💰 Balance: <b>${bal}</b> (₹{inr})\n💱 Rate: 1 USDT = ₹{rate}\n<i>Top-up limits: ${min} — ${max}</i>\n\nPick a method ⬇️",
        "btn_upi": "🏦 UPI (30 min)",
        "ask_amount_upi": "🏦 <b>UPI · INR</b>\n\nSend the amount to add in USDT (${min} — ${max}).\n💱 Rate: 1 USDT = ₹{rate}",
        "upi_created": "🏦 <b>Pay by UPI</b>\n\n🧾 ID: <code>{id}</code>\n💰 You'll receive: <b>${credit}</b>\n💵 Pay: <b>₹{inr}</b> (₹{rate} per USDT)\n\n📲 UPI ID:\n<code>{upi}</code>\n\n1️⃣ Pay exactly <b>₹{inr}</b>\n2️⃣ Tap <b>I've paid</b> and send the 12-digit <b>UTR</b>\n\n⏳ Admin checks it manually — up to <b>30 min</b>. Send the UTR within {minutes} min.",
        "btn_upi_paid": "✅ I've paid — send UTR",
        "utr_ask": "🔢 Send the <b>12-digit UTR</b> (UPI reference number) of your payment of <b>₹{inr}</b>:",
        "utr_invalid": "UTR must be exactly 12 digits. Check your UPI app and send it again.",
        "utr_dup": "⚠️ This UTR was already submitted. If you think this is a mistake, open a Support ticket.",
        "utr_closed": "⌛ This UPI request is closed. Open Add Funds to start again.",
        "utr_sent": "✅ <b>UTR received</b>\n\n🧾 ID: <code>{id}</code>\n🔢 UTR: <code>{utr}</code>\n💵 ₹{inr} → ${credit}\n\n⏳ Admin is verifying. Your balance is added within <b>30 min</b> — we'll message you here.",
        "upi_rejected": "❌ <b>UPI payment not verified</b>\n\n🧾 <code>{id}</code>\n🔢 UTR: <code>{utr}</code>\n\nIf you did pay, open Support and send a screenshot.",
        "upi_none": "UPI is not available right now. Please use Crypto.",
        "btn_bpay": "Binance Pay",
        "btn_crypto": "⚡ Crypto · USDT BEP-20 (Instant)",
        "no_methods": "\n\n⚠️ Top-ups are not available right now.",
        "ask_amount": "💵 <b>{method}</b>\n\nSend the amount to add (${min} — ${max}):",
        "amount_invalid": "Send a number between ${min} and ${max}.",
        "dep_created": "✅ <b>Deposit created</b>\n\n🧾 ID: <code>{id}</code>\n💰 You'll receive: <b>${credit}</b>\n\nSend <b>exactly {amount} USDT</b>. No triple-decimal amount is needed.\n\n{details}\n\n⏳ Expires in <b>{minutes} min</b>. After paying, tap <b>Submit TXN Hash</b>. Once confirmed on-chain, your balance is added automatically.",
        "txhash_ask": "🧾 Send the transaction hash (TXID) for your USDT BEP-20 payment. It starts with <code>0x</code>. We'll verify it on-chain and add your balance after {confirmations} confirmations.",
        "txhash_invalid": "That doesn't look like a valid transaction hash. Send the full 66-character hash starting with 0x.",
        "txhash_dup": "This transaction hash has already been submitted for another deposit.",
        "txhash_wait": "⏳ TXN hash received. Verification is automatic; balance will be added after the transaction is confirmed.",
        "dep_paid": "🎉 <b>Deposit received</b>\n\n➕ ${amount}\n👛 Balance: <b>${bal}</b>",
        "dep_expired": "⌛ Deposit expired. Open Add Funds to try again.",
        "check": "🔄 Check Payment",
        "cancel_dep": "❌ Cancel deposit",
        "not_yet": "Payment not confirmed yet. Try again in a minute.",
        "check_down": "Payment check temporarily unavailable.",
        "pay_down": "Payment service is temporarily unavailable.",
        "bpay_down": "Binance Pay is temporarily unavailable. Please choose Crypto.",
        "not_configured": "This payment method is not configured yet.",
        "out": "❌ Not enough stock.",
        "unavailable": "This product is sold out.",
        "invalid": "Please send a valid number.",
        "profile": "👤 <b>My account</b>\n\n💰 <b>Balance</b>\n<b>${bal}</b>\n\n<blockquote>📦 Spent: <b>${spent}</b>\n🎁 Referral earnings: <b>${ref}</b>\n🏅 Tier: <b>{tier}</b></blockquote>\n<blockquote>👤 {name}{uname}\n🆔 <code>{id}</code>\n⏳ Member since {since}</blockquote>",
        "no_tier": "No tier yet",
        "btn_orders": "🧾 My Orders",
        "btn_tx": "🧾 Transactions",
        "btn_api": "🔑 API",
        "btn_api_docs": "📖 API Docs",
        "btn_docs_dl": "📄 Download docs",
        "api_docs_1": "📖 <b>API Docs · 1/3 · Quick start</b>\n\n1️⃣ Create your key: <b>🔑 Reseller API → Create API key</b>\n2️⃣ Top up your wallet with <b>Add Funds</b>\n3️⃣ Paste this in your panel or bot:\n\n<b>API URL</b>\n<code>{url}/api/v1/index.php</code>\n<b>API key</b>\n<code>{key}</code> (starts with rs_live_)\n\n4️⃣ Test it in your browser:\n<code>{url}/api/v1/index.php?action=balance&amp;api_key={key}</code>\n\nYou can send the key as header <code>Authorization: Bearer {key}</code>, header <code>X-API-Key</code>, or field <code>api_key</code>.\n\nUse https:// and the full path, not only the domain.",
        "api_docs_2": "📖 <b>API Docs · 2/3 · Actions</b>\n\nOne URL, choose with <code>action</code>:\n\n<code>action=products</code> products, price, stock\n<code>action=balance</code> wallet balance\n<code>action=buy</code> place an order\n<code>action=order</code> one order (<code>order_id</code>)\n<code>action=orders</code> last 100 orders\n\n<b>Buy example</b>\n<code>{url}/api/v1/index.php?action=buy&amp;product_id=1&amp;quantity=2&amp;idempotency_key=order-1001&amp;api_key={key}</code>\n\nMax 20 per order. Always send a unique <code>idempotency_key</code>: if you retry after a timeout you get the same order back and are not charged twice.\n\nREST also works: <code>GET /api/v1/products</code>, <code>GET /api/v1/balance</code>, <code>POST /api/v1/orders</code>.",
        "api_docs_3": "📖 <b>API Docs · 3/3 · Replies and errors</b>\n\nEvery reply is JSON.\nSuccess: <code>{{\"ok\": true, ...}}</code>\nError: <code>{{\"ok\": false, \"error\": \"code\"}}</code>\n\nBuy reply has <code>order.items</code> = your delivered accounts and <code>balance</code> = wallet left.\n\n<b>Errors</b>\n401 <code>missing_api_key</code> / <code>invalid_api_key</code>\n402 <code>insufficient_balance</code> (top up)\n409 <code>insufficient_stock</code>\n404 <code>product_not_found</code> / <code>not_found</code>\n400 bad quantity or unknown action\n403 <code>reseller_disabled</code> (contact Support)\n\nPress <b>📄 Download docs</b> for the full guide with curl, Python and PHP examples.",
        "btn_api_new": "🔑 Create API key",
        "btn_api_regen": "🔄 New API key",
        "btn_api_regen_ok": "Yes, replace my key",
        "api_intro": "🔑 <b>Reseller API</b>\n\nSell our products from your own website or bot. Orders are paid from your wallet balance at the normal shop price (your discount, if any, is set by the admin).\n\nTap below to create your API key.",
        "api_home": "🔑 <b>Reseller API</b>\n\n💰 Wallet: <b>${bal}</b> (top up with Add Funds)\n🏷 Discount: <b>{disc}%</b>\n🌐 API URL (paste this in your panel): <code>{url}/api/v1/index.php</code>\n🔑 API key: <code>{key}</code>\n📖 Docs: <code>{url}/api/docs</code>\n\nKey goes in header <code>Authorization: Bearer {key}</code> (or <code>?api_key={key}</code>)\n\nQuick test: <code>{url}/api/v1/index.php?action=balance&amp;api_key={key}</code>\n\nREST also works:\n<code>GET /api/v1/products</code> — products, price, stock\n<code>GET /api/v1/balance</code> — wallet balance\n<code>POST /api/v1/orders</code> — body: product_id, quantity (max 20)\n<code>GET /api/v1/orders</code> — your orders\n\nSend a unique <code>Idempotency-Key</code> header with every order. Only auto-delivery products are available. Not enough balance gives error 402.{keynote}",
        "api_key_new": "✅ <b>Your API key</b>\n\n<code>{key}</code>\n\n⚠️ Never share it. You can see it again anytime in 🔑 Reseller API.",
        "api_regen_ask": "Replace your API key? The old key stops working immediately.",
        "api_disabled": "⛔ Your API access is disabled. Please open Support.",
        "api_key_sent": "✅ <b>API key created.</b>\nYour key is in the message below. Copy it now.",
        "api_key_replaced": "✅ <b>New API key created.</b>\nYour new key is in the message below. The old key no longer works.",
        "api_error": "⚠️ Something went wrong. Please try again, or open Support if it keeps happening.",
        "orders": "🧾 <b>My orders</b>\n\n{rows}",
        "no_orders": "📦 No orders yet.",
        "tx": "🧾 <b>Transactions</b>\n\n{rows}",
        "no_tx": "No transactions yet.",
        "tx_deposit": "➕ Deposit",
        "tx_purchase": "🛒 Purchase",
        "tx_refund": "↩️ Refund",
        "referral": "🎁 <b>Referrals</b>\n\n<blockquote>👥 Invited: <b>{n}</b>\n💰 Total earned: <b>${earned}</b>\n✅ Paid out: <b>${paid}</b></blockquote>\n\n⚡ You get <b>{pct}%</b> of everything they buy.\n\n🔗 <b>Your link</b>\n<code>{link}</code>\n\n<i>Code: {code}</i>",
        "btn_share": "🔗 Share link",
        "share_text": "Check out {shop}!",
        "support_none": "🛟 <b>Support</b>\n\nNo tickets yet. Open one if something is wrong.",
        "support_list": "🛟 <b>Support</b>\n\n{rows}",
        "btn_new_ticket": "➕ New ticket",
        "ticket_kind": "❓ <b>What kind of problem?</b>",
        "k_pay": "💳 Payment issue",
        "k_gen": "ℹ️ General question",
        "k_bug": "🤖 Bot bug",
        "ticket_describe": "✏️ <b>Describe the problem</b>\n\nBe specific. You can attach a photo.",
        "ticket_created": "✅ <b>Ticket #{id} created</b>\n\nWe'll reply here as soon as possible.",
        "ticket_reply": "💬 <b>Support reply</b> · ticket #{id}\n\n{text}",
        "settings": "⚙️ <b>Settings</b>\n\n🌐 Language: <b>{lang}</b>",
        "st_paid": "Done", "st_processing": "Processing", "st_pending": "Waiting for payment",
        "st_failed": "Cancelled", "st_refunded": "Refunded",
        "admin_only": "Admin access required.",
        "owner_only": "Owner access required.",
        "link_added": "✅ Item added to stock.",
        "help": "Use the menu below, or /start.",
    },
    "zh": {
        "welcome": "🛍 <b>{shop}</b>\n\n请选择语言 / Choose your language 👇",
        "menu_ready": "🛍 <b>{shop}</b>\n\n请使用下方菜单 👇",
        "lang_set": "✅ 语言已保存。",
        "shop_head": "📦 <b>全部商品</b>\n\n<blockquote>✅ 有货：<b>{avail}</b>\n📦 总数：<b>{total}</b>\n⚡ 自动即时发货</blockquote>\n\n请选择商品 ⬇️",
        "no_products": "🛍 <b>{shop}</b>\n\n暂无商品。",
        "cat_head": "📂 <b>{cat}</b>\n\n请选择套餐 ⬇️",
        "btn_search": "🔎 搜索",
        "search_ask": "🔎 <b>搜索</b>\n\n请输入商品名称：",
        "sold_out": "已售罄", "in_stock": "有货", "made_to_order": "按单定制",
        "none": "无",
        "btn_buy": "购买 — ${price}",
        "back": "‹ 返回", "shop": "‹ 商店", "main_menu": "主菜单", "browse": "浏览商店",
        "btn_pay": "💰 用余额支付 ${total}",
        "btn_addfunds": "💳 充值",
        "btn_cancel": "❌ 取消",
        "cancelled": "❌ 已取消。",
        "btn_bpay": "Binance Pay",
        "btn_crypto": "加密货币 · USDT BEP-20",
        "check": "🔄 检查付款",
        "btn_orders": "🧾 我的订单",
        "btn_tx": "🧾 交易记录",
        "no_orders": "📦 还没有订单。",
        "btn_share": "🔗 分享链接",
        "btn_new_ticket": "➕ 新工单",
        "k_pay": "💳 付款问题", "k_gen": "ℹ️ 一般问题", "k_bug": "🤖 机器人故障",
        "ticket_kind": "❓ <b>遇到什么问题？</b>",
        "ticket_describe": "✏️ <b>请描述问题</b>\n\n请尽量具体，可附上图片。",
        "support_none": "🛟 <b>客服</b>\n\n暂无工单。遇到问题请新建工单。",
        "settings": "⚙️ <b>设置</b>\n\n🌐 语言：<b>{lang}</b>",
        "st_paid": "已完成", "st_processing": "处理中", "st_pending": "等待付款",
        "st_failed": "已取消", "st_refunded": "已退款",
        "admin_only": "需要管理员权限。",
        "owner_only": "需要所有者权限。",
        "link_added": "✅ 商品已加入库存。",
        "help": "请使用下方菜单，或发送 /start。",
    },
}
LANG_NAMES = {"en": "English", "zh": "中文"}


def tt(language: str, key: str, **kw) -> str:
    s = TEXT.get(language, {}).get(key)
    if s is None:
        s = TEXT["en"][key]
    return s.format(**kw) if kw else s


def Btn(text, callback_data=None, style=None, url=None):
    """Inline button with optional colour: 'primary' (blue), 'success' (green), 'danger' (red).
    Colours need a Telegram app released after 9 Feb 2026; older apps just show a normal button."""
    kwargs = {"callback_data": callback_data} if url is None else {"url": url}
    if not style:
        return _Btn(text, **kwargs)
    try:
        return _Btn(text, style=style, **kwargs)
    except TypeError:  # older python-telegram-bot without the style field
        return _Btn(text, api_kwargs={"style": style}, **kwargs)


# ---------------------------------------------------------------- small helpers
MENU = {
    "shop": "🛍 Shop", "funds": "💳 Add Funds", "profile": "👤 Profile", "orders": "🧾 My Orders",
    "ref": "🎁 Referral", "support": "🎧 Support", "settings": "⚙️ Settings",
    "api": "🔑 Reseller API",
}
MENU_BY_LABEL = {v: k for k, v in MENU.items()}
MENU_RE = "^(" + "|".join(re.escape(v) for v in MENU.values()) + ")$"


def main_keyboard():
    return ReplyKeyboardMarkup(
        [[MENU["shop"]], [MENU["funds"], MENU["profile"]], [MENU["orders"], MENU["ref"]],
         [MENU["support"], MENU["settings"]], [MENU["api"]]],
        resize_keyboard=True, is_persistent=True)


def lang(user_id: int) -> str:
    return db.get_user_language(user_id) or "en"


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def is_admin(user_id: int) -> bool:
    return is_owner(user_id) or db.is_admin(user_id)


def admin_ids():
    c = db.conn()
    ids = {r["user_id"] for r in c.execute("SELECT user_id FROM admins").fetchall()}
    c.close()
    if OWNER_ID:
        ids.add(OWNER_ID)
    return ids


def esc(x) -> str:
    return html.escape(str(x))


def money(x) -> str:
    return f"{float(x):.2f}"


def inr_fmt(v) -> str:
    """Rupee amount as text: no decimals when it is a whole number, else 2 decimals."""
    v = round(float(v), 2)
    return f"{v:,.0f}" if abs(v - round(v)) < 0.005 else f"{v:,.2f}"


def inr(x) -> str:
    """USDT -> INR text at the admin's current rate."""
    return inr_fmt(float(x) * db.inr_rate())


def rate_text() -> str:
    r = db.inr_rate()
    return f"{r:g}"


def is_manual(p) -> bool:
    return (p["kind"] or "auto") == "manual"


def is_available(p) -> bool:
    return is_manual(p) or p["stock"] > 0


def cat_key(name: str) -> str:
    return f"{zlib.crc32(name.encode()):08x}"


def discount_pct(old, price):
    """Percent off when the product has a higher "old price" set (0 = no discount)."""
    try:
        if old and old > 0 and old > price:
            return int(round((old - price) / old * 100))
    except TypeError:
        pass
    return 0


def status_label(language: str, status: str) -> str:
    key = "st_" + str(status)
    return tt(language, key) if key in TEXT["en"] else str(status)


def parse_tiers():
    tiers = []
    for part in TIERS_ENV.split(","):
        if ":" in part:
            amount, name = part.split(":", 1)
            try:
                tiers.append((float(amount), name.strip()))
            except ValueError:
                pass
    return sorted(tiers, reverse=True)


def tier_for(spent: float, language: str) -> str:
    for amount, name in parse_tiers():
        if spent >= amount:
            return name
    return tt(language, "no_tier")


async def edit(q, text, markup=None):
    """Edit the message in place; ignore Telegram's 'message is not modified'."""
    try:
        await q.edit_message_text(text, reply_markup=markup, parse_mode=HTML, disable_web_page_preview=True)
    except BadRequest as e:
        err = str(e).lower()
        if "not modified" in err:
            return
        if "no text in the message" in err:   # the UPI screen is a photo (QR): replace it with a new message
            chat = q.message.chat
            try:
                await q.message.delete()
            except Exception:
                pass
            await chat.send_message(text, reply_markup=markup, parse_mode=HTML, disable_web_page_preview=True)
            return
        raise


async def respond(update, text, markup=None):
    """Edit the tapped message (inline button) or send a new one (menu button / typed text)."""
    q = update.callback_query
    if q:
        await edit(q, text, markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=markup, parse_mode=HTML,
                                                  disable_web_page_preview=True)


def clear_await(context):
    context.user_data.pop("await", None)
    context.user_data.pop("manual", None)


def main_menu_row(language):
    return [Btn(tt(language, "main_menu"), callback_data="shop", style="primary")]


# ---------------------------------------------------------------- views
def product_button(uid: int, p):
    price = db.get_effective_price(uid, p["id"], p["price"])
    label = f"🟢 {p['name']} | ${money(price)}"
    pct = discount_pct(p["old_price"], price)
    if pct:
        label += f" · 🔻{pct}% OFF"
    if is_available(p):
        if not is_manual(p):
            label += f" · {p['stock']} left"
        return Btn(label, callback_data=f"prod:{p['id']}", style="success")
    return Btn("🔴 " + label.removeprefix("🟢 ") + " · SOLD OUT", callback_data=f"prod:{p['id']}", style="danger")


def shop_view(uid: int, language: str, category: str = None):
    products = db.list_products()
    if not products:
        return tt(language, "no_products", shop=esc(SHOP_NAME)), Markup([[Btn(tt(language, "btn_search"), callback_data="search", style="primary")]])
    rows = []
    if category is None:
        avail = sum(1 for p in products if is_available(p))
        text = tt(language, "shop_head", avail=avail, total=len(products))
        cats = db.list_categories()
        pair = []
        for c in cats:
            pair.append(Btn("📂 " + c, callback_data=f"cat:{cat_key(c)}", style="primary"))
            if len(pair) == 2:
                rows.append(pair); pair = []
        if pair:
            rows.append(pair)
        for p in products:
            if not (p["category"] or "").strip():
                rows.append([product_button(uid, p)])
    else:
        text = tt(language, "cat_head", cat=esc(category))
        for p in products:
            if (p["category"] or "").strip() == category:
                rows.append([product_button(uid, p)])
        rows.append([Btn(tt(language, "back"), callback_data="shop", style="primary")])
        return text, Markup(rows)
    rows.append([Btn(tt(language, "btn_search"), callback_data="search", style="primary")])
    return text, Markup(rows)


def card_view(uid: int, p, language: str):
    price = db.get_effective_price(uid, p["id"], p["price"])
    pct = discount_pct(p["old_price"], price)
    disc = f"  <s>${money(p['old_price'])}</s> (−{pct}%)" if pct else ""
    if is_manual(p):
        stock = tt(language, "made_to_order")
    else:
        stock = (tt(language, "in_stock") + f" · {p['stock']}") if p["stock"] > 0 else tt(language, "sold_out")
    delivery_value = (p["delivery"] or "").strip() or (tt(language, "default_manual_delivery") if is_manual(p) else "")
    delivery = tt(language, "card_delivery", delivery=esc(delivery_value)) if delivery_value else ""
    warranty = esc((p["warranty"] or "").strip() or tt(language, "none"))
    lines = [l.strip() for l in (p["description"] or "").splitlines() if l.strip()]
    lines = [("✅ " + l) if l[0].isalnum() else l for l in lines]   # one ✅ per point unless it already has an icon
    desc_block = tt(language, "card_desc", desc=esc("\n".join(lines))) if lines else ""
    text = tt(language, "card", name=esc(p["name"]), price=money(price), inr=inr(price), disc=disc, stock=stock,
              delivery=delivery, warranty=warranty, desc=desc_block)
    cat = (p["category"] or "").strip()
    back = f"cat:{cat_key(cat)}" if cat else "shop"
    rows = []
    if is_available(p):
        rows.append([Btn(tt(language, "btn_buy", price=money(price)), callback_data=f"buy:{p['id']}", style="success")])
    rows.append([Btn(tt(language, "back"), callback_data=back, style="primary")])
    return text, Markup(rows)


def qty_view(uid: int, p, language: str):
    unit = db.get_effective_price(uid, p["id"], p["price"])
    acct = db.get_account(uid)
    text = tt(language, "qty_head", name=esc(p["name"]), unit=money(unit), inr=inr(unit), bal=money(acct["balance"] or 0))
    sizes = [n for n in QTY_PRESETS if n <= p["stock"]]
    rows = [[Btn(str(n), callback_data=f"qty:{p['id']}:{n}", style="primary") for n in sizes[i:i + 3]]
            for i in range(0, len(sizes), 3)]
    rows.append([Btn(tt(language, "other_qty"), callback_data=f"qtyo:{p['id']}", style="success")])
    rows.append([Btn(tt(language, "back"), callback_data=f"prod:{p['id']}", style="primary")])
    return text, Markup(rows)


def calc_view(uid: int, pid: int, qty: int, language: str):
    """Order summary, or None if the product/stock is not available."""
    p = db.get_product(pid)
    if not p or is_manual(p) or qty <= 0 or p["stock"] < qty:
        return None
    unit = db.get_effective_price(uid, pid, p["price"])
    total = round(unit * qty, 2)
    bal = float(db.get_account(uid)["balance"] or 0)
    text = tt(language, "calc", name=esc(p["name"]), qty=qty, unit=money(unit), total=money(total), inr=inr(total), bal=money(bal))
    rows = []
    if bal + 1e-9 >= total:
        rows.append([Btn(tt(language, "btn_pay", total=money(total)), callback_data=f"bal:{pid}:{qty}", style="success")])
    else:
        text += tt(language, "need_more", need=money(total - bal))
        rows.append([Btn(tt(language, "btn_addfunds"), callback_data="fund", style="success")])
    rows.append([Btn(tt(language, "back"), callback_data=f"buy:{pid}", style="primary")])
    return text, Markup(rows)


def wallet_view(uid: int, language: str):
    bal = db.get_account(uid)["balance"] or 0
    text = tt(language, "wallet", bal=money(bal), inr=inr(bal), rate=rate_text(),
              min=f"{MIN_DEPOSIT:g}", max=f"{MAX_DEPOSIT:g}")
    rows = []
    if BINANCE_ENABLED:
        rows.append([Btn(tt(language, "btn_bpay"), callback_data="fm:bpay")])
    if BSC_ENABLED:
        rows.append([Btn(tt(language, "btn_crypto"), callback_data="fm:bsc")])
    if db.upi_active_count() > 0:
        rows.append([Btn(tt(language, "btn_upi"), callback_data="fm:upi")])
    if not rows:
        text += tt(language, "no_methods")
    rows.append(main_menu_row(language))
    return text, Markup(rows)


def profile_view(user, language: str):
    acct = db.get_account(user.id)
    uname = f" · @{esc(user.username)}" if user.username else ""
    text = tt(language, "profile", bal=money(acct["balance"] or 0), spent=money(acct["spent"] or 0),
              ref=money(acct["ref_earned"] or 0), tier=esc(tier_for(acct["spent"] or 0, language)),
              name=esc(user.full_name), uname=uname, id=user.id, since=(acct["created_at"] or "")[:10] or "—")
    return text, Markup([[Btn(tt(language, "btn_orders"), callback_data="menu:orders"),
                          Btn(tt(language, "btn_tx"), callback_data="menu:tx")],
                         [Btn(tt(language, "btn_api"), callback_data="api:home")]])


STATUS_ICON = {"paid": "✅", "processing": "⏳", "pending": "⏳", "failed": "❌", "refunded": "↩️"}


def orders_view(uid: int, language: str):
    orders = db.user_orders(uid)
    if not orders:
        return (tt(language, "no_orders"),
                Markup([[Btn("🛍 " + tt(language, "browse"), callback_data="shop", style="success")]]))
    rows = "\n".join(
        f"{STATUS_ICON.get(o['status'], '•')} <code>{esc(o['id'])}</code> · {o['quantity']} × {esc(o['name'] or 'Product')} · "
        f"${money(o['amount'])} · {status_label(language, o['status'])}"
        for o in orders)
    return tt(language, "orders", rows=rows), Markup([[Btn(tt(language, "shop"), callback_data="shop", style="primary")]])


def tx_view(uid: int, language: str):
    txs = db.user_transactions(uid)
    if not txs:
        return tt(language, "no_tx"), Markup([main_menu_row(language)])
    sign = {"deposit": "+", "purchase": "−", "refund": "+"}
    rows = "\n".join(
        f"{tt(language, 'tx_' + t['kind'])} · {sign[t['kind']]}${money(t['amount'])} · {(t['ts'] or '')[:10]}"
        for t in txs)
    return tt(language, "tx", rows=rows), Markup([main_menu_row(language)])


def referral_view(uid: int, bot_username: str, language: str):
    code = db.get_ref_code(uid)
    link = f"https://t.me/{bot_username}?start=ref_{code}"
    acct = db.get_account(uid)
    text = tt(language, "referral", n=db.referral_count(uid), earned=money(acct["ref_earned"] or 0), paid="0.00",
              pct=f"{db.referral_percent():g}", link=esc(link), code=code)
    share = f"https://t.me/share/url?url={quote(link)}&text={quote(tt(language, 'share_text', shop=SHOP_NAME))}"
    return text, Markup([[Btn(tt(language, "btn_share"), url=share, style="success")]])


def support_view(uid: int, language: str):
    tickets = db.user_tickets(uid)
    if tickets:
        rows = "\n".join(f"🎫 #{t['id']} · {esc(t['kind'] or '')} · {'🟢' if t['status'] == 'open' else '⚪'} {esc(t['status'])}"
                         for t in tickets)
        text = tt(language, "support_list", rows=rows)
    else:
        text = tt(language, "support_none")
    return text, Markup([[Btn(tt(language, "btn_new_ticket"), callback_data="sup:new", style="success")],
                         main_menu_row(language)])


def settings_view(uid: int, language: str):
    text = tt(language, "settings", lang=LANG_NAMES.get(language, language))
    return text, Markup([[Btn("🇬🇧 English", callback_data="setlang:en", style="primary"),
                          Btn("🇨🇳 中文", callback_data="setlang:zh", style="primary")],
                         main_menu_row(language)])


def kb_lang():
    return Markup([[Btn("🇬🇧 English", callback_data="lang:en", style="primary"),
                    Btn("🇨🇳 中文", callback_data="lang:zh", style="primary")]])


def delivery_text(language: str, order, items) -> str:
    p = db.get_product_any(order["product_id"])
    name = esc(p["name"]) if p else "Product"
    bal = db.get_account(order["user_id"])["balance"] or 0
    return tt(language, "bought", order=esc(order["id"]), name=name, qty=order["quantity"],
              items="\n".join(f"<code>{esc(i)}</code>" for i in items), bal=money(bal))


def payment_details(method: str) -> str:
    if method == "bpay":
        return f"🟡 Binance Pay ID / UID:\n<code>{esc(PAY_ID)}</code>\n\nBinance app → Pay → Send → Pay ID, then send the exact amount in USDT."
    net = f" ({esc(DEPOSIT_NETWORK)})" if DEPOSIT_NETWORK else ""
    return f"💳 USDT{net} address:\n<code>{esc(DEPOSIT_ADDRESS)}</code>"


async def notify_admins(context, text, ticket_id=None, photo_id=None):
    for cid in admin_ids():
        try:
            if photo_id:
                m = await context.bot.send_photo(cid, photo_id, caption=text[:1000], parse_mode=HTML)
            else:
                m = await context.bot.send_message(cid, text, parse_mode=HTML, disable_web_page_preview=True)
            if ticket_id:
                db.link_ticket_msg(cid, m.message_id, ticket_id)
        except Exception:
            log.exception("could not notify admin %s", cid)


# ---------------------------------------------------------------- channel join gate + sales announcements
async def is_required_channel_member(bot, user_id: int) -> bool:
    if not REQUIRED_CHANNEL_ID:
        return True
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator") or (member.status == "restricted" and getattr(member, "is_member", False))
    except Exception as exc:
        log.warning("Required channel membership check failed: %s", exc)
        # Fail closed when a required channel is configured; ensure bot is admin there.
        return False


def join_channel_markup():
    rows = []
    if REQUIRED_CHANNEL_URL:
        rows.append([Btn("📢 Join channel", url=REQUIRED_CHANNEL_URL)])
    rows.append([Btn("✅ I've joined — Check", callback_data="joincheck", style="success")])
    return Markup(rows)


async def announce_sale(bot, product_name: str, quantity: int, order_id: str, source: str = "Shop"):
    if not SALES_CHANNEL_ID:
        return
    try:
        await bot.send_message(
            chat_id=SALES_CHANNEL_ID,
            text=(f"🛒 <b>New order completed</b>\n\n"
                  f"📦 Product: <b>{esc(product_name)}</b>\n"
                  f"🔢 Quantity sold: <b>{int(quantity)}</b>\n"
                  f"🧾 Order: <code>{esc(order_id)}</code>\n"
                  f"🔗 Source: {esc(source)}"),
            parse_mode=HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("Could not post sale update to channel %s", SALES_CHANNEL_ID)


async def send_start_screen(message, user, context):
    created = db.ensure_user(user.id, user.username)
    language = db.get_user_language(user.id)
    if not language:
        await message.reply_text(tt("en", "welcome", shop=esc(SHOP_NAME)), reply_markup=kb_lang(), parse_mode=HTML)
        return
    await message.reply_text(tt(language, "menu_ready", shop=esc(SHOP_NAME)),
                             reply_markup=main_keyboard(), parse_mode=HTML)
    text, markup = shop_view(user.id, language)
    await message.reply_text(text, reply_markup=markup, parse_mode=HTML)


# ---------------------------------------------------------------- commands + menu buttons
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    clear_await(context)
    if REQUIRED_CHANNEL_ID and not await is_required_channel_member(context.bot, u.id):
        prompt = "📢 <b>Join our channel first</b>\n\nPlease join the channel and then tap <b>I've joined — Check</b> to use the bot."
        await update.effective_message.reply_text(prompt, reply_markup=join_channel_markup(), parse_mode=HTML)
        return
    created = db.ensure_user(u.id, u.username)
    if created and context.args and context.args[0].startswith("ref_"):
        referrer = db.user_by_ref_code(context.args[0][4:])
        if referrer:
            db.set_referrer(u.id, referrer)
    await send_start_screen(update.effective_message, u, context)


async def show_menu(update, context, key):
    """Open one of the main screens (works from the bottom menu and from inline buttons)."""
    user = update.effective_user
    uid = user.id
    language = lang(uid)
    clear_await(context)
    if key == "shop":
        text, markup = shop_view(uid, language)
    elif key == "funds":
        text, markup = wallet_view(uid, language)
    elif key == "profile":
        text, markup = profile_view(user, language)
    elif key == "orders":
        text, markup = orders_view(uid, language)
    elif key == "tx":
        text, markup = tx_view(uid, language)
    elif key == "ref":
        text, markup = referral_view(uid, context.bot.username, language)
    elif key == "support":
        text, markup = support_view(uid, language)
    elif key == "settings":
        text, markup = settings_view(uid, language)
    elif key == "api":
        try:
            text, markup = api_view(uid, language)
        except Exception as e:   # never stay silent: log it and tell the owner the real reason
            log.exception("Reseller API menu failed for user %s", uid)
            text = tt(language, "api_error")
            if is_owner(uid):
                text += "\n\nDebug (owner only): " + esc(f"{type(e).__name__}: {str(e)[:300]}")
            markup = Markup([main_menu_row(language)])
    else:
        return
    await respond(update, text, markup)


async def menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.touch_username(update.effective_user.id, update.effective_user.username)
    await show_menu(update, context, MENU_BY_LABEL[update.message.text])


# ---------------------------------------------------------------- callbacks
async def cb_lang(update, context, arg):
    q = update.callback_query
    db.set_user_language(q.from_user.id, arg, q.from_user.username)
    await q.answer()
    await edit(q, tt(arg, "lang_set"))
    await q.message.reply_text(tt(arg, "menu_ready", shop=esc(SHOP_NAME)), reply_markup=main_keyboard(), parse_mode=HTML)
    text, markup = shop_view(q.from_user.id, arg)
    await q.message.reply_text(text, reply_markup=markup, parse_mode=HTML)


async def cb_setlang(update, context, arg):
    q = update.callback_query
    db.set_user_language(q.from_user.id, arg, q.from_user.username)
    await q.answer()
    text, markup = settings_view(q.from_user.id, arg)
    await edit(q, text, markup)
    await q.message.reply_text(tt(arg, "lang_set"), reply_markup=main_keyboard())


async def cb_menu(update, context, arg):
    await update.callback_query.answer()
    await show_menu(update, context, arg)


async def cb_shop(update, context, arg):
    await update.callback_query.answer()
    await show_menu(update, context, "shop")


async def cb_fund(update, context, arg):
    await update.callback_query.answer()
    await show_menu(update, context, "funds")


async def cb_cat(update, context, arg):
    q = update.callback_query
    await q.answer()
    language = lang(q.from_user.id)
    category = next((c for c in db.list_categories() if cat_key(c) == arg), None)
    if category is None:
        await show_menu(update, context, "shop")
        return
    text, markup = shop_view(q.from_user.id, language, category)
    await edit(q, text, markup)


async def cb_prod(update, context, arg):
    q = update.callback_query
    language = lang(q.from_user.id)
    p = db.get_product(int(arg))
    if not p:
        await q.answer()
        return
    if not is_available(p):
        await q.answer(tt(language, "unavailable"), show_alert=True)
        return
    await q.answer()
    clear_await(context)
    text, markup = card_view(q.from_user.id, p, language)
    await edit(q, text, markup)


async def cb_buy(update, context, arg):
    q = update.callback_query
    uid = q.from_user.id
    language = lang(uid)
    p = db.get_product(int(arg))
    if not p or not is_available(p):
        await q.answer(tt(language, "unavailable"), show_alert=True)
        return
    await q.answer()
    if is_manual(p):
        prompt = (p["prompt"] or "").strip() or tt(language, "email_prompt_default")
        context.user_data["await"] = {"type": "email", "pid": p["id"]}
        markup = Markup([[Btn(tt(language, "btn_cancel"), callback_data="cx", style="danger")]])
        await edit(q, tt(language, "email_ask", prompt=esc(prompt)), markup)
    else:
        text, markup = qty_view(uid, p, language)
        await edit(q, text, markup)


async def cb_qty(update, context, arg):
    q = update.callback_query
    pid, qty = arg.split(":")
    language = lang(q.from_user.id)
    view = calc_view(q.from_user.id, int(pid), int(qty), language)
    if not view:
        await q.answer(tt(language, "out"), show_alert=True)
        return
    await q.answer()
    await edit(q, *view)


async def cb_qty_other(update, context, arg):
    q = update.callback_query
    await q.answer()
    context.user_data["await"] = {"type": "qty", "pid": int(arg)}
    await q.message.reply_text(tt(lang(q.from_user.id), "ask_qty"))


async def cb_bal(update, context, arg):
    """Pay an auto-delivery order from the wallet."""
    q = update.callback_query
    pid, qty = (int(x) for x in arg.split(":"))
    uid = q.from_user.id
    language = lang(uid)
    p = db.get_product(pid)
    if not p or is_manual(p) or qty <= 0:
        await q.answer(tt(language, "out"), show_alert=True)
        return
    unit = db.get_effective_price(uid, pid, p["price"])
    result = await asyncio.to_thread(db.buy_with_balance, uid, pid, qty, unit)
    if result == "balance":
        await q.answer()
        view = calc_view(uid, pid, qty, language)
        if view:
            await edit(q, *view)
        return
    if result == "stock":
        await q.answer(tt(language, "out"), show_alert=True)
        return
    await q.answer()
    order_id, items = result
    order = db.get_order(order_id)
    p_sold = db.get_product_any(pid)
    await announce_sale(context.bot, p_sold["name"] if p_sold else "Product", qty, order_id, "Telegram bot")
    markup = Markup([[Btn("🛍 " + tt(language, "browse"), callback_data="shop", style="success")]])
    await edit(q, delivery_text(language, order, items), markup)


async def cb_cancel_flow(update, context, arg):
    q = update.callback_query
    await q.answer()
    clear_await(context)
    await edit(q, tt(lang(q.from_user.id), "cancelled"))


async def cb_manual_ok(update, context, arg):
    """Confirm a made-to-order purchase: hold the money and queue the order."""
    q = update.callback_query
    uid = q.from_user.id
    language = lang(uid)
    pending = context.user_data.get("manual")
    if not pending:
        await q.answer()
        return
    p = db.get_product(pending["pid"])
    if not p or not is_manual(p):
        await q.answer(tt(language, "unavailable"), show_alert=True)
        return
    unit = db.get_effective_price(uid, p["id"], p["price"])
    result = db.buy_manual(uid, p["id"], unit, pending["contact"])
    if result == "balance":
        await q.answer()
        need = unit - float(db.get_account(uid)["balance"] or 0)
        markup = Markup([[Btn(tt(language, "btn_addfunds"), callback_data="fund", style="success")], main_menu_row(language)])
        await edit(q, tt(language, "need_more", need=money(max(need, 0))).strip(), markup)
        return
    await q.answer()
    context.user_data.pop("manual", None)
    delivery = (p["delivery"] or "").strip() or tt(language, "default_manual_delivery")
    await edit(q, tt(language, "placed", order=esc(result), name=esc(p["name"]), contact=esc(pending["contact"]),
                     delivery=esc(delivery)),
               Markup([main_menu_row(language)]))
    if MANUAL_ALERTS:
        u = q.from_user
        site = public_url()
        who = f"@{esc(u.username)}" if u.username else esc(u.full_name)
        await notify_admins(context,
                            f"🆕 <b>New order to deliver</b>\n\n🧾 <code>{esc(result)}</code>\n📦 {esc(p['name'])} · ${money(unit)}\n"
                            f"📧 <code>{esc(pending['contact'])}</code>\n👤 {who} ({u.id})\n\n"
                            + (f"Open: {esc(site)}/manual" if site else "Open the admin site → Pending orders."))


async def cb_search(update, context, arg):
    q = update.callback_query
    await q.answer()
    language = lang(q.from_user.id)
    context.user_data["await"] = {"type": "search"}
    await edit(q, tt(language, "search_ask"), Markup([[Btn(tt(language, "back"), callback_data="shop", style="primary")]]))


async def cb_fund_method(update, context, arg):
    q = update.callback_query
    language = lang(q.from_user.id)
    if arg == "upi":
        if db.upi_active_count() == 0:
            await q.answer(tt(language, "upi_none"), show_alert=True)
            return
        await q.answer()
        context.user_data["await"] = {"type": "amount", "method": "upi"}
        await edit(q, tt(language, "ask_amount_upi", rate=rate_text(), min=f"{MIN_DEPOSIT:g}", max=f"{MAX_DEPOSIT:g}"),
                   Markup([[Btn(tt(language, "back"), callback_data="fund", style="primary")]]))
        return
    if (arg == "bsc" and not BSC_ENABLED) or (arg == "bpay" and not BINANCE_ENABLED) or arg not in ("bsc", "bpay"):
        await q.answer(tt(language, "not_configured"), show_alert=True)
        return
    await q.answer()
    context.user_data["await"] = {"type": "amount", "method": arg}
    name = tt(language, "btn_bpay" if arg == "bpay" else "btn_crypto")
    await edit(q, tt(language, "ask_amount", method=esc(name), min=f"{MIN_DEPOSIT:g}", max=f"{MAX_DEPOSIT:g}"),
               Markup([[Btn(tt(language, "back"), callback_data="fund", style="primary")]]))


async def cb_dep_check(update, context, arg):
    q = update.callback_query
    uid = q.from_user.id
    language = lang(uid)
    dep = db.get_deposit(arg)
    if not dep or dep["user_id"] != uid:
        await q.answer()
        return
    if dep["status"] == "paid":
        await q.answer()
        return
    if dep["status"] == "failed":
        await q.answer()
        await q.message.reply_text(tt(language, "dep_expired"))
        return
    if datetime.now(timezone.utc) >= datetime.fromisoformat(dep["expires_at"]):
        db.fail_deposit(arg)
        binance.release(arg)
        await q.answer()
        await edit(q, tt(language, "dep_expired"))
        return
    try:
        status = await asyncio.to_thread(binance.query_order, arg)
    except Exception:
        await q.answer(tt(language, "check_down"), show_alert=True)
        return
    if status != "PAID":
        await q.answer(tt(language, "not_yet"), show_alert=True)
        return
    amount = db.credit_deposit(arg)
    await q.answer()
    if amount is None:
        return
    bal = db.get_account(uid)["balance"] or 0
    await edit(q, tt(language, "dep_paid", amount=money(amount), bal=money(bal)),
               Markup([[Btn("🛍 " + tt(language, "browse"), callback_data="shop", style="success")]]))


async def cb_dep_txhash(update, context, arg):
    q = update.callback_query
    uid = q.from_user.id
    language = lang(uid)
    dep = db.get_deposit(arg)
    if not dep or dep["user_id"] != uid or dep["method"] != "bsc" or dep["status"] != "pending":
        await q.answer()
        return
    await q.answer()
    context.user_data["await"] = {"type": "txhash", "dep": arg}
    await q.message.reply_text(tt(language, "txhash_ask", confirmations=3), parse_mode=HTML,
        reply_markup=Markup([[Btn(tt(language, "btn_cancel"), callback_data="cx", style="danger")]]))


async def cb_dep_cancel(update, context, arg):
    q = update.callback_query
    await q.answer()
    dep = db.get_deposit(arg)
    if not dep or dep["user_id"] != q.from_user.id:
        return
    if dep["status"] in ("pending", "upi_open"):
        db.fail_deposit(arg)
        binance.release(arg)
    await edit(q, tt(lang(q.from_user.id), "cancelled"), Markup([main_menu_row(lang(q.from_user.id))]))


async def cb_upi_paid(update, context, arg):
    """Customer tapped 'I've paid' -> ask for the UTR."""
    q = update.callback_query
    uid = q.from_user.id
    language = lang(uid)
    dep = db.get_deposit(arg)
    if not dep or dep["user_id"] != uid:
        await q.answer()
        return
    if dep["status"] != "upi_open":
        await q.answer(tt(language, "utr_closed"), show_alert=True)
        return
    await q.answer()
    context.user_data["await"] = {"type": "utr", "dep": arg}
    await q.message.reply_text(tt(language, "utr_ask", inr=inr_fmt(dep["inr_amount"])),
                               parse_mode=HTML,
                               reply_markup=Markup([[Btn(tt(language, "btn_cancel"), callback_data="cx", style="danger")]]))


def upi_site_hint() -> str:
    url = public_url()
    link = f'<a href="{url}/upipay">admin site → UPI payments</a>' if url else "admin site → UPI payments"
    return f"\n\n👉 Verify and approve on the {link} (not in the bot)."


def upi_admin_text(d, extra: str = "") -> str:
    user = db.find_users(str(d["user_id"]))
    uname = f"@{esc(user[0]['username'])} · " if user and user[0]["username"] else ""
    return (f"🏦 <b>New UPI payment waiting</b>\n\n🧾 <code>{esc(d['id'])}</code>\n"
            f"👤 {uname}<code>{d['user_id']}</code>\n"
            f"💵 <b>₹{inr_fmt(d['inr_amount'])}</b> → <b>${money(d['amount'])}</b> (₹{d['rate']:g}/USDT)\n"
            f"🔢 UTR: <code>{esc(d['utr'] or '-')}</code>\n📲 UPI: <code>{esc(d['upi_id'] or '-')}</code>{extra}")


async def cb_support(update, context, arg):
    q = update.callback_query
    await q.answer()
    language = lang(q.from_user.id)
    if arg == "new":
        markup = Markup([[Btn(tt(language, "k_pay"), callback_data="sup:k:pay")],
                         [Btn(tt(language, "k_gen"), callback_data="sup:k:gen")],
                         [Btn(tt(language, "k_bug"), callback_data="sup:k:bug")],
                         [Btn(tt(language, "btn_cancel"), callback_data="cx", style="danger")]])
        await edit(q, tt(language, "ticket_kind"), markup)
    elif arg.startswith("k:"):
        kind = arg[2:]
        if kind not in ("pay", "gen", "bug"):
            return
        context.user_data["await"] = {"type": "ticket", "kind": kind}
        await edit(q, tt(language, "ticket_describe"),
                   Markup([[Btn(tt(language, "btn_cancel"), callback_data="cx", style="danger")]]))


def _api_key_info(uid: int):
    """(html-safe key to show, note). Old keys made before key storage cannot be shown."""
    try:
        k = reseller_api.get_key_for_user(uid)
    except Exception:
        k = None
    note = "" if k else ("\n\nℹ️ Your key is hidden because it was created before this update. "
                         "Tap 🔄 New API key once and it will show here from then on.")
    return esc(k or "YOUR_API_KEY"), note


def api_view(uid: int, language: str):
    reseller_api.init_schema()   # safe to repeat
    r = reseller_api.get_reseller_by_user(uid)
    if not r:
        return tt(language, "api_intro"), Markup([[Btn(tt(language, "btn_api_new"), callback_data="api:new", style="success")],
                                                  [Btn(tt(language, "btn_api_docs"), callback_data="api:docs1")],
                                                  main_menu_row(language)])
    if not r["active"]:
        return tt(language, "api_disabled"), Markup([main_menu_row(language)])
    key_html, keynote = _api_key_info(uid)
    text = tt(language, "api_home", bal=money(reseller_api.wallet_balance(uid)), disc=f"{float(r['price_discount'] or 0):g}",
              url=esc(API_BASE_URL or "(ask the admin for the site link)"), key=key_html, keynote=keynote)
    return text, Markup([[Btn(tt(language, "btn_api_docs"), callback_data="api:docs1", style="primary")],
                         [Btn(MENU["funds"], callback_data="menu:funds", style="success"),
                          Btn(tt(language, "btn_api_regen"), callback_data="api:regen")],
                         main_menu_row(language)])


async def cb_api(update, context, arg):
    q = update.callback_query
    uid = q.from_user.id
    language = lang(uid)
    await q.answer()
    chat = q.message.chat
    try:
        reseller_api.init_schema()
        if arg == "home":
            text, markup = api_view(uid, language)
            await edit(q, text, markup)
        elif arg in ("docs1", "docs2", "docs3"):
            n = int(arg[-1])
            nav = []
            if n > 1:
                nav.append(Btn("◀️", callback_data=f"api:docs{n - 1}"))
            if n < 3:
                nav.append(Btn("▶️", callback_data=f"api:docs{n + 1}"))
            await edit(q, tt(language, f"api_docs_{n}", url=esc(API_BASE_URL or "https://YOUR-DOMAIN"), key=_api_key_info(uid)[0]),
                       Markup([nav, [Btn(tt(language, "btn_docs_dl"), callback_data="api:dl")],
                               [Btn(tt(language, "btn_api"), callback_data="api:home")]]))
        elif arg == "dl":
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "API_DOCS.md")
            with open(path, "r", encoding="utf-8") as fh:
                body = fh.read().replace("https://YOUR-DOMAIN", API_BASE_URL or "https://YOUR-DOMAIN")
            my_key = await asyncio.to_thread(reseller_api.get_key_for_user, uid)
            if my_key:
                body = body.replace("YOUR_KEY", my_key)
            bio = io.BytesIO(body.encode("utf-8"))
            bio.name = "API_DOCS.md"
            await chat.send_document(bio, caption="📄 Reseller API docs")
        elif arg == "new":
            key = await asyncio.to_thread(reseller_api.create_for_user, uid, q.from_user.full_name)
            if not key:   # already has an account
                text, markup = api_view(uid, language)
                await edit(q, text, markup)
                return
            # The key goes in its OWN message (no buttons), so nothing can edit it away.
            await chat.send_message(tt(language, "api_key_new", key=key), parse_mode=HTML)
            await edit(q, tt(language, "api_key_sent"), Markup([[Btn(tt(language, "btn_api"), callback_data="api:home", style="primary")]]))
        elif arg == "regen":
            await edit(q, tt(language, "api_regen_ask"),
                       Markup([[Btn(tt(language, "btn_api_regen_ok"), callback_data="api:regok", style="danger"),
                                Btn(tt(language, "btn_cancel"), callback_data="api:home")]]))
        elif arg == "regok":
            key = await asyncio.to_thread(reseller_api.regenerate_key, uid)
            if not key:
                text, markup = api_view(uid, language)
                await edit(q, text, markup)
                return
            await chat.send_message(tt(language, "api_key_new", key=key), parse_mode=HTML)
            await edit(q, tt(language, "api_key_replaced"), Markup([[Btn(tt(language, "btn_api"), callback_data="api:home", style="primary")]]))
    except Exception as e:
        log.exception("API screen failed (arg=%s, user=%s)", arg, uid)
        try:
            await chat.send_message(tt(language, "api_error"))
            if is_owner(uid):   # owner sees the real reason so it can be fixed quickly
                await chat.send_message(f"Debug (owner only): action={arg} {type(e).__name__}: {str(e)[:300]}")
        except Exception:
            pass


CALLBACKS = {
    "lang": cb_lang, "setlang": cb_setlang, "menu": cb_menu, "shop": cb_shop, "fund": cb_fund,
    "cat": cb_cat, "prod": cb_prod, "buy": cb_buy, "qty": cb_qty, "qtyo": cb_qty_other, "bal": cb_bal,
    "cx": cb_cancel_flow, "mok": cb_manual_ok, "search": cb_search, "fm": cb_fund_method,
    "fc": cb_dep_check, "ftx": cb_dep_txhash, "fx": cb_dep_cancel, "sup": cb_support,
    "fu": cb_upi_paid, "api": cb_api,
}


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, _, arg = (q.data or "").partition(":")
    if action == "joincheck":
        if REQUIRED_CHANNEL_ID and not await is_required_channel_member(context.bot, q.from_user.id):
            await q.answer("I can't see your channel membership yet. Join the channel first.", show_alert=True)
            return
        await q.answer("Membership verified!")
        await send_start_screen(q.message, q.from_user, context)
        return
    handler = CALLBACKS.get(action)
    if not handler:
        await q.answer()
        return
    db.touch_username(q.from_user.id, q.from_user.username)
    await handler(update, context, arg)


# ---------------------------------------------------------------- typed text / photos
async def create_deposit_flow(update, context, method: str, amount: float):
    uid = update.effective_user.id
    language = lang(uid)
    expires = datetime.now(timezone.utc) + timedelta(minutes=DEPOSIT_TIMEOUT_MINUTES)
    dep_id = db.create_deposit(uid, amount, method, expires)
    try:
        pay = await asyncio.to_thread(binance.create_order, dep_id, amount, f"{SHOP_NAME} deposit {dep_id}", method)
    except Exception:
        log.exception("Deposit creation failed (%s)", method)
        db.fail_deposit(dep_id)
        await update.message.reply_text(tt(language, "bpay_down" if (method == "bpay" and BSC_ENABLED) else "pay_down"))
        return
    if method == "bsc":
        markup = Markup([[Btn("🧾 Submit TXN Hash", callback_data=f"ftx:{dep_id}", style="success")],
                         [Btn(tt(language, "check"), callback_data=f"fc:{dep_id}")],
                         [Btn(tt(language, "cancel_dep"), callback_data=f"fx:{dep_id}", style="danger")]])
    else:
        markup = Markup([[Btn(tt(language, "check"), callback_data=f"fc:{dep_id}", style="success")],
                         [Btn(tt(language, "cancel_dep"), callback_data=f"fx:{dep_id}", style="danger")]])
    await update.message.reply_text(
        tt(language, "dep_created", id=esc(dep_id), credit=money(amount), amount=pay.get("amount_text", money(amount)),
           details=payment_details(method), minutes=DEPOSIT_TIMEOUT_MINUTES),
        reply_markup=markup, parse_mode=HTML)


async def create_upi_flow(update, context, amount: float):
    uid = update.effective_user.id
    language = lang(uid)
    acct = db.upi_pick()
    if not acct:
        await update.message.reply_text(tt(language, "upi_none"))
        return
    rate = db.inr_rate()
    inr_amount = round(amount * rate, 2)
    expires = datetime.now(timezone.utc) + timedelta(minutes=UPI_TIMEOUT_MINUTES)
    dep_id = db.create_upi_deposit(uid, amount, inr_amount, rate, acct["upi_id"], expires)
    inr_txt = inr_fmt(inr_amount)
    text = tt(language, "upi_created", id=esc(dep_id), credit=money(amount), inr=inr_txt, rate=f"{rate:g}",
              upi=esc(acct["upi_id"]), minutes=UPI_TIMEOUT_MINUTES)
    markup = Markup([[Btn(tt(language, "btn_upi_paid"), callback_data=f"fu:{dep_id}", style="success")],
                     [Btn(tt(language, "cancel_dep"), callback_data=f"fx:{dep_id}", style="danger")]])
    if acct["qr"]:
        try:
            await update.message.reply_photo(bytes(acct["qr"]), caption=text, reply_markup=markup, parse_mode=HTML)
            return
        except Exception:
            log.exception("could not send the UPI QR image")
    await update.message.reply_text(text, reply_markup=markup, parse_mode=HTML)


async def admin_ticket_reply(update, context, tid):
    ticket = db.get_ticket(tid)
    msg = update.message
    text = (msg.text or msg.caption or "").strip()
    if not ticket or not text:
        return
    db.add_ticket_msg(tid, "admin", text)
    try:
        await context.bot.send_message(ticket["user_id"],
                                       tt(lang(ticket["user_id"]), "ticket_reply", id=tid, text=esc(text)), parse_mode=HTML)
        await msg.reply_text(f"✅ Sent to the customer (ticket #{tid}).")
    except Exception:
        log.exception("ticket reply failed")
        await msg.reply_text("⚠️ Could not deliver (the customer may have blocked the bot).")


async def text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id
    # an admin replying to a ticket notification answers the customer
    if msg.reply_to_message and is_admin(uid):
        tid = db.ticket_from_link(msg.chat_id, msg.reply_to_message.message_id)
        if tid:
            await admin_ticket_reply(update, context, tid)
            return
    st = context.user_data.get("await")
    if not st:
        return
    language = lang(uid)
    text = (msg.text or "").strip()
    kind = st["type"]

    if kind == "qty":
        try:
            qty = int(text)
            if qty <= 0:
                raise ValueError
        except ValueError:
            await msg.reply_text(tt(language, "invalid"))
            return
        view = calc_view(uid, st["pid"], qty, language)
        if not view:
            await msg.reply_text(tt(language, "out"))
            return
        clear_await(context)
        await msg.reply_text(view[0], reply_markup=view[1], parse_mode=HTML)

    elif kind == "email":
        if not EMAIL_RE.match(text):
            await msg.reply_text(tt(language, "email_invalid"))
            return
        p = db.get_product(st["pid"])
        if not p or not is_manual(p):
            clear_await(context)
            return
        unit = db.get_effective_price(uid, p["id"], p["price"])
        delivery = (p["delivery"] or "").strip() or tt(language, "default_manual_delivery")
        context.user_data["manual"] = {"pid": p["id"], "contact": text}
        context.user_data.pop("await", None)
        markup = Markup([[Btn(tt(language, "btn_confirm", price=money(unit)), callback_data="mok", style="success")],
                         [Btn(tt(language, "btn_cancel"), callback_data="cx", style="danger")]])
        await msg.reply_text(tt(language, "confirm", name=esc(p["name"]), contact=esc(text), price=money(unit),
                                delivery=esc(delivery)), reply_markup=markup, parse_mode=HTML)

    elif kind == "amount":
        try:
            # Normalize user input to cents: .4 and .40 are the same amount.
            amount_decimal = Decimal(text.replace(",", ".").strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            amount = float(amount_decimal)
            if not amount_decimal.is_finite() or not (Decimal(str(MIN_DEPOSIT)) <= amount_decimal <= Decimal(str(MAX_DEPOSIT))):
                raise ValueError
        except (ValueError, InvalidOperation):
            await msg.reply_text(tt(language, "amount_invalid", min=f"{MIN_DEPOSIT:g}", max=f"{MAX_DEPOSIT:g}"))
            return
        clear_await(context)
        if st["method"] == "upi":
            await create_upi_flow(update, context, amount)
        else:
            await create_deposit_flow(update, context, st["method"], amount)

    elif kind == "txhash":
        tx_hash = re.sub(r"\s+", "", text).lower()
        result = await asyncio.to_thread(binance.submit_tx_hash, st["dep"], tx_hash)
        if result == "invalid":
            await msg.reply_text(tt(language, "txhash_invalid"))
            return
        if result == "duplicate":
            await msg.reply_text(tt(language, "txhash_dup"))
            return
        clear_await(context)
        if result == "paid":
            await msg.reply_text("This deposit is already paid.")
            return
        if result != "ok":
            await msg.reply_text(tt(language, "dep_expired"))
            return
        await msg.reply_text(tt(language, "txhash_wait"))
        # Check immediately once, then background polling continues automatically.
        try:
            status = await asyncio.to_thread(binance.query_order, st["dep"])
            if status == "PAID":
                amount = db.credit_deposit(st["dep"])
                if amount is not None:
                    bal = db.get_account(uid)["balance"] or 0
                    await msg.reply_text(tt(language, "dep_paid", amount=money(amount), bal=money(bal)), parse_mode=HTML)
        except Exception:
            log.exception("TX hash verification failed for deposit %s", st["dep"])

    elif kind == "utr":
        utr = re.sub(r"\s+", "", text)
        if not UTR_RE.match(utr):
            await msg.reply_text(tt(language, "utr_invalid"))
            return
        result = db.submit_utr(st["dep"], uid, utr)
        if result == "dup":
            await msg.reply_text(tt(language, "utr_dup"))
            return
        clear_await(context)
        if result != "ok":
            await msg.reply_text(tt(language, "utr_closed"))
            return
        d = db.get_deposit(st["dep"])
        await msg.reply_text(tt(language, "utr_sent", id=esc(d["id"]), utr=esc(utr), credit=money(d["amount"]),
                                inr=inr_fmt(d["inr_amount"])), parse_mode=HTML)
        await notify_admins(context, upi_admin_text(d, upi_site_hint()))

    elif kind == "search":
        clear_await(context)
        found = db.search_products(text)
        if not found:
            await msg.reply_text(tt(language, "search_none", q=esc(text)), parse_mode=HTML)
            return
        rows = [[product_button(uid, p)] for p in found]
        rows.append([Btn(tt(language, "back"), callback_data="shop", style="primary")])
        await msg.reply_text(tt(language, "search_head", q=esc(text)), reply_markup=Markup(rows), parse_mode=HTML)

    elif kind == "ticket":
        await open_ticket(update, context, st["kind"], text, None)


async def photo_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = context.user_data.get("await")
    if st and st["type"] == "ticket":
        msg = update.message
        await open_ticket(update, context, st["kind"], (msg.caption or "").strip(), msg.photo[-1].file_id)


async def open_ticket(update, context, kind: str, text: str, photo_id):
    user = update.effective_user
    language = lang(user.id)
    if not text and not photo_id:
        return
    clear_await(context)
    label = tt("en", {"pay": "k_pay", "gen": "k_gen", "bug": "k_bug"}[kind])
    tid = db.create_ticket(user.id, label, text or "(photo)", photo_id)
    await update.message.reply_text(tt(language, "ticket_created", id=tid), parse_mode=HTML)
    who = f"@{esc(user.username)}" if user.username else esc(user.full_name)
    await notify_admins(
        context,
        f"🎫 <b>Ticket #{tid}</b> · {esc(label)}\n👤 {who} ({user.id})\n\n{esc(text or '(photo)')}\n\n<i>Reply to this message to answer the customer.</i>",
        ticket_id=tid, photo_id=photo_id)


# ---------------------------------------------------------------- background jobs
async def poll_deposits(context: ContextTypes.DEFAULT_TYPE):
    """Auto-verify pending deposits so the customer does not have to press Check Payment."""
    now = datetime.now(timezone.utc)
    for d in db.pending_deposits():
        try:
            if now >= datetime.fromisoformat(d["expires_at"]):
                continue
            status = await asyncio.to_thread(binance.query_order, d["id"])
        except Exception:
            log.exception("auto deposit check failed for %s", d["id"])
            continue
        if status != "PAID":
            continue
        amount = db.credit_deposit(d["id"])
        if amount is None:
            continue
        language = lang(d["user_id"])
        bal = db.get_account(d["user_id"])["balance"] or 0
        try:
            await context.bot.send_message(d["user_id"], tt(language, "dep_paid", amount=money(amount), bal=money(bal)),
                                           parse_mode=HTML)
        except Exception:
            log.exception("could not tell %s about deposit %s", d["user_id"], d["id"])


async def expiry_job(context: ContextTypes.DEFAULT_TYPE):
    await poll_deposits(context)
    for d in db.expire_upi_open(datetime.now(timezone.utc)):
        try:
            await context.bot.send_message(d["user_id"], tt(lang(d["user_id"]), "utr_closed"))
        except Exception:
            pass
    for d in db.expire_deposits(datetime.now(timezone.utc)):
        binance.release(d["id"])
        try:
            await context.bot.send_message(d["user_id"], tt(lang(d["user_id"]), "dep_expired"))
        except Exception:
            pass


async def outbox_job(context: ContextTypes.DEFAULT_TYPE):
    """Deliver messages queued by the admin site. Each message is claimed first, so it is sent only once."""
    for m in db.outbox_claim():
        try:
            await context.bot.send_message(m["user_id"], m["text"], parse_mode=HTML, disable_web_page_preview=True)
        except Exception:
            log.exception("outbox message %s could not be sent", m["id"])
            db.outbox_failed(m["id"])


# ---------------------------------------------------------------- admin commands
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(tt(lang(update.effective_user.id), "admin_only"))
        return
    await update.message.reply_text(
        "ADMIN\n\n"
        "/addlink <product> <price> <item_link_or_code>\n"
        "/products\n"
        "Admins can only add/manage stock items.\n"
        "Descriptions, warranty, delivery time and made-to-order orders are managed on the admin site.\n\n"
        "OWNER ONLY\n"
        "/addadmin <telegram_id>\n"
        "/removeadmin <telegram_id>\n"
        "/setprice <product_id> <price>\n"
        "/setuserprice <username> <product_id> <price>\n"
        "/setref <percent>\n"
        "/setrate <inr per USDT>\n"
        "/paymentcenter\n"
        "/apiproducts - provider catalog (keys, cost, stock)\n"
        "/linkapi <product_id> <provider_key>\n"
        "/unlinkapi <product_id>\n"
        "/apibalance - provider wallet\n\n"
        "UPI accounts, QR codes, UPI verification and support replies are done on the admin site."
    )

async def addlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(tt(lang(update.effective_user.id), "admin_only"))
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
    await update.message.reply_text(tt(lang(update.effective_user.id), "link_added"))

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
        await update.message.reply_text(tt("en", "owner_only"))
        return
    await update.message.reply_text(
        "OWNER COMMANDS\n\n"
        "/addadmin <telegram_id>\n/removeadmin <telegram_id>\n"
        "/setprice <product_id> <price>\n"
        "/setuserprice <username> <product_id> <price>\n"
        "/setref <percent>\n"
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


# ---------------------------------------------------------------- backup / restore (owner only)
def _make_backup_file() -> str:
    import sqlite3, tempfile
    tmp = os.path.join(tempfile.gettempdir(), "shop_backup.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    dst.close(); src.close()
    return tmp


async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /backup - send a safe copy of the database to this chat."""
    if not is_owner(update.effective_user.id):
        return
    try:
        path = await asyncio.to_thread(_make_backup_file)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        with open(path, "rb") as fh:
            await update.message.reply_document(fh, filename=f"shop_{stamp}.db",
                                                caption="✅ Backup. Keep this file safe (it contains user data and API key hashes).")
    except Exception as e:
        log.exception("backup failed")
        await update.message.reply_text(f"Backup failed: {str(e)[:300]}")


async def backup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        path = await asyncio.to_thread(_make_backup_file)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        with open(path, "rb") as fh:
            await context.bot.send_document(OWNER_ID, fh, filename=f"shop_{stamp}.db", caption="🗂 Daily backup")
    except Exception:
        log.exception("daily backup failed")


async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /restore - then send the backup .db file."""
    if not is_owner(update.effective_user.id):
        return
    context.bot_data["await_restore"] = True
    await update.message.reply_text("♻️ Restore mode ON.\nNow send your backup file (.db) as a document.\n"
                                    "⚠️ It will REPLACE the current database. A copy of the current one is kept as .pre_restore.\n"
                                    "Send /cancelrestore to cancel.")


async def cancel_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    context.bot_data.pop("await_restore", None)
    await update.message.reply_text("Restore cancelled.")


async def restore_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import sqlite3, shutil, tempfile
    if not is_owner(update.effective_user.id) or not context.bot_data.get("await_restore"):
        return
    doc = update.message.document
    tmp = os.path.join(tempfile.gettempdir(), "restore_in.db")
    try:
        f = await context.bot.get_file(doc.file_id)
        await f.download_to_drive(tmp)
        chk = sqlite3.connect(tmp)
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        chk.close()
        if ok != "ok" or not {"users", "products", "stock"} <= tables:
            await update.message.reply_text("❌ This file is not a valid shop database. Nothing changed.")
            return
        d = os.path.dirname(os.path.abspath(DB_PATH))
        os.makedirs(d, exist_ok=True)
        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, DB_PATH + ".pre_restore")
        for ext in ("-wal", "-shm"):
            if os.path.exists(DB_PATH + ext):
                os.remove(DB_PATH + ext)
        shutil.copy2(tmp, DB_PATH + ".new")
        os.replace(DB_PATH + ".new", DB_PATH)
        context.bot_data.pop("await_restore", None)
        await update.message.reply_text("✅ Database restored. Restarting the bot now (if it does not come back in a minute, press Restart in Railway).")
        await asyncio.sleep(1)
        os._exit(1)   # non-zero exit so Railway restarts the service with the restored data
    except Exception as e:
        log.exception("restore failed")
        await update.message.reply_text(f"❌ Restore failed: {str(e)[:300]}")

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

async def setrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /setrate <inr> - how many INR one USDT costs (shown next to every USDT price)."""
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text(f"1 USDT = ₹{db.inr_rate():g}. Change it with /setrate <inr>, e.g. /setrate 95")
        return
    try:
        rate = float(context.args[0].replace(",", "."))
        if not (1 <= rate <= 100000):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Send a number, e.g. /setrate 95")
        return
    db.set_inr_rate(rate)
    await update.message.reply_text(f"✅ Rate set: 1 USDT = ₹{rate:g}. Requests already open keep their old rate.")


async def setref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /setref <percent> - referral commission paid on everything a referred user buys."""
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text(f"Referral commission is {db.referral_percent():g}%. Change it with /setref <percent>.")
        return
    try:
        pct = float(context.args[0].replace("%", "").replace(",", "."))
        if not (0 <= pct <= 100):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Send a percentage between 0 and 100, e.g. /setref 3")
        return
    db.set_setting("referral_percent", pct)
    await update.message.reply_text(f"✅ Referral commission set to {pct:g}%.")

async def apiproducts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: list provider products with their keys, cost and stock."""
    if not is_owner(update.effective_user.id):
        return
    if not provider.ENABLED:
        await update.message.reply_text("Set CUPPON_API_KEY in the environment first.")
        return
    try:
        items = await asyncio.to_thread(provider.catalog)
    except Exception as e:
        await update.message.reply_text(f"Provider error: {str(e)[:200]}")
        return
    linked = {m["provider_key"]: pid for pid, m in db._links().items()}
    lines = []
    for p in items:
        tag = f" -> #{linked[p['key']]}" if p["key"] in linked else ""
        lines.append(f"{p['key']} | {p['name']} | cost ${float(p['price_usdt_from']):.2f} | stock {p['stock']}{tag}")
    await update.message.reply_text("\n".join(lines) or "Provider has no products.")


async def linkapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /linkapi <product_id> <provider_key>"""
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) != 2 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /linkapi <product_id> <provider_key>   (keys: /apiproducts)")
        return
    try:
        await asyncio.to_thread(db.link_api, int(context.args[0]), context.args[1])
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)[:300]}")
        return
    await update.message.reply_text("✅ Linked. Stock now = your own stock + provider stock. Your price stays as set on the site.")


async def unlinkapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or len(context.args) != 1 or not context.args[0].isdigit():
        return
    db.unlink_api(int(context.args[0]))
    await update.message.reply_text("✅ Unlinked.")


async def apibalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    try:
        bal = await asyncio.to_thread(provider.wallet)
    except Exception as e:
        await update.message.reply_text(f"Provider error: {str(e)[:200]}")
        return
    await update.message.reply_text(f"Provider wallet: {bal:.4f} USDT")


async def provider_job(context: ContextTypes.DEFAULT_TYPE):
    """Every 2 min: refresh provider stock/cost, warn admins once per new problem."""
    if not provider.ENABLED:
        return
    seen = context.bot_data.setdefault("provider_alerts", set())
    try:
        alerts = await asyncio.to_thread(db.sync)
        stuck = await asyncio.to_thread(db.stuck_provider_orders)
        alerts += [f"⚠️ Provider order {r['idem']} bought {r['qty']} item(s) but they are not in stock yet. "
                   f"Check table provider_orders (items are saved there)." for r in stuck]
    except Exception as e:
        log.warning("provider sync failed: %s", e)
        return
    for a in alerts:
        if a not in seen:
            seen.add(a)
            await notify_admins(context, a)
    seen.intersection_update(alerts)     # a fixed problem can alert again if it returns


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
    app.add_handler(CommandHandler("setref", setref))
    app.add_handler(CommandHandler("setrate", setrate))
    app.add_handler(CommandHandler("paymentcenter", paymentcenter))
    app.add_handler(CommandHandler("reopen", reopen))
    app.add_handler(CommandHandler("apitest", apitest))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("restore", restore_cmd))
    app.add_handler(CommandHandler("cancelrestore", cancel_restore))
    app.add_handler(MessageHandler(filters.Document.ALL, restore_doc))
    app.add_handler(CommandHandler("apiproducts", apiproducts))
    app.add_handler(CommandHandler("linkapi", linkapi))
    app.add_handler(CommandHandler("unlinkapi", unlinkapi))
    app.add_handler(CommandHandler("apibalance", apibalance))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Regex(MENU_RE), menu_text))
    app.add_handler(MessageHandler(filters.PHOTO, photo_msg))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))
    app.job_queue.run_repeating(expiry_job, interval=20, first=10)
    app.job_queue.run_repeating(outbox_job, interval=10, first=5)
    app.job_queue.run_repeating(provider_job, interval=120, first=15)
    app.job_queue.run_repeating(backup_job, interval=86400, first=600)
    app.run_polling()

if __name__ == "__main__":
    main()
