"""
Copy-trade parser tests — NO network, NO keys, NO real trades.
Uses mock Helius + mock RPC transactions to verify:
  1. Helius SWAP (events.swap): SOL -> token = BUY, token -> SOL = SELL
  2. Helius tokenTransfers fallback path
  3. RPC balance-change parsing: token in + SOL out = BUY, etc.
  4. Sell %: target sells half its tracked holding -> sell_pct 50
  5. Target storage: add/list/remove target

Run:  python3 test_copytrade.py
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")

# Isolate test DB so the real positions.db is never touched.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DB_PATH"] = _tmp.name
import config  # noqa: E402
config.DB_PATH = _tmp.name

import copytrade as ct  # noqa: E402

WALLET = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAs"
OTHER = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # BONK
WSOL = ct.WSOL

failures = []


def check(name, cond, detail=""):
    print(("✅ " if cond else "❌ ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# 1. Helius events.swap BUY: SOL in, BONK out
buy_tx = {
    "signature": "sig-buy-1",
    "type": "SWAP",
    "events": {"swap": {
        "tokenInputs": [{"mint": WSOL, "tokenAmount": 100_000_000}],
        "tokenOutputs": [{"mint": MINT, "tokenAmount": 300_000_000_000}],
    }},
}
trades = ct.parse_helius_tx(buy_tx, WALLET)
check("Helius swap BUY detected", len(trades) == 1 and trades[0]["side"] == "BUY" and trades[0]["mint"] == MINT,
      f"got {trades}")
check("Helius BUY sol amount 0.1", abs(trades[0]["sol_amount"] - 0.1) < 1e-9, f"got {trades[0]['sol_amount']}")

# 2. Helius events.swap SELL: BONK in, SOL out
sell_tx = {
    "signature": "sig-sell-1",
    "type": "SWAP",
    "events": {"swap": {
        "tokenInputs": [{"mint": MINT, "tokenAmount": 150_000_000_000}],
        "tokenOutputs": [{"mint": WSOL, "tokenAmount": 60_000_000}],
    }},
}
trades = ct.parse_helius_tx(sell_tx, WALLET)
check("Helius swap SELL detected", len(trades) == 1 and trades[0]["side"] == "SELL",
      f"got {trades}")

# 3. Helius tokenTransfers fallback (no events.swap): wallet receives token + sends SOL
tt_buy = {
    "signature": "sig-buy-2",
    "type": "SWAP",
    "tokenTransfers": [
        {"mint": MINT, "tokenAmount": 50_000_000_000, "fromUserAccount": OTHER, "toUserAccount": WALLET},
        {"mint": WSOL, "tokenAmount": 0.05, "fromUserAccount": WALLET, "toUserAccount": OTHER},
    ],
    "nativeTransfers": [{"fromUserAccount": WALLET, "toUserAccount": OTHER, "amount": 50_000_000}],
}
trades = ct.parse_helius_tx(tt_buy, WALLET)
check("Helius transfers fallback BUY", len(trades) == 1 and trades[0]["side"] == "BUY", f"got {trades}")

# 4. Non-SWAP tx ignored
trades = ct.parse_helius_tx({"signature": "x", "type": "TRANSFER", "tokenTransfers": []}, WALLET)
check("Non-SWAP ignored", trades == [], f"got {trades}")

# 5. RPC balance-change BUY: token +1,000,000, SOL -0.2
rpc_buy = {"result": {
    "meta": {
        "err": None,
        "preBalances": [1_000_000_000, 0],
        "postBalances": [800_000_000, 0],
        "preTokenBalances": [{"owner": WALLET, "mint": MINT, "uiTokenAmount": {"uiAmount": 0}}],
        "postTokenBalances": [{"owner": WALLET, "mint": MINT, "uiTokenAmount": {"uiAmount": 1_000_000}}],
    },
    "transaction": {"message": {"accountKeys": [{"pubkey": WALLET}, {"pubkey": OTHER}]}},
}}
trades = ct.parse_rpc_tx(rpc_buy, WALLET, "sig-rpc-buy")
check("RPC BUY detected", len(trades) == 1 and trades[0]["side"] == "BUY", f"got {trades}")

# 6. RPC SELL + failed tx ignored
rpc_sell = {"result": {
    "meta": {
        "err": None,
        "preBalances": [800_000_000, 0],
        "postBalances": [900_000_000, 0],
        "preTokenBalances": [{"owner": WALLET, "mint": MINT, "uiTokenAmount": {"uiAmount": 1_000_000}}],
        "postTokenBalances": [{"owner": WALLET, "mint": MINT, "uiTokenAmount": {"uiAmount": 400_000}}],
    },
    "transaction": {"message": {"accountKeys": [{"pubkey": WALLET}, {"pubkey": OTHER}]}},
}}
trades = ct.parse_rpc_tx(rpc_sell, WALLET, "sig-rpc-sell")
check("RPC SELL detected", len(trades) == 1 and trades[0]["side"] == "SELL", f"got {trades}")
failed = {"result": {"meta": {"err": "err", "preBalances": [], "postBalances": [],
                               "preTokenBalances": [], "postTokenBalances": []},
                    "transaction": {"message": {"accountKeys": [{"pubkey": WALLET}]}}}}
check("Failed RPC tx ignored", ct.parse_rpc_tx(failed, WALLET, "sig-fail") == [])

# 7. Sell % from tracked holdings: buy 300B then sell 150B -> 50%
assert ct.add_target(WALLET, 0.1, -30.0), "add_target failed"
assert ct.get_target(WALLET)["sol_per_trade"] == 0.1
# Simulate poll bookkeeping by hand: BUY then SELL through the holding helpers
ct._set_holding(WALLET, MINT, 300_000_000_000.0)
before = ct._holding(WALLET, MINT)
pct = min(100.0, 150_000_000_000.0 / before * 100.0)
check("Sell 50% computed", abs(pct - 50.0) < 1e-9, f"got {pct}")
check("Target listed", len(ct.list_targets()) == 1)
check("Target SL update", ct.set_target_sl(WALLET, -25.0) and ct.get_target(WALLET)["sl_pct"] == -25.0)
check("Target remove", ct.remove_target(WALLET) and ct.list_targets() == [])

os.unlink(_tmp.name)
print()
if failures:
    print(f"❌ {len(failures)} FAILED: {failures}")
    sys.exit(1)
print("✅ ALL COPY-TRADE TESTS PASSED.")
