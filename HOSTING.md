# হোস্টিং গাইড — Memecoin বট ২৪ ঘণ্টা চালু রাখা

বটটা তোমার কম্পিউটারে চালালে কম্পিউটার বন্ধ করলেই বট বন্ধ হয়ে যাবে,
আর auto TP/SL (প্রতি ২০ সেকেন্ডে চেক) তখন কাজ করবে না। তাই হোস্টিং লাগবে।

## সুপারিশ: Railway (প্রথম পছন্দ)

কেন: সবসময় চালু থাকে, ঘুমায় না। বট long polling ব্যবহার করে, তাই
ডোমেইন বা webhook-এর দরকার নেই।

খরচ (২০২৬-১১-১৬ যাচাই করা): ট্রায়ালে একবার $৫ ক্রেডিট, ৩০ দিনের জন্য;
তারপর Hobby প্ল্যান $৫/মাস, যাতে $৫ ব্যবহারের ক্রেডিট থাকে — ছোট বট
সাধারণত এর ভেতরেই চলে। একটা সতর্কতা: ২০২৬-এর নভেম্বরে Railway ফোরামে
কিছু নতুন অ্যাকাউন্টে ট্রায়াল দিয়ে deploy আটকে যাওয়ার রিপোর্ট আছে —
সাইন আপের সময় paid upgrade চাইলে সেটা দেখে সিদ্ধান্ত নিও, ফ্রি
ট্রায়াল ধরে নিও না।

### ধাপ
1. কোডটা GitHub-এ push করো (`.env` কখনো push করবে না — `.gitignore`-এ
   আছে, তবু push-এর আগে একবার দেখে নিও)।
2. railway.com → New Project → GitHub repo বেছে নাও।
3. Variables-এ `.env.example`-এর সব ভ্যারিয়েবল বসাও:
   `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`, `SOLANA_RPC_URL`,
   `WALLET_PRIVATE_KEY` (শুধু টেস্টের পর), `DRY_RUN=true`।
   আরও wallet connect করতে চাইলে `WALLET_PRIVATE_KEY_2` / `_3` (নাম:
   `WALLET_NAME` / `_2` / `_3`) — যেকোনো wallet চলে: Phantom, Jupiter,
   Solflare। key কখনো চ্যাটে পাঠাবে না, শুধু এখানে বসাবে।
4. Start command: `python3 bot.py` (Dockerfile থাকায় Railway এটা
   নিজেই ধরবে; না ধরলে Settings → Start Command-এ বসিয়ে দিও)।
5. **Volume লাগাও — এটা জরুরি:** খোলা পজিশন আর PNL শুধু
   `positions.db` ফাইলে থাকে। Volume ছাড়া redeploy হলে ফাইলটা
   মুছে যাবে, আর বট ভুলে যাবে তুমি কী ধরে আছো — auto TP/SL তখন
   আর ওই পজিশন বিক্রি করবে না। Railway-তে একটা Volume বানিয়ে
   `/data`-তে mount করো, আর Variables-এ বসাও: `DB_PATH=/data/positions.db`
6. Deploy → Telegram-এ বটকে `/start` দাও। প্রথমে DRY_RUN-এই
   `/check` দিয়ে একটা কয়েন চেক করো।

## Render ফ্রি — এই বটের জন্য নয়

ফ্রি web service ১৫ মিনিট নিষ্ক্রিয় থাকলেই ঘুমিয়ে যায়, জাগতে প্রায়
১ মিনিট লাগে; আর বটের জন্য দরকার Background Worker, যেটা ফ্রিতে নেই
(Starter প্রায় $৭/মাস)। ফ্রিতে local ফাইলও থাকে না — `positions.db`
মুছে যাবে। তাই Render ফ্রি দিয়ে ২৪ ঘণ্টার auto TP/SL চলবে না।

## VPS — সস্তা, কিন্তু নিজে সামলাতে হয়

মাসে প্রায় $৫-এর VPS-এও চলবে (`git clone` → `.env` বসাও →
`nohup python3 bot.py &` বা systemd service)। Railway-এর চেয়ে ঝামেলা
বেশি, তাই প্রথমে Railway-ই ভালো।

## লাইভ করার ক্রম (ভুলো না)

1. BotFather token + নিজের Telegram ID — token কাউকে পাঠাবে না,
   শুধু Railway Variables / `.env`-এ বসাবে।
2. `python3 generate_wallet.py` দিয়ে নতুন ছোট ওয়ালেট — মেইন ওয়ালেট নয়।
   Private key কাউকে দেবে না, আমাকেও না।
3. 0.05 SOL দিয়ে একটা ছোট ট্রেড টেস্ট করো, `/positions` আর `/pnl`
   দেখে নিশ্চিত হও।
4. তারপরই `DRY_RUN=false` করো — তার আগে নয়।
