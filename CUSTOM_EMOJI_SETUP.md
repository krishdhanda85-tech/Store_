# Custom Emoji setup

The supplied custom emoji IDs are configured as defaults in `bot.py`; no Railway variables are required for these IDs. You can override any ID with its matching `EMOJI_*_ID` Railway variable if needed.

Configured products include Crunchyroll, HBO Max, Netflix, Spotify, Duolingo Super, Adobe Express, Apple Music, Muse AI, Microsoft Office 365, iLovePDF, CapCut Pro, Instagram, Canva Pro, NordVPN, LinkedIn Career, Amazon Prime Video, Windows 11 Pro, Coursera Premium, Proton VPN, Steam, and Gemini Pro.

HMA VPN is not configured yet because its Custom Emoji ID was not supplied. Add `EMOJI_HMA_ID` in Railway once you have it.

Custom emojis are applied to product names in supported message text (such as product cards and order summaries). Telegram inline keyboard button labels use a different API format and are not changed by this patch. Please test a few product/order messages after deploying; Telegram may reject custom emoji entities if the bot/account/API setup does not permit them.

## Edit from inside the bot (owner + admins)

Open any product in the bot -> tap **✏️ Edit this product** (only owner/admins see it).
- **Name / Description / Delivery / Warranty**: send the new text; premium emojis and Telegram formatting
  (bold, italic, quote, links) you put in the message are kept. Send `-` to clear Description/Delivery/Warranty.
- **🎨 Button emoji**: send one premium emoji (or its numeric ID) -> shown in front of that product's button.
- Inside a category page: **✏️ Category emoji** does the same for the category button.
Plain text stays in the database (website, buttons, orders); the premium-emoji version is stored in settings
(`fmt_text`, `prod_icons`). If the name/description is later changed on the website, the plain text is used again.
Telegram only shows premium emojis in buttons when the bot owner has Telegram Premium.
