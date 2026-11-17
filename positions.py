"""
Positions + PNL tracker (SQLite, local file positions.db).
No cloud, no sharing. One row per open position.
Auto TP/SL logic lives here so bot.py stays thin:
  - TP: price >= entry * TP_MULT -> sell TP_SELL_PCT%
  - SL: price <= entry * (1 + SL_PCT/100) -> sell 100%
Prices come from DexScreener (free, no key).
"""
import sqlite3
import time

from config import DB_PATH


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS positions (
            mint TEXT PRIMARY KEY,
            symbol TEXT,
            entry_price_usd REAL,
            amount_tokens REAL,
            sol_spent REAL,
            tp_mult REAL,
            tp_sell_pct INTEGER,
            sl_pct REAL,
            tp_done INTEGER DEFAULT 0,
            opened_at INTEGER,
            source TEXT DEFAULT 'manual'
        )"""
    )
    # Migration for DBs created by v1/v1.1 (no source column yet).
    cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN source TEXT DEFAULT 'manual'")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint TEXT, symbol TEXT, side TEXT,
            price_usd REAL, amount_tokens REAL, sol_amount REAL,
            pnl_sol REAL, ts INTEGER
        )"""
    )
    return conn


def open_position(mint, symbol, entry_price_usd, amount_tokens, sol_spent,
                  tp_mult, tp_sell_pct, sl_pct, source="manual"):
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (mint, symbol, entry_price_usd, amount_tokens, sol_spent,
         tp_mult, tp_sell_pct, sl_pct, 0, int(time.time()), source),
    )
    conn.execute(
        "INSERT INTO trades (mint,symbol,side,price_usd,amount_tokens,sol_amount,pnl_sol,ts)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (mint, symbol, "BUY", entry_price_usd, amount_tokens, sol_spent, 0, int(time.time())),
    )
    conn.commit()
    conn.close()


def get_position(mint):
    conn = _conn()
    row = conn.execute("SELECT * FROM positions WHERE mint=?", (mint,)).fetchone()
    conn.close()
    if not row:
        return None
    keys = ["mint", "symbol", "entry_price_usd", "amount_tokens", "sol_spent",
            "tp_mult", "tp_sell_pct", "sl_pct", "tp_done", "opened_at", "source"]
    d = dict(zip(keys, row))
    d.setdefault("source", "manual")
    return d


def list_positions():
    conn = _conn()
    rows = conn.execute("SELECT * FROM positions ORDER BY opened_at DESC").fetchall()
    conn.close()
    keys = ["mint", "symbol", "entry_price_usd", "amount_tokens", "sol_spent",
            "tp_mult", "tp_sell_pct", "sl_pct", "tp_done", "opened_at", "source"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d.setdefault("source", "manual")
        out.append(d)
    return out


def update_after_partial_sell(mint, tokens_sold, sol_received):
    conn = _conn()
    pos = conn.execute("SELECT * FROM positions WHERE mint=?", (mint,)).fetchone()
    if not pos:
        conn.close()
        return
    # Cost-basis accounting: only the sold fraction's cost counts against this sale,
    # so total PNL (partial + final) adds up correctly.
    frac = tokens_sold / pos[3] if pos[3] else 0
    cost_of_sold = pos[4] * frac
    pnl_partial = sol_received - cost_of_sold
    remaining = max(0.0, pos[3] - tokens_sold)
    remaining_cost = max(0.0, pos[4] - cost_of_sold)
    conn.execute(
        "UPDATE positions SET amount_tokens=?, sol_spent=?, tp_done=1 WHERE mint=?",
        (remaining, remaining_cost, mint),
    )
    conn.execute(
        "INSERT INTO trades (mint,symbol,side,price_usd,amount_tokens,sol_amount,pnl_sol,ts)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (mint, pos[1], "SELL", None, tokens_sold, sol_received, pnl_partial, int(time.time())),
    )
    conn.commit()
    conn.close()


def close_position(mint, tokens_sold, sol_received):
    conn = _conn()
    pos = conn.execute("SELECT * FROM positions WHERE mint=?", (mint,)).fetchone()
    if pos:
        pnl = sol_received - pos[4]
        conn.execute(
            "INSERT INTO trades (mint,symbol,side,price_usd,amount_tokens,sol_amount,pnl_sol,ts)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (mint, pos[1], "SELL", None, tokens_sold, sol_received, pnl, int(time.time())),
        )
    conn.execute("DELETE FROM positions WHERE mint=?", (mint,))
    conn.commit()
    conn.close()


def total_pnl_sol() -> float:
    conn = _conn()
    row = conn.execute("SELECT COALESCE(SUM(pnl_sol),0) FROM trades WHERE side='SELL'").fetchone()
    conn.close()
    return float(row[0] or 0)


def recent_trades(limit=10):
    conn = _conn()
    rows = conn.execute(
        "SELECT mint,symbol,side,amount_tokens,sol_amount,pnl_sol,ts FROM trades"
        " ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows
