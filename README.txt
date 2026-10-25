=====================================================================
MULTI-SELLER PLATFORM (new)  -  one site, many sellers
=====================================================================
New files: tenancy.py, sellers_web.py.   Changed: bot.py, web.py, binance_pay.py, provider.py,
reseller_api.py, reseller_admin.py.   Deploy ALL files together. Your existing shop.db is kept:
it becomes the MAIN shop (id 1) and is configured exactly as before with Railway variables.

Each seller gets (completely separate from you and from other sellers):
  - their own database file  (<DB_PATH folder>/sellers/s<id>.db): products, stock, coupon/user prices,
    customers, wallets, orders, tickets, UPI accounts, settings, reseller API keys
  - their own Telegram bot (their BOT TOKEN) and their own owner Telegram ID
  - their own Binance API key + secret + Binance Pay ID and their own BEP20 address
    (customer money goes straight to the seller; keys are stored ENCRYPTED with API_KEY_SECRET)
  - their own admin login on this same website (e-mail + password)
Sellers do NOT get your supplier/coupon API (CUPPON_API_KEY), products, customers or coupons,
and the Suppliers API page is owner-only. Your owner Telegram ID is not an admin inside sellers' bots.

Flow
  1. Seller opens  https://YOUR-SITE/register  -> creates an account.
  2. Seller opens "Bot & payments" and saves: bot token, Telegram ID, Binance key/secret/Pay ID,
     BEP20 address (optional), channels (optional). "Test payments" checks the Binance/BSC connection.
  3. You open "Sellers" in the menu and press Approve. The seller's bot goes live within ~10 seconds,
     no redeploy needed. Suspend stops their bot, API keys and login at once.
  4. Seller adds products and manages everything on the site / in their bot like you do.

New Railway variables (all optional)
  SELLER_SIGNUP=approval   approval (default: you approve each seller) | open | closed
  MAX_SELLERS=50           how many seller bots one service may run (each bot polls Telegram)
  MASTER_DB_PATH, SELLER_DATA_DIR   default: next to DB_PATH
  NODEREAL_RPC_URL, USDT_CONTRACT_ADDRESS, USDT_DECIMALS, BSC_CONFIRMATIONS, BINANCE_PROXY, BINANCE_API_BASE
     are shared by all shops (same chain, same server). Binance keys and addresses are per shop.

IMPORTANT
  - Put DB_PATH on a Railway VOLUME (e.g. DB_PATH=/data/shop.db). master.db and sellers/ are created in
    the same folder, so one volume keeps everything. Without a volume, a redeploy wipes all sellers.
  - Log in to the admin site once after updating (old sessions are signed out). You still log in with
    ADMIN_PASSWORD and an EMPTY e-mail field.
  - Reseller API keys of sellers' customers look like rs_t<id>_..., yours stay rs_live_... ; the same
    /api/v1/index.php URL serves every shop (the key tells which shop).
  - /restore works only on the main shop (it restarts the whole service). /backup works for everyone.
  - Binance keys must be READ-ONLY ("Enable Reading" only). Tell sellers never to enable withdrawals.
  - Binance blocks US servers (HTTP 451): the same region/proxy rule applies to every seller.

Digital Shop bot: BSC BEP20 USDT + Binance Pay (UID)

Files: bot.py, binance_pay.py, db.py, requirements.txt  (deploy all four, keep shop.db)

Railway Variables
-- Common: BOT_TOKEN, OWNER_ID, (optional) AMOUNT_DECIMALS=3, DB_PATH
-- BSC BEP20 (on-chain):
   NODEREAL_RPC_URL, DEPOSIT_ADDRESS, USDT_CONTRACT_ADDRESS,
   USDT_DECIMALS=18, DEPOSIT_NETWORK=BNB Smart Chain (BEP20), BSC_CONFIRMATIONS=3
-- Binance Pay (UID transfer):
   BINANCE_API_KEY, BINANCE_SECRET_KEY, BINANCE_PAY_ID
   (optional) BINANCE_API_BASE, BINANCE_PROXY

