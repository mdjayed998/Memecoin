# Solana Memecoin Telegram Bot — v1.7

তোমার জন্য বানানো Smart Memecoin বট। Telegram থেকেই rug check, fast buy/sell, auto take-profit / stop-loss, PNL — আর এখন copy trade-ও দেখা যাবে।

## v1.2-এ যা আছে

| ফিচার | কী করে |
|---|---|
| **Fast Buy/Sell** | Mint পেস্ট করলেই Buy 0.02 / 0.1 / 0.5 / 1 SOL বাটন। Sell 25% / 50% / 100%। Jupiter দিয়ে swap |
| **Rug Check** | কেনার আগে RugCheck + DexScreener: mint/freeze authority, LP locked %, top-10 holder %, liquidity। DANGER হলে buy ব্লক |
| **Auto TP/SL** | ডিফল্ট: 2x হলে 50% সেল, -30% হলে 100% সেল। `/set_tp`, `/set_sl` দিয়ে বদলানো যায়। প্রতি 20 সেকেন্ডে চেক করে |
| **PNL Tracker** | `/positions` — খোলা ট্রেড + লাইভ %, `/pnl` — মোট লাভ-লস (SOL-এ), SQLite-এ সেভ থাকে |
| **Copy Trade (নতুন)** | কোনো ট্রেডারের **ওয়ালেট address** দিলে তার buy/sell কপি করে — নিচে দেখো |

## Copy Trade — কীভাবে কাজ করে

তুমি যেটা বলেছিলে, ঠিক সেটাই: **কোনো ইউজারের ওয়ালেট address দিলে বট সেই ইউজার যা buy/sell করবে, তা-ই করবে।**

- `/copy_add <wallet> [sol]` — যেমন `/copy_add 7xK...9Qp 0.1`। এটা ট্রেডারের **ওয়ালেট address**, টোকেনের mint (CA) নয়।
- **Buy কপি:** টার্গেট কোনো টোকেন কিনলেই বট সেই টোকেন কিনবে — তবে টার্গেটের সাইজে নয়, তোমার ঠিক করা **fixed SOL**-এ (ডিফল্ট 0.1 SOL, সর্বোচ্চ 1 SOL)। কেনার আগে rug check চলবেই — DANGER হলে কপি buy হবে না।
- **Sell কপি (প্রফিট):** টার্গেট যখন সেল করবে, বট একই % সেল করবে — টার্গেট 50% সেল করলে বটও তার কপি পজিশনের 50% সেল করবে। টার্গেট পুরো সেল করলে বটও পুরো সেল করবে।
- **তোমার নিজের সেল লিমিট (সবচেয়ে জরুরি):** টার্গেট ধরে থাকলেও বট অপেক্ষা করবে না — টোকেন **-30% ডাউন** গেলেই অটো 100% সেল হয়ে যাবে (প্রতি ওয়ালেটে `/copy_sl` দিয়ে বদলানো যায়)। আর 2x হলে 50% TP সেলও চলবে।
- বট প্রতি ~15 সেকেন্ডে টার্গেটের নতুন swap চেক করে। Helius API key থাকলে detection দ্রুত ও নির্ভরযোগ্য হয় (ফ্রি key: dashboard.helius.dev → `HELIUS_API_KEY` in `.env`)। Key না থাকলে plain RPC দিয়ে চলে, তবে ধীর আর rate-limit হতে পারে।
- বট শুধু ওই মুহূর্ত থেকে ট্র্যাক শুরু করে — টার্গেট আগে যা কিনেছিল, সেটা কপি হবে না।

**সত্যি কথাটা:** কপি করলেই লাভ হয় না। ~15 সেকেন্ড দেরি হয়, তাই entry price খারাপ হতে পারে, আর টার্গেট ভালো ট্রেডার হলেও rug খেতে পারে। ছোট অ্যামাউন্ট দিয়ে শুরু করো, আর একসাথে সর্বোচ্চ ৫টা ওয়ালেট follow করা যায়।

## API — যেগুলো যাচাই করা হয়েছে (2026-11-16)

