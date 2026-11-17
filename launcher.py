"""
Tweet -> Pump.fun token launcher (v1.3).

Flow (verified against live docs 2026-11-16):
  1. Image + metadata -> Pump.fun free IPFS: POST https://pump.fun/api/ipfs
     (multipart: file, name, symbol, description, twitter, telegram, website)
     -> {"metadataUri": "https://ipfs.io/ipfs/..."} (keys vary; parsed defensively)
  2. Create tx -> PumpPortal Local API (you sign, they never see your key):
     POST https://pumpportal.fun/api/trade-local
     {publicKey, action: "create", tokenMetadata: {name, symbol, uri},
      mint: <new mint pubkey>, denominatedInSol: "true", amount: <dev buy SOL>,
      slippage: 10, priorityFee: 0.0005, pool: "pump"}
     Optional PUMPPORTAL_API_KEY appended as ?api-key= when set (trade streams
     need a key since May 2026; local trades historically did not).
     -> unsigned VersionedTransaction (base64)
  3. Sign with BOTH the new mint keypair and the wallet keypair, send via RPC.

Costs: ~0.02 SOL network fees for creation + your dev buy + PumpPortal 0.5%
on the dev buy. Launching is IRREVERSIBLE — the mint can never be un-created.
In DRY_RUN mode nothing is uploaded or sent; launch() returns a preview dict.
"""
import base64
import json
import os
from io import BytesIO

import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

import config
from config import SOLANA_RPC_URL as RPC_URL


def _wallet_keypair() -> Keypair:
    # v1.6: uses the ACTIVE connected wallet (/wallet_use to switch).
    import wallets
    name, kp = wallets.get_active_keypair()
    if kp is None:
        raise ValueError("No trading wallet set. See /connect in Telegram.")
    return kp

_PROXY = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or None
_VERIFY = os.getenv("SSL_CERT_FILE") or True
TIMEOUT = 60.0

PUMP_IPFS_URL = "https://pump.fun/api/ipfs"
PUMPPORTAL_TRADE_LOCAL = "https://pumpportal.fun/api/trade-local"
PUMPPORTAL_API_KEY = os.getenv("PUMPPORTAL_API_KEY", "").strip()

MAX_IMAGE_BYTES = 5 * 1024 * 1024


async def download_image(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=30.0, trust_env=False, proxy=_PROXY,
                                 verify=_VERIFY, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if ctype and not ctype.startswith("image/"):
            raise ValueError(f"Tweet media is not an image ({ctype}).")
        data = r.content
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Tweet image is too big (max 5 MB). Send a smaller image.")
    if len(data) < 100:
        raise ValueError("Tweet image download looks empty.")
    return data


async def upload_to_pump_ipfs(image: bytes, name: str, symbol: str,
                              description: str, twitter_url: str,
                              filename: str = "image.jpg") -> str:
    """Upload image + metadata to Pump.fun IPFS. Returns metadata URI."""
    files = {"file": (filename, BytesIO(image), "image/jpeg")}
    data = {"name": name, "symbol": symbol, "description": description,
            "twitter": twitter_url, "telegram": "", "website": "", "showName": "true"}
    async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False, proxy=_PROXY,
                                 verify=_VERIFY) as c:
        r = await c.post(PUMP_IPFS_URL, files=files, data=data)
        r.raise_for_status()
        j = r.json()
    for key in ("metadataUri", "metadata_uri", "uri", "metadataUrl"):
        if j.get(key):
            return j[key]
    if j.get("ipfs"):
        return j["ipfs"]
    if j.get("hash"):
        return f"https://ipfs.io/ipfs/{j['hash']}"
    raise ValueError(f"Pump.fun IPFS upload gave no metadata URI: {str(j)[:200]}")


def build_create_payload(wallet_pubkey: str, mint_pubkey: str, name: str,
                         symbol: str, metadata_uri: str, dev_buy_sol: float) -> dict:
    return {
        "publicKey": wallet_pubkey,
        "action": "create",
        "tokenMetadata": {"name": name, "symbol": symbol, "uri": metadata_uri},
        "mint": mint_pubkey,
        "denominatedInSol": "true",
        "amount": dev_buy_sol,
        "slippage": 10,
        "priorityFee": 0.0005,
        "pool": "pump",
    }


async def create_token(name: str, symbol: str, description: str, twitter_url: str,
                       image: bytes, dev_buy_sol: float, dry_run: bool = False) -> dict:
    """
    Launch the token. Returns {mint, signature, metadata_uri, pumpfun_url}
    (in dry-run: {dry_run: True, ...preview fields, no mint/signature}).
    Raises ValueError / httpx errors with a human-readable message.
    """
    if dev_buy_sol <= 0 or dev_buy_sol > 1.0:
        raise ValueError("Dev buy must be between 0.01 and 1 SOL.")
    if dry_run:
        return {"dry_run": True, "name": name, "symbol": symbol,
                "description": description, "twitter_url": twitter_url,
                "dev_buy_sol": dev_buy_sol,
                "note": "DRY-RUN: nothing was uploaded or launched."}
    metadata_uri = await upload_to_pump_ipfs(image, name, symbol, description, twitter_url)

    wallet = _wallet_keypair()
    mint_kp = Keypair()
    payload = build_create_payload(str(wallet.pubkey()), str(mint_kp.pubkey()),
                                   name, symbol, metadata_uri, dev_buy_sol)
    url = PUMPPORTAL_TRADE_LOCAL
    if PUMPPORTAL_API_KEY:
        url += f"?api-key={PUMPPORTAL_API_KEY}"
    async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False, proxy=_PROXY,
                                 verify=_VERIFY) as c:
        r = await c.post(url, json=payload)
        if r.status_code != 200:
            raise ValueError(f"PumpPortal create failed (HTTP {r.status_code}): {r.text[:300]}")
        raw = r.content
    # Response may be raw base64 tx bytes, or JSON {"transaction": "..."}.
    tx_b64 = raw.decode().strip()
    if tx_b64.startswith("{"):
        tx_b64 = json.loads(tx_b64).get("transaction") or json.loads(tx_b64).get("tx") or tx_b64
    tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    # Sign with BOTH mint keypair (creates the mint) and wallet keypair (pays).
    signed = VersionedTransaction(tx.message, [mint_kp, wallet])
    from solana.rpc.async_api import AsyncClient
    async with AsyncClient(RPC_URL) as rpc:
        sig = await rpc.send_raw_transaction(bytes(signed))
    return {"mint": str(mint_kp.pubkey()), "signature": str(sig.value),
            "metadata_uri": metadata_uri, "dev_buy_sol": dev_buy_sol,
            "pumpfun_url": f"https://pump.fun/{mint_kp.pubkey()}"}
