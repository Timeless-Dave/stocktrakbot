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
    ticker: str = Field(description="The asset ticker symbol.")
    action: str = Field(description="Exactly one of: BUY, SELL, or HOLD.")
    confidence: int = Field(
        description=(
            "Integer 1-100. Use 80-95 for a BUY/SELL you genuinely want executed. "
            "Use 50-70 for HOLD. Never inflate confidence to justify a trade."
        )
    )
    reasoning: str = Field(description="One concise sentence citing the key numerical signals driving this decision.")


class PortfolioDecisions(BaseModel):
    decisions: list[TradeDecision]


# ── System prompt — conservative, commission-aware execution ─────────────────

_SYSTEM_PROMPT = """
You are a disciplined, low-turnover quantitative portfolio manager.
Each trade costs $10 commission in AND $10 out — a round-trip costs $20.
On a typical $8,000 position that is a 0.25% drag before any market move.
Excessive trading is the #1 destroyer of returns. Your mandate is QUALITY over QUANTITY.

RULES FOR EXECUTION (STRICT ADHERENCE REQUIRED):
0. PORTFOLIO AWARENESS: You are provided a list of OWNED ASSETS. You may ONLY issue a "SELL"
   action for tickers that appear in that list. Never issue SELL for un-owned assets.

1. BUY MANDATE — AT MOST 1 BUY per response:
   - Only issue a BUY if ONE asset has a clearly strong setup (avoid churn):
     * RSI divergence or clear oversold bounce (RSI < 35) with rising MACD histogram, OR
     * Strong upside breakout: price above SMA-20 AND SMA-50, high volume surge (> 120%), AND
       positive MACD cross, OR
     * Analyst consensus "buy" with a meaningful target upside (> 10%) and positive momentum.
   - If no asset clears this bar, issue NO BUYs. "HOLD" is always acceptable.
   - Confidence for a BUY must be 80–95; otherwise output "HOLD".

2. SELL MANDATE — AT MOST 1 SELL per response:
   - Only issue a SELL for an owned asset that shows a clear deterioration signal:
     * RSI overbought (> 70) with falling MACD histogram and price below SMA-20, OR
     * Stop-loss situation: asset is down significantly and has no bullish reversal, OR
     * A clearly better opportunity exists and selling frees capital for it.
   - Do NOT sell just to churn the portfolio. Commissions make frequent sells losing trades.
   - Confidence for a SELL must be 80–95; otherwise output "HOLD".

3. THE VIX CONTEXT: If VIX > 25, be more selective, but still allow exceptional setups
   (e.g., RSI < 32 with improving MACD histogram or breakout with strong volume surge).

4. THE REST: Output "HOLD" with confidence 50-70 for all other assets.

5. OUTPUT COVERAGE: Return exactly one decision per asset in the matrix. Never omit a ticker.

Remember: the best trade is often no trade. Missing a move costs nothing; a bad trade costs $20
plus the drawdown. Default to HOLD unless the evidence is overwhelming.
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
        self._model  = (os.environ.get("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o")

    # ─────────────────────────────────────────────────────────────────────────
    def analyze_portfolio(
        self,
        market_matrix: dict,
        macro_context: dict | None = None,
        owned_assets: list[str] | None = None,
    ) -> list[dict]:
        """
        Send the entire market data snapshot + macro context in one API call.

        Parameters
        ----------
        market_matrix : { ticker: { asset_class, current_price, rsi_14, volume_surge_pct, ... } }
        macro_context : { VIX, SPY_5D_Trend_Pct } — optional; defaults if omitted.

        Returns
        -------
        List of dicts: [{ticker, action, confidence, reasoning}, ...]
        Returns [] on failure (caller treats all as HOLD).
        """
        if macro_context is None:
            macro_context = {"VIX": 20.0, "SPY_5D_Trend_Pct": 0.0}
        if owned_assets is None:
            owned_assets = []

        n = len(market_matrix)
        print(f"[Brain] Batch-analysing {n} assets via OpenAI {self._model} (with macro context)...")

        # Compact the matrix — drop internal bookkeeping keys to save tokens
        _DROP = {"last_updated", "asset_class"}
        compact = {
            tkr: {k: v for k, v in data.items() if k not in _DROP and v is not None}
            for tkr, data in market_matrix.items()
        }

        # Per-ticker asset-class labels
        asset_labels = "\n".join(
            f"  {tkr}: {data.get('asset_class', 'stocks')}"
            for tkr, data in market_matrix.items()
        )

        user_prompt = (
            f"MACRO CONTEXT:\n"
            f"  VIX (fear/volatility): {macro_context['VIX']}\n"
            f"  SPY 5-day trend %: {macro_context['SPY_5D_Trend_Pct']}%\n\n"
            f"OWNED ASSETS (eligible for SELL):\n{owned_assets}\n\n"
            f"Asset-class labels:\n{asset_labels}\n\n"
            f"Market Matrix ({n} assets):\n"
            f"{json.dumps(compact, indent=2)}\n\n"
            "Apply the rules (volume surge, VIX, relative strength) and return decisions for ALL assets listed."
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
                temperature=0.05,       # Very low temp → consistent, deterministic ranking
                max_tokens=1500,        # Sufficient for 40 × ~30-token decision objects
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

    # Mock matrix (with volume_surge_pct and optional macro)
    mock_matrix = {
        "PLTR": {
            "asset_class": "stocks",
            "current_price": 151.74, "price_change_5d": -4.1,
            "rsi_14": 28.5, "macd": -0.4, "macd_hist": 0.6,
            "sma_20": 158.0, "sma_50": 162.0,
            "bb_lower": 148.0, "bb_upper": 170.0, "bb_pct": 0.11,
            "atr_14": 4.9, "volume": 42_000_000, "volume_vs_avg": 1.8,
            "volume_surge_pct": 180.0,
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
            "volume_surge_pct": 95.0, "trailing_pe": 23, "recommendation": "hold",
        },
        "BTC-USD": {
            "asset_class": "crypto",
            "current_price": 71000, "price_change_5d": -2.5,
            "rsi_14": 42.0, "macd_hist": -180,
            "bb_lower": 65000, "bb_upper": 82000, "bb_pct": 0.40,
            "atr_14": 2800, "volume_vs_avg": 0.9, "volume_surge_pct": 110.0,
        },
    }
    mock_macro = {"VIX": 18.5, "SPY_5D_Trend_Pct": 1.2}

    decisions = b.analyze_portfolio(mock_matrix, mock_macro)
    print("\n--- Batch Decisions ---")
    for d in decisions:
        print(f"  {d['ticker']:<10} {d['action']:<5} conf={d['confidence']}%  {d['reasoning']}")
