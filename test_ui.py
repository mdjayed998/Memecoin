"""Tests for v1.7: clean /start welcome + quick-action buttons + /help split.
Run: python3 test_ui.py. No Telegram, no network, no trades.
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test-ui.db")
sys.path.insert(0, ".")

import bot


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


def main():
    # --- welcome text: short, structured, versioned ---
    text = bot._welcome_text()
    check("welcome has v1.7", "v1.7" in text)
    check("welcome has DRY-RUN", "DRY-RUN" in text)
    check("welcome has TP/SL", "TP 2.0x" in text and "SL -30" in text)
    check("welcome is short (<600 chars)", len(text) < 600)
    check("welcome has no /buy wall", "/buy" not in text and "/copy_add" not in text)
    check("welcome mentions buttons", "tap below" in text)

    # --- start keyboard: 5 buttons, menu: callbacks ---
    kb = bot._start_keyboard()
    rows = kb.inline_keyboard
    check("keyboard has 3 rows", len(rows) == 3)
    btns = [b for row in rows for b in row]
    check("keyboard has 5 buttons", len(btns) == 5)
    check("all callbacks are menu:*",
          all(b.callback_data.startswith("menu:") for b in btns))
    check("menu targets exist",
          {b.callback_data.split(":")[1] for b in btns}
          == {"check", "wallet", "mode", "launch", "help"})
    for target in ("check", "wallet", "mode", "launch", "help"):
        check(f"menu target {target} maps to a handler",
              target in bot._MENU_TARGETS
              and hasattr(bot, bot._MENU_TARGETS[target]))

    # --- help text: full list, grouped, no wall-of-text start ---
    help_text = bot._help_text()
    for cmd in ("/check", "/buy", "/sell", "/positions", "/pnl", "/wallet",
                "/copy_add", "/launch", "/mode", "/dip_add", "/trail", "/rug"):
        check(f"help lists {cmd}", cmd in help_text)
    check("help has sections", "Trade" in help_text and "Wallet" in help_text)
    check("bot has on_menu_button", hasattr(bot, "on_menu_button"))

    print("\nAll UI tests passed.")


if __name__ == "__main__":
    main()
