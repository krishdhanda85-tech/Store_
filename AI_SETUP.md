# Gemini AI features - setup (Railway Variables)

Needed for all AI features:
- GEMINI_API_KEY        key from aistudio.google.com/apikey (never put it in code)
- GEMINI_MODEL          optional, default gemini-2.5-flash

1) Promo posts in your channel/group  (Manager + Marketing agents, agents.py)
- SALES_GROUP_ID        your channel (@name or -100...), bot must be admin there
- PROMO_ADS=1           (0 = off)
- PROMO_INTERVAL_MIN=60 minutes between posts
- AI_DAILY_MAX=40       max AI hook lines per day (keeps you inside the free tier)

2) Promo inside the bot chat (personal: buyers get a message about what THEY bought)
- PROMO_DM=1            (default off)
- PROMO_DM_EVERY_HOURS=24   (minimum 6)
- PROMO_DM_AUDIENCE=all     all | buyers | nobuy | balance | inactive
- PROMO_DM_PERSONAL=1       0 = same message for everyone

3) AI support (General question tickets only; payment/bug tickets go to a human)
- SUPPORT_AI=1          (0 = off)
- SUPPORT_AI_DAILY_MAX=150

4) Optional separate marketing bot (own token, posts to channels only)
- MARKETING_BOT_TOKEN, MARKETING_CHAT_IDS, MARKETING_INTERVAL_MIN, MARKETING_ACTIVE_HOURS
  Not started unless MARKETING_BOT_TOKEN is set.

Without GEMINI_API_KEY everything falls back to fixed taglines / normal tickets.

## 5) Smart promo from purchase history (smart_promo.py)  - on by default when PROMO_DM=1
Each customer's PAID orders are analysed and they get ONE message with the best reason:
renew (their repeat cycle is due) > pricedrop (something they bought is discounted) > crosssell
(other buyers of their product also bought X) > loyal (3+ orders) > winback (45+ days away) > newcomer.
Every number in the message (days ago, orders, how many customers) comes from your real database.
Wallet balance is shown when > $0.50. Gemini only writes the hook line (1 call per reason+product).
Variables: PROMO_DM_SMART=1 (0 = old simple promo), PROMO_DM_EVERY_HOURS=6 (recommended; per-user
cooldowns stop spam), SMART_RENEW_DAYS=28, SMART_WINBACK_DAYS=45, SMART_COOLDOWN_DAYS=3,
SMART_PRODUCT_COOLDOWN_DAYS=14, SMART_MIN_COPURCHASE=2, SMART_LOYAL_ORDERS=3, SMART_MAX_PER_RUN=300.
Custom emojis: send /emoji and map 🔥 ⏰ 📉 🤝 🏆 💌 🧾 💰 📦 💳 to premium emojis; they apply automatically.
