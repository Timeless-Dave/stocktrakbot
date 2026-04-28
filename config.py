"""
config.py – Centralized configuration loader.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── OpenAI ───────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# ── Stock-Trak ───────────────────────────────────────────────────────────────
STOCKTRAK_USER: str = os.getenv("STOCKTRAK_USER", "")
STOCKTRAK_PASS: str = os.getenv("STOCKTRAK_PASS", "")

# ── Watchlist (organized by asset class) ─────────────────────────────────────
WATCHLIST: dict[str, list[str]] = {
    "stocks": [
        "PLTR", "MCD", "JNJ", "PEP", "NOW", "INTU",
        "MSFT", "NVDA", "GE", "AAPL", "AMZN", "META",
        "TSLA", "AMD", "CRWD", "APP", "MSTR", "COIN",
        "SMCI", "NET", "SNOW", "DDOG", "HOOD",
    ],
    "etfs": [
        "SPY", "QQQ", "ITA", "GLD", "TLT", "VTI", "ARKK", "XLE",
    ],
    "crypto": [
        "BTC-USD", "ETH-USD", "SOL-USD",
    ],
    "bonds": [
        "BND", "AGG", "HYG",
    ],
    "mutual": [
        "VFIAX", "FXAIX", "VTSAX",
    ],
}

TARGET_TICKERS: list[str] = [t for tickers in WATCHLIST.values() for t in tickers]
SUPPORTED_ASSET_CLASSES: tuple[str, ...] = ("stocks", "etfs", "crypto", "bonds")

# ── Bot Behaviour ─────────────────────────────────────────────────────────────
TRADE_QUANTITY: int = 15

# Competition mode: 72 threshold catches strong setups the model rates 72-79
# that would previously be silently dropped. 302 trades at $10 is bad, but
# 0 trades per cycle because the gate is too tight is worse.
CONFIDENCE_THRESHOLD: int = int(os.getenv("CONFIDENCE_THRESHOLD", "72"))

# 30-min cycles: ~13 cycles/trading day. More chances to catch intraday moves.
CYCLE_SLEEP_SECONDS: int = 1800      # 30 min between cycles

MAX_FETCH_RETRIES: int = 3
HEADLESS: bool = True
CRYPTO_ALWAYS_ON: bool = True        # crypto analysed even when market closed

# Fully automated — no manual prompt needed. Set to "skip" if you want to
# protect a lead, or "prompt" to gate manually.
RANK_GUARD_THRESHOLD: int = 10
RANK_GUARD_MODE: str = (os.getenv("RANK_GUARD_MODE", "allow").strip().lower() or "allow")

# OpenAI model selection. Override in `.env` as needed.
OPENAI_MODEL: str = (os.getenv("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o")

# Competition-mode hold period: 2h minimum so we don't churn within the same
# session, but short enough to take profits on intraday moves.
MIN_HOLD_HOURS: float = 2.0

# 2 buys + 2 sells per 30-min cycle → up to 4 trades/cycle, ~52 trades/day max.
# In practice the confidence gate keeps actual trades around 1-2/cycle.
MAX_BUYS_PER_CYCLE: int = 2
MAX_SELLS_PER_CYCLE: int = 2

# Sell threshold: $10 commission on a $12,000 position = 0.083% drag.
# 0.25% gain covers commissions with room to spare.
MIN_SELL_GAIN_PCT: float = 0.25      # don't sell unless at least +0.25% up
STOP_LOSS_PCT: float = -3.0          # override the hold filter if down ≥ 3%

# How long to sleep BETWEEN each ticker's API call (keeps us under ~15 RPM).
TICKER_SLEEP_SECONDS: int = 6

# Persistent bot state and append-only trade ledger.
BOT_STATE_FILE: str = os.getenv("BOT_STATE_FILE", "bot_state.json")
TRADE_LEDGER_FILE: str = os.getenv("TRADE_LEDGER_FILE", "trade_ledger.jsonl")

# Competition end date (YYYY-MM-DD). Used to modulate aggression in the brain.
COMPETITION_END_DATE: str = os.getenv("COMPETITION_END_DATE", "2026-05-16")

# Assumed starting capital for position sizing ($100K is StockTrak default).
POSITION_BASE_CAPITAL: float = float(os.getenv("POSITION_BASE_CAPITAL", "100000"))

# ── Data fetch interval ───────────────────────────────────────────────────────
# "1h" gives intraday-responsive indicators (RSI/MACD react to hourly candles).
# "1d" gives classic daily indicators (slower, smoother).
# For a 30-min cycle bot, "1h" is recommended — signals are fresh every cycle.
# yfinance limit: 1h data available up to 730 days back; "60d" period is safe.
DATA_INTERVAL: str = os.getenv("DATA_INTERVAL", "1h")
DATA_PERIOD: str    = os.getenv("DATA_PERIOD",   "60d")

# ── Validation ────────────────────────────────────────────────────────────────
def validate_config() -> None:
    missing = []
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if not STOCKTRAK_USER:
        missing.append("STOCKTRAK_USER")
    if not STOCKTRAK_PASS:
        missing.append("STOCKTRAK_PASS")
    if missing:
        raise EnvironmentError(
            f"[Config] Missing required env vars: {', '.join(missing)}\n"
            "Copy .env.example to .env, fill in the values, and re-run."
        )
    if RANK_GUARD_MODE not in {"prompt", "skip", "allow"}:
        raise EnvironmentError(
            "[Config] RANK_GUARD_MODE must be one of: prompt, skip, allow."
        )
    if CONFIDENCE_THRESHOLD < 50 or CONFIDENCE_THRESHOLD > 99:
        raise EnvironmentError(
            "[Config] CONFIDENCE_THRESHOLD must be between 50 and 99."
        )
