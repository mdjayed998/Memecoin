"""
Generate a FRESH trading wallet for the bot. Run once on your own computer/server:
    python3 generate_wallet.py

It prints the public key (safe to share) and the private key (base58).
- Send only a SMALL amount of SOL to this wallet for trading.
- NEVER use your main wallet for a trading bot.
- NEVER paste the private key in Telegram, WhatsApp, or any chat.
- Put it in .env as WALLET_PRIVATE_KEY=... and never commit .env to git.
"""
import base58
from solders.keypair import Keypair


def main():
    kp = Keypair()
    pubkey = str(kp.pubkey())
    secret_b58 = base58.b58encode(bytes(kp)).decode()
    print("=== NEW TRADING WALLET (bot only) ===")
    print(f"Public key (deposit address): {pubkey}")
    print(f"Private key (base58, SECRET): {secret_b58}")
    print()
    print("1. Send a SMALL test amount of SOL to the public key above.")
    print("2. Put the private key in .env as WALLET_PRIVATE_KEY=<key>")
    print("3. NEVER share the private key with anyone — not even the assistant in chat.")
    print("4. Keep DRY_RUN=true until you have tested a 0.05 SOL trade end-to-end.")


if __name__ == "__main__":
    main()
