"""Tests for v1.6: connect any wallet (multi-wallet registry + switching).
Run: python3 test_wallets.py
Sets wallet env vars BEFORE importing config/wallets, uses a temp DB.
No Telegram, no network, no trades. Keys here are freshly generated
throwaways, never real wallets.
"""
import base58
import os
import sys
import tempfile

from solders.keypair import Keypair

# Two fresh throwaway wallets + one invalid key, before config loads.
KP1, KP2 = Keypair(), Keypair()
KEY1 = base58.b58encode(bytes(KP1)).decode()
KEY2 = base58.b58encode(bytes(KP2)).decode()
os.environ["WALLET_PRIVATE_KEY"] = KEY1
os.environ["WALLET_NAME"] = "Phantom"
os.environ["WALLET_PRIVATE_KEY_2"] = KEY2
os.environ["WALLET_NAME_2"] = "Jupiter"
os.environ["WALLET_PRIVATE_KEY_3"] = "not-a-real-key"
os.environ["WALLET_NAME_3"] = "Broken"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test-wallets.db")
sys.path.insert(0, ".")

import wallets


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


def main():
    # --- registry ---
    all_w = wallets.list_wallets()
    check("3 slots listed", len(all_w) == 3)
    check("slot names", [w["name"] for w in all_w] == ["Phantom", "Jupiter", "Broken"])
    check("slot 1 valid + pubkey", all_w[0]["valid"] and all_w[0]["pubkey"] == str(KP1.pubkey()))
    check("slot 2 valid + pubkey", all_w[1]["valid"] and all_w[1]["pubkey"] == str(KP2.pubkey()))
    check("slot 3 invalid", all_w[2]["valid"] is False and all_w[2]["pubkey"] is None)
    check("2 valid wallets", len(wallets.valid_wallets()) == 2)

    # --- active wallet: default first valid, switch persists ---
    check("default active is Phantom", wallets.get_active_name() == "Phantom")
    name, kp = wallets.get_active_keypair()
    check("active keypair is KP1", kp is not None and str(kp.pubkey()) == str(KP1.pubkey()))
    check("switch to Jupiter", wallets.set_active_name("Jupiter") == "Jupiter")
    check("active is Jupiter", wallets.get_active_name() == "Jupiter")
    name, kp = wallets.get_active_keypair()
    check("active keypair is KP2", kp is not None and str(kp.pubkey()) == str(KP2.pubkey()))
    check("switch back to Phantom", wallets.set_active_name("Phantom") == "Phantom")
    for bad in ("Broken", "Nope", ""):
        try:
            wallets.set_active_name(bad)
            check(f"reject switch to {bad!r}", False)
        except ValueError:
            check(f"reject switch to {bad!r}", True)

    # --- bot.get_keypair follows the active wallet ---
    import bot
    wallets.set_active_name("Phantom")
    kp = bot.get_keypair()
    check("bot keypair is Phantom", kp is not None and str(kp.pubkey()) == str(KP1.pubkey()))
    wallets.set_active_name("Jupiter")
    kp = bot.get_keypair()
    check("bot keypair switches to Jupiter", kp is not None and str(kp.pubkey()) == str(KP2.pubkey()))
    wallets.set_active_name("Phantom")

    # --- bot has the new commands ---
    for name in ("wallet_use_cmd", "connect_cmd"):
        check(f"bot has {name}", hasattr(bot, name))

    print("\nAll wallet tests passed.")


if __name__ == "__main__":
    main()
