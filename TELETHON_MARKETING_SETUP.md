# Telethon Marketing - setup and use

1. Deploy (replace `telethon_admin.py`). `web.py` and `requirements.txt` stay as before.
2. Owner website -> **Telethon Marketing**.
3. Save Telegram API ID + API Hash (my.telegram.org -> API development tools). Stored encrypted.
4. Connect the Telegram account (phone, login code, 2-step password if asked).
5. Add groups: one per line, no limit.
6. **Auto-post**: set repeat interval (minutes), gap between groups (seconds) and the message,
   tick the confirmation box, turn ON. Posting then repeats by itself.

Limits for account safety: interval minimum 10 minutes, gap minimum 5 seconds.
Auto-post stops by itself on Telegram flood limits, after 8 failures in a row, and switches OFF
if the Telegram session is no longer valid.
Settings and the schedule are saved in the database, so they survive restarts/redeploys.
Use a dedicated Telegram account and only groups where promotions are allowed.
