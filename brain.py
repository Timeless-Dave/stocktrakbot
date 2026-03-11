"""
brain.py – The Brain (OpenAI Structured Outputs Edition)
O(1) API calls per cycle — sends the entire market matrix in a single request.
OpenAI's Structured Outputs (Pydantic schema) guarantee 100% valid JSON every time;
no markdown leakage, no KeyError crashes, no retries needed for parsing failures.
"""
import os
import json
from openai import OpenAI
from pydantic import BaseModel, Field
from dotenv import load_dotenv


# ── Pydantic schema — enforced at the token-generation level by OpenAI ────────

class TradeDecision(BaseModel):
    ticker: str = Field(description="The asset ticker symbol, e.g. AAPL or BTC-USD.")
    action: str = Field(description="Exactly one of: BUY, SELL, or HOLD.")
    confidence: int = Field(
        description=(
            "Integer 1-100. Only exceed 75 for strict technical+fundamental confluence. "
            "Mixed signals must stay below 50 with HOLD."
        )
    )
    reasoning: str = Field(description="One concise sentence citing the key signals.")


class PortfolioDecisions(BaseModel):
    decisions: list[TradeDecision]


# ── System prompt — aggressive, relative-strength mandate for gpt-4o-mini ─────

_SYSTEM_PROMPT = """
You are a highly aggressive, relative-value quantitative hedge fund manager.
You are evaluating a matrix of assets. Your mandate is to deploy capital, not to sit in cash.
You must abandon the search for "perfect" setups. Instead, look for RELATIVE STRENGTH and MOMENTUM.

RULES FOR EXECUTION:
1. MANDATORY ACTION: You MUST identify the top 2 to 3 most bullish assets in this matrix and
   assign them a "BUY" action with a confidence score BETWEEN 80 AND 95.
2. BULLISH CRITERIA: Prioritize assets with RSI between 45 and 65 that are bouncing off their
   Lower Bollinger Band, or have strong positive news catalysts, even if MACD is slightly lagging.
3. MANDATORY SHORT/SELL: You MUST identify the 1 or 2 most overextended assets
   (e.g., RSI > 70, hitting Upper Bollinger Band, negative news) and assign them a "SELL" action
   with a confidence > 80.
4. THE REST: The remaining assets that lack clear momentum should be assigned "HOLD"
   with a confidence of 45.
5. DO NOT BE CAUTIOUS. Your job is to find the best available trades in the current matrix,
   even if the overall market conditions are mixed.
6. OUTPUT COVERAGE: For every asset in the market matrix, you MUST return exactly one decision
   object {ticker, action, confidence, reasoning}. Never omit any ticker.
7. RELATIVE RANKING: Confidence scores must reflect relative ranking across the matrix so that
   the strongest BUY and SELL candidates clearly stand out above the HOLDs.
""".strip()


# ── TradingBrain ──────────────────────────────────────────────────────────────

class TradingBrain:
    """Single-call batch analyser: one OpenAI request for the entire portfolio."""

    def __init__(self) -> None:
        load_dotenv()
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "[Brain] OPENAI_API_KEY not set. Add it to your .env file."
            )
        self._client = OpenAI(api_key=api_key)
        self._model  = "gpt-4o-mini"

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
        print(f"[Brain] Batch-analysing {n} assets via OpenAI gpt-4o-mini...")

        # Compact the matrix — drop internal bookkeeping keys to save tokens
        _DROP = {"last_updated", "asset_class"}
        compact = {
            tkr: {k: v for k, v in data.items() if k not in _DROP and v is not None}
            for tkr, data in market_matrix.items()
        }

        # Add per-ticker asset-class labels as a brief prefix
        asset_labels = "\n".join(
            f"  {tkr}: {data.get('asset_class', 'stocks')}"
            for tkr, data in market_matrix.items()
        )

        user_prompt = (
            f"Asset-class labels:\n{asset_labels}\n\n"
            f"Market Matrix ({n} assets):\n"
            f"{json.dumps(compact, indent=2)}\n\n"
            "Apply your confluence rules and return decisions for ALL assets listed."
        )

        try:
            # parse() enforces the Pydantic schema at the token level — guaranteed JSON
            response = self._client.beta.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                response_format=PortfolioDecisions,
                temperature=0.10,       # Low temp → consistent, deterministic ranking
                max_tokens=1024,        # Sufficient for 29 × ~25-token decision objects
            )

            parsed: PortfolioDecisions = response.choices[0].message.parsed
            decisions = [d.model_dump() for d in parsed.decisions]
            print(f"[Brain] Received {len(decisions)} decisions from OpenAI.")
            return decisions

        except Exception as exc:
            print(f"[Brain][Error] Batch analysis failed: {exc}")
            return []


# ── Standalone smoke-test ─────────────────────────────────────────────────────
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
