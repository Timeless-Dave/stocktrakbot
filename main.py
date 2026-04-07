"""
main.py – The Conductor (Batch Processing Edition)
Three-phase cycle per iteration:
  PHASE 1 – Ingest:  Fetch data for all assets from yfinance (no OpenAI calls)
  PHASE 2 – Analyse: ONE OpenAI API call for the entire portfolio
  PHASE 3 – Execute: Trade each asset that beat the confidence threshold

API call math:
  Before: 29 tickers × 4 cycles/hr = 116 OpenAI calls/hr  → daily limit hit
  After:   1 batch  × 4 cycles/hr =   4 OpenAI calls/hr  → 26 calls/trading day
"""
import sys
import time
import pytz
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import (
    WATCHLIST,
    TRADE_QUANTITY,
    CONFIDENCE_THRESHOLD,
    CYCLE_SLEEP_SECONDS,
    MAX_FETCH_RETRIES,
    HEADLESS,
    CRYPTO_ALWAYS_ON,
    RANK_GUARD_THRESHOLD,
    MIN_HOLD_HOURS,
    MAX_BUYS_PER_CYCLE,
    MAX_SELLS_PER_CYCLE,
    MIN_SELL_GAIN_PCT,
    STOP_LOSS_PCT,
    STOCKTRAK_USER,
    STOCKTRAK_PASS,
    validate_config,
)
from data_fetcher import MarketDataFetcher
from brain import TradingBrain
from executor import StockTrakExecutor
from decision_utils import sanitize_decisions


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def is_market_open() -> bool:
    """Returns True when US equity markets are open (9:30–16:00 ET, Mon–Fri)."""
    tz  = pytz.timezone("US/Eastern")
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return False
    open_  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= now < close_


