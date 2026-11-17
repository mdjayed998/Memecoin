"""
Copy trading v1.2 — follow a trader's WALLET (not a token mint).

You give the bot a wallet address with /copy_add <wallet> [sol].
Every ~15s the bot checks that wallet's newest swaps and copies them:
  - Target BUYs token X  -> bot buys X with a FIXED amount (default 0.1 SOL,
    per-target, capped by MAX_BUY_SOL). It does NOT copy the target's size.
  - Target SELLs X       -> bot sells the SAME % of its own copy position
    (target sells 50% -> bot sells 50%). If the bot holds nothing, nothing happens.
  - YOUR OWN sell limits still apply on top, even if the target keeps holding:
    SL -30% (per-target, change with /copy_sl) auto-sells 100%, and the
    normal TP 2x -> sell 50% still applies. So profit is taken when the
    target sells, but a -30% drop never waits for the target.

How the bot sees the target's trades (verified 2026-11-16):
  1. Helius Enhanced Transactions (preferred, needs free HELIUS_API_KEY):
     GET https://api.helius.xyz/v0/addresses/{wallet}/transactions
         ?api-key=KEY&type=SWAP
     Returns type=SWAP with events.swap / tokenTransfers / nativeTransfers.
     Free tier: 1M credits/mo, no card — dashboard.helius.dev.
  2. Fallback — plain Solana RPC, no key needed:
     getSignaturesForAddress + getTransaction (jsonParsed).
     Compare the wallet's SOL balance change vs each token's balance change:
     token in + SOL out = BUY, token out + SOL in = SELL.
     Slower and rate-limited on the public RPC — Helius is strongly recommended
     if you follow more than 1 wallet.

Honest limits (tell the user, don't hide them):
  - Polling is ~15s behind the target. In memecoins that delay can cost
    entry price, and the target may already be out before you are in.
  - Copying does NOT copy results. A wallet that won last week can rug you
    this week. Rug-check still runs before every copy BUY; DANGER = no copy.
  - The bot only sees swaps. Plain token transfers between wallets are
    ignored (they are not trades).
  - If the target bought a token BEFORE you started following it, the bot
    has no record of that holding, so its first SELL is treated as 100%.
"""
import os
import sqlite3
import time

import httpx

import config

WSOL = config.WSOL_MINT
HELIUS_TX_URL = "https://api.helius.xyz/v0/addresses/{wallet}/transactions"

# Same proxy/CA handling as rugcheck.py / jupiter.py (sandbox egress).
_PROXY = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or None
_VERIFY = os.getenv("SSL_CERT_FILE") or True
HEADERS = {"Accept": "application/json"}
TIMEOUT = 20.0


def _client():
    return httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, trust_env=False,
                             proxy=_PROXY, verify=_VERIFY)


# ---------- storage (same SQLite file as positions, so Railway volume covers it) ----------

