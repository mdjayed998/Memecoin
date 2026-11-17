"""
Buy-the-dip watchlist (v1.5).

/dip_add <mint> <target price USD> <sol> — when DexScreener price falls to
the target or below, the bot buys (rug check still runs first) and the
watch is removed. Max 5 watches. Stored in the same SQLite DB (DB_PATH).
"""
import sqlite3
import time

from config import DB_PATH

MAX_DIPS = 5


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS dips (
            mint TEXT PRIMARY KEY,
            symbol TEXT,
            target_price_usd REAL,
            sol REAL,
            created_at INTEGER
        )"""
    )
    return conn


def add_dip(mint: str, symbol: str, target_price_usd: float, sol: float) -> bool:
    """Add or update a watch. Returns False only when the list is full
    (5 watches) and this mint is not already watched."""
    if target_price_usd <= 0 or sol <= 0:
        raise ValueError("Target price and SOL must be positive.")
    conn = _conn()
    exists = conn.execute("SELECT 1 FROM dips WHERE mint=?", (mint,)).fetchone()
    if not exists:
        count = conn.execute("SELECT COUNT(*) FROM dips").fetchone()[0]
        if count >= MAX_DIPS:
            conn.close()
            return False
    conn.execute(
        "INSERT OR REPLACE INTO dips VALUES (?,?,?,?,?)",
        (mint, symbol, target_price_usd, sol, int(time.time())),
    )
    conn.commit()
    conn.close()
    return True


def list_dips() -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT mint, symbol, target_price_usd, sol, created_at FROM dips"
        " ORDER BY created_at"
    ).fetchall()
    conn.close()
    keys = ["mint", "symbol", "target_price_usd", "sol", "created_at"]
    return [dict(zip(keys, r)) for r in rows]


def remove_dip(mint: str) -> bool:
    conn = _conn()
    cur = conn.execute("DELETE FROM dips WHERE mint=?", (mint,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0
