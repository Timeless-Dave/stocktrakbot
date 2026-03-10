"""
data_fetcher.py – The Eyes
Pulls OHLCV price data from yfinance, calculates RSI / MACD / SMA via the
`ta` library (pure Python, works on Python 3.14+), and caches results in an
in-memory hash map for O(1) lookups.
"""
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime


class MarketDataFetcher:
    """Fetches live market data and technical indicators for a list of tickers."""

    def __init__(self) -> None:
        # Hash-map cache: ticker → latest computed metrics dict
        self.market_cache: dict = {}

    # ─────────────────────────────────────────────────────────────────────────
    def fetch_stock_data(
        self,
        ticker: str,
        period: str = "3mo",
        interval: str = "1d",
    ) -> dict | None:
        """
        Download OHLCV history, compute technical indicators, cache & return
        a flat dictionary ready to be injected into the Gemini prompt.

        Parameters
        ----------
        ticker   : NYSE/NASDAQ symbol, e.g. "PLTR"
        period   : yfinance look-back period  (e.g. "1mo", "3mo")
        interval : bar interval               (e.g. "1d", "1h")
        """
        print(f"[Data] Fetching market data for {ticker}…")
        try:
            stock = yf.Ticker(ticker)
            df: pd.DataFrame = stock.history(period=period, interval=interval)

            if df.empty:
                print(f"[Data][Error] No data returned for {ticker}.")
                return None

            # ── Clean ─────────────────────────────────────────────────────────
            df.dropna(inplace=True)

            close = df["Close"]
            high  = df["High"]
            low   = df["Low"]

            # ── Technical Indicators via `ta` ─────────────────────────────────
            # RSI (14-period momentum oscillator)
            rsi_series   = ta.momentum.RSIIndicator(close=close, window=14).rsi()

            # MACD (12/26 EMA, 9-period signal)
            macd_obj     = ta.trend.MACD(close=close, window_fast=12,
                                         window_slow=26, window_sign=9)
            macd_series  = macd_obj.macd()
            signal_series= macd_obj.macd_signal()
            hist_series  = macd_obj.macd_diff()

            # SMA-20
            sma_series   = ta.trend.SMAIndicator(close=close, window=20).sma_indicator()

            # ── Build the cache entry (latest bar) ────────────────────────────
            def _last(series: pd.Series, decimals: int = 2) -> float:
                val = series.iloc[-1]
                return round(float(val) if not pd.isna(val) else 0.0, decimals)

            self.market_cache[ticker] = {
                "last_updated":  datetime.now().isoformat(timespec="seconds"),
                "current_price": _last(close,         2),
                "rsi_14":        _last(rsi_series,    2),
                "macd":          _last(macd_series,   4),
                "macd_signal":   _last(signal_series, 4),
                "macd_hist":     _last(hist_series,   4),
                "sma_20":        _last(sma_series,    2),
                "volume":        int(df["Volume"].iloc[-1]),
            }

            return self.market_cache[ticker]

        except Exception as exc:
            print(f"[Data][Error] Failed to fetch {ticker}: {exc}")
            return None

    # ─────────────────────────────────────────────────────────────────────────
    def get_cached_data(self, ticker: str) -> dict | None:
        """Return the last cached snapshot for a ticker in O(1) time."""
        return self.market_cache.get(ticker)


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    fetcher = MarketDataFetcher()

    test_tickers = ["PLTR", "MCD"]
    for sym in test_tickers:
        data = fetcher.fetch_stock_data(sym)
        if data:
            print(f"\n─── {sym} Snapshot ───────────────────────────────")
            for key, val in data.items():
                print(f"  {key:<16}: {val}")
