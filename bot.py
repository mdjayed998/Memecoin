"""
Solana Memecoin Telegram Bot v1.6 (connect any wallet)
Commands:
  /start /help  - intro + safety
  /wallet       - all connected wallets + balances + which is active
  /wallet_use <name> - switch the active trading wallet
  /connect      - how to connect Phantom / Jupiter / Solflare
  /check <mint> - RugCheck + DexScreener report
  /buy <mint> <sol>   - e.g. /buy <mint> 0.5 (min 0.02 SOL)
  /sell <mint> <pct>  - e.g. /sell <mint> 50
  /positions    - open positions + live PNL
  /pnl          - total realised PNL
  /settings     - show TP/SL/slippage, change with /set_tp 2 /set_sl -30
  /set_tp <mult> /set_sl <pct> /set_slippage <bps>
  /mode         - manual/auto modes for TP/SL, copy trade, launch
  /mode <feature> <manual|auto> - e.g. /mode copy manual
  /launch_set <sol> - preset dev buy for auto-launch (e.g. /launch_set 0.1)
  Advanced (v1.5):
  /trail on|off - trailing breakeven SL after TP (default on)
  /rug strict|normal - strict blocks WARNING tokens too (default normal)
  /dip_add <mint> <target price USD> <sol> - buy the dip automatically
  /dip_list / /dip_remove <mint>
  /set_priority <lamports> - fixed priority fee, 0 = Jupiter auto
  Copy trade:
  /copy_add <wallet> [sol] - follow a trader's WALLET (default 0.1 SOL/copy buy)
  /copy_list    - followed wallets + on/off
  /copy_remove <wallet>
  /copy_on <wallet> / /copy_off <wallet>
  /copy_sl <wallet> <pct> - own stop-loss for that wallet's copies, e.g. -30
  /copy_set <wallet> <sol> - SOL per copied buy
  Launch:
  /launch <tweet link> - tweet -> Pump.fun token (manual: preview + confirm,
                        auto: launches at once with the preset dev buy)

Paste a mint address alone and the bot replies with a rug check
+ Buy 0.02 / 0.1 / 0.5 / 1 SOL buttons.

SAFETY:
- Only OWNER_TELEGRAM_ID can use the bot. Everyone else gets refused.
- DRY_RUN=true (default) = no real transaction is sent. It logs what it WOULD do.
- Real trading needs a separate small wallet. Never use your main wallet.
- The private key is only read from .env on the server. Never paste it in Telegram.
"""
import asyncio
import base64
import logging
import secrets

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import copytrade as ct
import jupiter
import launcher
import modes as md
import positions as pos
import twitter_fetch as tw
import wallets
import watch
from rugcheck import check_token, fetch_dexscreener, format_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("memecoin-bot")

SOL_MINT = config.WSOL_MINT
_client: AsyncClient | None = None
_keypair: Keypair | None = None

# Per-user TP/SL overrides (in-memory, defaults from config)
_settings = {
    "tp_mult": config.DEFAULT_TP_MULT,
    "tp_sell_pct": config.DEFAULT_TP_SELL_PCT,
    "sl_pct": config.DEFAULT_SL_PCT,
    "slippage_bps": config.DEFAULT_SLIPPAGE_BPS,
    "priority_fee_lamports": config.DEFAULT_PRIORITY_FEE_LAMPORTS,
}

# Buy buttons — start at 0.02 SOL (v1.5 floor).
BUY_AMOUNTS = (0.02, 0.1, 0.5, 1.0)


def _buy_keyboard(mint: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🟢 Buy {a} SOL", callback_data=f"buy:{mint}:{a}")
        for a in BUY_AMOUNTS
    ]])

# Manual/auto mode defaults (from .env; DB rows in modes.py override them).
_MODE_DEFAULTS = {
    "tpsl": config.MODE_TP_SL,
    "copy": config.MODE_COPY,
    "launch": config.MODE_LAUNCH,
}


def _mode(feature: str) -> str:
    return md.get_mode(feature, _MODE_DEFAULTS[feature])


# Manual-mode TP/SL alerts already sent: {(mint, "tp"|"sl")} — so the
# 20s monitor does not spam the same alert. Cleared when the price
# recovers or the position is sold.
_manual_alerts: set = set()

# Manual-mode copy approvals waiting for a tap: {key: {mint, sol, wallet}}.
# Keys are short random ids so the approve button fits Telegram's 64-byte
# callback limit (a mint + wallet would not fit).
_pending_copy: dict = {}


def get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = AsyncClient(config.SOLANA_RPC_URL)
    return _client


_keypair_name: str | None = None


def get_keypair() -> Keypair | None:
    """Active connected wallet's keypair (v1.6 multi-wallet).
    Cache is keyed by wallet name, so /wallet_use switches take effect."""
    global _keypair, _keypair_name
    name, kp = wallets.get_active_keypair()
    if kp is None:
        _keypair, _keypair_name = None, None
        return None
    if _keypair is not None and _keypair_name == name:
        return _keypair
    _keypair, _keypair_name = kp, name
    return _keypair


def is_owner(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    if config.OWNER_TELEGRAM_ID == 0:
        return True  # first-run convenience; set OWNER_TELEGRAM_ID in .env!
    return uid == config.OWNER_TELEGRAM_ID


async def guard(update: Update) -> bool:
    if not is_owner(update):
        await update.effective_message.reply_text("⛔ This bot is private.")
        return False
    return True


def looks_like_mint(text: str) -> bool:
    t = text.strip()
    return 32 <= len(t) <= 44 and all(c.isalnum() for c in t)


async def sol_balance(pubkey: Pubkey) -> float:
    client = get_client()
    resp = await client.get_balance(pubkey)
    return resp.value / 1e9


# ---------- commands ----------

def _welcome_text() -> str:
    """Short, clean /start message (v1.7). Full list lives in /help."""
    dry = ("🧪 <b>DRY-RUN ON</b> — no real trades yet"
           if config.DRY_RUN else "🔴 <b>LIVE TRADING ON</b>")
    return (
        "👋 <b>Solana Memecoin Bot v1.7</b>\n\n"
        f"{dry}\n"
        f"🎯 TP {_settings['tp_mult']}x → sell {_settings['tp_sell_pct']}%"
        f"   •   🛑 SL {_settings['sl_pct']}%\n\n"
        "<b>What I do</b>\n"
        "🔍 Rug check + fast buy / sell\n"
        "📋 Copy trade — follow a trader's wallet\n"
        "🚀 Tweet → Pump.fun token launch\n"
        "📉 Buy the dip   •   ⚙️ Manual / Auto modes\n\n"
        "Paste a mint address to check it, or tap below 👇\n\n"
        "⚠️ Memecoins can go to zero. Only trade what you can afford to lose."
    )


def _start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Check a token", callback_data="menu:check"),
         InlineKeyboardButton("👛 My wallet", callback_data="menu:wallet")],
        [InlineKeyboardButton("⚙️ Modes", callback_data="menu:mode"),
         InlineKeyboardButton("🚀 Launch token", callback_data="menu:launch")],
        [InlineKeyboardButton("📖 All commands", callback_data="menu:help")],
    ])