- **Jupiter:** `lite-api.jup.ag/swap/v1/quote` + `/swap/v1/swap` — এটাই লাইভ। পুরনো `quote-api.jup.ag/v6` এখন ডেড, কোডে ব্যবহার করা হয়নি।
- **RugCheck:** `api.rugcheck.xyz/v1/tokens/{mint}/report` — ফ্রি, কি লাগে না। Score বেশি = বেশি রিস্ক।
- **DexScreener:** `api.dexscreener.com/latest/dex/tokens/{mint}` — ফ্রি, কি লাগে না। প্রাইস + লিকুইডিটি।
- **Solana RPC:** ডিফল্ট `api.mainnet-beta.solana.com`। ফাস্ট ট্রেডের জন্য Helius / QuickNode RPC নিলে ভালো।
- **Copy trade (v1.2):** Helius Enhanced Transactions — `api.helius.xyz/v0/addresses/{wallet}/transactions?type=SWAP` (ফ্রি key লাগে, 1M credits/mo)। Key না থাকলে fallback: RPC `getSignaturesForAddress` + `getTransaction` (jsonParsed) দিয়ে SOL vs token balance change দেখে BUY/SELL বোঝা হয়।

## সেটআপ — ৫ ধাপ

### ১. Telegram বট বানাও (BotFather)
1. Telegram-এ `@BotFather` খোলো → `/newbot` → নাম দাও → username দাও (শেষে `bot` থাকতে হবে)
2. BotFather যে **token** দেবে, সেটা কপি করে রাখো। এটা শুধু `.env`-এ বসবে, চ্যাটে পাঠাবে না।
3. নিজের Telegram user ID বের করো — `@userinfobot`-কে মেসেজ দিলেই ID দেবে।

### ২. কোড চালানো
```bash
cd solana-memecoin-bot
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### ৩. `.env` ভরো
```
TELEGRAM_BOT_TOKEN=BotFather-এর-token
OWNER_TELEGRAM_ID=তোমার-Telegram-ID
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
WALLET_PRIVATE_KEY=
DRY_RUN=true
```

### ৪. ডেটা লেয়ার টেস্ট করো (টাকা ছাড়া)
```bash
python3 test_dry_run.py
```
BONK-এর RugCheck + DexScreener + Jupiter quote — তিনটাই OK আসলে ডেটা লেয়ার ঠিক আছে।

### ৫. বট চালাও
```bash
python3 bot.py
```
Telegram-এ তোমার বটকে `/start` দাও।

## লাইভ ট্রেড চালু করার আগে — অবশ্যই পড়ো

1. **আলাদা ওয়ালেট বানাও:** `python3 generate_wallet.py` — নতুন ওয়ালেটের address আসবে। মেইন ওয়ালেট কখনোই ব্যবহার করবে না।
2. **ছোট অ্যামাউন্ট দাও:** প্রথমে 0.05–0.1 SOL পাঠিয়ে টেস্ট করো।
3. **Private key কাউকে দিও না** — আমাকে, Telegram-এ, WhatsApp-এ, কাউকে না। শুধু `.env` ফাইলে রাখো। `.env` কখনো git-এ দিও না।
4. **DRY_RUN=false** করো শুধু ছোট টেস্ট সফল হওয়ার পর।
5. **24 ঘণ্টা চালু রাখতে** হোস্টিং লাগবে — Railway / Render / VPS। নিজের কম্পিউটার বন্ধ করলে বটও বন্ধ হয়ে যাবে, auto TP/SL তখন কাজ করবে না।

## কমান্ড

```
/start, /help — শুরু
/wallet — ওয়ালেট + ব্যালেন্স
/check <mint> — rug check
/buy <mint> <sol> — যেমন /buy <mint> 0.5
/sell <mint> <pct> — যেমন /sell <mint> 50
/positions — খোলা ট্রেড
/pnl — মোট লাভ-লস
/settings — TP/SL/slippage দেখো
/set_tp 2 — TP বদলাও
/set_sl -30 — SL বদলাও
/set_slippage 100 — slippage (bps, 100 = 1%)

