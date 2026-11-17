"""Tests for v1.4 manual/auto mode system. Run: python3 test_modes.py
Uses a temp SQLite DB (DB_PATH env set before importing modes).
No Telegram, no network, no trades.
"""
import os
import sys
import tempfile

TMP_DB = os.path.join(tempfile.mkdtemp(), "test-modes.db")
os.environ["DB_PATH"] = TMP_DB
sys.path.insert(0, ".")

import modes as md


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


def main():
    # --- defaults (fresh DB) ---
    check("default tpsl auto", md.get_mode("tpsl") == "auto")
    check("default copy auto", md.get_mode("copy") == "auto")
    check("default launch manual", md.get_mode("launch") == "manual")
    check("list_modes", md.list_modes() == {"tpsl": "auto", "copy": "auto", "launch": "manual"})

    # --- set/get roundtrip, survives re-read (DB-backed) ---
    check("set tpsl manual", md.set_mode("tpsl", "manual") == "manual")
    check("get tpsl manual", md.get_mode("tpsl") == "manual")
    check("set copy manual", md.set_mode("copy", "manual") == "manual")
    check("set launch auto", md.set_mode("launch", "auto") == "auto")
    check("list after sets",
          md.list_modes() == {"tpsl": "manual", "copy": "manual", "launch": "auto"})
    check("set back to auto", md.set_mode("tpsl", "auto") == "auto")

    # --- invalid inputs rejected ---
    for bad_feature, bad_mode in [("buy", "auto"), ("tpsl", "sometimes"), ("", "auto")]:
        try:
            md.set_mode(bad_feature, bad_mode)
            check(f"reject set_mode({bad_feature!r}, {bad_mode!r})", False)
        except ValueError:
            check(f"reject set_mode({bad_feature!r}, {bad_mode!r})", True)
    try:
        md.get_mode("nope")
        check("reject get_mode unknown", False)
    except ValueError:
        check("reject get_mode unknown", True)

    # --- launch preset dev buy (v1.5 floor: min 0.02) ---
    check("launch dev buy default 0.1", md.get_launch_dev_buy() == 0.1)
    check("set dev buy 0.5", md.set_launch_dev_buy(0.5) == 0.5)
    check("get dev buy 0.5", md.get_launch_dev_buy() == 0.5)
    check("set dev buy 0.02 (floor)", md.set_launch_dev_buy(0.02) == 0.02)
    md.set_launch_dev_buy(0.5)
    for bad in (0, 0.01, -0.1, 1.5, 5):
        try:
            md.set_launch_dev_buy(bad)
            check(f"reject dev buy {bad}", False)
        except ValueError:
            check(f"reject dev buy {bad}", True)
    check("dev buy still 0.5 after rejects", md.get_launch_dev_buy() == 0.5)

    # --- modes live in the same DB file as positions ---
    check("modes table in temp DB", os.path.exists(TMP_DB) and os.path.getsize(TMP_DB) > 0)

    # --- bot.py wires the mode system ---
    import bot
    for name in ("mode_cmd", "launch_set_cmd", "on_mode_button",
                 "on_sell_button", "on_copybuy_button", "_do_launch", "_mode"):
        check(f"bot has {name}", hasattr(bot, name))
    check("bot mode defaults map",
          bot._MODE_DEFAULTS == {"tpsl": "auto", "copy": "auto", "launch": "manual"})
    check("sell callback fits 64 bytes",
          len(f"sell:{'A' * 44}:100") <= 64)
    check("copybuy callback fits 64 bytes", len("copybuy:abcdef12") <= 64)

    print("\nAll mode tests passed.")


if __name__ == "__main__":
    main()