def _help_text() -> str:
    return (
        "📖 <b>All commands</b>\n\n"
        "<b>Trade</b>\n"
        "/check &lt;mint&gt; — rug check\n"
        "/buy &lt;mint&gt; &lt;sol&gt; — e.g. /buy &lt;mint&gt; 0.5 (min 0.02)\n"
        "/sell &lt;mint&gt; &lt;pct&gt; — e.g. /sell &lt;mint&gt; 50\n"
        "/positions — open trades\n"
        "/pnl — total PNL\n\n"
        "<b>Wallet</b>\n"
        "/wallet — balances\n"
        "/wallet_use &lt;name&gt; — switch active wallet\n"
        "/connect — connect Phantom / Jupiter / Solflare\n\n"
        "<b>Copy trade</b>\n"
        "/copy_add &lt;wallet&gt; [sol] — follow a trader\n"
        "/copy_list — followed wallets\n"
        "/copy_remove /copy_on /copy_off /copy_sl /copy_set\n\n"
        "<b>Launch</b>\n"
        "/launch &lt;tweet link&gt; — tweet → Pump.fun token\n"
        "/launch_name /launch_symbol /launch_set\n\n"
        "<b>Modes &amp; advanced</b>\n"
        "/mode — manual / auto for TP/SL, copy, launch\n"
        "/settings — TP/SL/slippage/priority\n"
        "/dip_add &lt;mint&gt; &lt;price&gt; &lt;sol&gt; — buy the dip\n"
        "/trail on|off — trailing breakeven SL\n"
        "/rug strict|normal — strict rug check\n"
        "/set_priority &lt;lamports&gt; — faster swaps"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.effective_message.reply_text(
        _welcome_text(), parse_mode="HTML", reply_markup=_start_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.effective_message.reply_text(_help_text(), parse_mode="HTML")


_MENU_TARGETS = {
    "check": "check_cmd",
    "wallet": "wallet_cmd",
    "mode": "mode_cmd",
    "launch": "launch_cmd",
    "help": "help_cmd",
}


async def on_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick-action buttons under /start. Runs the matching command —
    with no args each one shows its info/usage, exactly like typing it."""
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        await query.edit_message_text("⛔ This bot is private.")
        return
    target = (query.data or "").split(":")[1] if ":" in (query.data or "") else ""
    fn_name = _MENU_TARGETS.get(target)
    if not fn_name:
        return
    await globals()[fn_name](update, context)


async def wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    all_w = wallets.list_wallets()
    valid = [w for w in all_w if w["valid"]]
    if not valid:
        await update.effective_message.reply_text(
            "No wallet connected yet.\n"
            "Phantom, Jupiter, Solflare — যেকোনো wallet connect করা যায়, দেখো: /connect\n"
            "Or make a fresh one: python3 generate_wallet.py"
        )
        return
    active = wallets.get_active_name()
    lines = ["👛 Connected wallets:\n"]
    for w in all_w:
        if not w["valid"]:
            if w["connected"]:
                lines.append(f"\n⚠️ <b>{w['name']}</b> — key is invalid, check Railway Variables.")
            continue
        try:
            bal = await sol_balance(Pubkey.from_string(w["pubkey"]))
            bal_txt = f"{bal:.4f} SOL"
        except Exception:
            bal_txt = "?"
        mark = " ✅ ACTIVE" if w["name"] == active else ""
        lines.append(
            f"\n<b>{w['name']}</b>{mark}\n<code>{w['pubkey']}</code>\nBalance: <b>{bal_txt}</b>")
    lines.append(f"\nMode: {'DRY-RUN 🧪' if config.DRY_RUN else 'LIVE 🔴'}")
    lines.append("Switch wallet: /wallet_use <name>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def wallet_use_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        names = [w["name"] for w in wallets.valid_wallets()]
        await update.effective_message.reply_text(
            "Usage: /wallet_use <name>\nConnected: "
            + (", ".join(names) if names else "none — see /connect"))
        return
    name = " ".join(context.args).strip()
    try:
        wallets.set_active_name(name)
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}")
        return
    kp = get_keypair()
    await update.effective_message.reply_text(
        f"✅ Active wallet: <b>{name}</b>\n<code>{kp.pubkey() if kp else '?'}</code>\n"
        f"All buys/sells/launches will now use this wallet.",
        parse_mode="HTML")


async def connect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.effective_message.reply_text(
        "👛 <b>Connect any wallet — Phantom / Jupiter / Solflare</b>\n\n"
        "Telegram bot-এ ওয়েবসাইটের মতো 'Connect' বোতাম হয় না — auto TP/SL, copy trade, "
        "dip buy-এর জন্য বটকে নিজে sign করতে হয়, তাই key বটের কাছেই থাকতে হয়।\n"
        "তাই connect মানে: wallet-এর <b>private key export</b> করে Railway Variables-এ বসানো। "
        "একবার বসালেই connect — যেকোনো Solana wallet চলবে, একসাথে ৩টা পর্যন্ত।\n\n"
        "<b>Phantom</b>\n"
        "Phantom app → Settings → Manage Accounts → তোমার wallet → Show Private Key "
        "(বা ⋯ → Export Private Key) → key কপি করো।\n\n"
        "<b>Jupiter Mobile</b>\n"
        "Jupiter app → Profile/Settings → Wallets → তোমার wallet → Export Private Key → key কপি করো।\n\n"
        "<b>Solflare</b>\n"
        "Solflare → Settings → Export Private Key → key কপি করো।\n\n"
        "<b>Railway-তে বসাও (key কখনো চ্যাটে পাঠাবা না)</b>\n"
        "Variables-এ: <code>WALLET_PRIVATE_KEY</code> = key (১ম wallet)\n"
        "আরও wallet চাইলে: <code>WALLET_PRIVATE_KEY_2</code> / <code>_3</code>, "
        "নাম দিতে: <code>WALLET_NAME</code> / <code>_2</code> / <code>_3</code> (যেমন Phantom, Jupiter)\n"
        "তারপর redeploy → <b>/wallet</b> দিয়ে দেখো, <b>/wallet_use &lt;name&gt;</b> দিয়ে switch করো।\n\n"
        "⚠️ <b>সতর্কতা:</b> key দেওয়া মানে বট ওই wallet থেকে খরচ করতে পারবে। Main wallet-এর সব টাকা "
        "রাখা wallet connect করো না — Phantom-এই নতুন একটা wallet বানিয়ে (Manage Accounts → Add Wallet) "
        "অল্প SOL রেখে সেটা connect করো, সেটাই সবচেয়ে নিরাপদ।",
        parse_mode="HTML")


async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /check <mint address>")
        return
    mint = context.args[0].strip()
    await update.effective_message.reply_text("🔍 Checking… (RugCheck + DexScreener)")
    result = await check_token(mint)
    text = format_report(mint, result)
    keyboard = [[b for b in _buy_keyboard(mint).inline_keyboard[0]]] if result.get("safe_to_buy") else []
    await update.effective_message.reply_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


def _rug_blocked(verdict: str, strict: bool) -> bool:
    """Pure helper (v1.5): is this verdict blocked from buying?
    DANGER/UNKNOWN are always blocked. WARNING is blocked only in strict mode."""
    if verdict in ("DANGER", "UNKNOWN"):
        return True
    return bool(strict) and verdict == "WARNING"


async def do_buy(update_or_query, mint: str, sol_amount: float, context,
                 source: str = "manual", sl_pct: float | None = None) -> str:
    """Shared buy logic for /buy, inline buttons and copy trades. Returns reply text.
    Copy buys pass source='copy:<wallet>' and their own per-target sl_pct.
    """
    if sol_amount < config.MIN_BUY_SOL or sol_amount > config.MAX_BUY_SOL:
        return (f"⛔ Buy amount must be {config.MIN_BUY_SOL} ≤ SOL ≤ {config.MAX_BUY_SOL} "
                f"(min 0.02 SOL, safety cap {config.MAX_BUY_SOL} SOL).")
    # 1. Rug check first — refuse DANGER
    result = await check_token(mint)
    if result["verdict"] == "DANGER":
        return (
            f"⛔ BLOCKED — RugCheck says DANGER (score {result.get('score')}).\n"
            + "\n".join(result.get("reasons") or [])
            + "\nI did not buy."
        )
    if result["verdict"] == "UNKNOWN":
        return (
            "⛔ BLOCKED — could not verify this token (no RugCheck report).\n"
            "New / unlisted tokens are the riskiest. Check manually on rugcheck.xyz first."
        )
    if _rug_blocked(result["verdict"], md.get_setting("rug") == "strict"):
        return (
            f"⛔ BLOCKED — strict rug mode: WARNING tokens are not bought "
            f"(score {result.get('score')}).\n"
            + "\n".join(result.get("reasons") or [])
            + "\nSwitch back with /rug normal if you really want this one."
        )
    # 2. Quote
    try:
        quote = await jupiter.get_quote(
            SOL_MINT, mint, jupiter.sol_to_lamports(sol_amount), _settings["slippage_bps"]
        )
    except Exception as e:
        return f"❌ Jupiter quote failed: {e}\nNo trade was made."
    out_amount = int(quote.get("outAmount") or 0)
    if out_amount <= 0:
        return "❌ No route / no liquidity for this token. No trade was made."

    price_usd = result.get("price_usd")
    symbol = result.get("token_symbol") or mint[:6]

    if config.DRY_RUN:
        return (
            f"🧪 DRY-RUN BUY (no real tx)\n"
            f"{symbol} — {sol_amount} SOL → ~{out_amount:,} tokens\n"
            f"RugCheck: {result['verdict']} (score {result.get('score')})\n"
            f"Price: ${price_usd if price_usd else '?'}\n"
            f"Set DRY_RUN=false in .env to trade live."
        )

    # 3. Live: build + sign + send
    kp = get_keypair()
    if kp is None:
        return "❌ No wallet configured. See /wallet."
    try:
        swap = await jupiter.build_swap_tx(quote, str(kp.pubkey()), _settings["slippage_bps"],
                                           _settings["priority_fee_lamports"])
        raw_tx = jupiter.sign_and_serialize(swap["swapTransaction"], kp)
        client = get_client()
        resp = await client.send_raw_transaction(raw_tx)
        sig = str(resp.value)
    except Exception as e:
        return f"❌ Swap failed: {e}\nCheck SOL balance + slippage, then retry small."
    eff_sl = sl_pct if sl_pct is not None else _settings["sl_pct"]
    if price_usd:
        pos.open_position(mint, symbol, price_usd, float(out_amount),
                          sol_amount, _settings["tp_mult"],
                          _settings["tp_sell_pct"], eff_sl, source=source)
    return (
        f"✅ BOUGHT {symbol}\n{sol_amount} SOL → {out_amount:,} tokens\n"
        f"TX: https://solscan.io/tx/{sig}\n"
        f"TP {_settings['tp_mult']}x → sell {_settings['tp_sell_pct']}%, "
        f"SL {eff_sl}% — auto-monitor is on."
    )


async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /buy <mint> <sol> — e.g. /buy <mint> 0.5")
        return
    try:
        amount = float(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("SOL amount must be a number, e.g. 0.5")
        return
    await update.effective_message.reply_text("🔍 Rug-checking, then quoting…")
    text = await do_buy(update, context.args[0].strip(), amount, context)
    await update.effective_message.reply_text(text)


async def do_sell(mint: str, pct: float) -> str:
    position = pos.get_position(mint)
    if not position and config.DRY_RUN:
        return "🧪 DRY-RUN SELL — no open position recorded for this mint in dry-run mode."
    if not position:
        return "No open position for this mint. Use /positions to see what you hold."
    tokens = position["amount_tokens"] * (pct / 100.0)
    if tokens <= 0:
        return "Nothing to sell."
    if config.DRY_RUN:
        return f"🧪 DRY-RUN SELL {pct}% of {position['symbol']} (~{tokens:,.0f} tokens). No real tx."
    kp = get_keypair()
    if kp is None:
        return "❌ No wallet configured."
    # Need token decimals: fetch via DexScreener pairs is not enough;
    # use Jupiter quote with the token as input — amount in raw units.
    # We approximate raw units from stored amount (Jupiter outAmount was raw).
    try:
        quote = await jupiter.get_quote(
            mint, SOL_MINT, int(tokens), _settings["slippage_bps"]
        )
        swap = await jupiter.build_swap_tx(quote, str(kp.pubkey()), _settings["slippage_bps"],
                                           _settings["priority_fee_lamports"])
        raw_tx = jupiter.sign_and_serialize(swap["swapTransaction"], kp)
        client = get_client()
        resp = await client.send_raw_transaction(raw_tx)
        sig = str(resp.value)
        sol_out = int(quote.get("outAmount") or 0) / 1e9
    except Exception as e:
        return f"❌ Sell failed: {e}"
    if pct >= 100:
        pos.close_position(mint, tokens, sol_out)
    else:
        pos.update_after_partial_sell(mint, tokens, sol_out)
    return f"✅ SOLD {pct}% of {position['symbol']} → ~{sol_out:.4f} SOL\nTX: https://solscan.io/tx/{sig}"


async def sell_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /sell <mint> <pct> — e.g. /sell <mint> 50")
        return
    try:
        pct = float(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("Percent must be a number, e.g. 50")
        return
    text = await do_sell(context.args[0].strip(), pct)
    await update.effective_message.reply_text(text)


async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    items = pos.list_positions()
    if not items:
        await update.effective_message.reply_text("No open positions.")
        return
    lines = ["📊 Open positions:"]
    for p in items:
        result = await check_token(p["mint"])
        cur = result.get("price_usd")
        if cur and p["entry_price_usd"]:
            chg = (cur / p["entry_price_usd"] - 1) * 100
            lines.append(f"\n{p['symbol']} — {chg:+.1f}%  (entry ${p['entry_price_usd']:.8g} → ${cur:.8g})")
        else:
            lines.append(f"\n{p['symbol']} — price unavailable")
        lines.append(f"  {p['amount_tokens']:,.0f} tokens • {p['sol_spent']} SOL in")
        src = p.get("source") or "manual"
        lines.append(f"  TP {p['tp_mult']}x / SL {p['sl_pct']}%"
                     + (f" • 📋 copy {src[5:11]}…" if src.startswith("copy:") else ""))
    await update.effective_message.reply_text("\n".join(lines))


async def pnl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    total = pos.total_pnl_sol()
    trades = pos.recent_trades(5)
    lines = [f"💰 Total realised PNL: {total:+.4f} SOL"]
    for mint, sym, side, amt, sol_amt, pnl, _ts in trades:
        lines.append(f"{side} {sym} {sol_amt} SOL (pnl {pnl:+.4f})")
    await update.effective_message.reply_text("\n".join(lines))


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.effective_message.reply_text(
        f"⚙️ Settings\nTP: {_settings['tp_mult']}x → sell {_settings['tp_sell_pct']}%\n"
        f"SL: {_settings['sl_pct']}%\nSlippage: {_settings['slippage_bps']} bps\n"
        f"Min buy: {config.MIN_BUY_SOL} SOL • Max buy: {config.MAX_BUY_SOL} SOL\n"
        f"Priority fee: {_settings['priority_fee_lamports']} lamports "
        f"({'auto' if _settings['priority_fee_lamports'] == 0 else 'fixed'})\n"
        f"Trailing breakeven SL: {md.get_setting('trail').upper()}\n"
        f"Rug check: {md.get_setting('rug').upper()}\n"
        "Change: /set_tp 2  /set_sl -30  /set_slippage 100\n"
        "Advanced: /trail on|off  /rug strict|normal  /set_priority 10000"
    )


async def set_tp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    try:
        _settings["tp_mult"] = float(context.args[0])
        await update.effective_message.reply_text(f"TP set to {_settings['tp_mult']}x")
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /set_tp 2")


async def set_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    try:
        _settings["sl_pct"] = float(context.args[0])
        await update.effective_message.reply_text(f"SL set to {_settings['sl_pct']}%")
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /set_sl -30")


async def set_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    try:
        _settings["slippage_bps"] = int(context.args[0])
        await update.effective_message.reply_text(f"Slippage set to {_settings['slippage_bps']} bps")
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /set_slippage 100")


async def on_mint_paste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    mint = update.message.text.strip()
    if not looks_like_mint(mint):
        return
    await update.effective_message.reply_text("🔍 Checking…")
    result = await check_token(mint)
    text = format_report(mint, result)
    keyboard = [[b for b in _buy_keyboard(mint).inline_keyboard[0]]] if result.get("safe_to_buy") else []
    await update.effective_message.reply_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        await query.edit_message_text("⛔ This bot is private.")
        return
    try:
        _kind, mint, amount = query.data.split(":")
        sol_amount = float(amount)
    except ValueError:
        return
    await query.edit_message_text("🔍 Rug-checking, then quoting…")
    text = await do_buy(query, mint, sol_amount, context)
    await query.edit_message_text(text)


# ---------- tweet -> Pump.fun token launcher (v1.3) ----------
# Flow: /launch <tweet link> -> bot fetches the tweet (FxTwitter mirror,
# no X API key), takes its first image + proposes name/symbol from the text,
# puts YOUR tweet link in the token's social field, then you pick a dev buy
# amount and confirm. Nothing launches until you tap a Launch button.
# Safety: the token is a MEME inspired by the post — never the creator's
# official token. Don't present it as official. Launching is irreversible
# and costs ~0.02 SOL in fees + your dev buy + PumpPortal 0.5% on the buy.
# In DRY_RUN the bot only shows what it WOULD launch.

LAUNCH_AMOUNTS = (0.02, 0.05, 0.1, 0.5)


def _launch_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🚀 Launch {a} SOL", callback_data=f"launch:{a}")
        for a in LAUNCH_AMOUNTS
    ], [InlineKeyboardButton("❌ Cancel", callback_data="launch:cancel")]])


def _launch_preview_text(p: dict) -> str:
    return (
        f"🚀 Token preview — check it before launching!\n\n"
        f"Name: <b>{p['name']}</b>\n"
        f"Symbol: <b>${p['symbol']}</b>\n"
        f"From: @{p.get('handle') or '?'} — {p.get('author') or ''}\n"
        f"Tweet: {p['tweet_url']}\n"
        f"Image: {'✅ from tweet' if p.get('image') else '❌ none — send a photo, or cancel'}\n"
        f"Description: {p['description']}\n\n"
        f"Change it first if you want:\n"
        f"/launch_name &lt;new name&gt;   /launch_symbol &lt;NEW&gt;\n\n"
        f"⚠️ This is a MEME inspired by the post — NOT the creator's official token.\n"
        f"Launching is permanent. Cost: ~0.02 SOL fees + your dev buy.\n"
        f"Pick a dev buy amount to launch:"
    )


async def _send_launch_preview(update, context, p: dict):
    kb = _launch_keyboard()
    if p.get("image"):
        await update.message.reply_photo(
            photo=p["image"], caption=_launch_preview_text(p),
            parse_mode="HTML", reply_markup=kb)
    else:
        await update.effective_message.reply_text(_launch_preview_text(p),
                                        parse_mode="HTML", reply_markup=kb)


async def launch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /launch <tweet link>\n"
            "Example: /launch https://x.com/someone/status/123...\n\n"
            "The bot reads the tweet, takes its image + a name from the text, "
            "puts the tweet link in the token's socials, and asks you how much "
            "SOL to buy at launch. Nothing launches until you confirm."
        )
        return
    url = context.args[0].strip()
    await update.effective_message.reply_text("🔍 Reading the tweet…")
    try:
        tweet = await tw.fetch_tweet(url)
    except Exception as e:
        await update.effective_message.reply_text(f"❌ {e}")
        return
    meta = tw.derive_token_meta(tweet["text"], tweet["handle"])
    image = None
    if tweet.get("image_url"):
        try:
            image = await launcher.download_image(tweet["image_url"])
        except Exception as e:
            await update.effective_message.reply_text(f"⚠️ Could not download the tweet image ({e}) — send a photo yourself, or cancel.")
    pending = {
        "tweet_url": tweet["tweet_url"], "author": tweet["author"],
        "handle": tweet["handle"], "text": tweet["text"],
        "image": image, "name": meta["name"], "symbol": meta["symbol"],
        "description": meta["description"],
    }
    # Auto-launch mode: skip the preview, launch at once with the preset
    # dev buy. Manual mode (default): show the preview, you tap to launch.
    if _mode("launch") == "auto":
        if not image:
            context.user_data["pending_launch"] = pending
            await update.effective_message.reply_text(
                "⚠️ Auto-launch is ON, but this tweet has no image — send a photo "
                "and I will launch it at once. Or switch back: /mode launch manual")
            await _send_launch_preview(update, context, pending)
            return
        if not config.DRY_RUN and get_keypair() is None:
            await update.effective_message.reply_text(
                "❌ No trading wallet connected. See /connect in Telegram, or run generate_wallet.py and set WALLET_PRIVATE_KEY in Railway Variables.")
            return
        dev_buy = md.get_launch_dev_buy(config.LAUNCH_DEFAULT_DEV_BUY)
        await update.effective_message.reply_text(
            f"🚀 Auto-launch ON — launching <b>{pending['name']}</b> "
            f"(${pending['symbol']}) with {dev_buy} SOL dev buy…",
            parse_mode="HTML")
        try:
            msg = await _do_launch(pending, dev_buy)
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Launch failed: {e}\nNothing was launched (or check Solscan before retrying).")
            return
        await update.effective_message.reply_text(msg, parse_mode="HTML")
        return
    context.user_data["pending_launch"] = pending
    await _send_launch_preview(update, context, pending)


async def launch_name_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    p = context.user_data.get("pending_launch")
    if not p:
        await update.effective_message.reply_text("No launch pending. Start with /launch <tweet link> first.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /launch_name <new token name>")
        return
    try:
        p["name"] = tw.sanitize_name(" ".join(context.args))
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}")
        return
    await update.effective_message.reply_text(f"Name updated to: {p['name']}\nTap a Launch button on the preview to launch, or /launch again to refresh it.")


async def launch_symbol_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    p = context.user_data.get("pending_launch")
    if not p:
        await update.effective_message.reply_text("No launch pending. Start with /launch <tweet link> first.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /launch_symbol <NEW>")
        return
    try:
        p["symbol"] = tw.sanitize_symbol(context.args[0])
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}")
        return
    await update.effective_message.reply_text(f"Symbol updated to: ${p['symbol']}\nTap a Launch button on the preview to launch, or /launch again to refresh it.")


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Photo sent while a launch is pending without an image -> use it as the token image."""
    if not await guard(update):
        return
    p = context.user_data.get("pending_launch")
    if not p or p.get("image"):
        return
    try:
        photo = update.message.photo[-1]
        f = await photo.get_file()
        data = bytes(await f.download_as_bytearray())
        if len(data) > launcher.MAX_IMAGE_BYTES:
            await update.effective_message.reply_text("❌ That image is too big (max 5 MB). Send a smaller one.")
            return
        p["image"] = data
        # Auto-launch mode + photo just arrived = launch at once.
        if _mode("launch") == "auto":
            if not config.DRY_RUN and get_keypair() is None:
                await update.effective_message.reply_text("❌ No trading wallet connected. See /connect in Telegram, or run generate_wallet.py and set WALLET_PRIVATE_KEY in Railway Variables.")
                return
            dev_buy = md.get_launch_dev_buy(config.LAUNCH_DEFAULT_DEV_BUY)
            await update.effective_message.reply_text(
                f"🚀 Photo received — auto-launching <b>{p['name']}</b> with {dev_buy} SOL…",
                parse_mode="HTML")
            try:
                msg = await _do_launch(p, dev_buy)
            except Exception as e:
                await update.effective_message.reply_text(f"❌ Launch failed: {e}")
                return
            context.user_data.pop("pending_launch", None)
            await update.effective_message.reply_text(msg, parse_mode="HTML")
            return
        await update.effective_message.reply_text("✅ Image saved for the launch. Tap a Launch button on the preview.")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Could not read that photo: {e}")


async def _do_launch(p: dict, dev_buy: float) -> str:
    """Launch the token from a pending dict. Returns the result message.
    Shared by the manual Launch button, auto-launch, and auto-launch-by-photo.
    Raises on create failure."""
    result = await launcher.create_token(
        p["name"], p["symbol"], p["description"], p["tweet_url"],
        p["image"], dev_buy, dry_run=config.DRY_RUN)
    if result.get("dry_run"):
        return (
            f"🧪 DRY-RUN LAUNCH (nothing was created)\n\n"
            f"Would launch: <b>{result['name']}</b> (${result['symbol']})\n"
            f"Tweet: {result['twitter_url']}\nDev buy: {result['dev_buy_sol']} SOL\n"
            f"Description: {result['description']}\n\n"
            f"Set DRY_RUN=false in .env to launch for real."
        )
    mint = result["mint"]
    msg = (
        f"🎉 LAUNCHED <b>{p['name']}</b> (${p['symbol']})\n\n"
        f"Mint: <code>{mint}</code>\n"
        f"Pump.fun: https://pump.fun/{mint}\n"
        f"TX: https://solscan.io/tx/{result['signature']}\n"
        f"Dev buy: {dev_buy} SOL\n\n"
        f"⚠️ Remember: this is a meme inspired by the tweet, not the creator's official token."
    )
    # Try to register the dev buy as a position so TP/SL monitor can track it.
    try:
        await asyncio.sleep(10)
        dex = await fetch_dexscreener(mint)
        pairs = dex.get("pairs") or []
        if pairs:
            best = max(pairs, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
            price_native = float(best.get("priceNative") or 0)
            price_usd = float(best.get("priceUsd") or 0)
            if price_native > 0 and price_usd > 0:
                amount_tokens = dev_buy / price_native
                pos.open_position(mint, p["symbol"], price_usd, amount_tokens,
                                  dev_buy, _settings["tp_mult"],
                                  _settings["tp_sell_pct"], _settings["sl_pct"],
                                  source="launch")
                msg += f"\n✅ Dev buy registered as a position — TP {_settings['tp_mult']}x / SL {_settings['sl_pct']}% auto-monitor ON."
    except Exception as e:
        log.warning("launch position registration failed for %s: %s", mint, e)
        msg += "\n(Price not indexed yet — use /check to track it, then /buy works as normal.)"
    return msg


async def on_launch_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        await query.edit_message_text("⛔ This bot is private.")
        return
    data = (query.data or "").split(":")
    if len(data) < 2:
        return
    if data[1] == "cancel":
        context.user_data.pop("pending_launch", None)
        await query.edit_message_text("Launch cancelled. Nothing was created.")
        return
    try:
        dev_buy = float(data[1])
    except ValueError:
        return
    p = context.user_data.get("pending_launch")
    if not p:
        await query.edit_message_text("This launch preview expired. Run /launch <tweet link> again.")
        return
    if not p.get("image"):
        await query.edit_message_text("❌ No image for this token yet — send a photo first, then tap Launch again.")
        return
    if dev_buy <= 0 or dev_buy > config.LAUNCH_MAX_DEV_BUY_SOL:
        await query.edit_message_text(f"⛔ Dev buy must be ≤ {config.LAUNCH_MAX_DEV_BUY_SOL} SOL.")
        return
    if not config.DRY_RUN and get_keypair() is None:
        await query.edit_message_text("❌ No trading wallet connected. See /connect in Telegram, or run generate_wallet.py and set WALLET_PRIVATE_KEY in Railway Variables.")
        return
    await query.edit_message_text(
        f"🚀 Launching <b>{p['name']}</b> (${p['symbol']}) on Pump.fun…\n"
        f"Dev buy: {dev_buy} SOL. This takes ~30s (IPFS upload + on-chain tx).",
        parse_mode="HTML")
    try:
        msg = await _do_launch(p, dev_buy)
    except Exception as e:
        await query.edit_message_text(f"❌ Launch failed: {e}\nNothing was launched (or check Solscan before retrying).")
        return
    context.user_data.pop("pending_launch", None)
    await query.edit_message_text(msg, parse_mode="HTML")


# ---------- manual / auto modes (v1.4) ----------

_MODE_LABELS = {
    "tpsl": "TP/SL sell",
    "copy": "Copy trade",
    "launch": "Tweet-to-Token launch",
}
_MODE_HELP = {
    "tpsl": "auto = TP/SL hit হলেই বট নিজে বিক্রি করে; manual = শুধু অ্যালার্ট দেয়, তুমি Sell বাটন চাপলে বিক্রি হয়।",
    "copy": "auto = টার্গেট কিনলে/বেচলেই বট কপি করে; manual = প্রতিটা কপির আগে approve বাটন আসে।",
    "launch": "manual = /launch-এ প্রিভিউ দেখে তুমি Launch চাপো; auto = /launch দিলেই প্রিভিউ ছাড়া preset dev buy-তে লঞ্চ হয়।",
}


def _mode_keyboard():
    rows = []
    for f in md.FEATURES:
        cur = _mode(f)
        rows.append([
            InlineKeyboardButton(
                f"{_MODE_LABELS[f]}: {'🤖 AUTO' if cur == 'auto' else '✋ MANUAL'}",
                callback_data=f"mode:{f}:{'manual' if cur == 'auto' else 'auto'}"),
        ])
    return InlineKeyboardMarkup(rows)


def _mode_text() -> str:
    lines = ["⚙️ Manual / Auto modes — ট্যাপ করে বদলাও:\n"]
    for f in md.FEATURES:
        cur = _mode(f)
        lines.append(f"\n<b>{_MODE_LABELS[f]}</b>: {'🤖 AUTO' if cur == 'auto' else '✋ MANUAL'}\n{_MODE_HELP[f]}")
    lines.append(f"\nAuto-launch preset dev buy: {md.get_launch_dev_buy(config.LAUNCH_DEFAULT_DEV_BUY)} SOL "
                 f"(বদলাতে: /launch_set <sol>)")
    lines.append("\nBuy-এর আলাদা auto নেই — Buy-এর auto রূপই হলো Copy trade আর Auto-launch।")
    return "\n".join(lines)


async def mode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    # /mode <feature> <manual|auto> — text form
    if len(context.args) >= 2:
        try:
            new = md.set_mode(context.args[0].strip().lower(), context.args[1])
        except ValueError as e:
            await update.effective_message.reply_text(f"❌ {e}\nUsage: /mode <tpsl|copy|launch> <manual|auto>")
            return
        await update.effective_message.reply_text(
            f"✅ {_MODE_LABELS[context.args[0].strip().lower()]} → {'🤖 AUTO' if new == 'auto' else '✋ MANUAL'}\n"
            f"{_MODE_HELP[context.args[0].strip().lower()]}",
            parse_mode="HTML")
        return
    await update.effective_message.reply_text(_mode_text(), parse_mode="HTML",
                                    reply_markup=_mode_keyboard())


async def launch_set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            f"Auto-launch preset dev buy: {md.get_launch_dev_buy(config.LAUNCH_DEFAULT_DEV_BUY)} SOL\n"
            "Usage: /launch_set <sol> — e.g. /launch_set 0.1 (max 1.0)")
        return
    try:
        sol = float(context.args[0])
        md.set_launch_dev_buy(sol)
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}")
        return
    await update.effective_message.reply_text(f"✅ Auto-launch dev buy set to {sol} SOL.")


async def on_mode_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        await query.edit_message_text("⛔ This bot is private.")
        return
    try:
        _, feature, new = (query.data or "").split(":")
        md.set_mode(feature, new)
    except ValueError:
        return
    await query.edit_message_text(_mode_text(), parse_mode="HTML",
                                  reply_markup=_mode_keyboard())


def _sell_keyboard(mint: str, pcts) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🔴 Sell {int(p)}%", callback_data=f"sell:{mint}:{int(p)}")
        for p in pcts
    ]])


