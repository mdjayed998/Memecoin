"""
Manual / Auto mode system (v1.4).

Every automated action has a manual counterpart, and every manual action
has an automatic form. Modes persist in the same SQLite DB as positions
(DB_PATH), so they survive redeploys and restarts.

Features:
  tpsl   — Take-profit / stop-loss sell.
           auto:   monitor sells immediately when TP/SL hits (v1 default).
           manual: monitor only alerts "TP/SL hit — sell?" with Sell buttons;
                   nothing sells until you tap.
  copy   — Copy trading.
           auto:   target buy/sell is copied immediately (v1.2 default).
           manual: target buy/sell only sends an alert with an approve
                   button; nothing executes until you approve it.
  launch — Tweet-to-Token launcher.
           manual: /launch shows a preview; you tap a dev-buy button (v1.3).
           auto:   /launch <link> skips the preview and launches at once
                   with the preset dev buy (see launch_dev_buy below).
  buy    — Plain buy has no auto trigger of its own: its automatic forms
           ARE copy trade and auto-launch. Manual buy is tap-to-buy / /buy.

Also stored here:
  launch_dev_buy — preset dev-buy SOL for auto-launch (default 0.1, cap 1.0).
"""
import sqlite3

from config import DB_PATH

VALID_MODES = ("manual", "auto")
FEATURES = ("tpsl", "copy", "launch")
DEFAULTS = {"tpsl": "auto", "copy": "auto", "launch": "manual"}


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS modes (feature TEXT PRIMARY KEY, value TEXT)"
    )
    return conn


def get_mode(feature: str, default: str | None = None) -> str:
    if feature not in FEATURES:
        raise ValueError(f"Unknown feature: {feature}")
    conn = _conn()
    row = conn.execute("SELECT value FROM modes WHERE feature=?", (feature,)).fetchone()
    conn.close()
    if row and row[0] in VALID_MODES:
        return row[0]
    return default if default is not None else DEFAULTS[feature]


def set_mode(feature: str, mode: str) -> str:
    if feature not in FEATURES:
        raise ValueError(f"Unknown feature: {feature}. Use: {', '.join(FEATURES)}")
    mode = (mode or "").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError("Mode must be 'manual' or 'auto'.")
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO modes VALUES (?,?)", (feature, mode))
    conn.commit()
    conn.close()
    return mode


def list_modes() -> dict:
    return {f: get_mode(f) for f in FEATURES}


def get_launch_dev_buy(default: float = 0.1) -> float:
    conn = _conn()
    row = conn.execute("SELECT value FROM modes WHERE feature='launch_dev_buy'").fetchone()
    conn.close()
    try:
        v = float(row[0]) if row else default
    except (TypeError, ValueError):
        return default
    return v if 0.02 <= v <= 1.0 else default


def set_launch_dev_buy(sol: float) -> float:
    if sol < 0.02 or sol > 1.0:
        raise ValueError("Dev buy must be between 0.02 and 1 SOL.")
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO modes VALUES ('launch_dev_buy',?)", (str(sol),))
    conn.commit()
    conn.close()
    return sol


# --- generic values (v1.6): same table, no validation (e.g. active_wallet) ---
def get_value(name: str, default: str | None = None) -> str | None:
    conn = _conn()
    row = conn.execute("SELECT value FROM modes WHERE feature=?", (name,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_value(name: str, value: str) -> str:
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO modes VALUES (?,?)", (name, value))
    conn.commit()
    conn.close()
    return value


# --- v1.5 advanced settings (same key-value table, on/off style) ---
# trail: after TP sells 50%, the remaining position's SL moves to entry
#        price (breakeven) — "on" (default) or "off".
# rug:   "normal" (default) = DANGER/UNKNOWN blocked, WARNING allowed;
#        "strict" = WARNING blocked too, only GOOD tokens can be bought.
SETTINGS = {"trail": ("on", "off"), "rug": ("strict", "normal")}
SETTING_DEFAULTS = {"trail": "on", "rug": "normal"}


def get_setting(name: str, default: str | None = None) -> str:
    if name not in SETTINGS:
        raise ValueError(f"Unknown setting: {name}")
    conn = _conn()
    row = conn.execute("SELECT value FROM modes WHERE feature=?", (name,)).fetchone()
    conn.close()
    if row and row[0] in SETTINGS[name]:
        return row[0]
    return default if default is not None else SETTING_DEFAULTS[name]


def set_setting(name: str, value: str) -> str:
    if name not in SETTINGS:
        raise ValueError(f"Unknown setting: {name}. Use: {', '.join(SETTINGS)}")
    value = (value or "").strip().lower()
    if value not in SETTINGS[name]:
        raise ValueError(f"{name} must be {' or '.join(SETTINGS[name])}.")
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO modes VALUES (?,?)", (name, value))
    conn.commit()
    conn.close()
    return value
