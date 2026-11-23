# Profit centre + Replace product update

Deploy these 4 files (keep your database, nothing else changes):
- web.py              (new routes, menu link, dashboard counts the new profit)
- profit.py           (profit numbers now include DM sales, replacements, +/-)
- profit_plus.py      (NEW - DM sale, profit add/minus, replace logic)
- profit_templates.py (NEW - new Profit centre UI + Replace product page)

New tables are created automatically on start (profit_entries, replacements).

Profit centre: Net profit hero, + DM sale, + Add profit, - Minus profit, Replace product, daily chart,
filterable Activity list with Undo for manual entries, CSV includes everything.
Replace product (menu): find order by ID / @username / Telegram ID -> Replace this item ->
next stock item (or one you paste) goes to the customer on Telegram, cost is booked as a loss.