Copy trade:
/copy_add <wallet> [sol] — ট্রেডার follow করো, যেমন /copy_add <wallet> 0.1
/copy_list — follow করা ওয়ালেটগুলো
/copy_remove <wallet> — follow বন্ধ করো
/copy_on <wallet> / /copy_off <wallet>
/copy_sl <wallet> <pct> — ওই ওয়ালেটের কপির SL, যেমন /copy_sl <wallet> -30
/copy_set <wallet> <sol> — কপি buy-এর SOL বদলাও
```

Mint address এমনি পেস্ট করলেও rug check + Buy বাটন আসবে।

## ম্যানুয়াল / অটো মোড (v1.4) ⚙️

সব কিছুতে এখন দুটো সিস্টেম — যেটা চাও সেটা চালাও। `/mode` দিলে সব দেখাবে, ট্যাপ করলেই বদলে যাবে (বা লিখে: `/mode copy manual`)। পছন্দ DB-তে সেভ থাকে, restart-এও বদলায় না।

| জিনিস | 🤖 AUTO | ✋ MANUAL |
|---|---|---|
| **TP/SL sell** | TP/SL হিট হলেই বট নিজে বিক্রি করে (আগের মতো) | শুধু অ্যালার্ট দেয় — "TP hit, sell?" + Sell বাটন; তুমি না চাপলে কিছু বিক্রি হয় না |
| **Copy trade** | টার্গেট কিনলে/বেচলেই বট কপি করে (আগের মতো) | প্রতিটা কপির আগে approve বাটন আসে — "Copy buy 0.1 SOL?" ✅ চাপলে তবেই কপি হয় |
| **Tweet-to-Token launch** | `/launch <link>` দিলেই প্রিভিউ ছাড়া preset dev buy-তে লঞ্চ হয় | প্রিভিউ দেখে তুমি Launch বাটন চাপো (আগের মতো) |
| **Buy** | আলাদা auto নেই — Buy-এর auto রূপই হলো Copy trade আর Auto-launch | mint পেস্ট করে Buy বাটন চাপো / `/buy` |

ডিফল্ট: TP/SL auto, Copy auto, Launch manual।

Auto-launch-এর preset dev buy বদলাতে: `/launch_set 0.1` (সর্বোচ্চ 1 SOL)। টুইটে ছবি না থাকলে auto মোডেও ছবি চাইবে — ছবি পাঠালেই সঙ্গে সঙ্গে লঞ্চ হবে।

⚠️ মনে রেখো: auto মানে বট তোমার হয়ে টাকা খরচ করবে — auto-launch আর auto-copy চালু করার আগে DRY_RUN-এ দেখে নাও বট কী করতো, আর ছোট amount দিয়ে শুরু করো।

## 0.02 SOL থেকে শুরু + অ্যাডভান্সড অপশন (v1.5) 🚀

সব Buy এখন **0.02 SOL থেকে শুরু** — Buy বাটন: 0.02 / 0.1 / 0.5 / 1 SOL, Launch dev buy বাটন: 0.02 / 0.05 / 0.1 / 0.5 SOL, আর `/buy`, copy trade, `/launch_set` — সব জায়গায় সর্বনিম্ন 0.02 SOL। এর নিচে দিলে বট মানা করবে (fee/slippage-এ ছোট trade খেয়ে যায়)।

আরও ৪টা অ্যাডভান্সড অপশন যোগ হয়েছে:

1. **Trailing breakeven SL** — TP 2x-এ 50% বেচার পর বাকি 50%-এর SL entry price-এ (0%) উঠে যায়। মানে লাভের অংশটা আর লসে বন্ধ হবে না। ডিফল্ট ON — বন্ধ করতে `/trail off`।
2. **Buy the dip** — `/dip_add <mint> <target price USD> <sol>` — দাম target-এ নামলেই বট rug check করে auto-buy করে (সর্বোচ্চ ৫টা watch)। দেখতে `/dip_list`, বাতিল করতে `/dip_remove <mint>`।
3. **Strict rug check** — `/rug strict` দিলে WARNING টোকেনও আর কেনা হবে না, শুধু GOOD টোকেন কিনবে। ফিরতে `/rug normal` (ডিফল্ট)।
4. **Priority fee** — `/set_priority 10000` — নেটওয়ার্ক busy থাকলে swap-এ fixed priority fee (lamports) দেবে, buy দ্রুত land করবে। 0 মানে Jupiter auto (ডিফল্ট)।

সব সেটিং দেখতে `/settings`।

## যেকোনো wallet connect করো — Phantom / Jupiter / Solflare (v1.6) 👛

একটা সত্যি কথা আগে: Telegram bot-এ ওয়েবসাইটের মতো "Connect Wallet" বোতাম হয় না। Auto TP/SL, copy trade, dip buy — এগুলোর জন্য বটকে নিজে sign করতে হয়, আর সেটা শুধু তখনই সম্ভব যখন key বটের কাছেই থাকে। তাই এখানে connect মানে: **wallet-এর private key export করে Railway Variables-এ বসানো**। একবার বসালেই connect — Phantom, Jupiter Mobile, Solflare, যেকোনো Solana wallet চলবে।

**কীভাবে connect করবা (বটে `/connect` দিলেও এটাই দেখাবে):**
- **Phantom:** app → Settings → Manage Accounts → wallet → Show Private Key → key কপি করো।
- **Jupiter Mobile:** app → Profile/Settings → Wallets → wallet → Export Private Key।
- **Solflare:** Settings → Export Private Key।
- তারপর Railway Variables-এ: `WALLET_PRIVATE_KEY` = key। আর কখনো key চ্যাটে পাঠাবা না।

**একসাথে ৩টা wallet:** `WALLET_PRIVATE_KEY_2` / `_3` (নাম দিতে `WALLET_NAME` / `_2` / `_3`, যেমন Phantom, Jupiter)। `/wallet` দিলে সব wallet + balance দেখাবে, `/wallet_use <name>` দিয়ে active wallet বদলাতে পারবা — সব buy/sell/launch তখন ওই wallet দিয়ে হবে।

⚠️ **সতর্কতা:** key দেওয়া মানে বট ওই wallet থেকে খরচ করতে পারবে। Main wallet connect করো না — Phantom-এই নতুন wallet বানিয়ে (Manage Accounts → Add Wallet) অল্প SOL রেখে সেটা connect করো, সেটাই সবচেয়ে নিরাপদ।

## সুন্দর /start + বাটন (v1.7) ✨

`/start` এখন ছোট আর পরিষ্কার — শুধু welcome, DRY-RUN/TP/SL স্ট্যাটাস আর কী কী করে সেটা। নিচে ৫টা বাটন: 🔍 Check a token, 👛 My wallet, ⚙️ Modes, 🚀 Launch token, 📖 All commands — ট্যাপ করলেই সেই কাজটা খুলবে। পুরো কমান্ডের লিস্ট এখন শুধু `/help`-এ।

## Tweet থেকে টোকেন লঞ্চ (v1.3) 🚀

কোনো জনপ্রিয় X/Twitter পোস্ট ভাইরাল হলে, সেই পোস্টের লিংক দিলেই বট Pump.fun-এ টোকেন লঞ্চ করে দেবে:

```
/launch https://x.com/someone/status/123...
```

1. বট পোস্টটি পড়ে (FxTwitter mirror — X API key লাগে না, শুধু public পোস্ট)
2. পোস্টের **প্রথম ছবি** নেয় + টেক্সট থেকে **নাম/সিম্বল** বানায়
3. টোকেনের সোশ্যাল লিংকের জায়গায় **তোমার দেওয়া টুইট লিংক** বসায়
4. প্রিভিউ দেখায় — চাইলে `/launch_name <নাম>` / `/launch_symbol <NEW>` দিয়ে বদলাতে পারো
5. তুমি **dev buy** বাছো (0.05 / 0.1 / 0.5 SOL বাটন) → ট্যাপ করলেই লঞ্চ

টুইটে ছবি না থাকলে বট ছবি চাইবে — একটা ছবি পাঠালেই সেটা টোকেনের ছবি হবে।

⚠️ **সত্যি কথা:**
- এটা **মিম টোকেন** — পোস্টের ক্রিয়েটরের official টোকেন না। কখনো official বলে চালাবে না।
- লঞ্চ **irreversible** — একবার লঞ্চ হলে ফেরানো যায় না। খরচ ~0.02 SOL ফি + তোমার dev buy + PumpPortal 0.5%।
- DRY_RUN-এ কিছুই লঞ্চ হয় না — শুধু প্রিভিউ দেখায়। লাইভ করার আগে DRY_RUN-এ একবার দেখে নাও।
- লঞ্চের পর dev buy-টা position হিসেবে রেজিস্টার হয়, তাই আগের TP/SL auto-monitor সেটার উপরও চলবে।

## API নোট (যাচাই করা 2026-11-16)

- Pump.fun লঞ্চ: Pump.fun IPFS (`https://pump.fun/api/ipfs`, ফ্রি, key ছাড়া) + PumpPortal Local API (`https://pumpportal.fun/api/trade-local`, action `create`) — বট নিজেই sign করে, PumpPortal কখনো তোমার private key দেখে না।
- Tweet পড়া: `api.fxtwitter.com` (v1/v2) + fallback `api.vxtwitter.com` — কোনো key লাগে না, শুধু public টুইট।

