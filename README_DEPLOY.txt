Digital Store - Essential Security Files

Files:
1. binance_pay.py - BSC transaction verification hardening. This is the code file to review/replace.
2. SECURITY.md - security checklist and deployment notes.

IMPORTANT:
- Back up the Railway database before deploying.
- Do NOT replace shop.db or any database/backup file.
- Do NOT change Railway environment variables as part of this file update.
- Keep BSC deposits paused until the new code is deployed and tested.
- Syntax compilation alone is not live payment testing. Test old transaction hash rejection and a fresh valid test payment before enabling deposits.
