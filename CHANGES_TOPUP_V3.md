# Top-up v3 - replace the same 3 files (signatures unchanged; provider.py / web.py / bot.py need NO change)
binance_withdraw.py, supplier_topup.py, topup_admin.py. Tables upgrade themselves (new columns/tables added automatically).

## New safety
- Emergency pause (admin > Safety controls): stops NEW withdrawals (orders + auto-refill). Resume needs password + "yes".
- Wallet change lock: if a supplier's saved address is CHANGED, top-ups to it are locked for 6 h (env TOPUP_WALLET_COOLDOWN_H, 0 = off). First-time save is not locked. Telegram alert says "ADDRESS CHANGED".
- Velocity limit: max 4 top-ups per supplier per hour (MAX_TOPUPS_PER_HOUR).
- Admin password rate limit: 5 wrong tries / 15 min per IP, alert after 3.
- Dry run: env TOPUP_DRY_RUN=1 -> full flow, no real withdrawal (manual withdraw blocked too).
- Health check button: key has Withdraw ON, IP restricted, NO trading/transfer permission, Binance balance, Telegram login, each supplier bot exists.
- Audit log of every admin action (wallet save/delete, settings, close, withdraw, pause/resume).

## New reliability
- Binance: clock auto-sync (error -1021), GET calls retry on network/429/5xx. Withdraw POST is never blindly retried (order id reconcile as before).
- Auto-refill backoff after failures: 5, 10, 20, 40, 60 min (no more retry spam).
- Telegram FloodWait waited out once automatically.
- Manual withdraw now also uses a Binance order id.

## New visibility
- Stats per supplier: today / 7d / 30d, OK vs failed, average time to credit.
- Balance before -> after stored per top-up and shown in history.
- Alert when Binance USDT falls below BINANCE_LOW_ALERT (30).
- CSV export of full history (formula-injection safe).
- More networks: MATIC, ARBITRUM, OPTIMISM, BASE, AVAXC (besides BSC / ETH / TRX).

## New env variables (all optional)
TOPUP_DRY_RUN, TOPUP_WALLET_COOLDOWN_H

## Tested
Logic tested with mocks (normal flow, pause, dry-run, velocity, wallet lock, backoff, stats, retry/time-sync) and the admin page with Flask test client. NOT tested against real Binance / Telegram: do a small real top-up first (min amount), with Health check green.

## Fix (first deploy)
"sqlite3.OperationalError: cannot add rowid alias: oid" -> the DB column was renamed from `oid` (reserved SQLite name) to `order_ref`. Old tables upgrade automatically.
