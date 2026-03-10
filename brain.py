"""
brain.py – The Brain (Batch Processing Edition)
O(1) Gemini API calls per cycle instead of O(N).
Sends the entire market matrix in one prompt; Gemini ranks all assets
against each other and returns a JSON array of decisions.
"""
import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types


_SYSTEM_PROMPT = """
You are a ruthless quantitative trading algorithm managing a diversified portfolio.
You will receive a "Market Matrix" containing technical and fundamental data for
multiple assets simultaneously.

ASSET CLASSES in the matrix:
  • stocks  – equities (RSI, MACD, Bollinger Bands, P/E, EPS, news, analyst rec)
  • etfs    – exchange-traded funds (trend + momentum focus)
  • crypto  – 24/7 digital assets (higher volatility thresholds required)
  • bonds   – fixed-income ETFs (inverse rate plays)
  • mutual  – mutual funds (end-of-day NAV; high bar for trading)

MANDATORY CONFLUENCE RULES:
1. Rank ALL assets. Only assign confidence > 75 to the BEST setups where
   multiple independent signal types agree simultaneously:
     BUY confluence  → price near/below Lower Bollinger Band AND RSI < 38
                       AND MACD histogram turning positive AND, for equities,
                       reasonable P/E + positive earnings/revenue growth.
     SELL confluence → price near/above Upper Bollinger Band AND RSI > 68
                       AND MACD histogram falling.
2. If signals conflict (e.g., RSI oversold but MACD still negative), output
   HOLD with confidence < 50. Do NOT force a signal.
3. Compare assets RELATIVELY — the best risk/reward ratio in the batch should
   get the highest confidence. Not every cycle needs a > 75 signal.
4. Catastrophic negative news overrides bullish technicals.
5. Crypto: require RSI < 35 or > 72 for any confidence > 60.
6. Mutual funds: require price_change_5d < -3% + RSI < 38 for BUY.
7. OUTPUT: a JSON array with exactly one object per asset in the matrix.
   Required keys: ticker, action (BUY/SELL/HOLD), confidence (1-100), reasoning (one sentence).
""".strip()

# Response schema: array of per-asset decisions
_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "ticker":     {"type": "string"},
            "action":     {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
            "confidence": {"type": "integer",
                           "description": "1-100; >75 requires strict confluence."},
            "reasoning":  {"type": "string",
                           "description": "One sentence citing the key signals."},
        },
        "required": ["ticker", "action", "confidence", "reasoning"],
    },
}


class TradingBrain:
    """Single-call batch analyser: one Gemini request for the entire portfolio."""

    def __init__(self) -> None:
        load_dotenv()
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "[Brain] GEMINI_API_KEY not set. Fill in your .env file."
            )
        self._client = genai.Client(api_key=api_key)
        self._model  = "gemini-2.0-flash"

    # ─────────────────────────────────────────────────────────────────────────
    def analyze_portfolio(self, market_matrix: dict) -> list[dict]:
        """
        Send the entire market data snapshot in one API call.

        Parameters
        ----------
        market_matrix : { ticker: {asset_class, current_price, rsi_14, ...} }

        Returns
        -------
        List of dicts: [{ticker, action, confidence, reasoning}, ...]
        Returns [] on failure (caller treats all as HOLD).
        """
        n = len(market_matrix)
        print(f"[Brain] Batch-analysing {n} assets in 1 API call...")

        # Compact the matrix — drop internal bookkeeping keys to save tokens
        _DROP = {"last_updated", "asset_class"}
        compact = {
            tkr: {k: v for k, v in data.items() if k not in _DROP and v is not None}
            for tkr, data in market_matrix.items()
        }

        # Add asset_class as a top-level label per ticker (saves prompt tokens vs nesting)
        asset_labels = "\n".join(
            f"  {tkr}: {data.get('asset_class', 'stocks')}"
            for tkr, data in market_matrix.items()
        )

        user_prompt = (
            f"Asset-class labels:\n{asset_labels}\n\n"
            f"Market Matrix ({n} assets):\n"
            f"{json.dumps(compact, indent=2)}\n\n"
            "Apply your confluence rules and return the execution list for ALL assets."
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=_RESPONSE_SCHEMA,
                    temperature=0.10,       # Very low — consistent, deterministic ranking
                    max_output_tokens=1024, # Enough for 29 × ~25-token objects
                ),
            )
            decisions: list = json.loads(response.text)
            print(f"[Brain] Received {len(decisions)} decisions from Gemini.")
            return decisions

        except Exception as exc:
            print(f"[Brain][Error] Batch analysis failed: {exc}")
            return []


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    load_dotenv()
    b = TradingBrain()

    # Mock a 3-asset matrix with deliberately strong confluence on PLTR
    mock_matrix = {
        "PLTR": {
            "asset_class": "stocks",
            "current_price": 151.74, "price_change_5d": -4.1,
            "rsi_14": 28.5, "macd": -0.4, "macd_hist": 0.6,
            "sma_20": 158.0, "sma_50": 162.0,
            "bb_lower": 148.0, "bb_upper": 170.0, "bb_pct": 0.11,
            "atr_14": 4.9, "volume": 42_000_000, "volume_vs_avg": 1.8,
            "trailing_pe": 145, "forward_pe": 88,
            "revenue_growth": 0.28, "earnings_growth": 0.42,
            "recommendation": "buy", "analyst_target": 198.0,
            "recent_news": ["Palantir wins $480M DoD AI contract"],
        },
        "MCD": {
            "asset_class": "stocks",
            "current_price": 329.0, "price_change_5d": 1.2,
            "rsi_14": 54.0, "macd": 0.9, "macd_hist": 0.2,
            "sma_20": 327.0, "sma_50": 318.0,
            "bb_lower": 310.0, "bb_upper": 344.0, "bb_pct": 0.52,
            "trailing_pe": 23, "recommendation": "hold",
        },
        "BTC-USD": {
            "asset_class": "crypto",
            "current_price": 71000, "price_change_5d": -2.5,
            "rsi_14": 42.0, "macd_hist": -180,
            "bb_lower": 65000, "bb_upper": 82000, "bb_pct": 0.40,
            "atr_14": 2800, "volume_vs_avg": 0.9,
        },
    }

    decisions = b.analyze_portfolio(mock_matrix)
    print("\n--- Batch Decisions ---")
    for d in decisions:
        print(f"  {d['ticker']:<10} {d['action']:<5} conf={d['confidence']}%  {d['reasoning']}")
