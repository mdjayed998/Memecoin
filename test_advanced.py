"""Tests for v1.5: 0.02 SOL floor + advanced pack (trailing SL, dip watch,
strict rug check, priority fee). Run: python3 test_advanced.py
Uses a temp SQLite DB (DB_PATH env set before importing modes/watch).
No Telegram, no network, no trades.
"""
import os
import sys
import tempfile

TMP_DB = os.path.join(tempfile.mkdtemp(), "test-advanced.db")
os.environ["DB_PATH"] = TMP_DB
sys.path.insert(0, ".")

import config
import jupiter
import modes as md
import watch


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


def main():
    # --- 0.02 SOL floor ---
    check("MIN_BUY_SOL is 0.02", config.MIN_BUY_SOL == 0.02)
    check("MAX_BUY_SOL still 1.0", config.MAX_BUY_SOL == 1.0)
    check("launch dev buy floor 0.02", md.set_launch_dev_buy(0.02) == 0.02)
    for bad in (0, 0.01, -0.1, 1.5):
        try:
            md.set_launch_dev_buy(bad)
            check(f"reject dev buy {bad}", False)
        except ValueError:
            check(f"reject dev buy {bad}", True)

    # --- buy / launch buttons start at 0.02 ---
    import bot
    check("BUY_AMOUNTS start 0.02", bot.BUY_AMOUNTS[0] == 0.02)
    check("BUY_AMOUNTS", bot.BUY_AMOUNTS == (0.02, 0.1, 0.5, 1.0))
    check("LAUNCH_AMOUNTS start 0.02", bot.LAUNCH_AMOUNTS[0] == 0.02)
    kb = bot._buy_keyboard("A" * 44)
    btns = kb.inline_keyboard[0]
    check("buy keyboard has 4 buttons", len(btns) == 4)
    check("buy keyboard first is 0.02",
          btns[0].callback_data == f"buy:{'A' * 44}:0.02")

    # --- trailing breakeven SL (pure helper) ---
    p_open = {"sl_pct": -30.0, "tp_done": 0}
    p_after_tp = {"sl_pct": -30.0, "tp_done": 1}
    check("trail off keeps -30", bot._effective_sl_pct(p_after_tp, False) == -30.0)
    check("trail on before TP keeps -30", bot._effective_sl_pct(p_open, True) == -30.0)
    check("trail on after TP -> breakeven 0",
          bot._effective_sl_pct(p_after_tp, True) == 0.0)

    # --- trail / rug settings (DB-backed) ---
    check("trail default on", md.get_setting("trail") == "on")
    check("rug default normal", md.get_setting("rug") == "normal")
    check("set trail off", md.set_setting("trail", "off") == "off")
    check("set rug strict", md.set_setting("rug", "strict") == "strict")
    check("get trail off", md.get_setting("trail") == "off")
    for bad_name, bad_val in [("trail", "maybe"), ("rug", "hard"), ("nope", "on")]:
        try:
            md.set_setting(bad_name, bad_val)
            check(f"reject setting {bad_name}={bad_val}", False)
        except ValueError:
            check(f"reject setting {bad_name}={bad_val}", True)
    md.set_setting("trail", "on")
    md.set_setting("rug", "normal")

    # --- strict rug check (pure helper) ---
    check("DANGER blocked always", bot._rug_blocked("DANGER", False) is True)
    check("UNKNOWN blocked always", bot._rug_blocked("UNKNOWN", True) is True)
    check("WARNING allowed in normal", bot._rug_blocked("WARNING", False) is False)
    check("WARNING blocked in strict", bot._rug_blocked("WARNING", True) is True)
    check("GOOD allowed even strict", bot._rug_blocked("GOOD", True) is False)

    # --- dip watch storage ---
    check("dips start empty", watch.list_dips() == [])
    for i in range(5):
        check(f"add dip {i+1}", watch.add_dip(f"MINT{i}", f"T{i}", 0.000001, 0.1) is True)
    check("6th dip rejected (max 5)", watch.add_dip("MINT9", "T9", 0.000001, 0.1) is False)
    check("5 dips listed", len(watch.list_dips()) == 5)
    check("update existing dip ok",
          watch.add_dip("MINT0", "T0", 0.0000005, 0.02) is True)
    check("update keeps count 5", len(watch.list_dips()) == 5)
    updated = [d for d in watch.list_dips() if d["mint"] == "MINT0"][0]
    check("updated target", updated["target_price_usd"] == 0.0000005)
    check("updated sol", updated["sol"] == 0.02)
    check("remove dip", watch.remove_dip("MINT0") is True)
    check("remove again fails", watch.remove_dip("MINT0") is False)
    check("4 dips left", len(watch.list_dips()) == 4)
    try:
        watch.add_dip("X", "X", 0, 0.1)
        check("reject zero target", False)
    except ValueError:
        check("reject zero target", True)

    # --- priority fee payload (pure builder) ---
    q = {"quote": "x"}
    check("priority 0 -> auto",
          jupiter.swap_payload(q, "PUBKEY", 0)["prioritizationFeeLamports"] == "auto")
    check("priority 10000 -> '10000'",
          jupiter.swap_payload(q, "PUBKEY", 10000)["prioritizationFeeLamports"] == "10000")
    check("payload keeps quote",
          jupiter.swap_payload(q, "PUBKEY", 5000)["quoteResponse"] == q)

    # --- bot wires the advanced commands ---
    for name in ("trail_cmd", "rug_cmd", "dip_add", "dip_list", "dip_remove",
                 "set_priority", "_effective_sl_pct", "_rug_blocked"):
        check(f"bot has {name}", hasattr(bot, name))
    check("priority default 0",
          bot._settings["priority_fee_lamports"] == 0)

    print("\nAll advanced tests passed.")


if __name__ == "__main__":
    main()
