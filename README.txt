BSC USDT Telegram shop update

Files: bot.py, binance_pay.py, db.py, requirements.txt

Railway Variables required:
- BOT_TOKEN (existing)
- OWNER_ID (existing)
- NODEREAL_RPC_URL = your NodeReal BSC Mainnet RPC endpoint
- DEPOSIT_ADDRESS = your Binance USDT deposit address for BNB Smart Chain (BEP20)
- USDT_CONTRACT_ADDRESS = verified official USDT contract on BSC
- USDT_DECIMALS = 18 (for the commonly used USDT token contract on BSC)
- DEPOSIT_NETWORK = BNB Smart Chain (BEP20)
- BSC_CONFIRMATIONS = 3

Do not add Binance API keys for this on-chain verifier. Never share private keys/seed phrases.
Verify the token contract against an official trusted source before taking real payments.
Test first with a tiny amount. BSC RPC providers may limit eth_getLogs ranges; if the RPC rejects the query, adjust provider settings or use a dedicated indexer.

Deploy all four files together, preserving your existing Railway persistent volume/database. Do not delete shop.db. This update keeps the existing DB schema and fulfillment logic.
