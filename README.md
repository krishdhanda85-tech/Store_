# Digital Shop Telegram Bot

Generic version of the shop bot — sell any digital item (links, codes, files-as-links, keys) via Telegram with Binance Pay.

Features:
- English + Chinese
- Product stock as text items (links/codes)
- Quantity ordering with automatic amount calculation
- Timed payment expiry (default 5 minutes)
- Binance Pay adapter
- Payment verification before delivery
- Owner / Admin roles
- Admin can add stock items
- Owner controls global prices
- Owner can set username-specific prices
- Owner-only payment centre
- SQLite database

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows:
```powershell
.venv\Scripts\activate
```

Copy `.env.example` to `.env` and fill in the values.

## Run

```bash
python bot.py
```

## Admin

```text
/addlink <product> <price> <item_link_or_code>
/products
/admin
```

Example:
```text
/addlink PremiumKey 5.00 XXXX-XXXX-XXXX-XXXX
```

Each `/addlink` adds one stock item. If the product already exists, the new item is added to that product's stock pile.

## Owner

```text
/addadmin <telegram_id>
/removeadmin <telegram_id>
/setprice <product_id> <price>
/setuserprice <username> <product_id> <price>
/paymentcenter
```

## Payment

The bot creates a Binance Pay order and checks the merchant order status before releasing stock.

`binance_pay.py` isolates all Binance Pay API calls. Before production use, verify the current Binance Pay Merchant API endpoint/signature requirements for your merchant account.

Never expose Binance secret credentials to admins, buyers, or in Telegram chat.