## সতর্কতা

- RugCheck GOOD মানেই নিরাপদ না — এটা শুধু obvious rug flag ধরে। Memecoin কয়েক মিনিটে শূন্য হতে পারে।
- এই বট কোনো লাভের গ্যারান্টি দেয় না। শুধু হারানোর মতো টাকা দিয়ে ট্রেড করো।
- Jupiter quote 30 সেকেন্ডের বেশি পুরনো হলে বট নতুন quote নেবে — stale quote দিয়ে ট্রেড হবে না।
- RugCheck API মাঝে মাঝে 429 / Cloudflare ব্লক দেয়। তখন বট UNKNOWN দেখিয়ে buy ব্লক করবে — এটা ইচ্ছাকৃত, নিরাপত্তার জন্য।

## ফাইল

- `bot.py` — Telegram বট (মূল)
- `jupiter.py` — Jupiter swap (lite-api)
- `rugcheck.py` — RugCheck + DexScreener
- `positions.py` — position + PNL (SQLite)
- `copytrade.py` — copy trade: Helius / RPC দিয়ে টার্গেট ওয়ালেটের swap ধরা + follow করা
- `config.py` — `.env` থেকে সেটিংস
- `generate_wallet.py` — নতুন ট্রেডিং ওয়ালেট বানায়
- `test_dry_run.py` — টাকা/টোকেন ছাড়া API টেস্ট
- `test_copytrade.py` — কপি parser টেস্ট (mock tx, নেটওয়ার্ক ছাড়া)
- `twitter_fetch.py` — টুইট পড়া (FxTwitter/vxTwitter, key ছাড়া) + নাম/সিম্বল বানানো
- `launcher.py` — Pump.fun IPFS + PumpPortal create (mint + wallet দুটো দিয়েই sign)
- `test_launcher.py` — লঞ্চার টেস্ট (mock + live টুইট fetch, কোনো টোকেন লঞ্চ হয় না)
- `modes.py` — ম্যানুয়াল/auto মোড (SQLite-তে সেভ থাকে) + auto-launch preset dev buy
- `test_modes.py` — মোড টেস্ট (defaults, set/get, ভুল ইনপুট বাতিল, callback সাইজ)
- `watch.py` — dip watchlist (SQLite-তে সেভ থাকে, সর্বোচ্চ ৫টা)
- `test_advanced.py` — v1.5 টেস্ট (0.02 floor, trailing SL, dip watch, strict rug, priority fee)
- `wallets.py` — multi-wallet registry (সর্বোচ্চ ৩টা wallet, active wallet DB-তে সেভ থাকে)
- `test_wallets.py` — v1.6 টেস্ট (registry, switch, ভুল key বাতিল)
- `test_ui.py` — v1.7 টেস্ট (welcome টেক্সট, বাটন, /help লিস্ট)