A method shows up only if its variables are set. If both are set, the user gets
a "Choose payment method" screen after entering quantity.

Binance API key: permission ONLY "Enable Reading". No trading, no withdrawals.
Binance blocks US IPs (HTTP 451). Run the service in a non-US region, or set
BINANCE_PROXY to a non-US proxy. If Binance is blocked, BEP-20 keeps working and
the user is told to pick BEP-20 (the bot checks Binance before showing the UID).

Owner command /apitest shows Binance Pay + BSC RPC connectivity.
Test with a tiny amount first. Never share private keys / seed phrases.
The DB schema is kept; crypto_payments gets one extra "method" column automatically.

UI: one message edits in place (shop -> product -> quantity -> order calculation -> payment).
Quantity buttons 1/2/3/5/10/20 + "Other quantity", My orders, Language, Cancel order.
Item links are sent in tap-to-copy format. English + Chinese texts live in the TEXT dict in bot.py.
Button colours: green = buy/pay/check, blue = navigation/quantity, red = cancel (needs Telegram app updated after 9 Feb 2026).


=====================================================================
WALLET UI UPDATE (what changed)
=====================================================================
Bottom menu: Shop / Add Funds / Profile / My Orders / Referral / Support / Settings.
Customers top up a wallet (Binance Pay UID and USDT BEP-20) and buy from the balance.
After updating, existing customers must send /start once to get the new bottom menu.

New optional variables (all have defaults):
  MIN_DEPOSIT=0.1            smallest top-up
  MAX_DEPOSIT=100            largest top-up
  DEPOSIT_TIMEOUT_MINUTES=15 how long a top-up request stays open
  REFERRAL_PERCENT=3         starting referral %, then change it on the admin site
                             (Settings page) or with /setref <percent> (owner)
  TIERS=                     e.g. 10:Bronze,50:Silver,200:Gold  (empty = "No tier yet")
  MANUAL_ALERTS=1            0 = don't message the owner about new made-to-order orders
  PUBLIC_URL / RAILWAY_PUBLIC_DOMAIN  (optional) used for the link in owner alerts

Admin site (web.py) new pages:
  Products        edit name, price, old price (discount %), category, type (auto / made to order),
                  delivery time, warranty, question for the customer, description
  Pending orders  made-to-order orders: copy the customer's email, Mark done / Send note / Refund
  Users           add or remove wallet balance
  Support         read tickets and reply (the customer gets it in the bot)
  Settings        referral commission %

Made-to-order products: customer taps Buy, sends an email, confirms; the price is held from
the balance. "Mark done" tells the customer once; "Refund" returns the money to the wallet.
Support tickets: the owner/admins also get each ticket in Telegram - reply to that message
to answer the customer.


CHANNEL JOIN + SALES UPDATES (optional)
--------------------------------------
Set these Railway Variables on the worker service:
  REQUIRED_CHANNEL_ID=@YourChannelUsername (or numeric channel ID, e.g. -100...)
  REQUIRED_CHANNEL_URL=https://t.me/YourChannelUsername (or private invite link)
  SALES_GROUP_ID=-100xxxxxxxxxx   (group where sale announcements are posted; add the bot to the group)
  PROMO_ADS=1             (1 = auto promo posts in the group, 0 = off)
  PROMO_INTERVAL_MIN=5    (minutes between promo posts; each post is a random in-stock product with its real price/stock)

Add the bot as an administrator in that channel. It needs permission to view members/check join status and post messages. With REQUIRED_CHANNEL_ID set, /start asks users to join and tap “I've joined — Check”. Each successful normal Telegram purchase and each reseller API order posts product name, quantity sold, order ID, and source to SALES_CHANNEL_ID. Customer details and delivery links are not posted. Leave REQUIRED_CHANNEL_ID blank to disable the join requirement.


