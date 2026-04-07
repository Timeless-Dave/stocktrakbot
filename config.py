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
# Only act on very high-conviction signals. 302 trades in one session at $10/ea
# cost ~$3,020 in commissions and destroyed the Sharpe ratio. Raising this
# eliminates low-quality churn.
# Execution gate for BUY/SELL. If set too high, the model will never clear it
# (especially during high-VIX regimes) and the bot will place zero trades.
CONFIDENCE_THRESHOLD: int = int(os.getenv("CONFIDENCE_THRESHOLD", "80"))

# One cycle per hour instead of every 15 min.
# Old: 4 cycles/hr × 1 OpenAI call = 4 calls/hr → ~26 calls/day (ok)
# But per-cycle trade pressure was too high. Hourly pacing = ~6 cycles/trading day.
CYCLE_SLEEP_SECONDS: int = 3600      # 60 min between cycles

MAX_FETCH_RETRIES: int = 3
HEADLESS: bool = True
CRYPTO_ALWAYS_ON: bool = True        # crypto analysed even when market closed

# Leaderboard guard: currently ranked #4. Raise the guard so the bot ALWAYS
# asks before placing trades when we're inside the top 10.
RANK_GUARD_THRESHOLD: int = 10
RANK_GUARD_MODE: str = (os.getenv("RANK_GUARD_MODE", "prompt").strip().lower() or "prompt")

# OpenAI model selection. Override in `.env` as needed.
OPENAI_MODEL: str = (os.getenv("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o")

# Minimum hours a position must be held before the bot will consider selling it.
# Prevents same-session round-trips that cost 2 × $10 commission for no gain.
MIN_HOLD_HOURS: float = 6.0

# Hard cap on how many BUY and SELL orders the bot may place in a single cycle.
# Keeps total weekly trades low and commissions in check.
MAX_BUYS_PER_CYCLE: int = 1
MAX_SELLS_PER_CYCLE: int = 1

# Minimum price-gain % a position must show before the bot will SELL it (takes
# commissions into account: $10 in + $10 out on a ~$8,000 position ≈ 0.25%).
# Set to 0.5 so we never sell at a wash; set negative to allow stop-loss sells.
MIN_SELL_GAIN_PCT: float = 0.5       # don't sell unless at least +0.5% up
STOP_LOSS_PCT: float = -4.0          # override the hold filter if down ≥ 4%

# How long to sleep BETWEEN each ticker's API call (keeps us under ~15 RPM).
TICKER_SLEEP_SECONDS: int = 6

# Persistent bot state and append-only trade ledger.
BOT_STATE_FILE: str = os.getenv("BOT_STATE_FILE", "bot_state.json")
TRADE_LEDGER_FILE: str = os.getenv("TRADE_LEDGER_FILE", "trade_ledger.jsonl")

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
