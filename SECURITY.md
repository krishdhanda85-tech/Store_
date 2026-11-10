# Security and payment-hardening notes

## Payment verification

- BSC verification must run against BNB Smart Chain mainnet (`chainId=56`).
- A transaction is accepted only after successful receipt status and the configured confirmation count.
- The transaction hash, receipt hash, transaction block, receipt block and canonical block hash are cross-checked.
- The canonical block timestamp must not predate the payment intent creation time.
- The USDT Transfer log must come from the configured token contract, target the configured deposit wallet, and match the exact requested amount.
- A transaction hash is globally unique in the payment table and cannot pay two orders.
- Payment intents are immutable: retrying order creation cannot reset a paid order, change its amount/method, or reset its creation timestamp.
- A customer can submit only one transaction hash per order. A wrong hash requires staff to review and reset that order through a controlled admin process; do not edit the database casually.

## Railway secrets

- Keep `ADMIN_PASSWORD`, `SECRET_KEY`, Binance API secret and RPC credentials only in Railway Variables; never commit them or paste them into chat.
- Use a long random `SECRET_KEY` and a unique strong `ADMIN_PASSWORD`.
- Binance API key should have read-only permission; never enable trading or withdrawals.
- Never store a wallet seed phrase/private key in Railway or this application.
- Restrict Railway project access to trusted people. Anyone with project access may be able to change code, variables or database files.
- Keep encrypted/off-platform backups of the database and test restore procedures. Never replace `shop.db` during a code-only deployment.

## Operations

- Keep BSC deposits paused if RPC verification is unhealthy or the configured network is wrong.
- Review unusual credits and retain transaction hashes/logs for audit.
- Test changes in a staging deployment with a copied database before production.
- Rotate exposed passwords/API secrets immediately and revoke old credentials.

## Reporting

Report suspected vulnerabilities privately to the project owner. Do not publish customer data, API keys, passwords, wallet details or transaction evidence containing private information.