=====================================================================
RESELLER API: WALLET-LINKED (new)
=====================================================================
Files changed: bot.py, reseller_api.py, reseller_admin.py (deploy all three, keep shop.db).
- Every user sees Profile -> "🔑 API". Tap "Create API key": the key is shown once.
- API orders are paid from that user's bot wallet (Add Funds: BEP20 / Binance Pay). No manual top-up.
- Starts at 0% discount (normal shop price). Admin site -> Resellers API: set discount, top up wallet, disable.
- "New API key" in the bot replaces the key (old one stops working).
- Resellers created on the admin site WITHOUT a Telegram ID keep the old admin-added balance.
- Optional variable: API_BASE_URL (else PUBLIC_URL / RAILWAY_PUBLIC_DOMAIN) = site link shown in the bot.


=====================================================================
API KEY SHOWN INSIDE THE BOT (new)
=====================================================================
Files changed: bot.py, reseller_api.py, reseller_admin.py, requirements.txt (cryptography added).
- Profile -> API screen, Docs 1/3 and 2/3, and "Download docs" now show the user's own API URL and key.
- The key is stored encrypted (column api_resellers.key_enc). Lookup still uses the SHA-256 hash.
- Railway Variables:
    API_BASE_URL=https://your-app.up.railway.app   (site link shown in the bot, no trailing slash)
    API_KEY_SECRET=<long random text>              (encrypts the stored keys; set once, never change it)
  If API_KEY_SECRET is missing, BOT_TOKEN is used. If that secret changes, old keys stop showing
  and the user taps "New API key" again.
- Keys created before this update cannot be shown: tap Profile -> API -> New API key once
  (the old key stops working, so update it in any panel that uses it).
- Every API reply now also carries "success", "status" and (on errors) "message" next to "ok".
- Every /api request is logged (method, path, action, status; keys are masked as ***).
- Panel endpoint URL (e.g. Goatsell): https://your-app.up.railway.app/api/v1/index.php
  Health check (no key): https://your-app.up.railway.app/api/v1/health
- The old Reseller_API_Channel_Features_Essential.zip was removed (outdated copies of 4 files).


MADE-TO-ORDER RESELLER API UPDATE
- Products with kind other than `auto`/`automatic` are listed with stock=-1, available=true, made_to_order=true. Some supplier panels may display -1 as unlimited/manual availability.
- POST /api/v1/orders accepts `contact` (or `email` / `customer_email`) for made-to-order products, charges the reseller wallet, queues status `processing`, and alerts the shop owner.
- Telegram alert uses the active shop's BOT_TOKEN and OWNER_ID/tenant owner ID. Keep these private.
- Optional email alert: set SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, and MTO_NOTIFY_EMAIL in Railway Variables.
- No database file is bundled. The code adds the `contact` column to api_reseller_orders automatically without deleting existing orders. Back up shop.db before deploying.

MADE-TO-ORDER OTP FLOW (NEW)
----------------------------
1. Seller opens the admin panel > Resellers API and taps “I’m Online”. Keep that page open; a heartbeat keeps the online state fresh. If the page is closed or stops heartbeating for 3 minutes, the API reports the seller offline.
2. The reseller bot should call GET /api/v1/seller-status with its API key. Show “Request OTP” only when online.
3. When the customer taps Request OTP, the reseller bot calls POST /api/v1/orders/ORDER_ID/request-otp with its API key. If seller is offline, the API returns HTTP 409 with error seller_offline.
4. The seller enters the current OTP in the website and taps “Send OTP to reseller API”. Order status becomes otp_ready. The reseller bot should poll GET /api/v1/orders/ORDER_ID; when status is otp_ready, send the `otp` field to the matching customer.
5. After the customer has used the OTP, the seller enters profile and PIN on the website and completes the order. Status becomes completed; the reseller bot polls the order and sends `profile` and `pin` to that same customer.

Order statuses for this flow: processing, otp_requested, otp_ready, completed. Use order ID plus your own customer mapping so data never goes to a different customer. API keys must remain private. OTP/PIN are sensitive; do not log them or store them in public channels. The website retains them in the shop database for the order record; remove the order record when no longer needed if your retention requirements call for it.
