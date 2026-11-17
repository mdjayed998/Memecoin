"""
Connect any wallet (v1.6).

A Telegram bot cannot do a dApp-style "Connect Wallet" tap-to-approve:
auto TP/SL, copy trade and dip buys need the bot to sign by itself, and
that is only possible when the bot holds the key. So "connect" here means:
export the wallet's private key (Phantom / Jupiter / Solflare — any
Solana wallet) and put it in Railway Variables. Any wallet works, and
up to 3 can be connected at once — switch the active one with
/wallet_use <name> in Telegram.

The active wallet is saved in the DB (modes table), so a switch survives
restarts. Keys are never printed, logged or sent anywhere.
"""
from solders.keypair import Keypair

import config
import modes as md


def _keypair_from(key: str):
    try:
        return Keypair.from_base58_string(key)
    except Exception:
        return None


def list_wallets() -> list:
    """All configured slots: [{name, pubkey|None, valid, connected}]."""
    out = []
    for name, key in config.WALLETS:
        kp = _keypair_from(key) if key else None
        out.append({
            "name": name,
            "pubkey": str(kp.pubkey()) if kp else None,
            "valid": kp is not None,
            "connected": bool(key),
        })
    return out


def valid_wallets() -> list:
    return [w for w in list_wallets() if w["valid"]]


def get_active_name() -> str | None:
    """Stored active wallet name, or the first valid wallet if none stored."""
    stored = md.get_value("active_wallet")
    names = [w["name"] for w in valid_wallets()]
    if stored in names:
        return stored
    return names[0] if names else None


def set_active_name(name: str) -> str:
    names = [w["name"] for w in valid_wallets()]
    if name not in names:
        raise ValueError(
            f"No connected wallet named '{name}'. Connected: "
            f"{', '.join(names) if names else 'none — see /connect'}")
    md.set_value("active_wallet", name)
    return name


def get_active_keypair():
    """(name, Keypair) for the active wallet, or (None, None)."""
    name = get_active_name()
    if not name:
        return None, None
    for w_name, key in config.WALLETS:
        if w_name == name:
            return name, _keypair_from(key)
    return None, None
