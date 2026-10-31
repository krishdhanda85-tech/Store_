import asyncio
import html
import logging
import io
import os
import re
import time
import zlib
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from telegram import (Update, InlineKeyboardButton as _Btn, InlineKeyboardMarkup as Markup,
                      ReplyKeyboardMarkup)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application, ApplicationHandlerStop, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, TypeHandler, filters
)

import fampay_verify
import growth_bot
import provider
import textedit
import reseller_api
import tenancy
from binance_pay import public_url

DEPOSIT_TIMEOUT_MINUTES = int(os.getenv("DEPOSIT_TIMEOUT_MINUTES", os.getenv("PAYMENT_TIMEOUT_MINUTES", "15")))
UPI_TIMEOUT_MINUTES = int(os.getenv("UPI_TIMEOUT_MINUTES", "30"))   # time to pay + send the UTR
UTR_RE = re.compile(r"^\d{12}$")
MIN_DEPOSIT = float(os.getenv("MIN_DEPOSIT", "0.1"))
MAX_DEPOSIT = float(os.getenv("MAX_DEPOSIT", "100"))
# Public link of the admin/API website, shown in the bot's "🔑 API" screen.
API_BASE_URL = (os.getenv("API_BASE_URL") or os.getenv("PUBLIC_URL")
                or (("https://" + os.getenv("RAILWAY_PUBLIC_DOMAIN")) if os.getenv("RAILWAY_PUBLIC_DOMAIN") else "")).strip().rstrip("/")
# Optional mandatory channel join + order announcements. Bot must be admin in the channel.
MANUAL_ALERTS = os.getenv("MANUAL_ALERTS", "1") != "0"   # tell the owner about new made-to-order orders
TIERS_ENV = os.getenv("TIERS", "")                        # e.g. "10:Bronze,50:Silver,200:Gold"; empty = no tiers
HTML = ParseMode.HTML
QTY_PRESETS = (1, 5, 10, 20, 30, 50, 100)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("digital-shop")

# ---- the shop (seller) that is handling the current update. `db` and `binance` always point at it.
db = tenancy.db
binance = tenancy.binance


def owner_id():
    return tenancy.cur().owner_id


def shop_name():
    return tenancy.cur().name


def required_channel():
    return tenancy.cur().required_channel_id


def required_channel_url():
    return tenancy.cur().required_channel_url


def sales_channel():
    return tenancy.cur().sales_channel_id



# ---- values the admin can change on the website (Settings page); env vars are only the defaults
def min_deposit():
    return db.get_setting("min_deposit", MIN_DEPOSIT)


def max_deposit():
    return db.get_setting("max_deposit", MAX_DEPOSIT)


def deposit_timeout():
    return db.get_setting("deposit_timeout_minutes", DEPOSIT_TIMEOUT_MINUTES)


def upi_timeout():
    return db.get_setting("upi_timeout_minutes", UPI_TIMEOUT_MINUTES)


def manual_alerts():
    return bool(db.get_setting("manual_alerts", 1 if MANUAL_ALERTS else 0))


def welcome_text(language):
    """Greeting under the bottom menu: the admin's own text (Settings page) or the built-in one."""
    custom = (db.get_setting("welcome_text", "") or "").strip()
    if custom:
        return esc(custom).replace("{shop}", esc(shop_name()))
    return tt(language, "menu_ready", shop=esc(shop_name()))


def shop_closed_for(uid):
    """True when the admin closed the shop (Settings page). Owner and admins can still use the bot."""
    return (not db.get_setting("shop_open", 1)) and not is_admin(uid)


def closed_text():
    custom = (db.get_setting("closed_message", "") or "").strip()
    if custom:
        return esc(custom)
    return "🔧 <b>The shop is closed right now.</b>\n\nWe will be back soon. Please try again later."

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
        "qty_head": "🛒 <b>{name}</b>\n\n📦 Status: <b>{status}</b>\n🔢 Quantity: <b>1–{maxq}</b>\n💵 Price each: <b>${unit}</b> (₹{inr})\n👛 Wallet balance: <b>${bal}</b>\n\n<i>Select a quantity:</i>",
        "terms": "\n\n📋 <b>Terms of Service</b>\n<blockquote>{terms}</blockquote>\n\nTap <b>I Agree — Pay Now</b> to confirm.",
        "btn_agree": "✅ I Agree — Pay Now · ${total}",
        "warranty": "🛡 <b>Warranty</b>\n\n{text}",
        "warranty_default": "Every product has its own warranty, shown on the product page before you pay.\nIf something does not work, open Support and send your order number — we will replace or refund it.",
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
        "upi_created": "🏦 <b>Pay by UPI</b>\n\n🧾 ID: <code>{id}</code>\n💰 You'll receive: <b>${credit}</b>\n💵 Pay: <b>₹{inr}</b> (₹{rate} per USDT)\n\n📲 UPI ID:\n<code>{upi}</code>\n\n1️⃣ Pay exactly <b>₹{inr}</b>\n2️⃣ Tap <b>I've paid</b> and send the 12-digit <b>UTR</b>\n\n⏳ Usually approved within a minute. If it takes longer, an admin checks it — up to <b>30 min</b>. Send the UTR within {minutes} min.",
        "btn_upi_paid": "✅ I've paid — send UTR",
        "utr_ask": "🔢 Send the <b>12-digit UTR</b> (UPI reference number) of your payment of <b>₹{inr}</b>:",
        "utr_invalid": "UTR must be exactly 12 digits. Check your UPI app and send it again.",
        "utr_dup": "⚠️ This UTR was already submitted. If you think this is a mistake, open a Support ticket.",
        "utr_closed": "⌛ This UPI request is closed. Open Add Funds to start again.",
        "utr_sent": "✅ <b>UTR received</b>\n\n🧾 ID: <code>{id}</code>\n🔢 UTR: <code>{utr}</code>\n💵 ₹{inr} → ${credit}\n\n⏳ We're verifying it. Usually your balance is added within a minute (at most <b>30 min</b>) — we'll message you here.",
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
        "api_docs_1": "📖 <b>API Docs · 1/3 · Quick start</b>\n\n1️⃣ Create your key: <b>🔑 Reseller API → Create API key</b>\n2️⃣ Top up your wallet with <b>Add Funds</b>\n3️⃣ Paste this in your panel or bot:\n\n<b>API URL</b>\n<code>{url}/api/v1/index.php</code>\n<b>API key</b>\n<code>{key}</code> (starts with rs_)\n\n4️⃣ Test it in your browser:\n<code>{url}/api/v1/index.php?action=balance&amp;api_key={key}</code>\n\nYou can send the key as header <code>Authorization: Bearer {key}</code>, header <code>X-API-Key</code>, or field <code>api_key</code>.\n\nUse https:// and the full path, not only the domain.",
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
    "es": {
        'welcome': '👋 <b>¡Bienvenido a {shop}!</b>\n\n✨ Productos digitales premium a excelentes precios.\n⚡ Entrega rápida • 🔒 Pago seguro • 🎧 Atención al cliente\n\n🌐 Elige tu idioma 👇',
        'menu_ready': '👋 <b>¡Bienvenido a {shop}!</b>\n\n✨ Tu tienda digital de suscripciones y servicios premium.\n⚡ Entrega rápida • 💳 Recargas fáciles • 🎧 Soporte cuando lo necesites.\n\n👇 <b>¡Elige una opción para comenzar!</b>',
        'lang_set': '✅ Idioma guardado.',
        'shop_head': '🛍️ <b>Bienvenido a la tienda</b>\n\n<blockquote>🟢 Productos disponibles: <b>{avail}</b>\n📦 Productos totales: <b>{total}</b>\n⚡ Entrega digital rápida</blockquote>\n\n🔷 Elige una categoría o explora los productos ⬇️',
        'no_products': '🛍 <b>{shop}</b>\n\nNo hay productos disponibles en este momento.',
        'cat_head': '📂 <b>{cat}</b>\n\n🟢 Selecciona un producto ⬇️',
        'btn_search': '🔎 Buscar',
        'search_ask': '🔎 <b>Buscar</b>\n\nEscribe el nombre del producto:',
        'search_head': '🔎 <b>Resultados para «{q}»</b>',
        'search_none': '🔎 No se encontraron resultados para «{q}».',
        'sold_out': 'Agotado',
        'in_stock': 'Disponible',
        'made_to_order': 'Bajo pedido',
        'settings': '⚙️ <b>Configuración</b>\n\n🌐 Idioma: <b>{lang}</b>',
        'admin_only': 'Se requieren permisos de administrador.',
        'owner_only': 'Se requieren permisos del propietario.',
        'help': 'Usa el menú de abajo o envía /start.',
        'shop_closed': '🔧 <b>La tienda está cerrada en este momento.</b>\n\nVolveremos pronto. Inténtalo de nuevo más tarde.',
    },
}
LANG_NAMES = {"en": "English", "zh": "中文", "es": "Español"}


_TXT_CACHE = {}          # tenant id -> (loaded_at, {"lang:key": custom text})


def _text_overrides() -> dict:
    """Custom texts the owner saved with /texts (cached 30 s; /texts clears the cache on every change)."""
    try:
        tid = tenancy.cur().id
        c = _TXT_CACHE.get(tid)
        now = time.time()
        if c is None or now - c[0] > 30:
            conn = db.conn()
            try:
                rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'txt:%'").fetchall()
            finally:
                conn.close()
            c = (now, {r[0][4:]: r[1] for r in rows})
            _TXT_CACHE[tid] = c
        return c[1]
    except Exception:
        return {}


def tt(language: str, key: str, /, **kw) -> str:
    ov = _text_overrides()
    # order: custom text of this language, built-in of this language, custom English, built-in English
    for custom, s in ((True, ov.get(f"{language}:{key}")), (False, TEXT.get(language, {}).get(key)),
                      (True, ov.get(f"en:{key}")), (False, TEXT["en"].get(key))):
        if s is None:
            continue
        try:
            return s.format(**kw) if kw else s
        except Exception:
            if not custom:
                raise                     # a built-in text must format; a broken custom text is just skipped
    raise KeyError(key)


BUTTON_ICONS = os.getenv("BUTTON_ICONS", "1") != "0"   # premium emoji inside buttons (needs Telegram Premium on the bot owner)


def Btn(text, callback_data=None, style=None, url=None, icon=None):
    """Inline button with optional colour ('primary' blue, 'success' green, 'danger' red) and an
    optional premium custom emoji shown before the text (icon = custom emoji ID).
    Colours/icons need a Telegram app released after 9 Feb 2026; older apps show a normal button."""
    kwargs = {"callback_data": callback_data} if url is None else {"url": url}
    extra = {}
    if not icon and BUTTON_ICONS:
        try:
            text, icon = ui_button_icon(text)      # rules the admin set with /emoji
        except Exception:
            pass
    if icon and BUTTON_ICONS and str(icon).isdigit():
        extra["icon_custom_emoji_id"] = str(icon)
    if style:
        try:
            return _Btn(text, style=style, api_kwargs=extra or None, **kwargs)
        except TypeError:  # older python-telegram-bot without the style field
            extra["style"] = style
    return _Btn(text, api_kwargs=extra or None, **kwargs)


def strip_icons(markup):
    """Same keyboard without premium button icons (used when Telegram refuses them)."""
    if not isinstance(markup, Markup):
        return markup
    rows, changed = [], False
    for row in markup.inline_keyboard:
        new_row = []
        for b in row:
            d = b.to_dict()
            if d.pop("icon_custom_emoji_id", None):
                changed = True
                b = _Btn.de_json(d, None)
            new_row.append(b)
        rows.append(new_row)
    return Markup(rows) if changed else markup


