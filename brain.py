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
            "Integer 1-100. "
            "75-95: actionable BUY or SELL — execute. "
            "65-74: marginal signal — HOLD unless best in universe. "
            "50-64: no signal — HOLD."
        )
    )
    reasoning: str = Field(description="One concise sentence citing the key numerical signals driving this decision.")


class PortfolioDecisions(BaseModel):
    decisions: list[TradeDecision]


# ── System prompt — competition-mode aggressive momentum trading ──────────────

_SYSTEM_PROMPT = """
You are an aggressive competition trader in a simulated stock market contest (HBCU Stock Market Challenge).
Your SOLE OBJECTIVE is to maximize total portfolio return and finish #1 on the leaderboard.
Commission is $10/trade in + $10/trade out. On a $12,000 position that is ~0.17% round-trip —
negligible if the trade captures a 1-3% move. Do NOT let commission fear stop you from acting.

COMPETITION REALITY:
- Other contestants are taking concentrated, high-conviction bets. Sitting in cash = falling behind.
- Missing a 5% move on NVDA or BTC because you were "cautious" is a real cost in the standings.
- The winner takes risk. Your job is to take SMART, CALCULATED risk every single cycle.
- You have 2 BUY slots and 2 SELL slots per response. Use them when clear setups exist.

OWNED ASSETS RULE: You may ONLY issue SELL for tickers listed under OWNED ASSETS. Never sell un-owned assets.

═══════════════════════════════════════════════════════
BUY SIGNALS — issue BUY (confidence 75-95) if ANY apply:
═══════════════════════════════════════════════════════

A) MOMENTUM BREAKOUT (strongest signal):
   • price > sma_20 AND price > sma_50
   • RSI between 50-68 (trending, not overbought)
   • macd_hist > 0 and rising (positive momentum)
   • volume_surge_pct > 115% (buying interest confirmed)
   → Confidence 82-92. This is the competition-winning setup.

B) OVERSOLD BOUNCE (buy the dip):
   • RSI < 38 AND macd_hist turning from negative toward zero (improving)
   • analyst recommendation = "buy" or "strong_buy" with target upside > 10%
   • bb_pct < 0.25 (near lower Bollinger Band — value zone)
   → Confidence 78-88. Wait for the macd_hist to actually start improving.

C) TREND CONTINUATION (ride the winner):
   • price > sma_20, RSI 52-65 (healthy uptrend, not exhausted)
   • macd_hist positive for multiple periods
   • volume_surge_pct > 105% (consistent accumulation)
   → Confidence 75-85. Good for momentum stocks already moving.

D) CRYPTO MOMENTUM (24/7 opportunity):
   • RSI between 42-62, macd_hist positive or turning positive
   • bb_pct between 0.35-0.70 (middle range, room to move)
   • volume_surge_pct > 110%
   → Confidence 75-85. Crypto can gap 5-10% overnight.

E) NEWS CATALYST:
   • recent_news contains a clear positive catalyst (contract win, beat, upgrade)
   • analyst recommendation = "buy" or "strong_buy"
   • price is below analyst_target by > 12%
   → Confidence 78-90. News + analyst agreement = strong edge.

At most 2 BUYs per response. Rank your top picks by composite signal strength.
If only 1 clear setup exists, output 1 BUY. If none clear the bar, output 0 BUYs.

════════════════════════════════════════════════════════
SELL SIGNALS — issue SELL (confidence 75-95) if ANY apply:
════════════════════════════════════════════════════════

A) PROFIT ROTATION (take gains, redeploy):
   • RSI > 67 AND macd_hist declining (momentum fading)
   • bb_pct > 0.80 (near upper Bollinger Band — extended)
   • Price is up significantly and a clearly better BUY opportunity exists
   → Confidence 78-90. Lock in gains and rotate to the better setup.

B) MOMENTUM BREAKDOWN:
   • price drops below sma_20 AND macd turns negative
   • RSI < 45 and falling (distribution)
   → Confidence 78-88. Trend is broken; exit before it gets worse.

C) STOP-LOSS (capital protection):
   • Position down > 2.5% from entry with no reversal (no improving macd_hist)
   • RSI still falling, no volume reversal
   → Confidence 80-92. Cut the loss now; protect capital for better trade.

At most 2 SELLs per response. Never sell unless position clearly deteriorating or a better setup exists.

════════════════════════════════════════════════════════
VIX & MACRO GUIDANCE:
════════════════════════════════════════════════════════
VIX < 15: Fully aggressive. All valid setups get executed.
VIX 15-25: Normal. Use the signals above as written.
VIX 25-35: Prefer oversold bounces and crypto. Breakouts still valid with extra confirmation.
VIX > 35: Only highest-conviction setups (confidence ≥ 85). Buy the dip aggressively.
High VIX + strong negative SPY trend: protect capital, prefer HOLD. Wait for reversal signal.

════════════════════════════════════════════════════════
CONFIDENCE CALIBRATION:
════════════════════════════════════════════════════════
85-95: Multiple confirming signals. BUY or SELL immediately.
75-84: 2+ confirming signals. BUY or SELL.
65-74: Only 1 signal, marginal. HOLD (don't waste commission).
50-64: No clear signal. HOLD.

COMPETITION END NOTE: Days remaining is provided. If ≤ 7 days left, lower your bar — act on
any 72+ confidence signal. If ≤ 3 days left, any 68+ confidence setup with positive momentum is a BUY.
Time remaining changes the risk/reward math.

OUTPUT REQUIREMENT: Exactly one decision per ticker in the matrix. Never omit a ticker.
Target: find the 1-2 BEST buys and 0-2 SELLs per cycle. Find them and ACT.
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
        current_rank: int | None = None,
        days_remaining: int | None = None,
    ) -> list[dict]:
        """
        Send the entire market data snapshot + macro + competition context in one API call.

        Parameters
        ----------
        market_matrix    : { ticker: { asset_class, current_price, rsi_14, ... } }
        macro_context    : { VIX, SPY_5D_Trend_Pct }
        owned_assets     : tickers currently held (eligible for SELL)
        current_rank     : leaderboard rank (1 = first place) — tunes aggression
        days_remaining   : calendar days until competition ends

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
        print(f"[Brain] Batch-analysing {n} assets via OpenAI {self._model} "
              f"(rank={current_rank}, days_left={days_remaining})...")

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

        # Competition context block for the model
        rank_str = f"#{current_rank}" if current_rank else "unknown"
        days_str = str(days_remaining) if days_remaining is not None else "unknown"
        # Derive urgency guidance based on rank + time remaining
        if days_remaining is not None and days_remaining <= 3:
            urgency = "FINAL DAYS — lower the bar to 68+ confidence. Take every clean setup. No regrets."
        elif days_remaining is not None and days_remaining <= 7:
            urgency = "COMPETITION CRUNCH — act on 72+ confidence. Find at least 1-2 trades this cycle."
        elif current_rank is not None and current_rank > 5:
            urgency = "BEHIND THE LEADERS — need aggressive momentum trades to climb. At least 1-2 BUYs."
        elif current_rank is not None and current_rank <= 3:
            urgency = "IN THE TOP 3 — protect the lead with smart trades; still take strong setups."
        else:
            urgency = "Normal competition mode — find the best 1-2 setups and execute."

        competition_block = (
            f"COMPETITION STATUS:\n"
            f"  Current rank: {rank_str}\n"
            f"  Days remaining: {days_str}\n"
            f"  Guidance: {urgency}\n\n"
        )

        user_prompt = (
            f"MACRO CONTEXT:\n"
            f"  VIX (fear/volatility): {macro_context['VIX']}\n"
            f"  SPY 5-day trend %: {macro_context['SPY_5D_Trend_Pct']}%\n\n"
            f"{competition_block}"
            f"OWNED ASSETS (eligible for SELL):\n{owned_assets}\n\n"
            f"Asset-class labels:\n{asset_labels}\n\n"
            f"Market Matrix ({n} assets):\n"
            f"{json.dumps(compact, indent=2)}\n\n"
            "Scan every asset above. Identify the 1-2 STRONGEST BUY setups and any SELL candidates. "
            "Return one decision per asset. Never omit a ticker."
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
                temperature=0.03,       # Very low temp → consistent, deterministic decisions
                max_tokens=2000,        # Sufficient for 30 × ~40-token decision objects
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
