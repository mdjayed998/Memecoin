"""
Config for Solana Memecoin Telegram Bot v1
- All secrets come from .env, never hardcoded, never sent in Telegram chat.
- DRY_RUN=True by default: bot shows what it WOULD buy/sell, sends no real tx.
  Set DRY_RUN=false only after you tested with a small separate wallet.
"""
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "0") or 0)

SOLANA_RPC_URL = os.getenv(
    "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
)
# Private key: base58 string of the TRADING wallet only.
# Use a fresh, small wallet for this bot. Never your main wallet.
WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "")

# v1.6 multi-wallet: connect up to 3 wallets at once (Phantom, Jupiter,
# Solflare — any Solana wallet works, see /connect in Telegram).
# Each slot is (display name, private key). Empty key = slot unused.
# Switch the active one in Telegram with /wallet_use <name>.
def _wallet_slots():
    slots = []
    for key_env, name_env, dflt in [
        ("WALLET_PRIVATE_KEY", "WALLET_NAME", "Wallet 1"),
        ("WALLET_PRIVATE_KEY_2", "WALLET_NAME_2", "Wallet 2"),
        ("WALLET_PRIVATE_KEY_3", "WALLET_NAME_3", "Wallet 3"),
    ]:
        name = (os.getenv(name_env, dflt) or dflt).strip() or dflt
        slots.append((name, os.getenv(key_env, "")))
    return slots


WALLETS = _wallet_slots()

# Trading defaults (user can change with /settings in Telegram)
DEFAULT_SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "100"))  # 100 = 1%
DEFAULT_TP_MULT = float(os.getenv("TP_MULT", "2.0"))  # 2x -> sell 50%
DEFAULT_TP_SELL_PCT = int(os.getenv("TP_SELL_PCT", "50"))
DEFAULT_SL_PCT = float(os.getenv("SL_PCT", "-30"))  # -30% -> sell 100%
MAX_BUY_SOL = float(os.getenv("MAX_BUY_SOL", "1.0"))  # safety cap per buy
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")

# Live APIs verified 2026-11-16 (see README -> API notes):
# - Jupiter: lite-api.jup.ag/swap/v1/quote + /swap/v1/swap
#   (old quote-api.jup.ag/v6 is dead, do NOT use it)
# - RugCheck: api.rugcheck.xyz/v1/tokens/{mint}/report/summary
# - DexScreener: api.dexscreener.com/latest/dex/tokens/{mint}
JUPITER_QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
JUPITER_SWAP_URL = "https://lite-api.jup.ag/swap/v1/swap"
JUPITER_PRICE_URL = "https://lite-api.jup.ag/price/v3"
RUGCHECK_SUMMARY_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
RUGCHECK_REPORT_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
DEXSCREENER_TOKENS_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

WSOL_MINT = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 9
LAMPORTS_PER_SOL = 1_000_000_000

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
# Copy trading v1.2: fixed SOL per copied buy, poll interval, max targets.
# Helius (free key at dashboard.helius.dev) is preferred for copy detection;
# without it the bot falls back to plain RPC parsing (slower, rate-limited).
COPY_POLL_SECONDS = int(os.getenv("COPY_POLL_SECONDS", "15"))
COPY_DEFAULT_SOL = float(os.getenv("COPY_DEFAULT_SOL", "0.1"))
COPY_MAX_TARGETS = int(os.getenv("COPY_MAX_TARGETS", "5"))

# Tweet -> Pump.fun launcher v1.3: max SOL the bot will spend as a dev buy
# at launch (safety cap). PumpPortal takes 0.5% of the dev buy; creation
# itself costs ~0.02 SOL in network fees. Optional free API key at
# pumpportal.fun — set PUMPPORTAL_API_KEY in .env if the API asks for one.
LAUNCH_MAX_DEV_BUY_SOL = float(os.getenv("LAUNCH_MAX_DEV_BUY_SOL", "1.0"))

# Manual / Auto modes v1.4: initial defaults only — /mode in Telegram
# overrides them, and the choice is saved in the DB (modes.py) so it
# survives restarts. tpsl/copy default auto, launch defaults manual.
MODE_TP_SL = os.getenv("MODE_TP_SL", "auto").strip().lower()
MODE_COPY = os.getenv("MODE_COPY", "auto").strip().lower()
MODE_LAUNCH = os.getenv("MODE_LAUNCH", "manual").strip().lower()
LAUNCH_DEFAULT_DEV_BUY = float(os.getenv("LAUNCH_DEFAULT_DEV_BUY", "0.1"))

# v1.5: smallest allowed buy anywhere (buttons, /buy, copy, launch dev buy).
# 0.02 SOL is the floor — below that, Jupiter fees/slippage eat the trade.
MIN_BUY_SOL = float(os.getenv("MIN_BUY_SOL", "0.02"))
# Priority fee for swaps in lamports (0 = Jupiter "auto"). Set higher with
# /set_priority when the network is busy and buys keep landing late.
DEFAULT_PRIORITY_FEE_LAMPORTS = int(os.getenv("DEFAULT_PRIORITY_FEE_LAMPORTS", "0"))

DB_PATH = os.getenv(
    "DB_PATH", os.path.join(os.path.dirname(__file__), "positions.db")
)
# Hosting note: on Railway/Render the local disk is wiped on redeploy,
# which would erase open positions + PNL history. Set DB_PATH to a
# persistent volume path instead, e.g. DB_PATH=/data/positions.db
# with a Railway volume mounted at /data.
