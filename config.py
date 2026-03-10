"""
config.py – Centralized configuration loader.
Reads all credentials and settings from environment variables (or a .env file).
"""
import os
from dotenv import load_dotenv

# Load variables from a .env file in the project root (if it exists)
load_dotenv()

# ── Gemini ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# ── Stock-Trak ───────────────────────────────────────────────────────────────
STOCKTRAK_USER: str = os.getenv("STOCKTRAK_USER", "")
STOCKTRAK_PASS: str = os.getenv("STOCKTRAK_PASS", "")
STOCKTRAK_BASE_URL: str = "https://www.stocktrak.com"

# ── Bot Behaviour ─────────────────────────────────────────────────────────────
# Tickers to monitor every cycle
TARGET_TICKERS: list[str] = ["PLTR", "MCD", "JNJ", "PEP", "NOW", "INTU"]

# Shares to buy / sell per confirmed signal
TRADE_QUANTITY: int = 15

# Minimum Gemini confidence score (0-100) required before executing a trade
CONFIDENCE_THRESHOLD: int = 75

# Seconds to sleep between full analysis cycles (15 min = 900 s)
CYCLE_SLEEP_SECONDS: int = 900

# Seconds to sleep between individual ticker checks within a cycle
TICKER_SLEEP_SECONDS: int = 5

# Max retries for data fetching with exponential back-off
MAX_FETCH_RETRIES: int = 3

# Run the Playwright browser visibly (False) or in the background (True)
HEADLESS: bool = False

# ── Validation ────────────────────────────────────────────────────────────────
def validate_config() -> None:
    """Raise an error early if critical environment variables are missing."""
    missing = []
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if not STOCKTRAK_USER:
        missing.append("STOCKTRAK_USER")
    if not STOCKTRAK_PASS:
        missing.append("STOCKTRAK_PASS")
    if missing:
        raise EnvironmentError(
            f"[Config] Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env, fill in the values, and re-run."
        )
