"""
main.py – The Conductor
Initialises all three pillars (eyes, brain, hands), then runs a continuous,
fault-tolerant terminal loop that analyses your watchlist and executes trades
whenever Gemini returns a high-confidence BUY or SELL signal.

Usage
-----
  # 1. Copy .env.example → .env and fill in your secrets
  # 2. python main.py
  # 3. Press Ctrl+C to shut down gracefully
"""
import time
from datetime import datetime, timezone

from config import (
    TARGET_TICKERS,
    TRADE_QUANTITY,
    CONFIDENCE_THRESHOLD,
    CYCLE_SLEEP_SECONDS,
    TICKER_SLEEP_SECONDS,
    MAX_FETCH_RETRIES,
    HEADLESS,
    STOCKTRAK_USER,
    STOCKTRAK_PASS,
    validate_config,
)
from data_fetcher import MarketDataFetcher
from brain import TradingBrain
from executor import StockTrakExecutor


# ── Helpers ───────────────────────────────────────────────────────────────────
def ts() -> str:
    """Return a human-readable local timestamp."""
    return datetime.now().strftime("%H:%M:%S")


def is_market_open() -> bool:
    """
    Rough US market-hours check (09:30 – 16:00 ET, Mon-Fri).
    For production use, replace with a proper market-calendar library
    (e.g. `pandas_market_calendars`).
    """
    now = datetime.now()
    if now.weekday() >= 5:           # Saturday = 5, Sunday = 6
        return False
    # Assuming the bot runs in ET; adjust the hour range if your system
    # clock is in a different timezone.
    return (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and (now.hour < 16)


# ── TradingBot ────────────────────────────────────────────────────────────────
class TradingBot:
    """Orchestrates the full data→analysis→execution cycle."""

    def __init__(self) -> None:
        print(f"[{ts()}] [System] Initialising HBCUStockMarketChallenge2026 Bot…")

        # Fail fast: make sure all env vars are present
        validate_config()

        # ── Boot all three pillars ────────────────────────────────────────────
        self.eyes  = MarketDataFetcher()
        self.brain = TradingBrain()
        self.hands = StockTrakExecutor(headless=HEADLESS)

        # Log in once; the session stays alive for the entire run
        if not self.hands.login(STOCKTRAK_USER, STOCKTRAK_PASS):
            self.hands.close()
            raise RuntimeError("[System] Could not log in — aborting.")

        print(f"[{ts()}] [System] All systems online. Monitoring: {TARGET_TICKERS}")
        print("─" * 60)

    # ─────────────────────────────────────────────────────────────────────────
    def _fetch_with_retry(self, ticker: str) -> dict | None:
        """
        Attempt to fetch market data with exponential back-off.
        Waits 1 s → 2 s → 4 s between retries.
        """
        for attempt in range(MAX_FETCH_RETRIES):
            data = self.eyes.fetch_stock_data(ticker)
            if data:
                return data
            wait = 2 ** attempt
            print(f"[{ts()}] [Warning] Fetch failed for {ticker}. Retry in {wait}s…")
            time.sleep(wait)
        print(f"[{ts()}] [Error] Max retries reached for {ticker}. Skipping.")
        return None

    # ─────────────────────────────────────────────────────────────────────────
    def _process_ticker(self, ticker: str) -> None:
        """Full pipeline for a single ticker within one cycle."""

        # ── 1. Eyes: Fetch data ───────────────────────────────────────────────
        data = self._fetch_with_retry(ticker)
        if not data:
            return

        price = data.get("current_price", "N/A")
        print(f"[{ts()}] [Data]  {ticker} @ ${price}")

        # ── 2. Brain: Analyse ─────────────────────────────────────────────────
        decision   = self.brain.analyze_asset(ticker, data)
        action     = decision.get("action", "HOLD")
        confidence = decision.get("confidence", 0)
        reasoning  = decision.get("reasoning", "—")

        indicator = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(action, "⚪")
        print(
            f"[{ts()}] [Brain] {indicator} {ticker} | "
            f"{action} | Confidence: {confidence}% | {reasoning}"
        )

        # ── 3. Hands: Execute ─────────────────────────────────────────────────
        if action in ("BUY", "SELL") and confidence >= CONFIDENCE_THRESHOLD:
            success = self.hands.execute_trade(ticker, action, TRADE_QUANTITY)
            status  = "SUCCESS ✓" if success else "FAILED ✗"
            print(
                f"[{ts()}] [{status}] {action} {TRADE_QUANTITY} shares of {ticker}"
            )
        else:
            print(f"[{ts()}] [Skip]  {ticker} → confidence below threshold or HOLD.")

    # ─────────────────────────────────────────────────────────────────────────
    def run(self) -> None:
        """The main continuous terminal loop."""
        try:
            while True:
                # ── Market hours gate ─────────────────────────────────────────
                if not is_market_open():
                    print(
                        f"[{ts()}] [System] Market is closed. "
                        "Sleeping 5 minutes then re-checking…"
                    )
                    time.sleep(300)
                    continue

                print(f"[{ts()}] [System] ── Starting new analysis cycle ──")

                for ticker in TARGET_TICKERS:
                    self._process_ticker(ticker)
                    time.sleep(TICKER_SLEEP_SECONDS)   # Rate-limit buffer

                print("─" * 60)
                print(
                    f"[{ts()}] [System] Cycle complete. "
                    f"Next run in {CYCLE_SLEEP_SECONDS // 60} minutes."
                )
                time.sleep(CYCLE_SLEEP_SECONDS)

        except KeyboardInterrupt:
            print(f"\n[{ts()}] [System] Ctrl+C received — shutting down…")

        except Exception as exc:
            print(f"\n[{ts()}] [Fatal] {exc}")

        finally:
            self.hands.close()
            print(f"[{ts()}] [System] Bot terminated gracefully. Goodbye!")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