async def on_sell_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sell buttons from TP/SL manual alerts and copy-sell alerts."""
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        await query.edit_message_text("⛔ This bot is private.")
        return
    try:
        _, mint, pct = (query.data or "").split(":")
        pct = float(pct)
    except ValueError:
        return
    text = await do_sell(mint, pct)
    _manual_alerts.discard((mint, "tp"))
    _manual_alerts.discard((mint, "sl"))
    await query.edit_message_text(text)


async def on_copybuy_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve (copybuy:) or ignore (copyignore:) a manual copy-trade alert."""
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        await query.edit_message_text("⛔ This bot is private.")
        return
    data = (query.data or "").split(":")
    kind = data[0]
    key = data[1] if len(data) > 1 else ""
    pending = _pending_copy.pop(key, None)
    if kind == "copyignore":
        await query.edit_message_text("Copy ignored — nothing was bought.")
        return
    if not pending:
        await query.edit_message_text("This copy approval expired (already handled or bot restarted).")
        return
    await query.edit_message_text("🔍 Rug-checking, then quoting…")
    text = await do_buy(query, pending["mint"], pending["sol"], context,
                        source=f"copy:{pending['wallet']}",
                        sl_pct=pending.get("sl_pct"))
    await query.edit_message_text(f"📋 COPY BUY (approved by you)\n{text}")


