"""
main.py – The Conductor
Runs headless, continuous, fault-tolerant trading across all asset classes.
Market-hours gate uses pytz (US/Eastern) — only consumes Gemini quota during
active trading hours to stay under the 1,500 req/day free-tier cap.
"""
import sys
import time
import pytz
from datetime import datetime

# Force UTF-8 so all print calls work on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import (
    WATCHLIST,
    TRADE_QUANTITY,
    CONFIDENCE_THRESHOLD,
    CYCLE_SLEEP_SECONDS,
    TICKER_SLEEP_SECONDS,
    MAX_FETCH_RETRIES,
    HEADLESS,
    CRYPTO_ALWAYS_ON,
    STOCKTRAK_USER,
    STOCKTRAK_PASS,
    validate_config,
)
from data_fetcher import MarketDataFetcher
from brain import TradingBrain
from executor import StockTrakExecutor


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def is_market_open() -> bool:
    """
    Returns True when US equity markets are open (Mon-Fri 9:30-16:00 ET).
    Uses pytz for correct DST handling regardless of system timezone.
    """
    tz  = pytz.timezone("US/Eastern")
    now = datetime.now(tz)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now < market_close


class TradingBot:
    """Orchestrates fetch -> analyse -> execute across all asset classes."""

    def __init__(self) -> None:
        print(f"[{ts()}] [System] Initialising HBCU Stock Market Bot...")
        validate_config()

        self.eyes   = MarketDataFetcher()
        self.brain  = TradingBrain()
        self.hands  = StockTrakExecutor(headless=HEADLESS)

        if not self.hands.login(STOCKTRAK_USER, STOCKTRAK_PASS):
            self.hands.close()
            raise RuntimeError("[System] Could not log in — aborting.")

        # Position tracker: prevents doubling into the same side
        self.positions: dict[str, str | None] = {}

        total = sum(len(v) for v in WATCHLIST.values())
        print(f"[{ts()}] [System] Online. Watching {total} assets across "
              f"{len(WATCHLIST)} classes. "
              f"Inter-call gap: {TICKER_SLEEP_SECONDS}s.")
        print("-" * 60)

    # ─────────────────────────────────────────────────────────────────────────
    def _fetch_with_retry(self, ticker: str, asset_class: str) -> dict | None:
        for attempt in range(MAX_FETCH_RETRIES):
            data = self.eyes.fetch_full_data(ticker, asset_class)
            if data:
                return data
            wait = 2 ** attempt
            print(f"[{ts()}] [Warning] Fetch failed for {ticker}. Retry in {wait}s...")
            time.sleep(wait)
        print(f"[{ts()}] [Error] Max retries for {ticker}. Skipping.")
        return None

    # ─────────────────────────────────────────────────────────────────────────
    def _process_ticker(self, ticker: str, asset_class: str) -> None:
        # 1. Fetch
        data = self._fetch_with_retry(ticker, asset_class)
        if not data:
            return

        price = data.get("current_price", "N/A")
        print(f"[{ts()}] [Data]  {ticker} ({asset_class}) @ ${price}")

        # 2. Analyse
        decision   = self.brain.analyze_asset(ticker, data, asset_class)
        action     = decision.get("action", "HOLD")
        confidence = decision.get("confidence", 0)
        reasoning  = decision.get("reasoning", "--")

        tag = {"BUY": "[BUY]", "SELL": "[SELL]", "HOLD": "[HOLD]"}.get(action, "[?]")
        print(f"[{ts()}] [Brain] {tag} {ticker} | conf={confidence}% | {reasoning}")

        # 3. Position guard — no duplicate sides, no naked sells
        current = self.positions.get(ticker)
        if action == "BUY"  and current == "long":
            print(f"[{ts()}] [Skip]  Already long {ticker} — skipping duplicate BUY.")
            return
        if action == "SELL" and current is None:
            print(f"[{ts()}] [Skip]  No position in {ticker} — nothing to SELL.")
            return

        # 4. Execute trade if confidence >= threshold
        if action in ("BUY", "SELL") and confidence >= CONFIDENCE_THRESHOLD:
            note  = (f"[{action}] {ticker} ({asset_class}) — "
                     f"{reasoning} (conf: {confidence}%)")
            ok    = self.hands.execute_trade(ticker, action, TRADE_QUANTITY,
                                             asset_class=asset_class, notes=note)
            label = "SUCCESS" if ok else "FAILED"
            print(f"[{ts()}] [{label}] {action} {TRADE_QUANTITY}x {ticker}")
            if ok:
                self.positions[ticker] = "long" if action == "BUY" else None
        else:
            print(f"[{ts()}] [Skip]  {ticker} — conf {confidence}% < "
                  f"{CONFIDENCE_THRESHOLD}% or HOLD.")

    # ─────────────────────────────────────────────────────────────────────────
    def _run_cycle(self, market_open: bool) -> None:
        print(f"[{ts()}] [System] -- New analysis cycle --")
        for asset_class, tickers in WATCHLIST.items():
            # Crypto trades 24/7; everything else needs market hours
            if not market_open and not (asset_class == "crypto" and CRYPTO_ALWAYS_ON):
                continue
            for ticker in tickers:
                try:
                    self._process_ticker(ticker, asset_class)
                except Exception as exc:
                    print(f"[{ts()}] [Error] {ticker}: {exc}")
                # Mandatory gap between Gemini API calls (~15 RPM free-tier limit)
                time.sleep(TICKER_SLEEP_SECONDS)

        print("-" * 60)
        print(f"[{ts()}] [System] Cycle done. Next run in "
              f"{CYCLE_SLEEP_SECONDS // 60} min.")

    # ─────────────────────────────────────────────────────────────────────────
    def run(self) -> None:
        """Continuous loop. Sleeps 1 hour when market is closed to save quota."""
        print(f"[{ts()}] [System] Bot is now active.")
        try:
            while True:
                open_now   = is_market_open()
                any_crypto = CRYPTO_ALWAYS_ON and bool(WATCHLIST.get("crypto"))

                if not open_now and not any_crypto:
                    print(f"[{ts()}] [System] Market closed. Sleeping 1 hour...")
                    time.sleep(3600)
                    continue

                if not open_now and any_crypto:
                    print(f"[{ts()}] [System] Market closed — running crypto only.")

                self._run_cycle(market_open=open_now)
                time.sleep(CYCLE_SLEEP_SECONDS)

        except KeyboardInterrupt:
            print(f"\n[{ts()}] [System] Shutdown requested (Ctrl+C)...")

        except Exception as exc:
            print(f"\n[{ts()}] [Fatal] {exc}")

        finally:
            self.hands.close()
            print(f"[{ts()}] [System] Bot terminated. Goodbye!")


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
