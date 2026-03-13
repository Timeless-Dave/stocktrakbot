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

# ── Bot Behaviour ─────────────────────────────────────────────────────────────
TRADE_AMOUNT_DOLLARS: int = 5000
CONFIDENCE_THRESHOLD: int = 75
CYCLE_SLEEP_SECONDS: int = 900       # 15 min between cycles
MAX_FETCH_RETRIES: int = 3
HEADLESS: bool = True
CRYPTO_ALWAYS_ON: bool = True        # crypto analysed even when market closed

# Leaderboard guard: if your rank is <= this threshold, the bot pauses and
# asks for manual y/n confirmation before placing any trades that cycle.
# Set to 0 to disable the guard entirely.
RANK_GUARD_THRESHOLD: int = 3

# How long to sleep BETWEEN each ticker's API call (keeps us under ~15 RPM).
# Math: 29 tickers × 6s = 174s/cycle ≈ 3 min.  4 cycles/hr = 116 calls/hr.
# Over a 6.5-hr trading day ≈ 754 calls — well under the 1,500/day free-tier cap.
TICKER_SLEEP_SECONDS: int = 6

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