# ---------- advanced options (v1.5) ----------

async def trail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            f"Trailing breakeven SL: {'ON' if md.get_setting('trail') == 'on' else 'OFF'}\n"
            "TP 2x-এ 50% বেচার পর বাকি 50%-এর SL entry price-এ (0%) উঠে যায় — "
            "লাভের অংশটা আর লসে বন্ধ হয় না।\nUsage: /trail on | /trail off")
        return
    try:
        v = md.set_setting("trail", context.args[0])
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}")
        return
    await update.effective_message.reply_text(
        "✅ Trailing breakeven SL ON — TP-এর পর বাকি position entry-তে protect থাকবে."
        if v == "on" else
        "✅ Trailing SL OFF — TP-এর পরও আগের SL-ই থাকবে।")


async def rug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            f"Rug check mode: {md.get_setting('rug').upper()}\n"
            "normal = DANGER/UNKNOWN ব্লক, WARNING কেনা যায়।\n"
            "strict = WARNING-ও ব্লক, শুধু GOOD টোকেন কেনা যায়।\n"
            "Usage: /rug strict | /rug normal")
        return
    try:
        v = md.set_setting("rug", context.args[0])
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}")
        return
    await update.effective_message.reply_text(
        "✅ Rug check STRICT — WARNING টোকেনও আর কেনা হবে না।"
        if v == "strict" else
        "✅ Rug check NORMAL — WARNING টোকেন কেনা যাবে, DANGER ব্লক থাকবে।")