# ---------------------------------------------------------------- small helpers
MENU = {
    "shop": "🛍 Products", "support": "🎧 Support", "funds": "👛 Wallet", "api": "🔑 API",
    "warranty": "🛡 Warranty",
}
# labels of the old bottom menu still open the right screen for customers who did not send /start yet
LEGACY_MENU = {
    "🛍 Shop": "shop", "💳 Add Funds": "funds", "👤 Profile": "profile", "🧾 My Orders": "orders",
    "🎁 Referral": "ref", "🎧 Support": "support", "⚙️ Settings": "settings", "🔑 Reseller API": "api",
}
MENU_BY_LABEL = {v: k for k, v in MENU.items()}
MENU_BY_LABEL.update(LEGACY_MENU)
MENU_RE = "^(" + "|".join(re.escape(v) for v in MENU_BY_LABEL) + ")$"


def main_keyboard():
    return ReplyKeyboardMarkup(
        [[MENU["shop"], MENU["support"]], [MENU["funds"], MENU["api"]], [MENU["warranty"]]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="Choose a quick menu b…")


def lang(user_id: int) -> str:
    return db.get_user_language(user_id) or "en"


def is_owner(user_id: int) -> bool:
    return user_id == owner_id()


def is_admin(user_id: int) -> bool:
    return is_owner(user_id) or db.is_admin(user_id)


def admin_ids():
    c = db.conn()
    ids = {r["user_id"] for r in c.execute("SELECT user_id FROM admins").fetchall()}
    c.close()
    if owner_id():
        ids.add(owner_id())
    return ids


def esc(x) -> str:
    return html.escape(str(x))


# Telegram custom-emoji IDs supplied by the shop owner. Railway variables can override them.
CUSTOM_EMOJI_IDS = {
    "netflix": os.getenv("EMOJI_NETFLIX_ID", "5069081919279334693").strip(),
    "crunchyroll": os.getenv("EMOJI_CRUNCHYROLL_ID", "5091632048066528487").strip(),
    "spotify": os.getenv("EMOJI_SPOTIFY_ID", "5068818526114939895").strip(),
    "apple_music": os.getenv("EMOJI_APPLE_MUSIC_ID", "5287380220578372452").strip(),
    "duolingo": os.getenv("EMOJI_DUOLINGO_ID", "5069087043175318384").strip(),
    "hbo_max": os.getenv("EMOJI_HBO_MAX_ID", "5298588152485651370").strip(),
    "adobe_express": os.getenv("EMOJI_ADOBE_EXPRESS_ID", "5278589573574703984").strip(),
    "muse_ai": os.getenv("EMOJI_MUSE_AI_ID", "5170554095060124363").strip(),
    "office_365": os.getenv("EMOJI_OFFICE_365_ID", "5267375637303145924").strip(),
    "ilovepdf": os.getenv("EMOJI_ILOVEPDF_ID", "5793968666794005095").strip(),
    "capcut": os.getenv("EMOJI_CAPCUT_ID", "6008121985349979345").strip(),
    "instagram": os.getenv("EMOJI_INSTAGRAM_ID", "5312476345849094587").strip(),
    "canva": os.getenv("EMOJI_CANVA_ID", "5076038705441932295").strip(),
    "nordvpn": os.getenv("EMOJI_NORDVPN_ID", "5460709303755221342").strip(),
    "linkedin": os.getenv("EMOJI_LINKEDIN_ID", "5866084676682125789").strip(),
    "amazon_prime": os.getenv("EMOJI_AMAZON_PRIME_ID", "4947457194304996489").strip(),
    "windows": os.getenv("EMOJI_WINDOWS_ID", "5933550168996581523").strip(),
    "coursera": os.getenv("EMOJI_COURSERA_ID", "5924826149820567437").strip(),
    "protonvpn": os.getenv("EMOJI_PROTONVPN_ID", "5848177124074066676").strip(),
    "steam": os.getenv("EMOJI_STEAM_ID", "5453902695419104908").strip(),
    "gemini": os.getenv("EMOJI_GEMINI_ID", "5262838511161076891").strip(),
    # HMA VPN ID not provided yet.
    "hma": os.getenv("EMOJI_HMA_ID", "").strip(),
}


EMOJI_ALIASES = (
    (("crunchyroll", "crunchy roll", "crunchy"), "crunchyroll"),
    (("netflix",), "netflix"),
    (("spotify",), "spotify"),
    (("apple music",), "apple_music"),
    (("duolingo",), "duolingo"),
    (("hbo max", "max subscription", "max account"), "hbo_max"),
    (("adobe express",), "adobe_express"),
    (("muse ai",), "muse_ai"),
    (("microsoft office", "office 365"), "office_365"),
    (("hma", "hidemyass"), "hma"),
    (("ilovepdf", "i love pdf"), "ilovepdf"),
    (("capcut",), "capcut"),
    (("instagram",), "instagram"),
    (("canva",), "canva"),
    (("nordvpn", "nord vpn"), "nordvpn"),
    (("linkedin",), "linkedin"),
    (("amazon prime", "prime video", "prime"), "amazon_prime"),
    (("windows 11",), "windows"),
    (("coursera",), "coursera"),
    (("proton vpn", "protonvpn"), "protonvpn"),
    (("steam",), "steam"),
    (("gemini",), "gemini"),
)


def product_icon_id(name) -> str:
    """Telegram custom-emoji ID for a product name ('' when none is configured)."""
    key = str(name or "").lower().replace("+", " plus ").replace("&", " and ")
    try:                                   # rules the admin added on the website (Premium emojis page)
        import json
        for r in json.loads(db.get_setting("custom_emojis", "[]") or "[]"):
            if r.get("k") and r["k"] in key and str(r.get("id", "")).isdigit():
                return str(r["id"])
    except Exception:
        pass
    for terms, emoji_key in EMOJI_ALIASES:
        if any(term in key for term in terms):
            emoji_id = CUSTOM_EMOJI_IDS.get(emoji_key, "")
            return emoji_id if emoji_id.isdigit() else ""
    return ""


def product_name_html(name) -> str:
    """Return a safely escaped product name prefixed with its configured Telegram custom emoji."""
    raw = str(name or "")
    emoji_id = product_icon_id(raw)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">🔹</tg-emoji> {esc(raw)}'
    return esc(raw)


# ---------------------------------------------------------------- in-bot editing (owner/admin)
# Premium emojis typed by an admin are kept as HTML next to the plain text (plain text stays in the
# products table, so buttons, the website and old orders keep working).
import json as _json


def _jload(key, default):
    try:
        data = _json.loads(db.get_setting(key, "") or "")
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def fmt_get(pid, field, plain):
    """HTML version of a product text (with premium emojis / formatting) or None when it is stale."""
    e = _jload("fmt_text", {}).get(f"{pid}:{field}")
    if isinstance(e, dict) and e.get("p") == (plain or "").strip() and e.get("h"):
        return e["h"]
    return None


def fmt_set(pid, field, plain, html_text):
    data = _jload("fmt_text", {})
    if html_text and html_text != html.escape(plain, quote=False):
        data[f"{pid}:{field}"] = {"p": plain, "h": html_text}
    else:
        data.pop(f"{pid}:{field}", None)
    db.set_setting("fmt_text", _json.dumps(data))


def field_html(p, field, plain=None):
    plain = (p[field] if plain is None else plain) or ""
    return fmt_get(p["id"], field, plain) or esc(plain.strip())


def product_icon_for(p) -> str:
    """Button emoji of a product: the one set from the bot first, then the keyword rules."""
    own = str(_jload("prod_icons", {}).get(str(p["id"]), ""))
    return own if own.isdigit() else product_icon_id(p["name"])


def card_name_html(p) -> str:
    """Product name for messages: admin-edited HTML (premium emojis inside) or the icon + plain name."""
    h = fmt_get(p["id"], "name", p["name"])
    if h:
        return h
    icon = product_icon_for(p)
    raw = str(p["name"] or "")
    if icon:
        return f'<tg-emoji emoji-id="{icon}">🔹</tg-emoji> {esc(raw)}'
    return esc(raw)


def entities_to_html(text, entities):
    """Telegram message text + entities -> Telegram HTML (keeps premium emojis, bold, italic, links...)."""
    opens, closes = {}, {}
    simple = {"bold": "b", "italic": "i", "underline": "u", "strikethrough": "s", "code": "code",
              "pre": "pre", "spoiler": "tg-spoiler", "blockquote": "blockquote"}
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


def first_custom_emoji_id(msg) -> str:
    for e in (msg.entities or []):
        if e.type == "custom_emoji" and e.custom_emoji_id:
            return str(e.custom_emoji_id)
    t = (msg.text or "").strip()
    return t if t.isdigit() and 5 <= len(t) <= 30 else ""


def _cat_rule_key(category: str) -> str:
    k = category.lower().replace("+", " plus ").replace("&", " and ")
    return " ".join(k.split())[:40]


def set_category_icon(category: str, emoji_id: str):
    k = _cat_rule_key(category)
    rules = [r for r in _jload("custom_emojis", []) if isinstance(r, dict) and r.get("k") != k]
    if emoji_id:
        rules.append({"k": k, "id": emoji_id})
    db.set_setting("custom_emojis", _json.dumps(rules))



# ---------------------------------------------------------------- global emoji rules (/emoji)
# emoji_map : {"👤": id}        every 👤 in messages becomes the premium emoji; a button that starts
#                                with 👤 gets it as its icon
# btn_icons : {"binance pay": id}  the button with this text gets the premium emoji as icon
def _norm_emoji(s: str) -> str:
    return s.replace("\ufe0f", "").strip()


def ui_button_icon(label: str):
    """(new_label, icon_id) for a button label, applying the admin's /emoji rules."""
    bmap = _jload("btn_icons", {})
    emap = _jload("emoji_map", {})
    if not bmap and not emap or not label:
        return label, None
    plain = re.sub(r"^[^\w]+", "", label).strip()
    squash = lambda t: re.sub(r"[\W_]+", "", t.lower())      # ignore spaces, dots, dashes, brackets ...
    lab = squash(plain)
    items = [(k, str(v)) for k, v in bmap.items() if str(v).isdigit() and squash(k)]
    for k, v in items:                                            # 1) same text
        if squash(k) == lab:
            return plain or label, v
    low = plain.lower()
    for k, v in sorted(items, key=lambda kv: -len(kv[0])):        # 2) label starts with the rule's words
        kk = squash(k)
        if (len(kk) >= 8 and lab.startswith(kk)) or re.match(re.escape(k.lower()) + r"(\W|$)", low):
            return plain or label, v
    norm = _norm_emoji(label)
    for k in sorted(emap, key=len, reverse=True):
        if k and norm.startswith(k) and str(emap[k]).isdigit():
            rest = norm[len(k):].strip()
            return rest or label, str(emap[k])
    return label, None


_PROTECTED = re.compile(r"(<tg-emoji.*?</tg-emoji>|<code>.*?</code>|<pre>.*?</pre>)", re.S)


def apply_emoji_map(text: str) -> str:
    emap = {k: str(v) for k, v in _jload("emoji_map", {}).items() if k and str(v).isdigit()}
    if not emap:
        return text
    pat = re.compile("(" + "|".join(re.escape(k) for k in sorted(emap, key=len, reverse=True)) + ")\ufe0f?")
    parts = _PROTECTED.split(text)
    for i in range(0, len(parts), 2):          # odd parts are protected (code / existing premium emoji)
        parts[i] = pat.sub(lambda m: f'<tg-emoji emoji-id="{emap[m.group(1)]}">{m.group(1)}</tg-emoji>', parts[i])
    return "".join(parts)


ALL_BUTTONS = [
    "Binance Pay", "Crypto · USDT BEP-20 (Instant)", "UPI (30 min)", "Profile", "My Orders", "Referral",
    "Settings", "Support", "API", "Main menu", "Back", "Search", "Add Funds", "Buy", "Other quantity",
    "I Agree — Pay Now", "Confirm", "Cancel", "Check Payment", "Cancel deposit", "Transactions",
    "API Docs", "Create API key", "New API key", "Download docs", "New ticket", "Payment issue",
    "General question", "Bot bug", "Share link", "Submit TXN Hash", "I've paid — send UTR",
    "Browse the shop", "Join channel", "I've joined — Check",
]


async def emojiall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text(tt(lang(uid), "admin_only"))
        return
    context.user_data["await"] = {"type": "emojiall"}
    lst = "\n".join(f"{i}. {esc(n)}" for i, n in enumerate(ALL_BUTTONS, 1))
    await update.message.reply_text(
        "🎨 <b>Emoji for ALL buttons</b>\n\n" + lst +
        "\n\nSend <b>ONE message</b> with premium emojis <b>in this order</b> (1st emoji = button 1, 2nd = button 2 …).\n"
        "• To skip a button put <code>-</code> in its place.\n"
        "• To start from another number begin with it: <code>10: [emoji][emoji]…</code>\n"
        "• Fewer emojis than buttons is fine — the rest stay as they are.\n\n"
        "Works for every button with that text (Back, Cancel, Buy … everywhere). "
        "Product buttons use ✏️ Edit this product, other emojis use /emoji.",
        parse_mode=HTML, reply_markup=Markup([[Btn("✅ Done", callback_data="adm:ed", style="success")]]))


async def emojiall_text(update, context, st):
    msg = update.message
    if not is_admin(update.effective_user.id):
        clear_await(context)
        return
    text = msg.text or ""
    ids = {e.offset: str(e.custom_emoji_id) for e in (msg.entities or [])
           if e.type == "custom_emoji" and e.custom_emoji_id}
    if not ids:
        await msg.reply_text("❌ Send premium emojis in one message (or press Done).")
        return
    start = 1
    m = re.match(r"^\s*(\d{1,3})\s*[:.)]", text)
    seq, pos, skip_to = [], 0, 0
    if m:
        start = max(1, int(m.group(1)))
        skip_to = len(m.group(0))
    chars = list(text)
    ci = 0
    pos = 0
    covered = set()
    for e in (msg.entities or []):
        if e.type == "custom_emoji":
            covered.update(range(e.offset, e.offset + e.length))
    while ci < len(chars):
        ch = chars[ci]
        if ci >= skip_to:
            if pos in ids:
                seq.append(ids[pos])
            elif ch == "-" and pos not in covered:
                seq.append(None)
        pos += 2 if ord(ch) > 0xFFFF else 1
        ci += 1
    bmap = _jload("btn_icons", {})
    n = 0
    for i, eid in enumerate(seq):
        idx = start - 1 + i
        if idx >= len(ALL_BUTTONS):
            break
        if eid:
            bmap[ALL_BUTTONS[idx].lower()] = eid
            n += 1
    db.set_setting("btn_icons", _json.dumps(bmap))
    await msg.reply_text(f"✅ {n} button emoji(s) saved. Open the bot and check. "
                         "Send another message to change more, or press Done.",
                         reply_markup=Markup([[Btn("✅ Done", callback_data="adm:ed", style="success")]]))


def _install_emoji_hook():
    """Every HTML message the bot sends/edits gets the admin's emoji rules applied (safe fallback)."""
    from telegram import Bot
    for name in ("send_message", "edit_message_text"):
        orig = getattr(Bot, name)

        def make(orig):
            async def wrapper(self, *a, **kw):
                text = kw.get("text")
                if isinstance(text, str) and kw.get("parse_mode") == "HTML":
                    try:
                        new = apply_emoji_map(text)
                    except Exception:
                        new = text
                    if new != text:
                        try:
                            return await orig(self, *a, **{**kw, "text": new})
                        except BadRequest as e:
                            if "not modified" in str(e).lower():
                                raise
                return await orig(self, *a, **kw)
            wrapper.__name__ = orig.__name__
            wrapper.__doc__ = orig.__doc__
            return wrapper
        setattr(Bot, name, make(orig))


def emoji_rules_text() -> str:
    emap, bmap = _jload("emoji_map", {}), _jload("btn_icons", {})
    lines = []
    for k, v in list(emap.items())[:40]:
        lines.append(f'{k} → <tg-emoji emoji-id="{v}">{k}</tg-emoji>')
    for k, v in list(bmap.items())[:40]:
        lines.append(f'button “{esc(k)}” → <tg-emoji emoji-id="{v}">🔹</tg-emoji>')
    return "\n".join(lines) or "— no rules yet —"


EMOJI_HELP = (
    "🎨 <b>Emoji rules</b>\n\n"
    "Send a message with <b>what to change</b>, then the <b>premium emoji</b>:\n"
    "• <code>👤</code> then the premium emoji → every 👤 in the bot (messages + buttons) becomes it\n"
    "• <code>Binance Pay</code> then the premium emoji → that button gets the emoji\n"
    "• Several in one message is fine: <code>👤 [emoji] 💰 [emoji]</code>\n"
    "• Just type the words, no \"+\" needed\n"
    "• To delete a rule send: <code>👤 -</code> or <code>Binance Pay -</code>\n\n"
    "Premium emojis in buttons show only when the bot owner has Telegram Premium.\n\n"
    "<b>Current rules</b>\n{rules}")


async def emoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text(tt(lang(uid), "admin_only"))
        return
    context.user_data["await"] = {"type": "emojirule"}
    await update.message.reply_text(EMOJI_HELP.format(rules=emoji_rules_text()), parse_mode=HTML,
                                    reply_markup=Markup([[Btn("✅ Done", callback_data="adm:ed", style="success")]]))


async def emoji_rule_text(update, context, st):
    msg = update.message
    if not is_admin(update.effective_user.id):
        clear_await(context)
        return
    text = msg.text or ""
    u16 = text.encode("utf-16-le")
    sl = lambda a, b: u16[a * 2:b * 2].decode("utf-16-le", "ignore")
    emap, bmap = _jload("emoji_map", {}), _jload("btn_icons", {})
    done, prev = [], 0
    for e in sorted([e for e in (msg.entities or []) if e.type == "custom_emoji" and e.custom_emoji_id],
                    key=lambda e: e.offset):
        left = re.sub(r"[\s+=:>→\-–]+$", "", sl(prev, e.offset).strip())   # ignore a typed "+", "=", "->" ...
        prev = e.offset + e.length
        if not left:
            continue
        if not re.search(r"\w", left) and len(left) <= 12:
            emap[_norm_emoji(left)] = str(e.custom_emoji_id)
        else:
            bmap[" ".join(left.lower().split())[:60]] = str(e.custom_emoji_id)
        done.append(left)
    if not done:
        m = re.match(r"^(.+?)\s+-$", text.strip(), re.S)
        if m:
            t = m.group(1).strip()
            hit = emap.pop(_norm_emoji(t), None) is not None
            hit = (bmap.pop(" ".join(t.lower().split()), None) is not None) or hit
            if hit:
                done.append(t)
                head = "🗑 Removed"
            else:
                await msg.reply_text("❌ No rule found for that.")
                return
        else:
            await msg.reply_text("❌ Send: what to change + a premium emoji (e.g. 👤 then the emoji). Or press Done.")
            return
    else:
        head = "✅ Saved"
    db.set_setting("emoji_map", _json.dumps(emap))
    db.set_setting("btn_icons", _json.dumps(bmap))
    await msg.reply_text(f"{head}: {esc(', '.join(done))}\n\n<b>Current rules</b>\n{emoji_rules_text()}\n\n"
                         "Send more, or press Done.", parse_mode=HTML,
                         reply_markup=Markup([[Btn("✅ Done", callback_data="adm:ed", style="success")]]))


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
    vip_name, vip_pct = db.vip_level(spent)          # levels set on the admin site (Growth > Settings)
    if vip_name:
        return f"{vip_name} (−{vip_pct:g}%)" if vip_pct else vip_name
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
        if markup is not None and strip_icons(markup) is not markup:
            log.warning("Telegram refused button icons (%s) - sending without them", e)
            await edit(q, text, strip_icons(markup))
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
        try:
            await update.effective_message.reply_text(text, reply_markup=markup, parse_mode=HTML,
                                                      disable_web_page_preview=True)
        except BadRequest as e:
            if markup is None or strip_icons(markup) is markup:
                raise
            log.warning("Telegram refused button icons (%s) - sending without them", e)
            await update.effective_message.reply_text(text, reply_markup=strip_icons(markup), parse_mode=HTML,
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


GRID_COLS = 3
GRID_PAGE_SIZE = 30            # 10 rows x 3 columns
SHORT_NAME = 18


CAT_PAGE_SIZE = 15             # inside a category: one full-width button per row


def grid_button(uid: int, p, full: bool = False):
    """Product button: premium icon + name (red = sold out). full=True keeps the whole name
    (used inside a category, one button per row, so the plan length e.g. '1 Month' stays visible)."""
    name = (p["name"] or "").strip()
    if full:
        label = name if len(name) <= 60 else name[:59].rstrip() + "…"
    else:
        label = name if len(name) <= SHORT_NAME else name[:SHORT_NAME - 1].rstrip() + "…"
    icon = product_icon_for(p)
    if p["id"] in growth_bot.live_flash_ids():
        label = "⚡" + label
    if is_available(p):
        return Btn(label, callback_data=f"prod:{p['id']}", icon=icon)
    return Btn(label, callback_data=f"prod:{p['id']}", style="danger", icon=icon)


def category_button(c: str):
    """Category button styled exactly like the product grid buttons (dark, premium icon + name)."""
    name = (c or "").strip()
    label = name if len(name) <= SHORT_NAME else name[:SHORT_NAME - 1].rstrip() + "…"
    icon = product_icon_id(name)
    if not icon:
        label = "📂 " + label          # no premium icon configured for this name -> plain folder emoji
    return Btn(label, callback_data=f"cat:{cat_key(c)}", icon=icon)


def shop_view(uid: int, language: str, category: str = None, page: int = 0):
    products = db.list_products()
    if not products:
        return tt(language, "no_products", shop=esc(shop_name())), Markup([[Btn(tt(language, "btn_search"), callback_data="search", style="primary")]])
    rows = []
    if category is None:
        avail = sum(1 for p in products if is_available(p))
        text = tt(language, "shop_head", avail=avail, total=len(products))
        shown = list(products)
        nav_key = "shop"
        cats = db.list_categories()
        if cats and page == 0:        # categories (if the admin uses them) are one row of buttons above the grid
            for i in range(0, len(cats), GRID_COLS):
                rows.append([category_button(c) for c in cats[i:i + GRID_COLS]])
            shown = [p for p in products if not (p["category"] or "").strip()] or []
            # products that belong to a category are opened through that category
    else:
        text = tt(language, "cat_head", cat=esc(category))
        shown = [p for p in products if (p["category"] or "").strip() == category]
        nav_key = f"cat:{cat_key(category)}~"
    shown.sort(key=lambda p: (p["name"] or "").lower())
    in_cat = category is not None
    cols = 1 if in_cat else GRID_COLS
    per_page = CAT_PAGE_SIZE if in_cat else GRID_PAGE_SIZE
    pages = max(1, (len(shown) + per_page - 1) // per_page)
    page = min(max(page, 0), pages - 1)
    chunk = shown[page * per_page:(page + 1) * per_page]
    for i in range(0, len(chunk), cols):
        rows.append([grid_button(uid, p, full=in_cat) for p in chunk[i:i + cols]])
    if pages > 1:
        def target(n):
            return f"shop:{n}" if category is None else f"cat:{cat_key(category)}~{n}"
        rows.append([Btn("‹", callback_data=target(max(page - 1, 0)), style="primary"),
                     Btn(f"{page + 1}/{pages}", callback_data="noop"),
                     Btn("›", callback_data=target(min(page + 1, pages - 1)), style="primary")])
    if category is None:
        rows.append([Btn(tt(language, "btn_search"), callback_data="search", style="primary")])
    else:
        if is_admin(uid):
            rows.append([Btn("✏️ Category emoji", callback_data=f"adm:ce:{cat_key(category)}")])
        rows.append([Btn(tt(language, "back"), callback_data="shop", style="primary")])
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
    delivery_html = fmt_get(p["id"], "delivery", p["delivery"]) or esc(delivery_value)
    delivery = tt(language, "card_delivery", delivery=delivery_html) if delivery_value else ""
    warranty = fmt_get(p["id"], "warranty", p["warranty"]) or esc((p["warranty"] or "").strip() or tt(language, "none"))
    desc_fmt = fmt_get(p["id"], "description", p["description"])
    lines = [l.strip() for l in (p["description"] or "").splitlines() if l.strip()]
    lines = [("✅ " + l) if l[0].isalnum() else l for l in lines]   # one ✅ per point unless it already has an icon
    if desc_fmt:                                   # edited in the bot: keep exactly what the admin wrote
        desc_fmt = re.sub(r"</?blockquote[^>]*>", "", desc_fmt)
        desc_block = tt(language, "card_desc", desc=desc_fmt)
    else:
        desc_block = tt(language, "card_desc", desc=esc("\n".join(lines))) if lines else ""
    text = tt(language, "card", name=card_name_html(p), price=money(price), inr=inr(price), disc=disc, stock=stock,
              delivery=delivery, warranty=warranty, desc=desc_block)
    text += growth_bot.card_extras(uid, p)
    cat = (p["category"] or "").strip()
    back = f"cat:{cat_key(cat)}" if cat else "shop"
    rows = []
    if is_available(p):
        rows.append([Btn(tt(language, "btn_buy", price=money(price)), callback_data=f"buy:{p['id']}", style="success")])
    rows.extend(growth_bot.card_rows(uid, p))
    if is_admin(uid):
        rows.append([Btn("✏️ Edit this product", callback_data=f"adm:e:{p['id']}")])
    rows.append([Btn(tt(language, "back"), callback_data=back, style="primary")])
    return text, Markup(rows)


def qty_view(uid: int, p, language: str):
    unit = db.get_effective_price(uid, p["id"], p["price"])
    acct = db.get_account(uid)
    status = tt(language, "in_stock") + f" · {p['stock']}" if p["stock"] > 0 else tt(language, "sold_out")
    text = tt(language, "qty_head", name=card_name_html(p), unit=money(unit), inr=inr(unit),
              bal=money(acct["balance"] or 0), status=status, maxq=p["stock"])
    text += growth_bot.qty_extras(uid, p)
    sizes = [n for n in QTY_PRESETS if n <= p["stock"]]
    rows = []
    for chunk in (sizes[:4], sizes[4:]):
        if chunk:
            rows.append([Btn(str(n), callback_data=f"qty:{p['id']}:{n}", style="primary") for n in chunk])
    rows.extend(growth_bot.qty_rows(p))
    rows.append([Btn(tt(language, "other_qty"), callback_data=f"qtyo:{p['id']}", style="success")])
    rows.append([Btn(tt(language, "back"), callback_data=f"prod:{p['id']}", style="primary")])
    return text, Markup(rows)


def terms_block(p, language: str) -> str:
    """Short 'Terms of Service' box under the order summary (taken from the product's own fields)."""
    lines = []
    delivery = (p["delivery"] or "").strip()
    warranty = (p["warranty"] or "").strip()
    lines.append("🚀 Delivery: " + (esc(delivery) if delivery else ("Instant" if not is_manual(p) else "After you order")))
    lines.append("🛡 Warranty: " + (esc(warranty) if warranty else tt(language, "none")))
    for l in [x.strip() for x in (p["description"] or "").splitlines() if x.strip()][:6]:
        lines.append(("✅ " + esc(l)) if l[0].isalnum() else esc(l))
    return tt(language, "terms", terms="\n".join(lines))


def calc_view(uid: int, pid: int, qty: int, language: str):
    """Order summary, or None if the product/stock is not available."""
    p = db.get_product(pid)
    if not p or is_manual(p) or qty <= 0 or p["stock"] < qty:
        return None
    info = db.price_info(uid, pid, p["price"], qty, True)
    unit = info["unit"]
    total = round(unit * qty, 2)
    bal = float(db.get_account(uid)["balance"] or 0)
    text = tt(language, "calc", name=card_name_html(p), qty=qty, unit=money(unit), total=money(total), inr=inr(total), bal=money(bal))
    text += growth_bot.calc_extras(uid, p, qty, info, total)
    rows = []
    if bal + 1e-9 >= total:
        text += terms_block(p, language)
        rows.append([Btn(tt(language, "btn_agree", total=money(total)), callback_data=f"bal:{pid}:{qty}", style="success")])
    else:
        text += tt(language, "need_more", need=money(total - bal))
        rows.append([Btn(tt(language, "btn_addfunds"), callback_data="fund", style="success")])
    rows.append([Btn(tt(language, "back"), callback_data=f"buy:{pid}", style="primary")])
    return text, Markup(rows)


def wallet_view(uid: int, language: str):
    bal = db.get_account(uid)["balance"] or 0
    text = tt(language, "wallet", bal=money(bal), inr=inr(bal), rate=rate_text(),
              min=f"{min_deposit():g}", max=f"{max_deposit():g}") + growth_bot.wallet_extra_text()
    rows = []
    if binance.binance_enabled:
        rows.append([Btn(tt(language, "btn_bpay"), callback_data="fm:bpay")])
    if binance.bsc_enabled:
        rows.append([Btn(tt(language, "btn_crypto"), callback_data="fm:bsc")])
    if db.upi_active_count() > 0:
        rows.append([Btn(tt(language, "btn_upi"), callback_data="fm:upi")])
    if not rows:
        text += tt(language, "no_methods")
    rows.extend(growth_bot.wallet_rows())
    rows.append([Btn("👤 Profile", callback_data="menu:profile"), Btn("🧾 My Orders", callback_data="menu:orders")])
    rows.append([Btn("🎁 Referral", callback_data="menu:ref"), Btn("⚙️ Settings", callback_data="menu:settings")])
    rows.append(main_menu_row(language))
    return text, Markup(rows)


def warranty_view(uid: int, language: str):
    custom = (db.get_setting("warranty_text", "") or "").strip()
    body = esc(custom) if custom else tt(language, "warranty_default")
    return tt(language, "warranty", text=body), Markup(growth_bot.warranty_rows() + [
        [Btn("🎧 " + MENU["support"].split(" ", 1)[1], callback_data="menu:support", style="primary")],
        main_menu_row(language)])


def profile_view(user, language: str):
    acct = db.get_account(user.id)
    uname = f" · @{esc(user.username)}" if user.username else ""
    text = tt(language, "profile", bal=money(acct["balance"] or 0), spent=money(acct["spent"] or 0),
              ref=money(acct["ref_earned"] or 0), tier=esc(tier_for(acct["spent"] or 0, language)),
              name=esc(user.full_name), uname=uname, id=user.id, since=(acct["created_at"] or "")[:10] or "—")
    extra_text, extra_rows = growth_bot.profile_extra(user.id)
    return text + extra_text, Markup([[Btn(tt(language, "btn_orders"), callback_data="menu:orders"),
                                       Btn(tt(language, "btn_tx"), callback_data="menu:tx")],
                                      [Btn(tt(language, "btn_api"), callback_data="api:home")]] + extra_rows)


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
    return tt(language, "orders", rows=rows), Markup(growth_bot.orders_rows(uid) +
                                                     [[Btn(tt(language, "shop"), callback_data="shop", style="primary")]])


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
    share = f"https://t.me/share/url?url={quote(link)}&text={quote(tt(language, 'share_text', shop=shop_name()))}"
    return text, Markup([[Btn(tt(language, "btn_share"), url=share, style="success")]] + growth_bot.referral_rows())


def support_view(uid: int, language: str):
    tickets = db.user_tickets(uid)
    if tickets:
        rows = "\n".join(f"🎫 #{t['id']} · {esc(t['kind'] or '')} · {'🟢' if t['status'] == 'open' else '⚪'} {esc(t['status'])}"
                         for t in tickets)
        text = tt(language, "support_list", rows=rows)
    else:
        text = tt(language, "support_none")
    return text, Markup([[Btn(tt(language, "btn_new_ticket"), callback_data="sup:new", style="success")]]
                        + growth_bot.support_rows() + [main_menu_row(language)])


def settings_view(uid: int, language: str):
    text = tt(language, "settings", lang=LANG_NAMES.get(language, language))
    return text, Markup([[Btn("🇬🇧 English", callback_data="setlang:en", style="primary"),
                          Btn("🇨🇳 中文", callback_data="setlang:zh", style="primary"),
                          Btn("🇪🇸 Español", callback_data="setlang:es", style="success")],
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
        return f"🟡 Binance Pay ID / UID:\n<code>{esc(binance.pay_id)}</code>\n\nBinance app → Pay → Send → Pay ID, then send the exact amount in USDT."
    net = f" ({esc(binance.deposit_network)})" if binance.deposit_network else ""
    return f"💳 USDT{net} address:\n<code>{esc(binance.deposit_address)}</code>"


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
_channel_cfg_error = {"msg": ""}


def _normalized_channel():
    """Accept @name, -100id, or a pasted https://t.me/name link."""
    ch = (required_channel() or "").strip()
    m = re.match(r"^(?:https?://)?t\.me/([A-Za-z0-9_]{4,})/?$", ch)
    if m:
        return "@" + m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_]{4,}", ch) and not ch.isdigit():
        return "@" + ch
    return ch


async def is_required_channel_member(bot, user_id: int) -> bool:
    """True if joined. False if not joined OR if the check itself failed
    (in that case _channel_cfg_error['msg'] is set so users get a clear message)."""
    _channel_cfg_error["msg"] = ""
    if not required_channel():
        return True
    try:
        member = await bot.get_chat_member(_normalized_channel(), user_id)
        if member.status in ("member", "administrator", "creator"):
            return True
        if member.status == "restricted" and getattr(member, "is_member", False):
            return True
        return False  # left / kicked / never joined
    except Exception as exc:
        err = str(exc)
        low = err.lower()
        if "user not found" in low or "participant_id_invalid" in low:
            return False
        log.error("Required channel membership check FAILED for channel %r: %s. "
                  "Fix: make the bot an ADMIN of the channel and use @username or -100... ID.",
                  _normalized_channel(), err)
        _channel_cfg_error["msg"] = err
        return False


def join_channel_markup():
    rows = []
    if required_channel_url():
        rows.append([Btn("📢 Join channel", url=required_channel_url())])
    rows.append([Btn("✅ I've joined — Check", callback_data="joincheck", style="success")])
    return Markup(rows)


async def announce_sale(bot, product_name: str, quantity: int, order_id: str, source: str = "Shop"):
    """Post the sale announcement to the sales GROUP (SALES_GROUP_ID). Bot must be a member of the group
    and allowed to send messages there."""
    if not sales_channel():
        return
    try:
        markup = None
        uname = (getattr(tenancy.cur(), "bot_username", "") or "").lstrip("@")
        if not uname:
            try:
                uname = (await bot.get_me()).username or ""
            except Exception:
                uname = ""
        if uname:
            markup = Markup([[Btn("🛒 Buy Now", url=f"https://t.me/{uname}", style="success")]])
        await bot.send_message(
            chat_id=sales_channel(),
            text=(f"🚀 <b>New Purchase!</b>\n\n"
                  f"📦 Product: <b>{esc(product_name)}</b>\n"
                  f"🔢 QTY: <b>{int(quantity)}</b>\n"
                  f"🧾 Order: <code>{esc(order_id)}</code>\n"
                  f"🔗 Source: {esc(source)}\n\n"
                  f"⚡ Auto-delivered instantly!"),
            parse_mode=HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("Could not post sale update to group %s (is the bot a member of the group "
                      "and allowed to send messages? check SALES_GROUP_ID)", sales_channel())


async def send_start_screen(message, user, context):
    created = db.ensure_user(user.id, user.username)
    language = db.get_user_language(user.id)
    if not language:
        await message.reply_text(tt("en", "welcome", shop=esc(shop_name())), reply_markup=kb_lang(), parse_mode=HTML)
        return
    await message.reply_text(welcome_text(language),
                             reply_markup=main_keyboard(), parse_mode=HTML)
    text, markup = shop_view(user.id, language)
    await message.reply_text(text, reply_markup=markup, parse_mode=HTML)


# ---------------------------------------------------------------- commands + menu buttons
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    clear_await(context)
    if shop_closed_for(u.id):
        await update.effective_message.reply_text(closed_text(), parse_mode=HTML)
        return
    if required_channel() and not await is_required_channel_member(context.bot, u.id):
        prompt = "📢 <b>Join our channel first</b>\n\nPlease join the channel and then tap <b>I've joined — Check</b> to use the bot."
        if _channel_cfg_error["msg"]:
            prompt += "\n\n⚠️ <i>Channel check is not set up correctly yet. Please tell the shop admin.</i>"
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
    elif key == "warranty":
        text, markup = warranty_view(uid, language)
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
    if shop_closed_for(update.effective_user.id):
        await update.message.reply_text(closed_text(), parse_mode=HTML)
        return
    db.touch_username(update.effective_user.id, update.effective_user.username)
    await show_menu(update, context, MENU_BY_LABEL[update.message.text])


# ---------------------------------------------------------------- callbacks
async def cb_lang(update, context, arg):
    q = update.callback_query
    db.set_user_language(q.from_user.id, arg, q.from_user.username)
    await q.answer()
    await edit(q, tt(arg, "lang_set"))
    await q.message.reply_text(welcome_text(arg), reply_markup=main_keyboard(), parse_mode=HTML)
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
    q = update.callback_query
    await q.answer()
    if arg.isdigit():                       # page buttons of the product grid
        text, markup = shop_view(q.from_user.id, lang(q.from_user.id), None, int(arg))
        await edit(q, text, markup)
        return
    await show_menu(update, context, "shop")


async def cb_noop(update, context, arg):
    await update.callback_query.answer()


async def cb_fund(update, context, arg):
    await update.callback_query.answer()
    await show_menu(update, context, "funds")


async def cb_cat(update, context, arg):
    q = update.callback_query
    await q.answer()
    language = lang(q.from_user.id)
    key, _, pg = arg.partition("~")
    category = next((c for c in db.list_categories() if cat_key(c) == key), None)
    if category is None:
        await show_menu(update, context, "shop")
        return
    text, markup = shop_view(q.from_user.id, language, category, int(pg) if pg.isdigit() else 0)
    await edit(q, text, markup)


async def cb_prod(update, context, arg):
    q = update.callback_query
    language = lang(q.from_user.id)
    p = db.get_product(int(arg))
    if not p:
        await q.answer()
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
    info = db.price_info(uid, pid, p["price"], qty, True)
    unit = info["unit"]
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
    if info["promo_code"]:
        await asyncio.to_thread(db.promo_consume, uid)
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
    info = db.price_info(uid, p["id"], p["price"], 1, True)
    unit = info["unit"]
    result = db.buy_manual(uid, p["id"], unit, pending["contact"])
    if result == "balance":
        await q.answer()
        need = unit - float(db.get_account(uid)["balance"] or 0)
        markup = Markup([[Btn(tt(language, "btn_addfunds"), callback_data="fund", style="success")], main_menu_row(language)])
        await edit(q, tt(language, "need_more", need=money(max(need, 0))).strip(), markup)
        return
    await q.answer()
    context.user_data.pop("manual", None)
    if info["promo_code"]:
        db.promo_consume(uid)
    delivery = (p["delivery"] or "").strip() or tt(language, "default_manual_delivery")
    await edit(q, tt(language, "placed", order=esc(result), name=card_name_html(p), contact=esc(pending["contact"]),
                     delivery=esc(delivery)),
               Markup([main_menu_row(language)]))
    if manual_alerts():
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
        await edit(q, tt(language, "ask_amount_upi", rate=rate_text(), min=f"{min_deposit():g}", max=f"{max_deposit():g}"),
                   Markup([[Btn(tt(language, "back"), callback_data="fund", style="primary")]]))
        return
    if (arg == "bsc" and not binance.bsc_enabled) or (arg == "bpay" and not binance.binance_enabled) or arg not in ("bsc", "bpay"):
        await q.answer(tt(language, "not_configured"), show_alert=True)
        return
    await q.answer()
    context.user_data["await"] = {"type": "amount", "method": arg}
    name = tt(language, "btn_bpay" if arg == "bpay" else "btn_crypto")
    await edit(q, tt(language, "ask_amount", method=esc(name), min=f"{min_deposit():g}", max=f"{max_deposit():g}"),
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


_install_emoji_hook()

ADM_FIELDS = {"name": "Name", "description": "Description", "delivery": "Delivery", "warranty": "Warranty"}
ADM_LIMITS = {"name": 80, "description": 1000, "delivery": 200, "warranty": 200}


def adm_menu(p):
    icon = str(_jload("prod_icons", {}).get(str(p["id"]), ""))
    shown = product_icon_for(p)
    if shown:
        emoji_line = f'<tg-emoji emoji-id="{shown}">🔹</tg-emoji> <code>{shown}</code>' + ("" if icon else " (automatic)")
    else:
        emoji_line = "none"
    text = (f"✏️ <b>Edit product</b>\n\n📦 {card_name_html(p)}\n🎨 Button emoji: {emoji_line}\n\n"
            "Pick what to change. Premium emojis and Telegram formatting (bold, italic, quote…) "
            "work in Name, Description, Delivery and Warranty.")
    pid = p["id"]
    rows = [[Btn("📝 Name", callback_data=f"adm:f:{pid}:name"), Btn("📄 Description", callback_data=f"adm:f:{pid}:description")],
            [Btn("🚚 Delivery", callback_data=f"adm:f:{pid}:delivery"), Btn("🛡 Warranty", callback_data=f"adm:f:{pid}:warranty")],
            [Btn("🎨 Button emoji", callback_data=f"adm:i:{pid}", style="primary")]]
    if icon:
        rows.append([Btn("🗑 Remove button emoji", callback_data=f"adm:ix:{pid}", style="danger")])
    rows.append([Btn("‹ Back to product", callback_data=f"prod:{pid}", style="primary")])
    return text, Markup(rows)


async def cb_admin(update, context, arg):
    q = update.callback_query
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("Admins only.", show_alert=True)
        return
    op, _, rest = arg.partition(":")
    await q.answer()
    if op == "ed":
        clear_await(context)
        await edit(q, "✅ Done. Emoji rules are live.")
        return
    if op in ("e", "f", "i", "ix"):
        pid_s, _, field = rest.partition(":")
        p = db.get_product(int(pid_s)) if pid_s.isdigit() else None
        if not p:
            await edit(q, "Product not found.")
            return
        pid = p["id"]
        if op == "e":
            clear_await(context)
            text, markup = adm_menu(p)
            await edit(q, text, markup)
        elif op == "f" and field in ADM_FIELDS:
            context.user_data["await"] = {"type": "admedit", "pid": pid, "field": field}
            cur = (p[field] or "").strip()
            hint = "" if field == "name" else "\nSend <code>-</code> to clear it."
            await edit(q, f"✏️ <b>{ADM_FIELDS[field]}</b>\n\nCurrent:\n<code>{esc(cur) or '—'}</code>\n\n"
                          f"Send the new text as a normal message. Paste premium emojis straight into it "
                          f"(sending them needs Telegram Premium).{hint}",
                       Markup([[Btn("✖ Cancel", callback_data=f"adm:e:{pid}", style="danger")]]))
        elif op == "i":
            context.user_data["await"] = {"type": "admicon", "pid": pid}
            await edit(q, "🎨 <b>Button emoji</b>\n\nSend <b>one premium emoji</b> (or its numeric ID). "
                          "It will show in front of this product's button in the shop.\n\n"
                          "<i>Telegram only shows it if the bot owner has Telegram Premium; "
                          "otherwise the button stays normal.</i>",
                       Markup([[Btn("✖ Cancel", callback_data=f"adm:e:{pid}", style="danger")]]))
        elif op == "ix":
            clear_await(context)
            icons = _jload("prod_icons", {})
            icons.pop(str(pid), None)
            db.set_setting("prod_icons", _json.dumps(icons))
            text, markup = adm_menu(p)
            await edit(q, text, markup)
        return
    if op in ("ce", "cx"):
        category = next((c for c in db.list_categories() if cat_key(c) == rest), None)
        if category is None:
            await edit(q, "Category not found.")
            return
        if op == "cx":
            set_category_icon(category, "")
            text, markup = shop_view(uid, lang(uid), category)
            await edit(q, text, markup)
            return
        context.user_data["await"] = {"type": "admcat", "cat": category}
        await edit(q, f"🎨 <b>Category emoji</b> — {esc(category)}\n\nSend <b>one premium emoji</b> (or its numeric ID) "
                      "for this category's button.",
                   Markup([[Btn("🗑 Remove emoji", callback_data=f"adm:cx:{rest}", style="danger")],
                           [Btn("✖ Cancel", callback_data=f"cat:{rest}", style="primary")]]))


async def admin_text(update, context, st):
    """Typed answer of an admin during an edit flow (kept separate so entities/premium emojis survive)."""
    msg = update.message
    uid = update.effective_user.id
    if not is_admin(uid):
        clear_await(context)
        return
    kind = st["type"]
    language = lang(uid)
    raw = msg.text or ""
    plain = raw.strip()
    try:
        if kind == "admedit":
            pid, field = st["pid"], st["field"]
            if not db.get_product(pid):
                clear_await(context)
                await msg.reply_text("Product not found.")
                return
            if plain == "-" and field != "name":
                plain, html_text = "", ""
            else:
                if not plain or len(plain) > ADM_LIMITS[field] or (field != "description" and "\n" in plain):
                    limit = ADM_LIMITS[field]
                    one = "" if field == "description" else " (one line)"
                    await msg.reply_text(f"❌ Send the text up to {limit} characters{one}, or press Cancel.")
                    return
                html_text = entities_to_html(raw, msg.entities)
            if not db.update_product(pid, **{field: plain}):
                await msg.reply_text("❌ Could not save.")
                return
            fmt_set(pid, field, plain, html_text)
            clear_await(context)
            p = db.get_product(pid)
            text, markup = card_view(uid, p, language)
            await msg.reply_text("✅ Saved. Preview:", parse_mode=HTML)
            await msg.reply_text(text, reply_markup=markup, parse_mode=HTML, disable_web_page_preview=True)
        elif kind == "admicon":
            eid = first_custom_emoji_id(msg)
            if not eid:
                await msg.reply_text("❌ Send one premium emoji (or its numeric ID), or press Cancel.")
                return
            icons = _jload("prod_icons", {})
            icons[str(st["pid"])] = eid
            db.set_setting("prod_icons", _json.dumps(icons))
            clear_await(context)
            p = db.get_product(st["pid"])
            text, markup = adm_menu(p)
            await msg.reply_text("✅ Button emoji saved.\n\n" + text, reply_markup=markup, parse_mode=HTML)
        elif kind == "admcat":
            eid = first_custom_emoji_id(msg)
            if not eid:
                await msg.reply_text("❌ Send one premium emoji (or its numeric ID), or press Cancel.")
                return
            set_category_icon(st["cat"], eid)
            clear_await(context)
            text, markup = shop_view(uid, language, st["cat"])
            await msg.reply_text("✅ Category emoji saved.", parse_mode=HTML)
            await msg.reply_text(text, reply_markup=markup, parse_mode=HTML)
    except BadRequest as e:
        log.warning("Admin edit preview failed: %s", e)
        await msg.reply_text("✅ Saved, but Telegram could not render the preview. Open the product to check it.")


CALLBACKS = {
    "lang": cb_lang, "setlang": cb_setlang, "menu": cb_menu, "shop": cb_shop, "fund": cb_fund,
    "cat": cb_cat, "prod": cb_prod, "buy": cb_buy, "qty": cb_qty, "qtyo": cb_qty_other, "bal": cb_bal,
    "cx": cb_cancel_flow, "mok": cb_manual_ok, "search": cb_search, "fm": cb_fund_method,
    "fc": cb_dep_check, "ftx": cb_dep_txhash, "fx": cb_dep_cancel, "sup": cb_support,
    "fu": cb_upi_paid, "api": cb_api, "noop": cb_noop, "adm": cb_admin,
    "g": growth_bot.cb_growth,
}


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, _, arg = (q.data or "").partition(":")
    if shop_closed_for(q.from_user.id):
        await q.answer()
        try:
            await q.message.reply_text(closed_text(), parse_mode=HTML)
        except Exception:
            log.exception("Could not send the shop-closed message")
        return
    if action == "joincheck":
        if required_channel() and not await is_required_channel_member(context.bot, q.from_user.id):
            if _channel_cfg_error["msg"]:
                await q.answer("⚠️ Bot setup problem: the shop's bot cannot check the channel "
                               "(it must be an admin there). Please tell the shop admin.", show_alert=True)
            else:
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
    expires = datetime.now(timezone.utc) + timedelta(minutes=deposit_timeout())
    dep_id = db.create_deposit(uid, amount, method, expires)
    try:
        pay = await asyncio.to_thread(binance.create_order, dep_id, amount, f"{shop_name()} deposit {dep_id}", method)
    except Exception:
        log.exception("Deposit creation failed (%s)", method)
        db.fail_deposit(dep_id)
        await update.message.reply_text(tt(language, "bpay_down" if (method == "bpay" and binance.bsc_enabled) else "pay_down"))
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
           details=payment_details(method), minutes=deposit_timeout()),
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
    expires = datetime.now(timezone.utc) + timedelta(minutes=upi_timeout())
    dep_id = db.create_upi_deposit(uid, amount, inr_amount, rate, acct["upi_id"], expires)
    inr_txt = inr_fmt(inr_amount)
    text = tt(language, "upi_created", id=esc(dep_id), credit=money(amount), inr=inr_txt, rate=f"{rate:g}",
              upi=esc(acct["upi_id"]), minutes=upi_timeout())
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
    if shop_closed_for(uid):
        clear_await(context)
        await msg.reply_text(closed_text(), parse_mode=HTML)
        return
    language = lang(uid)
    text = (msg.text or "").strip()
    kind = st["type"]

    if kind in ("admedit", "admicon", "admcat"):
        await admin_text(update, context, st)
        return
    if kind == "emojirule":
        await emoji_rule_text(update, context, st)
        return
    if kind == "emojiall":
        await emojiall_text(update, context, st)
        return
    if kind == "txtedit":
        await texts_edit_input(update, context, st)
        return
    if kind in ("promo", "rvcomment", "wclaim"):
        await growth_bot.growth_text(update, context, st)
        return

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
        unit = db.get_effective_price(uid, p["id"], p["price"], 1, True)
        delivery = (p["delivery"] or "").strip() or tt(language, "default_manual_delivery")
        context.user_data["manual"] = {"pid": p["id"], "contact": text}
        context.user_data.pop("await", None)
        markup = Markup([[Btn(tt(language, "btn_confirm", price=money(unit)), callback_data="mok", style="success")],
                         [Btn(tt(language, "btn_cancel"), callback_data="cx", style="danger")]])
        await msg.reply_text(tt(language, "confirm", name=card_name_html(p), contact=esc(text), price=money(unit),
                                delivery=esc(delivery)), reply_markup=markup, parse_mode=HTML)

    elif kind == "amount":
        try:
            # Normalize user input to cents: .4 and .40 are the same amount.
            amount_decimal = Decimal(text.replace(",", ".").strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            amount = float(amount_decimal)
            if not amount_decimal.is_finite() or not (Decimal(str(min_deposit())) <= amount_decimal <= Decimal(str(max_deposit()))):
                raise ValueError
        except (ValueError, InvalidOperation):
            await msg.reply_text(tt(language, "amount_invalid", min=f"{min_deposit():g}", max=f"{max_deposit():g}"))
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
    if shop_closed_for(update.effective_user.id):
        return
    st = context.user_data.get("await")
    if st and st["type"] == "ticket":
        msg = update.message
        await open_ticket(update, context, st["kind"], (msg.caption or "").strip(), msg.photo[-1].file_id)
    elif st and st["type"] == "wclaim":
        await growth_bot.growth_photo(update, context, st)


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
        "/emojiall - set premium emojis for ALL buttons in one message\n"
        "/emoji - single rules (one button or one emoji everywhere)\n"
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
        "/texts [word] - edit ANY bot message (deposit, welcome, warranty...)\n"
        "/maintenance on|off, /fampay\n"
        "/apiproducts [supplier_id] - supplier catalogs (keys, cost, stock)\n"
        "/linkapi <product_id> <supplier_id> <provider_key>\n"
        "/unlinkapi <product_id> [supplier_id]\n"
        "/apibalance - supplier wallets\n\n"
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
    src = sqlite3.connect(db.path)
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
            await context.bot.send_document(owner_id(), fh, filename=f"shop_{stamp}.db", caption="🗂 Daily backup")
    except Exception:
        log.exception("daily backup failed")


async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /restore - then send the backup .db file."""
    if not is_owner(update.effective_user.id):
        return
    if not tenancy.cur().is_main:
        await update.message.reply_text("♻️ Restore is only available on the main shop. "
                                        "Your /backup file is safe - ask the platform owner to restore it.")
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
    if not tenancy.cur().is_main:
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
        d = os.path.dirname(os.path.abspath(db.path))
        os.makedirs(d, exist_ok=True)
        if os.path.exists(db.path):
            shutil.copy2(db.path, db.path + ".pre_restore")
        for ext in ("-wal", "-shm"):
            if os.path.exists(db.path + ext):
                os.remove(db.path + ext)
        shutil.copy2(tmp, db.path + ".new")
        os.replace(db.path + ".new", db.path)
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
    out = [f"Binance Pay set up: {binance.binance_enabled}   BEP20 set up: {binance.bsc_enabled}"]
    if binance.binance_enabled:
        try:
            rows = await asyncio.to_thread(binance._bpay_history, True)
            out.append(f"Binance Pay history OK: {len(rows)} rows (last 1h)")
            for t in rows[:5]:
                out.append(f"  {t.get('orderType')} {t.get('currency')} {t.get('amount')} @ {t.get('transactionTime')}")
        except Exception as e:
            out.append(f"Binance Pay ERROR: {str(e)[:300]}")
    if binance.bsc_enabled:
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
    """Owner only: list every supplier's products with keys, cost and stock."""
    if not is_owner(update.effective_user.id):
        return
    sups = [s for s in await asyncio.to_thread(db.list_suppliers, True) if s["key"]]
    if not sups:
        await update.message.reply_text("No supplier yet. Add one on the admin site -> Suppliers API.")
        return
    if context.args and context.args[0].isdigit():
        sups = [s for s in sups if s["id"] == int(context.args[0])]
    linked = {}
    for pid, d in (await asyncio.to_thread(db.sources_by_product)).items():
        for o in d["offers"]:
            linked[(o["supplier_id"], o["provider_key"])] = pid
    lines = []
    for s in sups:
        try:
            items = await asyncio.to_thread(provider.catalog_for, s)
        except Exception as e:
            lines.append(f"== {s['name']} (#{s['id']}): error {str(e)[:120]}")
            continue
        lines.append(f"== {s['name']} (#{s['id']})")
        for p in items:
            tag = f" -> #{linked[(s['id'], p['key'])]}" if (s["id"], p["key"]) in linked else ""
            lines.append(f"{p['key']} | {p['name']} | cost ${p['cost']:.2f} | stock {p['stock']}{tag}")
    text = "\n".join(lines)
    for i in range(0, len(text), 3800):
        await update.message.reply_text(text[i:i + 3800])


async def linkapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /linkapi <product_id> <supplier_id> <provider_key>   (old form without supplier_id still works)"""
    if not is_owner(update.effective_user.id):
        return
    a = context.args
    if len(a) not in (2, 3) or not a[0].isdigit() or (len(a) == 3 and not a[1].isdigit()):
        await update.message.reply_text("Usage: /linkapi <product_id> <supplier_id> <provider_key>   (see /apiproducts)")
        return
    try:
        if len(a) == 3:
            await asyncio.to_thread(db.link_api, int(a[0]), a[2], int(a[1]))
        else:
            await asyncio.to_thread(db.link_api, int(a[0]), a[1])
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)[:300]}")
        return
    await update.message.reply_text("✅ Linked. Choose which supplier is used first on the admin site -> Products. "
                                    "Your price stays as set on the site.")


async def unlinkapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /unlinkapi <product_id> [supplier_id]  (no supplier_id = remove all suppliers)"""
    a = context.args
    if not is_owner(update.effective_user.id) or not a or not a[0].isdigit():
        return
    await asyncio.to_thread(db.unlink_api, int(a[0]), int(a[1]) if len(a) > 1 and a[1].isdigit() else None)
    await update.message.reply_text("✅ Unlinked.")


async def apibalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    sups = [s for s in await asyncio.to_thread(db.list_suppliers, True) if s["key"]]
    if not sups:
        await update.message.reply_text("No supplier yet. Add one on the admin site -> Suppliers API.")
        return
    lines = []
    for s in sups:
        try:
            bal = await asyncio.to_thread(provider.balance_for, s)
            lines.append(f"{s['name']} (#{s['id']}): {bal:.4f}")
        except Exception as e:
            lines.append(f"{s['name']} (#{s['id']}): error {str(e)[:120]}")
    await update.message.reply_text("Supplier wallets:\n" + "\n".join(lines))


async def provider_job(context: ContextTypes.DEFAULT_TYPE):
    """Every 2 min: refresh provider stock/cost, warn admins once per new problem."""
    if not provider.ENABLED:
        return
    seen = context.bot_data.setdefault("provider_alerts", set())
    try:
        alerts = await asyncio.to_thread(db.sync)
        stuck = await asyncio.to_thread(db.stuck_provider_orders)
        alerts += [f"⚠️ Supplier order {r['idem']} ({r['status']}): {r['qty']} item(s) may be paid but are not fully "
                   f"in stock. Check table provider_orders (items are saved there)." for r in stuck]
    except Exception as e:
        log.warning("provider sync failed: %s", e)
        return
    for a in alerts:
        if a not in seen:
            seen.add(a)
            await notify_admins(context, a)
    seen.intersection_update(alerts)     # a fixed problem can alert again if it returns


# ---------------------------------------------------------------- one bot per seller
async def _bind(update, context):
    """Runs first for every update: point `db`, `binance`, owner id... at THIS bot's shop."""
    t = tenancy.get_tenant(context.application.bot_data["tenant_id"])
    if t is None or t.status != "active":
        raise ApplicationHandlerStop
    tenancy.set_current(t)          # same task as the real handler, so it stays set until it ends
    # Maintenance mode: owner/admins keep working, everyone else gets a short notice.
    # (Payment checking and delivery run as background jobs, so they are NOT affected.)
    try:
        if db.get_setting("maintenance", "0") == "1":
            uid = update.effective_user.id if update.effective_user else 0
            if not (is_owner(uid) or is_admin(uid)):
                note = "🛠 The shop is under maintenance for a few minutes. Please try again shortly."
                try:
                    if update.callback_query:
                        await update.callback_query.answer(note, show_alert=True)
                    elif update.effective_message:
                        await update.effective_message.reply_text(note)
                except Exception:
                    pass
                raise ApplicationHandlerStop
    except ApplicationHandlerStop:
        raise
    except Exception:
        log.exception("maintenance check failed")


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/chatid -> tells the ID of the chat it is used in (use it inside your group to get SALES_GROUP_ID)."""
    chat = update.effective_chat
    if chat is None or update.effective_message is None:
        return
    await update.effective_message.reply_text(
        f"Chat ID: <code>{chat.id}</code>\nType: {esc(chat.type)}", parse_mode=HTML)


PROMO_ADS = os.getenv("PROMO_ADS", "1") != "0"
PROMO_INTERVAL_MIN = max(1, int(os.getenv("PROMO_INTERVAL_MIN", "5") or 5))
PROMO_TAGLINES = (
    "🔥 <b>Deal alert!</b>",
    "⚡ <b>Instant delivery available</b>",
    "💎 <b>Featured product</b>",
    "🛍 <b>Check this out</b>",
    "✅ <b>Available now</b>",
    "🎯 <b>Today's pick</b>",
)


async def promo_job(context: ContextTypes.DEFAULT_TYPE):
    """Every PROMO_INTERVAL_MIN minutes post ONE random in-stock product (real name, price, stock)
    to the sales group. Content is always true: nothing here pretends a sale happened."""
    import random
    target = sales_channel()
    if not PROMO_ADS or not target:
        return
    try:
        products = [p for p in db.list_products() if is_available(p)]
        if not products:
            return
        last = context.application.bot_data.get("last_promo_pid")
        pool = [p for p in products if p["id"] != last] or products
        p = random.choice(pool)
        context.application.bot_data["last_promo_pid"] = p["id"]
        price = float(p["price"])
        pct = discount_pct(p["old_price"], price)
        lines = [random.choice(PROMO_TAGLINES), "", card_name_html(p)]
        if pct:
            lines.append(f"💰 <s>${money(p['old_price'])}</s> → <b>${money(price)}</b>  (🔻{pct}% OFF)")
        else:
            lines.append(f"💰 Price: <b>${money(price)}</b>")
        if not is_manual(p):
            lines.append(f"📦 In stock: <b>{p['stock']}</b>")
        lines.append("")
        lines.append("👇 Tap <b>Buy Now</b> to order")
        uname = (getattr(tenancy.cur(), "bot_username", "") or "").lstrip("@")
        if not uname:
            uname = (await context.bot.get_me()).username or ""
        markup = Markup([[Btn("🛒 Buy Now", url=f"https://t.me/{uname}", style="success")]]) if uname else None
        await context.bot.send_message(chat_id=target, text="\n".join(lines), parse_mode=HTML,
                                       reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        log.exception("Promo post to group %s failed (bot must be in the group and allowed to send)", target)


# ---------------------------------------------------------------- owner safety net (new)
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "3") or 3)
_last_error_alert = {}          # (tenant id, error type) -> unix time of the last alert


async def on_error(update, context):
    """Logs every unhandled error and tells the owner (max 1 alert / 10 min per error type)."""
    err = context.error
    log.error("Unhandled error", exc_info=err)
    try:
        import time
        tid = context.application.bot_data.get("tenant_id")
        key = (tid, type(err).__name__)
        now = time.time()
        if now - _last_error_alert.get(key, 0) < 600:
            return
        _last_error_alert[key] = now
        t = tenancy.get_tenant(tid)
        if t is None or not t.owner_id:
            return
        where = ""
        if isinstance(update, Update) and update.effective_user:
            where = f"\nUser: <code>{update.effective_user.id}</code>"
        await context.bot.send_message(
            t.owner_id,
            f"⚠️ <b>Bot error</b>: <code>{esc(type(err).__name__)}</code>\n"
            f"<code>{esc(str(err)[:300])}</code>{where}\n"
            f"Check the Railway logs if it keeps repeating.",
            parse_mode=HTML)
    except Exception:
        log.exception("could not send the error alert")


async def lowstock_job(context: ContextTypes.DEFAULT_TYPE):
    """Every 30 min: tell the owner once when an auto-delivery product runs low / out of stock,
    and again only after it was refilled and ran low again."""
    for p in db.list_products():
        if is_manual(p):
            continue
        flag = f"lowstock_alerted_{p['id']}"
        stock = int(p["stock"] or 0)
        alerted = db.get_setting(flag, "0") == "1"
        if stock <= LOW_STOCK_THRESHOLD and not alerted:
            icon, msg = ("🔴", "OUT OF STOCK") if stock == 0 else ("🟠", f"only {stock} left")
            try:
                await context.bot.send_message(
                    owner_id(), f"{icon} <b>{esc(p['name'])}</b>: {msg}. Add stock to keep selling.",
                    parse_mode=HTML)
                db.set_setting(flag, "1")
            except Exception:
                log.exception("low stock alert failed")
        elif stock > LOW_STOCK_THRESHOLD and alerted:
            db.set_setting(flag, "0")


async def maintenance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /maintenance on | off | (no argument = show status)."""
    if not is_owner(update.effective_user.id):
        return
    arg = (context.args[0].lower() if context.args else "")
    if arg in ("on", "off"):
        db.set_setting("maintenance", "1" if arg == "on" else "0")
    state = "ON 🛠" if db.get_setting("maintenance", "0") == "1" else "OFF ✅"
    await update.message.reply_text(
        f"Maintenance mode: <b>{state}</b>\nUse /maintenance on or /maintenance off.\n"
        f"Owner and admins can still use the bot; payments keep being checked.", parse_mode=HTML)


_fampay_logged = {}


# ---------------------------------------------------------------- /texts : edit every message from the bot
_TX_KEYS = sorted(TEXT["en"])
_TX_LANGS = (("en", "🇬🇧 EN"), ("zh", "🇨🇳 中文"), ("es", "🇪🇸 ES"))


def _tx_default(lg: str, key: str) -> str:
    return TEXT.get(lg, {}).get(key) or TEXT["en"][key]


def _tx_current(lg: str, key: str):
    """(text, is_custom)"""
    custom = _text_overrides().get(f"{lg}:{key}")
    return (custom, True) if custom is not None else (_tx_default(lg, key), False)


def _tx_list_view(lg: str, page: int, q: str):
    keys = [k for k in _TX_KEYS if textedit.match(k, TEXT["en"][k], q)]
    pages = max(1, -(-len(keys) // textedit.PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    chunk = keys[page * textedit.PAGE_SIZE:(page + 1) * textedit.PAGE_SIZE]
    ov = _text_overrides()
    rows = [[_Btn(("✏️ " if f"{lg}:{k}" in ov else "") + textedit.label(k)[:48],
                  callback_data=f"tx:K:{lg}:{_TX_KEYS.index(k)}")] for k in chunk]
    nav = []
    if page > 0:
        nav.append(_Btn("⬅️", callback_data=f"tx:L:{lg}:{page - 1}"))
    nav.append(_Btn(f"{page + 1}/{pages}", callback_data=f"tx:L:{lg}:{page}"))
    if page < pages - 1:
        nav.append(_Btn("➡️", callback_data=f"tx:L:{lg}:{page + 1}"))
    rows.append(nav)
    rows.append([_Btn(("• " if c == lg else "") + n, callback_data=f"tx:L:{c}:0") for c, n in _TX_LANGS])
    rows.append([_Btn("✖️ Close", callback_data="tx:X")])
    head = (f"✏️ <b>Edit bot messages</b> — language: <b>{lg.upper()}</b>\n"
            f"{len(keys)} messages" + (f" matching “{esc(q)}”" if q else "") + ". ✏️ = you changed it.\n"
            "Tip: <code>/texts deposit</code> searches.")
    return head, Markup(rows)


def _tx_key_view(lg: str, idx: int):
    key = _TX_KEYS[idx]
    cur, custom = _tx_current(lg, key)
    fields = textedit.fields_of(_tx_default("en", key))
    shown = cur if len(cur) <= 2500 else cur[:2500] + "\n… (shortened here, the full text is used)"
    text = (f"✏️ <b>{esc(textedit.label(key))}</b> · <code>{esc(key)}</code> · {lg.upper()}"
            f"{' · <b>custom</b>' if custom else ' · built-in'}\n\n"
            f"<pre>{esc(shown)}</pre>\n"
            + ("Placeholders (keep them, the bot fills them in): " + " ".join(f"<code>{{{esc(f)}}}</code>" for f in fields)
               if fields else "No placeholders in this message.") +
            ("\nThis is a button caption: plain text only." if textedit.is_plain(key) else ""))
    rows = [[_Btn("✏️ Edit", callback_data=f"tx:E:{lg}:{idx}"), _Btn("👁 Preview", callback_data=f"tx:P:{lg}:{idx}")]]
    if custom:
        rows.append([_Btn("♻️ Reset to built-in", callback_data=f"tx:R:{lg}:{idx}")])
    rows.append([_Btn("⬅️ Back to list", callback_data=f"tx:L:{lg}:0")])
    return text, Markup(rows)


async def texts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /texts [search word] - edit any message the bot sends."""
    if not is_owner(update.effective_user.id):
        return
    q = " ".join(context.args or []).strip()
    context.user_data["tx_q"] = q
    text, kb = _tx_list_view("en", 0, q)
    await update.message.reply_text(text, reply_markup=kb, parse_mode=HTML)


async def texts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("Owner only.", show_alert=True)
        return
    parts = (q.data or "").split(":")
    act = parts[1] if len(parts) > 1 else ""
    try:
        if act == "X":
            clear_await(context)
            await q.answer()
            await q.message.delete()
            return
        lg = parts[2] if len(parts) > 2 and parts[2] in dict(_TX_LANGS) else "en"
        if act == "L":
            page = int(parts[3]) if len(parts) > 3 else 0
            text, kb = _tx_list_view(lg, page, context.user_data.get("tx_q", ""))
        else:
            idx = int(parts[3])
            key = _TX_KEYS[idx]
            if act == "K":
                clear_await(context)
                text, kb = _tx_key_view(lg, idx)
            elif act == "R":
                db.del_settings([f"txt:{lg}:{key}"])
                _TXT_CACHE.pop(tenancy.cur().id, None)
                await q.answer("Reset to the built-in text.")
                text, kb = _tx_key_view(lg, idx)
            elif act == "P":
                cur, _ = _tx_current(lg, key)
                shown = textedit.render(cur, _tx_default("en", key))
                await q.answer()
                try:
                    await q.message.reply_text("👁 <b>Preview</b>\n\n" + shown if not textedit.is_plain(key)
                                               else "👁 Preview:\n\n" + shown,
                                               parse_mode=None if textedit.is_plain(key) else HTML)
                except Exception as e:
                    await q.message.reply_text(f"❌ Telegram cannot show this text: {e}")
                return
            elif act == "E":
                context.user_data["await"] = {"type": "txtedit", "lang": lg, "key": key}
                fields = textedit.fields_of(_tx_default("en", key))
                await q.answer()
                await q.message.reply_text(
                    f"✏️ Send the <b>new text</b> for <b>{esc(textedit.label(key))}</b> ({lg.upper()}).\n\n"
                    + ("Keep these placeholders where the bot should fill values: "
                       + " ".join(f"<code>{{{esc(f)}}}</code>" for f in fields) + "\n\n" if fields else "")
                    + ("Plain text only (it is a button caption).\n\n" if textedit.is_plain(key) else
                       "You can use Telegram bold/italic, or tags like <code>&lt;b&gt;bold&lt;/b&gt;</code>, "
                       "<code>&lt;i&gt;</code>, <code>&lt;code&gt;</code>.\n\n")
                    + "Send <code>cancel</code> to stop.", parse_mode=HTML,
                    reply_markup=Markup([[_Btn("❌ Cancel", callback_data=f"tx:K:{lg}:{idx}")]]))
                return
            else:
                await q.answer()
                return
        await q.answer()
        await q.edit_message_text(text, reply_markup=kb, parse_mode=HTML)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.exception("texts_callback")
        await q.answer()
    except Exception:
        log.exception("texts_callback")
        await q.answer("Something went wrong.", show_alert=True)


async def texts_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE, st):
    """The owner sent the new text for a message (called from text_msg)."""
    msg = update.message
    if not is_owner(update.effective_user.id):
        clear_await(context)
        return
    raw = msg.text or ""
    if raw.strip().lower() in ("cancel", "stop"):
        clear_await(context)
        await msg.reply_text("Cancelled. Nothing was changed.")
        return
    lg, key = st["lang"], st["key"]
    styled = {"bold", "italic", "underline", "strikethrough", "code", "pre", "text_link", "spoiler",
              "blockquote", "custom_emoji"}
    has_style = any(e.type in styled for e in (msg.entities or []))
    new = (msg.text_html if has_style and not textedit.is_plain(key) else raw).strip()
    err = textedit.validate(key, new, _tx_default("en", key))
    if err:
        await msg.reply_text(f"❌ Not saved: {err}\n\nSend the corrected text, or send cancel.")
        return
    shown = textedit.render(new, _tx_default("en", key))
    try:                                   # Telegram itself must accept it before we save
        await msg.reply_text(("👁 <b>Preview</b>\n\n" + shown) if not textedit.is_plain(key)
                             else "👁 Preview:\n\n" + shown,
                             parse_mode=None if textedit.is_plain(key) else HTML)
    except Exception as e:
        await msg.reply_text(f"❌ Telegram rejected this text, so it was not saved:\n{e}\n\n"
                             "Send the corrected text, or send cancel.")
        return
    db.set_setting(f"txt:{lg}:{key}", new)
    _TXT_CACHE.pop(tenancy.cur().id, None)
    clear_await(context)
    idx = _TX_KEYS.index(key)
    await msg.reply_text("✅ Saved. Customers see it from now on.",
                         reply_markup=Markup([[_Btn("♻️ Reset", callback_data=f"tx:R:{lg}:{idx}"),
                                               _Btn("⬅️ Messages", callback_data=f"tx:L:{lg}:0")]]))


async def fampay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /fampay - checks the FamPay setup and says why a waiting UPI deposit is not auto-approved."""
    if not is_owner(update.effective_user.id):
        return
    try:
        if not tenancy.cur().is_main:
            await update.message.reply_text("FamPay auto-approve works on the main shop only.")
            return
        await update.message.reply_text("🔎 Checking FamPay setup, please wait...")
        rows = [d for d in db.upi_payments(status="upi_review", limit=10) if d["utr"]]
        lines = await asyncio.to_thread(fampay_verify.status_lines, rows)
        await update.message.reply_text("🔎 <b>FamPay check</b>\n\n" + "\n".join(esc(x) for x in lines)[:3800],
                                        parse_mode=HTML)
    except Exception as e:
        log.exception("/fampay failed")
        await update.message.reply_text(f"❌ /fampay error: {esc(str(e))[:300]}", parse_mode=HTML)


async def fampay_job(context: ContextTypes.DEFAULT_TYPE):
    """Every 45 s: UPI top-ups that already have a UTR are checked against the FamPay e-mails in Gmail.
    A strong match is approved automatically; everything else stays for manual review on the admin site."""
    if not fampay_verify.enabled() or not tenancy.cur().is_main:      # main shop only (uses YOUR Gmail)
        return
    rows = [d for d in db.upi_payments(status="upi_review", limit=20) if d["utr"]]
    if not rows:
        return
    results = await asyncio.to_thread(fampay_verify.verify_pending, rows)
    for dep_id, ok, why in results:
        if not ok:
            if _fampay_logged.get(dep_id) != why:          # log each reason once, not every 45 s
                _fampay_logged[dep_id] = why
                log.info("FamPay %s not auto-approved: %s", dep_id, why)
            continue
        d = db.approve_upi(dep_id, "fampay-auto")
        if not d:
            continue                      # an admin handled it first
        try:
            bal = db.get_account(d["user_id"])["balance"] or 0
            await context.bot.send_message(
                d["user_id"], tt(lang(d["user_id"]), "dep_paid", amount=money(d["amount"]), bal=money(bal)),
                parse_mode=HTML)
        except Exception:
            log.exception("could not tell the customer about the auto-approved deposit %s", dep_id)
        try:
            await notify_admins(context, f"✅ <b>UPI auto-approved</b> (FamPay email matched)\n"
                                         f"🧾 <code>{esc(dep_id)}</code> · ₹{inr_fmt(d['inr_amount'])} → ${money(d['amount'])}\n"
                                         f"🔢 UTR: <code>{esc(d['utr'])}</code>")
        except Exception:
            log.exception("admin notice for auto-approval failed")


def _in_shop(fn):
    """Wrap a repeating job so it also runs inside its own shop."""
    async def run(context):
        t = tenancy.get_tenant(context.application.bot_data["tenant_id"])
        if t is None or t.status != "active":
            return
        with tenancy.use(t):
            await fn(context)
    run.__name__ = getattr(fn, "__name__", "job")
    return run


def build_application(tenant):
    app = Application.builder().token(tenant.bot_token).build()
    app.bot_data["tenant_id"] = tenant.id
    app.add_handler(TypeHandler(Update, _bind), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chatid", chatid_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("emoji", emoji_cmd))
    app.add_handler(CommandHandler("emojiall", emojiall_cmd))
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
    app.add_handler(CommandHandler("maintenance", maintenance_cmd))
    app.add_handler(CommandHandler("fampay", fampay_cmd))
    app.add_handler(CommandHandler("texts", texts_cmd))
    app.add_handler(CommandHandler("restore", restore_cmd))
    app.add_handler(CommandHandler("cancelrestore", cancel_restore))
    app.add_handler(MessageHandler(filters.Document.ALL, restore_doc))
    app.add_handler(CommandHandler("promo", growth_bot.promo_cmd))
    app.add_handler(CommandHandler("faq", growth_bot.faq_cmd))
    app.add_handler(CommandHandler("broadcast", growth_bot.broadcast_cmd))
    app.add_handler(CommandHandler("apiproducts", apiproducts))
    app.add_handler(CommandHandler("linkapi", linkapi))
    app.add_handler(CommandHandler("unlinkapi", unlinkapi))
    app.add_handler(CommandHandler("apibalance", apibalance))
    app.add_handler(CallbackQueryHandler(texts_callback, pattern=r"^tx:"))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Regex(MENU_RE), menu_text))
    app.add_handler(MessageHandler(filters.PHOTO, photo_msg))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))
    app.job_queue.run_repeating(_in_shop(expiry_job), interval=20, first=10)
    app.job_queue.run_repeating(_in_shop(outbox_job), interval=5, first=5)
    app.job_queue.run_repeating(_in_shop(growth_bot.growth_job), interval=120, first=45)
    app.job_queue.run_repeating(_in_shop(provider_job), interval=120, first=15)
    app.job_queue.run_repeating(_in_shop(backup_job), interval=86400, first=600)
    app.job_queue.run_repeating(_in_shop(lowstock_job), interval=1800, first=120)
    app.job_queue.run_repeating(_in_shop(fampay_job), interval=45, first=30)
    app.add_error_handler(on_error)
    if PROMO_ADS:
        app.job_queue.run_repeating(_in_shop(promo_job), interval=PROMO_INTERVAL_MIN * 60, first=60)
    return app


async def _start_bot(tenant):
    app = build_application(tenant)
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    return app


async def _stop_bot(app):
    for step in (app.updater.stop, app.stop, app.shutdown):
        try:
            await step()
        except Exception:
            log.exception("error while stopping a bot")


MANAGER_INTERVAL = 10      # seconds between checks for new / changed / suspended shops


async def run_all():
    """Keeps one running bot per active seller. Checks every 10 s, so a seller who just saved
    their bot token on the website (or was approved/suspended) goes live without a restart."""
    import signal
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    running = {}      # tenant id -> (application, token hash)
    failed = {}       # tenant id -> token hash that failed to start (do not retry until it changes)
    log.info("bot manager started")
    while not stop.is_set():
        try:
            wanted = {t.id: t for t in await asyncio.to_thread(tenancy.runnable_tenants)}
        except Exception:
            log.exception("could not read the shop list")
            wanted = None
        if wanted is not None:
            for tid in list(running):
                app, tok = running[tid]
                t = wanted.get(tid)
                if t is None or tenancy._hash(t.bot_token) != tok:
                    log.info("stopping bot of shop %s", tid)
                    await _stop_bot(app)
                    del running[tid]
                    tenancy.set_bot_state(tid, "stopped")
            for tid, t in wanted.items():
                tok = tenancy._hash(t.bot_token)
                if tid in running or failed.get(tid) == tok:
                    continue
                try:
                    running[tid] = (await _start_bot(t), tok)
                    failed.pop(tid, None)
                    tenancy.set_bot_state(tid, "running")
                    log.info("bot of shop %s started", tid)
                except Exception as e:
                    failed[tid] = tok
                    log.exception("bot of shop %s could not start", tid)
                    tenancy.set_bot_state(tid, "error", str(e))
        try:
            await asyncio.wait_for(stop.wait(), timeout=MANAGER_INTERVAL)
        except asyncio.TimeoutError:
            pass
    for tid, (app, _) in list(running.items()):
        await _stop_bot(app)


def main():
    tenancy.init_all()
    if not tenancy.get_tenant(tenancy.MAIN_ID).can_run_bot:
        log.warning("Main shop: BOT_TOKEN / OWNER_ID not set - only seller bots will run.")
    return run_all()
