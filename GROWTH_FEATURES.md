# Growth & retention features

New files: `growth.py`, `growth_bot.py`, `growth_admin.py`.
Changed files: `db.py`, `bot.py`, `web.py`. **Deploy all six together, keep your database.**
No new Railway variables are needed. New tables/columns are created automatically on start.
Every seller shop on the platform gets the same features with its own data.

## Where to set things up
Admin site → menu group **Growth** (7 pages). Everything is live in the bot right after saving.

| Feature | Admin page | What customers see in the bot |
|---|---|---|
| Deposit bonus (`10:5,50:10` = $10+ → +5%, $50+ → +10%) | Growth → Settings | Wallet shows the bonus; after a top-up they get a "🎁 Deposit bonus" message. Works for Binance Pay, BEP20, UPI and FamPay top-ups |
| Cashback % on every purchase | Growth → Settings | Line on the order summary, total on Profile |
| VIP levels (`50:Silver:2,200:Gold:5`) | Growth → Settings | Real discount on every order, progress on Profile, 🏅 VIP levels screen |
| Low-balance reminder | Growth → Settings | One reminder every 3 days to past buyers whose balance is under the limit |
| FAQ text | Growth → Settings | ❓ FAQ button (Support, Warranty, Profile) and `/faq` |
| Promo codes (percent on next purchase, or free wallet credit) | Growth → Promo codes | Wallet → 🎟 Promo code, or `/promo CODE`. One use per customer, optional max uses and expiry |
| Flash deals | Growth → Flash deals | ⚡ on the product button, countdown on the card, auto post to the sales group, optional message to all customers |
| Bulk (quantity) prices | Growth → Bulk prices | Levels shown on the product card, one-tap quantity buttons, "add 2 more to pay $X each" hint |
| Broadcast | Growth → Broadcast (or `/broadcast [all\|buyers\|nobuy\|balance\|inactive] text` in the bot) | Message sent in the background (about 5 users per second) |
| Ratings & reviews | Growth → Reviews | Customers are asked to rate an order 3 to 72 hours after delivery; average stars on the product card; ⭐ Rate button in My Orders |
| Restock alerts | Growth → Settings (shows demand) | Sold-out products can now be opened; "🔔 Notify me when back in stock" |
| Warranty claim | Support page (it arrives as a ticket) | Warranty → 🛠 Claim warranty: pick an order, describe the problem, optional screenshot |
| Referral leaderboard + prizes | Growth → Leaderboard | Referral → 🏆 Leaderboard (names partly hidden). Admin can pay prizes to the top 3 |
| Buy again | - | 🔁 buttons in My Orders |

## Price rules (in this order)
1. A custom **user price** (Users prices page) is final: no flash, bulk, VIP or promo is applied on top.
2. Otherwise the lowest of normal price / flash deal price / bulk price for the quantity.
3. Then the VIP discount.
4. Then a saved percent promo code (only when paying; used up after a successful purchase).

The Reseller API keeps its own pricing (reseller discount) and is not affected.

## Notes
- Bot texts of the new features are in English (the older screens keep en/zh/es).
- Telegram messages from the admin site accept `<b> <i> <u> <s> <code> <pre> <a href>`; the site checks the formatting before sending.
- Promo credit and bonuses appear in the customer's Transactions as deposits.