async def dip_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Usage: /dip_add <mint> <target price USD> <sol>\n"
            "Example: /dip_add <mint> 0.000001 0.1\n"
            "দাম target-এ নামলেই বট rug check করে auto-buy করবে। Max 5 টা watch।")
        return
    mint = context.args[0].strip()
    try:
        target = float(context.args[1])
        sol = float(context.args[2])
    except ValueError:
        await update.effective_message.reply_text("Target price and SOL must be numbers, e.g. 0.000001 0.1")
        return
    if target <= 0:
        await update.effective_message.reply_text("Target price must be above 0.")
        return
    if sol < config.MIN_BUY_SOL or sol > config.MAX_BUY_SOL:
        await update.effective_message.reply_text(
            f"SOL must be {config.MIN_BUY_SOL} ≤ SOL ≤ {config.MAX_BUY_SOL}.")
        return
    await update.effective_message.reply_text("🔍 Checking current price…")
    try:
        result = await check_token(mint)
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Could not check this token: {e}")
        return
    cur = result.get("price_usd")
    if not cur:
        await update.effective_message.reply_text("❌ No price found for this token yet.")
        return
    if target >= cur:
        await update.effective_message.reply_text(
            f"Target ${target:g} is already above the current price ${cur:g} — "
            f"it would buy immediately. Set a target below ${cur:g}.")
        return
    symbol = result.get("token_symbol") or mint[:6]
    if not watch.add_dip(mint, symbol, target, sol):
        await update.effective_message.reply_text(
            f"Max {watch.MAX_DIPS} dip watches. Remove one first (/dip_remove).")
        return
    await update.effective_message.reply_text(
        f"📉 Dip watch ON — {symbol}\nCurrent: ${cur:g} → buy at ${target:g} "
        f"with {sol} SOL (rug check first).\nSee all: /dip_list")


