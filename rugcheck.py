"""
Rug check: RugCheck + DexScreener combined.
No API key needed for either (Nov 2026 verified).
Returns a verdict, never a guarantee: a "GOOD" score only means
no obvious rug flag was found. Memecoins can still go to zero.

RugCheck score: higher = riskier. score_normalised 0-100.
DexScreener: liquidity, FDV, 24h volume, pair age.
"""
import os

import httpx

from config import (
    DEXSCREENER_TOKENS_URL,
    RUGCHECK_REPORT_URL,
    RUGCHECK_SUMMARY_URL,
)

HEADERS = {"User-Agent": "solana-memecoin-bot-v1/1.0", "Accept": "application/json"}
TIMEOUT = 15.0
# Explicit proxy (if the host sets one) + trust_env=False.
# This avoids httpx choking on malformed NO_PROXY entries like "[::1]"
# in sandboxed hosts, while working direct (proxy=None) on a normal VPS.
_PROXY = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or None
_VERIFY = os.getenv("SSL_CERT_FILE") or True  # sandbox egress CA, else system certs


def _client():
    return httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, trust_env=False, proxy=_PROXY, verify=_VERIFY)


async def fetch_rugcheck(mint: str) -> dict:
    """Full RugCheck report. Returns {} on 404 / error (new token, no report yet)."""
    url = RUGCHECK_REPORT_URL.format(mint=mint)
    try:
        async with _client() as c:
            r = await c.get(url)
            if r.status_code != 200:
                return {"_error": f"RugCheck HTTP {r.status_code}"}
            return r.json()
    except Exception as e:  # network down, Cloudflare block, etc. -> never green-light
        return {"_error": str(e)}


async def fetch_dexscreener(mint: str) -> dict:
    url = DEXSCREENER_TOKENS_URL.format(mint=mint)
    try:
        async with _client() as c:
            r = await c.get(url)
            if r.status_code != 200:
                return {"_error": f"DexScreener HTTP {r.status_code}"}
            return r.json()
    except Exception as e:
        return {"_error": str(e)}


def _top_holders_pct(report: dict) -> float:
    holders = report.get("topHolders") or []
    total = 0.0
    for h in holders[:10]:
        try:
            total += float(h.get("pct", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _risk_names(report: dict) -> list[str]:
    risks = report.get("risks") or []
    names = []
    for risk in risks:
        if isinstance(risk, dict):
            names.append(risk.get("name", "?"))
        elif isinstance(risk, str):
            names.append(risk)
    return names


def verdict(report: dict, dex: dict) -> dict:
    """Combine RugCheck + DexScreener into one verdict dict."""
    if "_error" in report and not dex.get("pairs"):
        return {
            "verdict": "UNKNOWN",
            "score": None,
            "reasons": [f"RugCheck unavailable: {report['_error']}. Do NOT buy blind."],
            "safe_to_buy": False,
        }

    score = report.get("score_normalised")
    if score is None:
        score = report.get("score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None

    reasons: list[str] = []
    danger = False
    warning = False

    # RugCheck risks (e.g. "Mutable metadata" is common — warning, not a block)
    for name in _risk_names(report):
        reasons.append(f"⚠️ {name}")
        warning = True
    # Authorities — mint/freeze still live is a hard danger
    mint_auth = report.get("mintAuthority")
    freeze_auth = report.get("freezeAuthority")
    if mint_auth:
        reasons.append(f"⛔ Mint authority NOT revoked: {str(mint_auth)[:12]}...")
        danger = True
    if freeze_auth:
        reasons.append(f"⛔ Freeze authority NOT revoked: {str(freeze_auth)[:12]}...")
        danger = True

    # LP locked: <20% danger, 20-50% warning
    lp_locked = report.get("lpLockedPct")
    if lp_locked is not None:
        try:
            lp_locked = float(lp_locked)
            if lp_locked < 20:
                reasons.append(f"⛔ LP locked only {lp_locked:.0f}%")
                danger = True
            elif lp_locked < 50:
                reasons.append(f"⚠️ LP locked only {lp_locked:.0f}%")
                warning = True
            else:
                reasons.append(f"✅ LP locked {lp_locked:.0f}%")
        except (TypeError, ValueError):
            pass

    # Top holders: >50% danger, 30-50% warning (40% alone must not block BONK etc.)
    top10 = _top_holders_pct(report)
    if top10:
        if top10 > 50:
            reasons.append(f"⛔ Top 10 holders own {top10:.1f}%")
            danger = True
        elif top10 > 30:
            reasons.append(f"⚠️ Top 10 holders own {top10:.1f}%")
            warning = True
        else:
            reasons.append(f"✅ Top 10 holders own {top10:.1f}%")

    # DexScreener price / liquidity
    pairs = dex.get("pairs") or []
    price_usd = None
    liquidity_usd = None
    if pairs:
        # Best pair = highest liquidity
        best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        try:
            price_usd = float(best.get("priceUsd") or 0)
        except (TypeError, ValueError):
            price_usd = None
        try:
            liquidity_usd = float((best.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            liquidity_usd = None
        if liquidity_usd is not None and liquidity_usd < 1_000:
            reasons.append(f"⛔ Liquidity very low: ${liquidity_usd:,.0f}")
            danger = True
        elif liquidity_usd is not None and liquidity_usd < 5_000:
            reasons.append(f"⚠️ Liquidity low: ${liquidity_usd:,.0f}")
            warning = True

    if score is not None:
        if score >= 60 or danger:
            v = "DANGER"
        elif score >= 30 or warning:
            v = "WARNING"
        else:
            v = "GOOD"
    else:
        v = "DANGER" if danger else ("WARNING" if warning else "UNKNOWN")

    return {
        "verdict": v,
        "score": score,
        "reasons": reasons or ["No obvious rug flags found."],
        # WARNING still shows Buy buttons (with the warnings visible);
        # only DANGER / UNKNOWN block a buy.
        "safe_to_buy": v in ("GOOD", "WARNING"),
        "price_usd": price_usd,
        "liquidity_usd": liquidity_usd,
        "top10_pct": top10,
        "lp_locked_pct": lp_locked,
        "token_name": (report.get("tokenMeta") or {}).get("name"),
        "token_symbol": (report.get("tokenMeta") or {}).get("symbol"),
    }


async def check_token(mint: str) -> dict:
    report = await fetch_rugcheck(mint)
    dex = await fetch_dexscreener(mint)
    out = verdict(report, dex)
    out["mint"] = mint
    out["rugcheck_error"] = report.get("_error")
    out["dexscreener_error"] = dex.get("_error")
    return out


def format_report(mint: str, result: dict) -> str:
    lines = [
        f"🪙 <code>{mint}</code>",
        f"RugCheck: <b>{result['verdict']}</b>"
        + (f" (score {result['score']:.0f}/100)" if result.get("score") is not None else ""),
    ]
    if result.get("token_name"):
        lines.append(f"Name: {result['token_name']} ({result.get('token_symbol') or ''})")
    if result.get("price_usd"):
        lines.append(f"Price: ${result['price_usd']:.8g}")
    if result.get("liquidity_usd"):
        lines.append(f"Liquidity: ${result['liquidity_usd']:,.0f}")
    lines.append("")
    lines.extend(result.get("reasons") or [])
    lines.append("")
    lines.append("✅ Safe to consider" if result.get("safe_to_buy") else "⛔ Do NOT buy on this report alone")
    lines.append("This is a risk check, not a guarantee. Memecoins can go to zero.")
    return "\n".join(lines)