def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS copy_targets (
            wallet TEXT PRIMARY KEY,
            sol_per_trade REAL,
            sl_pct REAL,
            enabled INTEGER DEFAULT 1,
            added_at INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS copy_seen (
            signature TEXT PRIMARY KEY,
            wallet TEXT,
            mint TEXT,
            side TEXT,
            ts INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS target_holdings (
            wallet TEXT,
            mint TEXT,
            tokens REAL,
            PRIMARY KEY (wallet, mint)
        )"""
    )
    return conn


def add_target(wallet: str, sol_per_trade: float, sl_pct: float) -> bool:
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) FROM copy_targets").fetchone()[0]
    if n >= config.COPY_MAX_TARGETS:
        conn.close()
        return False
    conn.execute(
        "INSERT OR REPLACE INTO copy_targets VALUES (?,?,?,?,?)",
        (wallet, sol_per_trade, sl_pct, 1, int(time.time())),
    )
    conn.commit()
    conn.close()
    return True


def remove_target(wallet: str) -> bool:
    conn = _conn()
    cur = conn.execute("DELETE FROM copy_targets WHERE wallet=?", (wallet,))
    conn.execute("DELETE FROM target_holdings WHERE wallet=?", (wallet,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def list_targets():
    conn = _conn()
    rows = conn.execute(
        "SELECT wallet, sol_per_trade, sl_pct, enabled FROM copy_targets ORDER BY added_at"
    ).fetchall()
    conn.close()
    return [{"wallet": w, "sol_per_trade": s, "sl_pct": sl, "enabled": bool(e)}
            for w, s, sl, e in rows]


def get_target(wallet: str):
    conn = _conn()
    row = conn.execute(
        "SELECT wallet, sol_per_trade, sl_pct, enabled FROM copy_targets WHERE wallet=?",
        (wallet,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"wallet": row[0], "sol_per_trade": row[1], "sl_pct": row[2], "enabled": bool(row[3])}


def set_target_enabled(wallet: str, enabled: bool) -> bool:
    conn = _conn()
    cur = conn.execute(
        "UPDATE copy_targets SET enabled=? WHERE wallet=?", (1 if enabled else 0, wallet)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def set_target_sol(wallet: str, sol: float) -> bool:
    conn = _conn()
    cur = conn.execute(
        "UPDATE copy_targets SET sol_per_trade=? WHERE wallet=?", (sol, wallet)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def set_target_sl(wallet: str, sl_pct: float) -> bool:
    conn = _conn()
    cur = conn.execute(
        "UPDATE copy_targets SET sl_pct=? WHERE wallet=?", (sl_pct, wallet)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def mark_seen(signature: str, wallet: str, mint: str, side: str):
    conn = _conn()
    conn.execute(
        "INSERT OR IGNORE INTO copy_seen VALUES (?,?,?,?,?)",
        (signature, wallet, mint, side, int(time.time())),
    )
    conn.commit()
    conn.close()


def already_seen(signature: str) -> bool:
    conn = _conn()
    row = conn.execute("SELECT 1 FROM copy_seen WHERE signature=?", (signature,)).fetchone()
    conn.close()
    return row is not None


def _holding(wallet: str, mint: str) -> float:
    conn = _conn()
    row = conn.execute(
        "SELECT tokens FROM target_holdings WHERE wallet=? AND mint=?", (wallet, mint)
    ).fetchone()
    conn.close()
    return float(row[0]) if row else 0.0


def _set_holding(wallet: str, mint: str, tokens: float):
    conn = _conn()
    if tokens <= 0:
        conn.execute("DELETE FROM target_holdings WHERE wallet=? AND mint=?", (wallet, mint))
    else:
        conn.execute(
            "INSERT OR REPLACE INTO target_holdings VALUES (?,?,?)", (wallet, mint, tokens)
        )
    conn.commit()
    conn.close()


# ---------- parsers: Helius enhanced tx -> trades ----------

def parse_helius_tx(tx: dict, wallet: str):
    """Return list of {mint, side, token_amount, sol_amount, signature}.

    Prefers events.swap when present, else tokenTransfers + nativeTransfers.
    token_amount is in the units Helius reports; only ratios (sell %) use it,
    so the exact unit does not matter as long as it is consistent per mint.
    """
    sig = tx.get("signature", "")
    if tx.get("type") not in ("SWAP", None):
        # Helius type filter already asks for SWAP, but be strict anyway.
        if tx.get("type") != "SWAP":
            return []
    trades = []

    # Path 1: events.swap {tokenInputs, tokenOutputs}
    swap = (tx.get("events") or {}).get("swap")
    if swap:
        ins = swap.get("tokenInputs") or []
        outs = swap.get("tokenOutputs") or []
        in_mints = {i.get("mint"): float(i.get("tokenAmount") or 0) for i in ins}
        out_mints = {o.get("mint"): float(o.get("tokenAmount") or 0) for o in outs}
        # SOL in, token out = BUY ; token in, SOL out = SELL
        non_sol_out = [m for m in out_mints if m != WSOL]
        non_sol_in = [m for m in in_mints if m != WSOL]
        if WSOL in in_mints and non_sol_out:
            mint = non_sol_out[0]
            trades.append({"mint": mint, "side": "BUY",
                           "token_amount": out_mints[mint],
                           "sol_amount": in_mints[WSOL] / 1e9,
                           "signature": sig})
        elif WSOL in out_mints and non_sol_in:
            mint = non_sol_in[0]
            trades.append({"mint": mint, "side": "SELL",
                           "token_amount": in_mints[mint],
                           "sol_amount": out_mints[WSOL] / 1e9,
                           "signature": sig})
        if trades:
            return trades

    # Path 2: tokenTransfers + nativeTransfers
    transfers = tx.get("tokenTransfers") or []
    sol_spent_lamports = 0
    sol_received_lamports = 0
    for nt in tx.get("nativeTransfers") or []:
        if nt.get("fromUserAccount") == wallet:
            sol_spent_lamports += int(nt.get("amount") or 0)
        if nt.get("toUserAccount") == wallet:
            sol_received_lamports += int(nt.get("amount") or 0)
    for t in transfers:
        mint = t.get("mint")
        if not mint or mint == WSOL:
            continue
        amt = float(t.get("tokenAmount") or 0)
        if amt <= 0:
            continue
        if t.get("toUserAccount") == wallet and t.get("fromUserAccount") != wallet:
            # Received token. If SOL also left the wallet, it was a buy.
            if sol_spent_lamports > 0:
                trades.append({"mint": mint, "side": "BUY", "token_amount": amt,
                               "sol_amount": sol_spent_lamports / 1e9, "signature": sig})
        elif t.get("fromUserAccount") == wallet and t.get("toUserAccount") != wallet:
            if sol_received_lamports > 0:
                trades.append({"mint": mint, "side": "SELL", "token_amount": amt,
                               "sol_amount": sol_received_lamports / 1e9, "signature": sig})
    return trades


# ---------- parsers: raw RPC tx -> trades (no-key fallback) ----------

def parse_rpc_tx(tx: dict, wallet: str, signature: str):
    """Parse a getTransaction jsonParsed result via balance changes.

    token delta > 0 and SOL delta < 0 -> BUY; token delta < 0 and SOL delta > 0 -> SELL.
    """
    try:
        meta = tx["result"]["meta"]
        msg = tx["result"]["transaction"]["message"]
    except (KeyError, TypeError):
        return []
    if meta.get("err"):
        return []  # failed tx: no trade happened
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in msg.get("accountKeys", [])]
    try:
        idx = keys.index(wallet)
    except ValueError:
        return []
    sol_delta = (meta["postBalances"][idx] - meta["preBalances"][idx]) / 1e9

    # Token balances keyed by mint, only for accounts owned by this wallet.
    def _by_mint(balances):
        out = {}
        for b in balances or []:
            if b.get("owner") != wallet:
                continue
            mint = b.get("mint")
            if not mint or mint == WSOL:
                continue
            amt = (b.get("uiTokenAmount") or {}).get("uiAmount")
            if amt is None:
                amt = float(b.get("uiTokenAmount", {}).get("amount", 0) or 0)
            out[mint] = out.get(mint, 0.0) + float(amt or 0)
        return out

    pre = _by_mint(meta.get("preTokenBalances"))
    post = _by_mint(meta.get("postTokenBalances"))
    trades = []
    for mint in set(pre) | set(post):
        delta = post.get(mint, 0.0) - pre.get(mint, 0.0)
        if delta > 0 and sol_delta < 0:
            trades.append({"mint": mint, "side": "BUY", "token_amount": delta,
                           "sol_amount": abs(sol_delta), "signature": signature})
        elif delta < 0 and sol_delta > 0:
            trades.append({"mint": mint, "side": "SELL", "token_amount": abs(delta),
                           "sol_amount": sol_delta, "signature": signature})
    return trades


# ---------- fetching ----------

async def fetch_helius_swaps(wallet: str, limit: int = 10):
    """Newest-first list of Helius enhanced txs for a wallet, SWAP only."""
    if not config.HELIUS_API_KEY:
        return []
    url = HELIUS_TX_URL.format(wallet=wallet)
    params = {"api-key": config.HELIUS_API_KEY, "type": "SWAP", "limit": str(limit)}
    async with _client() as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else []


async def _rpc_call(method: str, params):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with _client() as c:
        r = await c.post(config.SOLANA_RPC_URL, json=payload)
        r.raise_for_status()
        return r.json()


async def fetch_rpc_swaps(wallet: str, limit: int = 10):
    """Fallback: signatures + parsed txs. Returns newest-first parsed trades."""
    sigs_resp = await _rpc_call("getSignaturesForAddress", [wallet, {"limit": limit}])
    sigs = (sigs_resp.get("result") or [])
    trades = []
    for s in sigs:
        sig = s.get("signature")
        if not sig or already_seen(sig):
            continue
        tx = await _rpc_call("getTransaction", [sig, {"encoding": "jsonParsed",
                                                       "maxSupportedTransactionVersion": 0}])
        trades.extend(parse_rpc_tx(tx, wallet, sig))
    return trades


async def poll_target(wallet: str):
    """One poll: return new trades oldest-first, with sell_pct filled in.

    Each trade dict gains: wallet, sell_pct (SELL only), target_sol (BUY only,
    the SOL the target spent — informational; the bot buys its own fixed amount).
    """
    if config.HELIUS_API_KEY:
        txs = await fetch_helius_swaps(wallet)
        # Helius returns newest first; process oldest first so holdings track correctly.
        raw = []
        for tx in reversed(txs):
            raw.extend(parse_helius_tx(tx, wallet))
    else:
        raw = await fetch_rpc_swaps(wallet)
        raw = list(reversed(raw))  # signatures come newest-first; oldest first

    out = []
    for t in raw:
        sig = t["signature"]
        if already_seen(sig):
            continue
        mark_seen(sig, t["mint"], t["side"])
        if t["side"] == "BUY":
            _set_holding(wallet, t["mint"], _holding(wallet, t["mint"]) + t["token_amount"])
            t["sell_pct"] = None
        else:
            before = _holding(wallet, t["mint"])
            if before > 0:
                t["sell_pct"] = min(100.0, t["token_amount"] / before * 100.0)
            else:
                # Target held this before we started following: treat as full exit.
                t["sell_pct"] = 100.0
            _set_holding(wallet, t["mint"], max(0.0, before - t["token_amount"]))
        t["wallet"] = wallet
        out.append(t)
    return out