async def dip_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    dips = watch.list_dips()
    if not dips:
        await update.effective_message.reply_text(
            "No dip watches. Add one: /dip_add <mint> <target price USD> <sol>")
        return
    lines = ["📉 Dip watches:"]
    for d in dips:
        lines.append(
            f"\n<b>{d['symbol']}</b> — buy {d['sol']} SOL at ${d['target_price_usd']:g}\n"
            f"<code>{d['mint']}</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def dip_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /dip_remove <mint>")
        return
    ok = watch.remove_dip(context.args[0].strip())
    await update.effective_message.reply_text("Dip watch removed." if ok else "No watch for that mint. See /dip_list.")


async def set_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            f"Priority fee: {_settings['priority_fee_lamports']} lamports "
            f"({'Jupiter auto' if _settings['priority_fee_lamports'] == 0 else 'fixed'})\n"
            "Usage: /set_priority <lamports> — e.g. /set_priority 10000 (0 = auto).\n"
            "নেটওয়ার্ক busy থাকলে buy দেরিতে land করলে এটা বাড়াও।")
        return
    try:
        lamports = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Lamports must be a whole number, e.g. 10000")
        return
    if lamports < 0 or lamports > 1_000_000:
        await update.effective_message.reply_text("Priority fee must be 0 – 1,000,000 lamports.")
        return
    _settings["priority_fee_lamports"] = lamports
    await update.effective_message.reply_text(
        "Priority fee set to Jupiter auto." if lamports == 0 else
        f"Priority fee set to {lamports} lamports per swap.")


