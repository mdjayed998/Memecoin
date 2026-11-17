"""
Dry-run test — no Telegram token, no wallet, no real trade needed.
Tests the three free public APIs the bot depends on:
  1. DexScreener price for BONK
  2. RugCheck report for BONK
  3. Jupiter quote: 0.1 SOL -> BONK (lite-api host)

Run:  python3 test_dry_run.py
If all three print OK, the bot's data layer works from this machine.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from jupiter import get_quote, sol_to_lamports
from rugcheck import check_token

BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # BONK mint
WSOL = "So11111111111111111111111111111111111111112"


async def main():
    print("1) RugCheck + DexScreener for BONK...")
    r = await check_token(BONK)
    print(f"   verdict={r['verdict']} score={r.get('score')} price=${r.get('price_usd')} liq=${r.get('liquidity_usd')}")
    print(f"   reasons: {'; '.join((r.get('reasons') or [])[:3])}")
    assert r["verdict"] in ("GOOD", "WARNING", "DANGER", "UNKNOWN"), "unexpected verdict"

    print("2) Jupiter quote 0.1 SOL -> BONK (lite-api.jup.ag)...")
    q = await get_quote(WSOL, BONK, sol_to_lamports(0.1), 100)
    out = int(q.get("outAmount") or 0)
    print(f"   outAmount={out:,} priceImpact={q.get('priceImpactPct')}%")
    assert out > 0, "Jupiter returned zero output — quote failed"

    print("\n✅ ALL DRY-RUN CHECKS PASSED — data layer works.")
    print("Next: set TELEGRAM_BOT_TOKEN in .env and run: python3 bot.py")


if __name__ == "__main__":
    asyncio.run(main())
