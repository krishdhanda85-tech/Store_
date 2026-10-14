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
  SALES_CHANNEL_ID=@YourChannelUsername (defaults to REQUIRED_CHANNEL_ID)

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