# ---------- copy trading commands ----------

def _looks_like_wallet(text: str) -> bool:
    return looks_like_mint(text)  # same shape: base58, 32-44 chars


async def copy_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /copy_add <trader wallet address> [sol per copy buy]\n"
            "Example: /copy_add 7xK...9Qp 0.1\n"
            "This is the trader's WALLET address, not a token mint.\n"
            "The bot copies that wallet's buys/sells. Your own SL still applies."
        )
        return
    wallet = context.args[0].strip()
    if not _looks_like_wallet(wallet):
        await update.effective_message.reply_text("That doesn't look like a Solana wallet address.")
        return
    try:
        sol = float(context.args[1]) if len(context.args) > 1 else config.COPY_DEFAULT_SOL
    except ValueError:
        await update.effective_message.reply_text("SOL amount must be a number, e.g. 0.1")
        return
    if sol < config.MIN_BUY_SOL or sol > config.MAX_BUY_SOL:
        await update.effective_message.reply_text(f"SOL per copy buy must be {config.MIN_BUY_SOL} ≤ SOL ≤ {config.MAX_BUY_SOL}.")
        return
    if ct.get_target(wallet):
        await update.effective_message.reply_text("Already following this wallet. Use /copy_set to change the amount.")
        return
    if not ct.add_target(wallet, sol, _settings["sl_pct"]):
        await update.effective_message.reply_text(f"Max {config.COPY_MAX_TARGETS} copy targets. Remove one first (/copy_remove).")
        return
    mode = "Helius" if config.HELIUS_API_KEY else "plain RPC (slower — set HELIUS_API_KEY for faster detection)"
    await update.effective_message.reply_text(
        f"📋 Copy ON for <code>{wallet}</code>\n"
        f"Per copied buy: {sol} SOL • Own SL: {_settings['sl_pct']}%\n"
        f"Detection: {mode}, every ~{config.COPY_POLL_SECONDS}s.\n"
        "Target BUY → bot buys the same token. Target SELL → bot sells the same %.\n"
        "If the token drops to your SL first, the bot sells anyway — it never waits for the target.\n"
        "Note: the bot only starts tracking from NOW. Buys the target made before this are not copied.",
        parse_mode="HTML",
    )


async def copy_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    targets = ct.list_targets()
    if not targets:
        await update.effective_message.reply_text("No copy targets. Add one: /copy_add <wallet> [sol]")
        return
    lines = ["📋 Copy targets:"]
    for t in targets:
        lines.append(
            f"\n<code>{t['wallet'][:8]}…{t['wallet'][-4:]}</code> — "
            f"{'🟢 ON' if t['enabled'] else '🔴 OFF'} • {t['sol_per_trade']} SOL/buy • SL {t['sl_pct']}%"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def copy_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /copy_remove <wallet>")
        return
    ok = ct.remove_target(context.args[0].strip())
    await update.effective_message.reply_text("Removed." if ok else "That wallet was not in your copy list.")


async def copy_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /copy_on <wallet>")
        return
    ok = ct.set_target_enabled(context.args[0].strip(), True)
    await update.effective_message.reply_text("Copy ON." if ok else "Wallet not found. See /copy_list.")


async def copy_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /copy_off <wallet>")
        return
    ok = ct.set_target_enabled(context.args[0].strip(), False)
    await update.effective_message.reply_text("Copy OFF — the bot will ignore this wallet until /copy_on.")
    # Note: copy_off does NOT sell your open copy positions. Sell them with /sell.


async def copy_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /copy_sl <wallet> <pct> — e.g. /copy_sl <wallet> -30")
        return
    try:
        pct = float(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("Percent must be a number, e.g. -30")
        return
    ok = ct.set_target_sl(context.args[0].strip(), pct)
    await update.effective_message.reply_text(
        f"Copy SL set to {pct}% for this wallet." if ok else "Wallet not found. See /copy_list."
    )


async def copy_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /copy_set <wallet> <sol> — e.g. /copy_set <wallet> 0.1")
        return
    try:
        sol = float(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("SOL must be a number, e.g. 0.1")
        return
    if sol < config.MIN_BUY_SOL or sol > config.MAX_BUY_SOL:
        await update.effective_message.reply_text(f"SOL per copy buy must be {config.MIN_BUY_SOL} ≤ SOL ≤ {config.MAX_BUY_SOL}.")
        return
    ok = ct.set_target_sol(context.args[0].strip(), sol)
    await update.effective_message.reply_text(
        f"Copy buy set to {sol} SOL." if ok else "Wallet not found. See /copy_list."
    )


async def copy_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every COPY_POLL_SECONDS. Copies each enabled target's new swaps."""
    for target in ct.list_targets():
        if not target["enabled"]:
            continue
        wallet = target["wallet"]
        try:
            trades = await ct.poll_target(wallet)
        except Exception as e:
            log.warning("copy poll error for %s: %s", wallet, e)
            continue
        for trade in trades:
            mint = trade["mint"]
            chat_id = config.OWNER_TELEGRAM_ID
            short = f"{wallet[:6]}…{wallet[-4:]}"
            try:
                if trade["side"] == "BUY":
                    if config.DRY_RUN:
                        if chat_id:
                            await context.bot.send_message(
                                chat_id,
                                f"🧪 COPY (dry-run) — {short} BOUGHT <code>{mint}</code>\n"
                                f"Bot would buy {target['sol_per_trade']} SOL. No real tx.",
                                parse_mode="HTML",
                            )
                        continue
                    # Manual copy mode: only alert + approve button, never buy alone.
                    if _mode("copy") == "manual":
                        if not chat_id:
                            continue
                        key = secrets.token_hex(4)
                        _pending_copy[key] = {
                            "mint": mint, "sol": target["sol_per_trade"],
                            "wallet": wallet, "sl_pct": target["sl_pct"],
                        }
                        if len(_pending_copy) > 20:  # drop the oldest unanswered approval
                            _pending_copy.pop(next(iter(_pending_copy)))
                        kb = InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                f"✅ Copy buy {target['sol_per_trade']} SOL",
                                callback_data=f"copybuy:{key}"),
                            InlineKeyboardButton("❌ Ignore", callback_data=f"copyignore:{key}"),
                        ]])
                        await context.bot.send_message(
                            chat_id,
                            f"📋 COPY (manual) — {short} BOUGHT <code>{mint}</code>\n"
                            f"Copy buy {target['sol_per_trade']} SOL? Tap to approve — "
                            f"nothing is bought until you do.",
                            parse_mode="HTML", reply_markup=kb)
                        continue
                    msg = await do_buy(None, mint, target["sol_per_trade"], context,
                                       source=f"copy:{wallet}", sl_pct=target["sl_pct"])
                    if chat_id:
                        await context.bot.send_message(
                            chat_id, f"📋 COPY BUY — {short} bought\n{msg}")
                else:  # SELL — same % as the target
                    pct = trade.get("sell_pct") or 100.0
                    if config.DRY_RUN:
                        if chat_id:
                            await context.bot.send_message(
                                chat_id,
                                f"🧪 COPY (dry-run) — {short} SOLD {pct:.0f}% of <code>{mint}</code>\n"
                                "Bot would sell the same %. No real tx.",
                                parse_mode="HTML",
                            )
                        continue
                    # Manual copy mode: alert with Sell buttons, never sell alone.
                    if _mode("copy") == "manual":
                        if not chat_id:
                            continue
                        await context.bot.send_message(
                            chat_id,
                            f"📋 COPY SELL (manual) — {short} sold {pct:.0f}% of <code>{mint}</code>\n"
                            "Tap to sell the same % — nothing is sold until you do.",
                            parse_mode="HTML",
                            reply_markup=_sell_keyboard(mint, [pct, 100]))
                        continue
                    msg = await do_sell(mint, float(pct))
                    if chat_id:
                        await context.bot.send_message(
                            chat_id, f"📋 COPY SELL — {short} sold {pct:.0f}%\n{msg}")
            except Exception as e:
                log.warning("copy action error for %s: %s", mint, e)


