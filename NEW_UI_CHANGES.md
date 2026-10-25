# New UI update (bot + admin site)

Files changed: `bot.py`, `web.py`. Deploy both, keep your database. No new variables are required.

## Bot (what customers see)
- **Bottom menu:** Products / Support / Wallet / API / Warranty. Customers who still have the old menu
  keep working (old button labels open the same screens); a new `/start` shows the new menu.
- **Products:** 3 columns, A to Z, premium custom emoji on every button that has an ID in `CUSTOM_EMOJI_IDS`
  (HMA Vpn still needs `EMOJI_HMA_ID`). Sold-out products are red. Max 30 per page with `‹ 1/2 ›` buttons.
  If you use categories, they appear as a row of buttons above the grid.
- **Quantity screen:** status line, quantity range, buttons 1 5 10 20 / 30 50 100 / Other quantity / Back.
- **Order screen:** the order summary now ends with a "Terms of Service" box (delivery, warranty and the
  product description) and the green button **I Agree — Pay Now**.
- **Wallet:** shows balance and top-up methods, plus Profile, My Orders, Referral and Settings buttons.
- **Warranty:** new screen; the text is edited on the admin site (Settings > Messages > Warranty text).
- Optional variable `BUTTON_ICONS=0` turns off premium icons inside buttons. If Telegram refuses the icons,
  the bot automatically resends the same message without them.

## Admin site
- **Dashboard:** a "needs attention" strip at the top (orders to deliver, UPI payments to check, open tickets,
  products low on stock) with direct links.
- **Products:** "How customers see your products in the bot" preview (3 columns, A to Z, red = out of stock).
- **Settings:** new "Warranty text" field.

## Not included
- Tier prices by quantity (1-19, 20-49, ...): the database has one price per product, so this needs a new table.
- Button icons need a Telegram app released after 9 Feb 2026 and Telegram Premium on the bot owner.
