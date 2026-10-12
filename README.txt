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