# ---------- auto TP/SL monitor ----------

def _effective_sl_pct(p: dict, trail_on: bool) -> float:
    """Pure helper (v1.5 trailing breakeven SL): once TP has sold 50%
    (tp_done) and trail is on, the remaining position's stop moves up to
    entry price (0%) — the trade can no longer close at a loss."""
    if trail_on and p.get("tp_done"):
        return 0.0
    return p["sl_pct"]


async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 20s. AUTO mode sells on TP/SL; MANUAL mode only alerts
    with Sell buttons (once per trigger — no spam). Also checks dip watches."""
    if config.DRY_RUN:
        return
    manual = _mode("tpsl") == "manual"
    trail_on = md.get_setting("trail") == "on"
    for p in pos.list_positions():
        try:
            result = await check_token(p["mint"])
            cur = result.get("price_usd")
            if not cur or not p["entry_price_usd"]:
                continue
            mult = cur / p["entry_price_usd"]
            chg_pct = (mult - 1) * 100
            chat_id = config.OWNER_TELEGRAM_ID
            if not chat_id:
                continue
            eff_sl = _effective_sl_pct(p, trail_on)
            trailing = trail_on and p.get("tp_done")
            if not p["tp_done"] and mult >= p["tp_mult"]:
                if manual:
                    if (p["mint"], "tp") not in _manual_alerts:
                        _manual_alerts.add((p["mint"], "tp"))
                        await context.bot.send_message(
                            chat_id,
                            f"🎯 TAKE-PROFIT {mult:.2f}x on {p['symbol']} (manual mode)\n"
                            f"Tap to sell — nothing is sold until you do.",
                            reply_markup=_sell_keyboard(p["mint"], [p["tp_sell_pct"], 100]))
                else:
                    msg = await do_sell(p["mint"], float(p["tp_sell_pct"]))
                    await context.bot.send_message(chat_id, f"🎯 TAKE-PROFIT {mult:.2f}x on {p['symbol']}\n{msg}")
            elif chg_pct <= eff_sl:
                label = "🛑 TRAILING SL (breakeven)" if trailing else "🛑 STOP-LOSS"
                if manual:
                    if (p["mint"], "sl") not in _manual_alerts:
                        _manual_alerts.add((p["mint"], "sl"))
                        await context.bot.send_message(
                            chat_id,
                            f"{label} {chg_pct:.1f}% on {p['symbol']} (manual mode)\n"
                            f"Tap to sell 100% — nothing is sold until you do.",
                            reply_markup=_sell_keyboard(p["mint"], [100]))
                else:
                    msg = await do_sell(p["mint"], 100)
                    await context.bot.send_message(chat_id, f"{label} {chg_pct:.1f}% on {p['symbol']}\n{msg}")
            else:
                # Price recovered — allow a fresh alert if it hits again later.
                _manual_alerts.discard((p["mint"], "tp"))
                _manual_alerts.discard((p["mint"], "sl"))
        except Exception as e:
            log.warning("monitor error for %s: %s", p["mint"], e)
    # Dip watches: price at/below target -> rug-check + auto-buy, then remove.
    chat_id = config.OWNER_TELEGRAM_ID
    for d in watch.list_dips():
        try:
            result = await check_token(d["mint"])
            cur = result.get("price_usd")
            if not cur or cur > d["target_price_usd"]:
                continue
            msg = await do_buy(None, d["mint"], d["sol"], context, source="dip")
            watch.remove_dip(d["mint"])
            if chat_id:
                await context.bot.send_message(
                    chat_id,
                    f"📉 DIP HIT — {d['symbol']} fell to ${cur:g} "
                    f"(target ${d['target_price_usd']:g})\n{msg}",
                    parse_mode="HTML")
        except Exception as e:
            log.warning("dip check error for %s: %s", d["mint"], e)


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env first. See README.md")
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("wallet", wallet_cmd))
    app.add_handler(CommandHandler("wallet_use", wallet_use_cmd))
    app.add_handler(CommandHandler("connect", connect_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("sell", sell_cmd))
    app.add_handler(CommandHandler("positions", positions_cmd))
    app.add_handler(CommandHandler("pnl", pnl_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("set_tp", set_tp))
    app.add_handler(CommandHandler("set_sl", set_sl))
    app.add_handler(CommandHandler("set_slippage", set_slippage))
    app.add_handler(CommandHandler("copy_add", copy_add))
    app.add_handler(CommandHandler("copy_list", copy_list))
    app.add_handler(CommandHandler("copy_remove", copy_remove))
    app.add_handler(CommandHandler("copy_on", copy_on))
    app.add_handler(CommandHandler("copy_off", copy_off))
    app.add_handler(CommandHandler("copy_sl", copy_sl))
    app.add_handler(CommandHandler("copy_set", copy_set))
    app.add_handler(CommandHandler("launch", launch_cmd))
    app.add_handler(CommandHandler("launch_name", launch_name_cmd))
    app.add_handler(CommandHandler("launch_symbol", launch_symbol_cmd))
    app.add_handler(CommandHandler("launch_set", launch_set_cmd))
    app.add_handler(CommandHandler("mode", mode_cmd))
    app.add_handler(CommandHandler("trail", trail_cmd))
    app.add_handler(CommandHandler("rug", rug_cmd))
    app.add_handler(CommandHandler("dip_add", dip_add))
    app.add_handler(CommandHandler("dip_list", dip_list))
    app.add_handler(CommandHandler("dip_remove", dip_remove))
    app.add_handler(CommandHandler("set_priority", set_priority))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(on_launch_button, pattern=r"^launch:"))
    app.add_handler(CallbackQueryHandler(on_menu_button, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(on_mode_button, pattern=r"^mode:"))
    app.add_handler(CallbackQueryHandler(on_sell_button, pattern=r"^sell:"))
    app.add_handler(CallbackQueryHandler(on_copybuy_button, pattern=r"^(copybuy|copyignore):"))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_mint_paste))
    if app.job_queue:
        app.job_queue.run_repeating(monitor_job, interval=20, first=20)
        app.job_queue.run_repeating(copy_job, interval=config.COPY_POLL_SECONDS,
                                    first=config.COPY_POLL_SECONDS)
    log.info("Bot starting. DRY_RUN=%s", config.DRY_RUN)
    app.run_polling()


if __name__ == "__main__":
    main()
