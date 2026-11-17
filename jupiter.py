"""
Jupiter swaps via the LIVE lite-api host (verified 2026-11-16).
- GET /swap/v1/quote?inputMint&outputMint&amount&slippageBps
- POST /swap/v1/swap {quoteResponse, userPublicKey, ...}
  -> returns base64 VersionedTransaction, we sign it locally and send via RPC.
Old host quote-api.jup.ag/v6 is DEAD — do not use it.

Amount is always in atomic base units (lamports for SOL, raw units for SPL).
"""
import base64
import os

import httpx
from solders.transaction import VersionedTransaction

from config import JUPITER_QUOTE_URL, JUPITER_SWAP_URL, SOL_DECIMALS

HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
TIMEOUT = 20.0
# See rugcheck.py: explicit proxy + trust_env=False avoids httpx choking
# on malformed NO_PROXY entries in sandboxed hosts.
_PROXY = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or None
_VERIFY = os.getenv("SSL_CERT_FILE") or True  # sandbox egress CA, else system certs


def _client():
    return httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, trust_env=False, proxy=_PROXY, verify=_VERIFY)


async def get_quote(input_mint: str, output_mint: str, amount_atomic: int, slippage_bps: int) -> dict:
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount_atomic),
        "slippageBps": str(slippage_bps),
    }
    async with _client() as c:
        r = await c.get(JUPITER_QUOTE_URL, params=params)
        r.raise_for_status()
        return r.json()


def swap_payload(quote: dict, user_pubkey: str, priority_fee_lamports: int = 0) -> dict:
    """Pure payload builder (v1.5 priority fee).
    priority_fee_lamports=0 -> Jupiter "auto" (default). >0 -> fixed fee in
    lamports, for faster inclusion when the network is busy."""
    return {
        "quoteResponse": quote,
        "userPublicKey": user_pubkey,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": (
            str(int(priority_fee_lamports)) if priority_fee_lamports > 0 else "auto"
        ),
    }


async def build_swap_tx(quote: dict, user_pubkey: str, slippage_bps: int,
                        priority_fee_lamports: int = 0) -> dict:
    payload = swap_payload(quote, user_pubkey, priority_fee_lamports)
    async with _client() as c:
        r = await c.post(JUPITER_SWAP_URL, json=payload)
        r.raise_for_status()
        return r.json()


def sign_and_serialize(swap_b64: str, keypair) -> bytes:
    """Decode Jupiter's base64 VersionedTransaction, sign with local keypair."""
    raw = base64.b64decode(swap_b64)
    tx = VersionedTransaction.from_bytes(raw)
    # solders VersionedTransaction: sign with list of keypairs
    signed = VersionedTransaction(tx.message, [keypair])
    return bytes(signed)


def sol_to_lamports(sol: float) -> int:
    return int(round(sol * (10 ** SOL_DECIMALS)))


def quote_summary(quote: dict) -> str:
    out_amt = quote.get("outAmount")
    in_amt = quote.get("inAmount")
    impact = quote.get("priceImpactPct")
    return f"in={in_amt} out={out_amt} priceImpact={impact}%"
