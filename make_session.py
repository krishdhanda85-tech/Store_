"""Run ONCE on your own PC:  pip install telethon  &&  python make_session.py
Gives a TG_SESSION string. Put it in Railway variables (TG_API_ID, TG_API_HASH, TG_SESSION).
Get API id/hash from https://my.telegram.org -> API development tools. Use a spare Telegram account."""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API ID: "))
api_hash = input("API HASH: ").strip()
with TelegramClient(StringSession(), api_id, api_hash) as c:
    print("\nTG_SESSION =\n" + c.session.save())
