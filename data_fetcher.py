"""
data_fetcher.py – The Eyes
Technical + fundamental data for all asset classes.
Indicators: RSI-14, MACD, SMA-20/50, Bollinger Bands, ATR-14, volume ratio.
Fundamentals: P/E (trailing + forward), news headlines.
"""
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime


class MarketDataFetcher:
    """Fetches live market data + technical + fundamental indicators for any asset class."""

    def __init__(self) -> None:
        self.market_cache: dict = {}

    # ─────────────────────────────────────────────────────────────────────────
    def fetch_full_data(
        self,
        ticker: str,
        asset_class: str = "stocks",
        period: str = "3mo",
        interval: str = "1d",
    ) -> dict | None:
        """
        Download OHLCV + fundamentals, compute technicals, cache & return a
        flat dict ready for the Gemini prompt.
        """
        print(f"[Data] Fetching {asset_class.upper()} data for {ticker}...")
        try:
            tkr = yf.Ticker(ticker)
            df: pd.DataFrame = tkr.history(period=period, interval=interval)

            if df.empty:
                print(f"[Data][Error] No price history returned for {ticker}.")
                return None

            df.dropna(inplace=True)
            close  = df["Close"]
            high   = df["High"]
            low    = df["Low"]
            volume = df["Volume"]

            # ── Technical Indicators ──────────────────────────────────────────
            rsi       = ta.momentum.RSIIndicator(close=close, window=14).rsi()
            macd_obj  = ta.trend.MACD(close=close, window_fast=12,
                                      window_slow=26, window_sign=9)
            macd_line = macd_obj.macd()
            macd_sig  = macd_obj.macd_signal()
            macd_hist = macd_obj.macd_diff()
            sma20     = ta.trend.SMAIndicator(close=close, window=20).sma_indicator()
            sma50     = ta.trend.SMAIndicator(close=close, window=50).sma_indicator() \
                        if len(close) >= 50 else sma20

            # Bollinger Bands (20-period, 2σ)
            bb        = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
            bb_lower  = bb.bollinger_lband()
            bb_upper  = bb.bollinger_hband()
            bb_pct    = bb.bollinger_pband()   # 0 = at lower band, 1 = at upper band

            # Average True Range — measures volatility / momentum energy
            atr       = ta.volatility.AverageTrueRange(
                            high=high, low=low, close=close, window=14
                        ).average_true_range()

            # Volume vs 20-day average
            vol_sma   = ta.trend.SMAIndicator(
                            close=volume.astype(float), window=20
                        ).sma_indicator()

            def _last(s: pd.Series, dec: int = 2) -> float:
                v = s.iloc[-1]
                return round(float(v) if not pd.isna(v) else 0.0, dec)

            entry: dict = {
                "asset_class":    asset_class,
                "last_updated":   datetime.now().isoformat(timespec="seconds"),
                "current_price":  _last(close,     4),
                "rsi_14":         _last(rsi,        2),
                "macd":           _last(macd_line,  4),
                "macd_signal":    _last(macd_sig,   4),
                "macd_hist":      _last(macd_hist,  4),
                "sma_20":         _last(sma20,       2),
                "sma_50":         _last(sma50,       2),
                "bb_lower":       _last(bb_lower,    2),
                "bb_upper":       _last(bb_upper,    2),
                "bb_pct":         _last(bb_pct,      3),  # 0=lower band, 1=upper band
                "atr_14":         _last(atr,         4),  # Average True Range
                "volume":         int(volume.iloc[-1]),
                "volume_vs_avg":  round(
                    float(volume.iloc[-1]) / max(_last(vol_sma, 2), 1),
                    2,
                ),
                "price_change_5d": round(
                    ((float(close.iloc[-1]) - float(close.iloc[-5]))
                     / float(close.iloc[-5]) * 100)
                    if len(close) >= 5 else 0.0,
                    2,
                ),
            }

            # ── Fundamentals + News (equities, ETFs, bonds) ───────────────────
            if asset_class in ("stocks", "etfs", "bonds"):
                self._add_fundamentals(tkr, entry)

            # ── Crypto extras ─────────────────────────────────────────────────
            if asset_class == "crypto":
                self._add_crypto_info(tkr, entry)

            self.market_cache[ticker] = entry
            return entry

        except Exception as exc:
            print(f"[Data][Error] Failed to fetch {ticker}: {exc}")
            return None

    # ── Legacy alias ──────────────────────────────────────────────────────────
    def fetch_stock_data(self, ticker: str, period: str = "3mo",
                         interval: str = "1d") -> dict | None:
        return self.fetch_full_data(ticker, asset_class="stocks",
                                    period=period, interval=interval)

    # ─────────────────────────────────────────────────────────────────────────
    def _add_fundamentals(self, tkr: yf.Ticker, entry: dict) -> None:
        """Pull P/E, EPS, growth metrics, and recent news headlines."""
        try:
            info = tkr.info or {}
            entry.update({
                "trailing_pe":     info.get("trailingPE"),
                "forward_pe":      info.get("forwardPE"),
                "pb_ratio":        info.get("priceToBook"),
                "eps_ttm":         info.get("trailingEps"),
                "revenue_growth":  info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "debt_to_equity":  info.get("debtToEquity"),
                "profit_margin":   info.get("profitMargins"),
                "dividend_yield":  info.get("dividendYield"),
                "beta":            info.get("beta"),
                "analyst_target":  info.get("targetMeanPrice"),
                "recommendation":  info.get("recommendationKey"),
                "market_cap":      info.get("marketCap"),
            })
        except Exception:
            pass

        # Recent news headlines (top 2) — feed sentiment to Gemini
        try:
            news = tkr.news or []
            headlines = [
                n.get("content", {}).get("title", "") or n.get("title", "")
                for n in news[:2]
            ]
            entry["recent_news"] = [h for h in headlines if h] or ["No recent news"]
        except Exception:
            entry["recent_news"] = ["No recent news"]

    def _add_crypto_info(self, tkr: yf.Ticker, entry: dict) -> None:
        try:
            info = tkr.info or {}
            entry.update({
                "market_cap":          info.get("marketCap"),
                "circulating_supply":  info.get("circulatingSupply"),
                "volume_24h":          info.get("volume24Hr"),
            })
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    def get_cached_data(self, ticker: str) -> dict | None:
        return self.market_cache.get(ticker)


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    f = MarketDataFetcher()
    for sym, cls in [("PLTR", "stocks"), ("SPY", "etfs"), ("BTC-USD", "crypto")]:
        d = f.fetch_full_data(sym, cls)
        if d:
            print(f"\n--- {sym} ({cls}) ---")
            for k, v in d.items():
                print(f"  {k:<22}: {v}")
