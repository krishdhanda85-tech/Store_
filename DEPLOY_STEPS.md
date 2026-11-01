# Deploy steps - AI features (Gemini)

1. Get a key: aistudio.google.com/apikey  (free)
2. Railway -> your service -> Variables, add:
   GEMINI_API_KEY      = <your key>            (required, never put it in code)
   PROMO_DM            = 1                     (personal/smart messages inside the bot)
   PROMO_DM_EVERY_HOURS= 6
   SUPPORT_AI          = 1                     (AI assistant for general questions)
   Optional: PROMO_ADS=1 and SALES_GROUP_ID=@yourchannel (promo posts; bot must be admin there)
3. Upload/replace the project files (this whole folder) and redeploy.
4. Open the admin website -> Customers -> AI Assistant:
   - write Extra knowledge (refund rules, hours, how to renew)
   - add human keywords (refund, scam)
   - press "Ask" in the test box to check the answers
5. In the bot send /emoji and map 🔥 ⏰ 📉 🤝 🏆 💌 🧾 💰 📦 💳 to premium emojis (optional).
6. After a day: check Admin -> Growth -> Broadcast history for "[smart] ..." lines and the
   AI Assistant page for conversations.

Without GEMINI_API_KEY everything still works: fixed taglines, normal support tickets.
Full variable list: AI_SETUP.md
