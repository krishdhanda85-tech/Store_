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

## Premium (custom) emojis in ads
In the Auto message / Send once message write `{e:EMOJI_ID|🔥}` (🔥 = normal emoji shown if the viewer has no Premium).
`<tg-emoji emoji-id="ID">🔥</tg-emoji>` also works. Bold/italic/links: `<b>..</b>`, `<i>..</i>`, `<a href="URL">..</a>`.
The connected Telegram account must have Telegram Premium, otherwise Telegram shows only the normal emoji.

## Set the ad from the bot (owner only)
Send `/ad` to the store bot -> Set / change ad -> send the ad as one message (premium emojis, bold, italic, links are kept).
Buttons: Send now, Auto ON/OFF, Interval, Gap, Stop. Groups and the Telegram login are still managed on the website.

## Ad rotation + daily limit (bot: /ad and website)
Add up to 10 different real ads with "Add ad"; they are sent one after another each run.
"Daily limit" = max posts per group in 24h (default 2); groups that reached it are skipped.

## v2 upgrade (advanced)
- Auto-join: the Telethon account joins groups it is not in (public @name, t.me links, private invite links), max 5 new groups per run, with pauses. Toggle in /ad (Auto-join) or the website.
- Group formats accepted: `@name`, `name`, `t.me/name`, `https://t.me/name/123`, `https://t.me/+INVITE`, `t.me/joinchat/..`, `t.me/c/ID/..`, numeric ID.
- Smart lookup: groups the account is already in are read from its chat list (no username lookups, far fewer flood-waits).
- Dead-group protection: a group that fails 3 runs in a row with a permanent error (banned, no write rights, deleted) is paused for 48 h. "Retry" clears it.
- Slow-mode groups are skipped (not counted as failures) and retried next run.
- Random gap jitter (80-140%) and random group order each run.
- Posting window (UTC hours) so ads only go out at the hours you choose.
- Check account: shows name, Premium yes/no and how many groups it is in. Website button / `/ad` -> Check account.
- Reports: per-group reasons for the last run (website + `/ad` -> Last report).
- Warnings on the website and in `/ad`: telethon missing, session cannot be decrypted (API_KEY_SECRET changed), DB not on a Railway Volume, account without Premium while ads use premium emojis.
- Owner alert in the store bot when auto-post stops by itself (dead session) or a whole auto run sends 0 posts.
- Fix: auto-post can now be turned ON from the website when ads exist in the rotation list even if the single "Auto message" box is empty.

Files changed: telethon_admin.py, telethon_bot.py, bot.py (one line). requirements.txt is unchanged.