class TradingBot:

    def __init__(self, mega_universe: list[str] | None = None, top_n: int = 30) -> None:
        print(f"[{ts()}] [System] Initialising HBCU Stock Market Bot (Batch Mode)...")
        validate_config()

        self.eyes   = MarketDataFetcher()
        self.brain  = TradingBrain()
        self.hands  = StockTrakExecutor(headless=HEADLESS)
        self.mega_universe = mega_universe
        self.top_n = top_n

        if not self.hands.login(STOCKTRAK_USER, STOCKTRAK_PASS):
            self.hands.close()
            raise RuntimeError("[System] Login failed — aborting.")

        # Track open positions and their exact quantities
        self.positions: dict[str, float] = {}
        # Track when the bot opened each position (for minimum hold enforcement)
        self.entry_times:  dict[str, datetime] = {}
        # Track the price at entry (for P/L filter before selling)
        self.entry_prices: dict[str, float]    = {}

        # Seed positions from Stock-Trak so SELL decisions are valid even if the bot didn't open them
        known = self.mega_universe or [t for cls, tickers in WATCHLIST.items() for t in tickers]
        seeded = self.hands.sync_positions(known_tickers=known)
        for t, qty in seeded.items():
            self.positions[t] = qty
            # Seeded positions (manually opened) have no bot entry time — the hold guard
            # uses None to indicate "we don't know when this was bought, skip hold check"

        total = len(self.mega_universe) if self.mega_universe else sum(len(v) for v in WATCHLIST.values())
        print(f"[{ts()}] [System] Online. {total} assets | 1 OpenAI call/cycle")
        print("-" * 60)

    # ─────────────────────────────────────────────────────────────────────────
    def _ingest_all(self, market_open: bool) -> dict:
        """
        PHASE 1 — Data Ingestion.
        Fetch yfinance data for every asset. Zero OpenAI calls here.
        Sleeps 1s between tickers to be polite to Yahoo Finance.
        """
        print(f"[{ts()}] [Ingest] Fetching market data for all assets...")
        matrix: dict = {}

        for asset_class, tickers in WATCHLIST.items():
            # Skip non-crypto when market is closed
            if not market_open and not (asset_class == "crypto" and CRYPTO_ALWAYS_ON):
                continue

            for ticker in tickers:
                for attempt in range(MAX_FETCH_RETRIES):
                    data = self.eyes.fetch_full_data(ticker, asset_class)
                    if data:
                        matrix[ticker] = data
                        break
                    wait = 2 ** attempt
                    print(f"[{ts()}] [Warning] {ticker} fetch failed. Retry in {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"[{ts()}] [Error] Could not fetch {ticker}. Skipping.")

                time.sleep(1)   # 1s between yfinance calls (rate-limit courtesy)

        print(f"[{ts()}] [Ingest] Matrix ready: {len(matrix)} assets.")
        return matrix

    def _ingest_screened(self, market_open: bool) -> dict:
        """
        Two-stage pipeline ingest:
        - Scan a large universe in parallel
        - Send only top N by volume_surge_pct to the model (cost control)
        """
        universe = self.mega_universe or []
        if not universe:
            return {}

        if not market_open:
            # When market is closed, optionally run crypto-only
            if CRYPTO_ALWAYS_ON:
                universe = [t for t in universe if t.upper().endswith("-USD")]
            else:
                return {}

        return self.eyes.screen_universe(universe, top_n=self.top_n, max_workers=10)

    # ─────────────────────────────────────────────────────────────────────────
    def _execute_decisions(self, decisions: list, market_open: bool, matrix: dict) -> None:
        """
        PHASE 3 — Order Execution Queue.
        Work through the model's ranked list and fire trades for high-confidence signals.
        Guards applied (in order):
          1. Confidence threshold
          2. Per-cycle BUY / SELL caps
          3. Position existence check
          4. Minimum hold period (for bot-opened positions)
          5. P/L filter before SELL (commission-aware)
          6. Market hours check
        """
        class_map: dict[str, str] = {
            t: cls for cls, tickers in WATCHLIST.items() for t in tickers
        }

        buys_this_cycle  = 0
        sells_this_cycle = 0

        for decision in decisions:
            ticker     = decision.get("ticker", "")
            action     = decision.get("action",     "HOLD")
            confidence = decision.get("confidence", 0)
            reasoning  = decision.get("reasoning",  "--")

            tag = {"BUY": "[BUY]", "SELL": "[SELL]", "HOLD": "[HOLD]"}.get(action, "[?]")
            print(f"[{ts()}] [Signal] {tag} {ticker:<10} conf={confidence}% | {reasoning}")

            if action not in ("BUY", "SELL") or confidence < CONFIDENCE_THRESHOLD:
                continue

            # ── Per-cycle trade cap ──────────────────────────────────────────
            if action == "BUY" and buys_this_cycle >= MAX_BUYS_PER_CYCLE:
                print(f"[{ts()}] [Skip]   BUY cap ({MAX_BUYS_PER_CYCLE}/cycle) reached — {ticker} skipped.")
                continue
            if action == "SELL" and sells_this_cycle >= MAX_SELLS_PER_CYCLE:
                print(f"[{ts()}] [Skip]   SELL cap ({MAX_SELLS_PER_CYCLE}/cycle) reached — {ticker} skipped.")
                continue

            # ── Position existence guard ─────────────────────────────────────
            current_qty = self.positions.get(ticker, 0)
            if action == "BUY" and current_qty > 0:
                print(f"[{ts()}] [Skip]   Already long {ticker} ({current_qty} shares).")
                continue
            if action == "SELL" and current_qty <= 0:
                print(f"[{ts()}] [Skip]   No position in {ticker} to sell.")
                continue

            asset_class = class_map.get(ticker, "stocks")
            if asset_class == "stocks" and isinstance(ticker, str) and ticker.upper().endswith("-USD"):
                asset_class = "crypto"

            # ── Minimum hold period guard (SELL only) ────────────────────────
            if action == "SELL":
                entry_time = self.entry_times.get(ticker)
                if entry_time is not None:
                    hours_held = (datetime.now() - entry_time).total_seconds() / 3600.0
                    # Check P/L to decide whether the stop-loss override applies
                    entry_price  = self.entry_prices.get(ticker, 0.0)
                    current_price_for_pl = matrix.get(ticker, {}).get("current_price", 0.0)
                    if entry_price > 0 and current_price_for_pl > 0:
                        pl_pct = ((current_price_for_pl - entry_price) / entry_price) * 100.0
                    else:
                        pl_pct = 0.0  # unknown — be conservative

                    if hours_held < MIN_HOLD_HOURS and pl_pct > STOP_LOSS_PCT:
                        print(
                            f"[{ts()}] [Skip]   {ticker} held only {hours_held:.1f}h "
                            f"(min {MIN_HOLD_HOURS}h), P/L={pl_pct:+.1f}% — too soon to sell."
                        )
                        continue

            # ── P/L filter before SELL (commission-aware) ───────────────────
            if action == "SELL":
                entry_price   = self.entry_prices.get(ticker, 0.0)
                current_price = matrix.get(ticker, {}).get("current_price", 0.0)
                if entry_price > 0 and current_price > 0:
                    pl_pct = ((current_price - entry_price) / entry_price) * 100.0
                    # Allow the sell if: above min gain threshold OR in stop-loss territory
                    if pl_pct < MIN_SELL_GAIN_PCT and pl_pct > STOP_LOSS_PCT:
                        print(
                            f"[{ts()}] [Skip]   {ticker} P/L={pl_pct:+.1f}% is below "
                            f"min gain ({MIN_SELL_GAIN_PCT}%) and above stop-loss ({STOP_LOSS_PCT}%). "
                            f"Not worth the $20 round-trip commission."
                        )
                        continue

            # ── Market hours guard ───────────────────────────────────────────
            if not market_open and asset_class != "crypto":
                print(f"[{ts()}] [Skip]   Market closed — cannot trade {ticker} ({asset_class}).")
                continue

            note = f"[{action}] {ticker} ({asset_class}) — {reasoning} (conf: {confidence}%)"

            # ── Dynamic quantity sizing ──────────────────────────────────────
            if action == "SELL":
                qty = self.positions.get(ticker, 0)
            else:
                price = matrix.get(ticker, {}).get("current_price", 0)
                if price <= 0:
                    print(f"[{ts()}] [Fallback] Fetching live price for {ticker} to size trade...")
                    live_data = self.eyes.fetch_full_data(ticker, asset_class=asset_class)
                    price = live_data.get("current_price", 0) if live_data else 0

                if price <= 0:
                    print(f"[{ts()}] [Skip]   Missing price data for {ticker}, cannot size BUY.")
                    continue

                # Confidence scaling: 95% conf -> 95% of $9,000 = $8,550 (cap at $9,900)
                allocation_dollars = min((confidence / 100.0) * 9000.0, 9900.0)
                if asset_class == "crypto":
                    qty = round(allocation_dollars / price, 4)
                else:
                    qty = max(1, int(allocation_dollars / price))

            ok = self.hands.execute_trade(
                ticker, action, qty,
                asset_class=asset_class,
                notes=note,
            )
            label = "SUCCESS" if ok else "FAILED"
            print(f"[{ts()}] [{label}]  {action} {qty}x {ticker}")

            if ok:
                if action == "BUY":
                    self.positions[ticker]   = qty
                    self.entry_times[ticker]  = datetime.now()
                    # Store the price at entry for P/L tracking
                    buy_price = matrix.get(ticker, {}).get("current_price", 0.0)
                    if buy_price > 0:
                        self.entry_prices[ticker] = buy_price
                    buys_this_cycle += 1
                else:
                    self.positions[ticker]  = 0
                    self.entry_times.pop(ticker,  None)
                    self.entry_prices.pop(ticker, None)
                    sells_this_cycle += 1

    # ─────────────────────────────────────────────────────────────────────────────
    def _check_rank_guard(self) -> bool:
        """
        Check the leaderboard. If the current rank is within the guard threshold,
        prompt the user for manual confirmation before trading.

        Returns True  → safe to proceed with trades.
        Returns False → user said no, skip execution this cycle.
        """
        if RANK_GUARD_THRESHOLD <= 0:
            return True  # guard disabled

        rank = self.hands.sync_rank()

        if rank is None:
            print(f"[{ts()}] [Rank] Could not determine ranking (page may have changed). Proceeding.")
            return True

        print(f"[{ts()}] [Rank] Current leaderboard position: #{rank}")

        if rank <= RANK_GUARD_THRESHOLD:
            mode = "prompt"
            try:
                # Allow config to control behavior when in top N.
                from config import RANK_GUARD_MODE
                mode = (RANK_GUARD_MODE or "prompt").strip().lower()
            except Exception:
                mode = "prompt"

            if mode == "allow":
                print(f"[{ts()}] [Rank] Guard tripped but RANK_GUARD_MODE=allow — proceeding without prompt.")
                return True
            if mode == "skip":
                print(f"[{ts()}] [Rank] Guard tripped and RANK_GUARD_MODE=skip — skipping trades this cycle.")
                return False

            print()
            print("=" * 60)
            print(f"  ⚠️  YOU ARE RANK #{rank} — TOP {RANK_GUARD_THRESHOLD} GUARD ACTIVE")
            print("  The bot wants to execute trades this cycle.")
            print("  Proceeding could move you out of this position.")
            print("=" * 60)
            try:
                answer = input("  Allow trades this cycle? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            print()
            if answer != "y":
                print(f"[{ts()}] [Rank] Trade execution SKIPPED by user (rank guard).")
                return False
            print(f"[{ts()}] [Rank] User approved — proceeding with trades.")

        return True

    # ─────────────────────────────────────────────────────────────────────────────
    def run(self) -> None:
        print(f"[{ts()}] [System] Bot active.")
        try:
            while True:
                open_now   = is_market_open()
                any_crypto = CRYPTO_ALWAYS_ON and bool(WATCHLIST.get("crypto"))

                if not open_now and not any_crypto:
                    print(f"[{ts()}] [System] Market closed. Sleeping 1 hour...")
                    time.sleep(3600)
                    continue

                if not open_now:
                    print(f"[{ts()}] [System] Market closed — crypto-only cycle.")

                print(f"[{ts()}] [System] -- Batch Cycle Start --")

                # Macro context (VIX + SPY 5d trend) — once per cycle
                macro = self.eyes.fetch_macro_context()
                print(f"[{ts()}] [Macro] VIX: {macro['VIX']} | SPY 5D: {macro['SPY_5D_Trend_Pct']}%")

                # Refresh positions each cycle (prevents naked sells if you trade manually).
                # Explicitly zero out any ticker that is no longer in the synced data so
                # that manually-closed positions don't trigger ghost SELL orders next cycle.
                known = self.mega_universe or [t for cls, tickers in WATCHLIST.items() for t in tickers]
                seeded = self.hands.sync_positions(known_tickers=known)
                for t in known:
                    if t in seeded:
                        self.positions[t] = seeded[t]
                    else:
                        self.positions[t] = 0

                # PHASE 1: Ingest all data (no OpenAI calls)
                if self.mega_universe:
                    matrix = self._ingest_screened(market_open=open_now)
                else:
                    matrix = self._ingest_all(market_open=open_now)

                if not matrix:
                    print(f"[{ts()}] [Warning] Empty matrix — skipping analysis.")
                    time.sleep(CYCLE_SLEEP_SECONDS)
                    continue

                # PHASE 2: ONE batch OpenAI call (matrix + macro context)
                owned = [t for t, qty in self.positions.items() if qty > 0]
                if not open_now:
                    # Filter out non-crypto owned assets so AI doesn't try to sell them
                    class_map = {t: cls for cls, tickers in WATCHLIST.items() for t in tickers}
                    owned = [t for t in owned if class_map.get(t, "stocks") == "crypto" or t.endswith("-USD")]

                decisions = self.brain.analyze_portfolio(matrix, macro, owned_assets=owned)

                if not decisions:
                    print(f"[{ts()}] [Warning] No decisions returned from OpenAI.")
                else:
                    decisions, warnings = sanitize_decisions(decisions, matrix, owned_assets=owned)
                    for w in warnings:
                        print(f"[{ts()}] [Decision][Warn] {w}")
                    # PHASE 3: Execute trades — but ask first if we're in top N
                    if self._check_rank_guard():
                        self._execute_decisions(decisions, market_open=open_now, matrix=matrix)
                    else:
                        # Still print signals so user can see what was planned
                        for d in decisions:
                            if d.get("action") in ("BUY", "SELL"):
                                print(f"[{ts()}] [Skipped] {d['action']} {d.get('ticker','')} "
                                      f"(rank guard active, trade not placed)")

                print("-" * 60)
                print(f"[{ts()}] [System] Cycle done. Next in {CYCLE_SLEEP_SECONDS // 60} min.")
                time.sleep(CYCLE_SLEEP_SECONDS)

        except KeyboardInterrupt:
            print(f"\n[{ts()}] [System] Shutdown requested...")
        except Exception as exc:
            print(f"\n[{ts()}] [Fatal] {exc}")
        finally:
            self.hands.close()
            print(f"[{ts()}] [System] Bot terminated. Goodbye!")


if __name__ == "__main__":
    # Focused universe: 20 high-quality tickers.
    # Fewer tickers = faster scans, less yfinance churn, and tighter signal quality.
    # The screener will send only the top 10 by volume surge to OpenAI each cycle.
    MEGA_UNIVERSE = [
        # Core large-cap tech (highest liquidity, tight spreads)
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
        # High-beta / AI plays worth watching
        "PLTR", "TSLA", "AMD", "CRWD",
        # Defensive / dividend anchors (balance the portfolio)
        "JNJ", "PEP", "MCD",
        # Broad market ETFs (macro hedge)
        "SPY", "QQQ",
        # Crypto (always-on, 24/7 signals)
        "BTC-USD", "ETH-USD", "SOL-USD",
        # Commodities / sector hedge
        "GLD", "XLE",
    ]

    bot = TradingBot(mega_universe=MEGA_UNIVERSE, top_n=10)
    bot.run()
