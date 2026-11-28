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

## Exact amount (both modes)
Binance takes its fee OUT of the withdrawn amount. v3 now withdraws `amount + live Binance fee`, so the supplier receives EXACTLY the amount
(5.50 -> 5.50 on-chain). Works in fixed and invoice mode. Binance needs `amount + fee` in the account. Before the hash is sent,
the bot compares what Binance will really deliver with what was intended and alerts on a difference.
Switch off with env TOPUP_ADD_BINANCE_FEE=0.

## Exact product amount (default now)
Default: withdraw exactly `product amount - supplier balance` (fee buffer default 0, Binance fee NOT added). $5 product, balance 0 -> 5.00 sent.
A balance up to 0.001 USDT short still counts as credited (Binance's tiny fee comes out of the amount).
Old per-supplier settings saved with fee buffer 0.5 stay until you set the Fee buffer to 0 on the admin page.
Minimum top-up (default 5) still applies: set it to 0 on the admin page for a truly exact amount on every order.
To also add the Binance fee on top (supplier gets exactly the amount): env TOPUP_ADD_BINANCE_FEE=1.

# v4 additions (replace all 3 files again: binance_withdraw.py, supplier_topup.py, topup_admin.py)
- Approval gate: env TOPUP_APPROVE_ABOVE=50 -> a top-up above 50 USDT waits for Approve on the admin page (password + yes). One-time, valid 30 min. Auto-refill just waits (no backoff). Off when unset/0.
- Withdrawal reconciliation: every 30 min the bot reads Binance's withdrawal history; a withdrawal not made by this bot = Telegram alert (once each). TOPUP_AUTOPAUSE_ON_UNKNOWN=1 also pauses top-ups. "Check Binance withdrawals now" button. A withdrawal you make by hand in the Binance app also shows up: ignore it.
- Daily Telegram report (Binance balance, supplier balances, top-ups today). TOPUP_REPORT_HOUR (UTC, default 18, -1 = off).
- Spend anomaly alert: last 24 h > 3x the 7-day daily average and > 20 USDT.
- API key needs "Enable Reading" for the withdrawal history (normal for a withdraw key).

# v5 additions (replace supplier_topup.py + topup_admin.py; binance_withdraw.py is unchanged from v4)
- On-chain check: after Binance gives the TXID, the bot reads that transaction from the blockchain (public RPC) and confirms it pays the supplier's
  address the intended USDT amount; waits for 2 confirmations before the hash goes to the supplier. USDT on BSC, ETH, MATIC, ARBITRUM, OPTIMISM
  (TRX is not checked). Result is stored (history shows "on-chain tick/cross") and sent to Telegram. A mismatch only warns, an RPC outage only skips:
  it never blocks a top-up. Own RPC: env TOPUP_RPC_BSC / TOPUP_RPC_ETH / ...
  Note: with the Binance fee NOT added on top, a network with a bigger fee (ETH, MATIC) will show "pays 4.9 but 5.0 intended" - that is the fee, correct.
- "Active rules" card on the admin page: every limit and switch in one place.

# v5.1: guide on your admin site
topup_admin.py only: new page /topup/guide (flow, amounts, setup, Railway variables, safety, customer messages, troubleshooting + your live settings), linked at the top of /topup. Owner only, same as the rest of the top-up pages.
