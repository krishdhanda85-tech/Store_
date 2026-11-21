# Okiemyshop Telethon Marketing — One-time setup

1. Deploy this project to the existing Railway service.
2. Open the owner website → Telethon Marketing.
3. Get Telegram API ID and API Hash from https://my.telegram.org → API development tools.
4. Enter them once in the owner dashboard and save. They are encrypted in the existing store database.
5. Connect the Telegram account using phone number, login code, and 2-step password if requested.

No `TG_API_ID` or `TG_API_HASH` Railway Variables are needed. Encryption uses the existing
store encryption mechanism (`API_KEY_SECRET` if already set, otherwise the existing `BOT_TOKEN`).
Do not rotate the existing bot token/encryption secret without planning to re-encrypt saved data.

Only add groups where promotional posting is permitted. The dashboard sends only to explicitly
configured groups; it does not discover random groups, scrape members, send unsolicited DMs,
or bypass Telegram rate limits/restrictions. Sending is manual in this version.
