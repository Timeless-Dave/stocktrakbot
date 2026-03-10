"""
brain.py – The Brain
Sends technical + fundamental + news-sentiment data to Gemini.
CONFLUENCE rule: confidence > 75 only when multiple indicator types agree.
"""
import os
import re
import time
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types


# ── Asset-class system prompts ────────────────────────────────────────────────
_PROMPT_STOCKS = """
You are a ruthless, highly logical quantitative trading algorithm.
Analyse the technical indicators, valuation metrics, and news sentiment provided.

RULES FOR CONFLUENCE (MANDATORY):
1. Only output confidence > 75 when MULTIPLE signal types AGREE:
   - e.g., price bouncing off Lower Bollinger Band AND RSI < 35 AND MACD histogram turning positive = strong BUY
   - e.g., RSI > 72 AND price near Upper Bollinger Band AND MACD histogram falling = strong SELL
2. If indicators conflict (e.g., RSI oversold but MACD still negative), output HOLD with confidence < 50.
3. Incorporate news sentiment: catastrophic negative news overrides bullish technicals.
4. Low P/E + earnings growth + low debt reinforces BUY; high P/E + revenue decline = SELL bias.
5. ATR measures volatility energy — high ATR on a breakout confirms momentum; low ATR = choppy/uncertain.

Output ONLY a JSON object — no markdown, no extra keys.
""".strip()

_PROMPT_ETFS = """
You are a quantitative ETF strategist.
CONFLUENCE RULES:
1. confidence > 75 requires agreement from at least: trend (price vs SMA-50), momentum (MACD), and volatility (Bollinger %B).
2. Bollinger %B < 0.15 + RSI < 38 = strong BUY signal; %B > 0.85 + RSI > 68 = SELL.
3. Volume ratio > 1.5 on a directional day confirms breakout/breakdown.
4. Mixed signals = HOLD. Output ONLY JSON.
""".strip()

_PROMPT_CRYPTO = """
You are a quantitative cryptocurrency trader. Crypto is highly volatile.
CONFLUENCE RULES:
1. confidence > 75 requires RSI < 35 AND Bollinger %B < 0.2 AND MACD histogram turning up → BUY.
2. RSI > 72 AND %B > 0.85 AND volume_ratio > 2 → SELL.
3. 5-day price change > 15%? Risk/reward skews to SELL.
4. Anything less than strong multi-signal agreement = HOLD.
Output ONLY JSON.
""".strip()

_PROMPT_BONDS = """
You are a fixed-income portfolio manager trading bond ETFs.
CONFLUENCE RULES:
1. Bond prices RISE when rates fall. Buy dips in TLT/BND/AGG when RSI < 38 and SMA-50 trend is upward.
2. Sell when RSI > 68 and MACD histogram falls consistently.
3. High ATR in bonds = unusual macro stress — wait for clarity (HOLD).
4. Prefer HOLD unless signals clearly align. Output ONLY JSON.
""".strip()

_PROMPT_MUTUAL = """
You are a mutual fund analyst. Mutual funds execute at end-of-day NAV.
CONFLUENCE RULES (conservative — over-trading is costly):
1. BUY only if RSI < 38 AND MACD turning positive AND 5d-change < -3%.
2. SELL only if RSI > 68 AND MACD histogram negative AND 5d-change > +5%.
3. Default is HOLD — only trade on crystal-clear signals.
Output ONLY JSON.
""".strip()

_SYSTEM_PROMPTS = {
    "stocks": _PROMPT_STOCKS,
    "etfs":   _PROMPT_ETFS,
    "crypto": _PROMPT_CRYPTO,
    "bonds":  _PROMPT_BONDS,
    "mutual": _PROMPT_MUTUAL,
}

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action":     {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "integer",
                       "description": "Conviction 1-100; >75 requires multi-signal confluence."},
        "reasoning":  {"type": "string",
                       "description": "One sentence citing the specific indicators driving the decision."},
    },
    "required": ["action", "confidence", "reasoning"],
}


class TradingBrain:

    def __init__(self) -> None:
        load_dotenv()
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "[Brain] GEMINI_API_KEY is not set. Fill in your .env file."
            )
        self._client = genai.Client(api_key=api_key)
        self._model  = "gemini-2.0-flash"

    # ─────────────────────────────────────────────────────────────────────────
    def analyze_asset(
        self,
        ticker: str,
        market_data: dict,
        asset_class: str = "stocks",
    ) -> dict:
        """Return {action, confidence, reasoning}. Falls back to HOLD on error."""
        print(f"[Brain] Analysing {ticker} ({asset_class})...")

        # ── Build structured prompt ───────────────────────────────────────────
        price  = market_data.get("current_price", "N/A")
        ch5d   = market_data.get("price_change_5d", "N/A")
        prompt = f"""Asset: {ticker}  |  Class: {asset_class}
Current Price : ${price}  |  5-Day Change: {ch5d}%

--- TECHNICALS ---
RSI (14)          : {market_data.get('rsi_14', 'N/A')}
MACD              : {market_data.get('macd', 'N/A')}
MACD Histogram    : {market_data.get('macd_hist', 'N/A')}
SMA-20 / SMA-50   : {market_data.get('sma_20', 'N/A')} / {market_data.get('sma_50', 'N/A')}
Bollinger Bands   : Lower=${market_data.get('bb_lower', 'N/A')}  Upper=${market_data.get('bb_upper', 'N/A')}  %B={market_data.get('bb_pct', 'N/A')}
ATR (14)          : {market_data.get('atr_14', 'N/A')}
Volume            : {market_data.get('volume', 'N/A')}
Volume / Avg-20   : {market_data.get('volume_vs_avg', 'N/A')}x

--- FUNDAMENTALS & VALUATION ---
Trailing P/E      : {market_data.get('trailing_pe', 'N/A')}
Forward P/E       : {market_data.get('forward_pe', 'N/A')}
EPS (TTM)         : {market_data.get('eps_ttm', 'N/A')}
Revenue Growth    : {market_data.get('revenue_growth', 'N/A')}
Earnings Growth   : {market_data.get('earnings_growth', 'N/A')}
Debt/Equity       : {market_data.get('debt_to_equity', 'N/A')}
Profit Margin     : {market_data.get('profit_margin', 'N/A')}
Beta              : {market_data.get('beta', 'N/A')}
Analyst Target    : {market_data.get('analyst_target', 'N/A')}
Recommendation    : {market_data.get('recommendation', 'N/A')}

--- NEWS SENTIMENT ---
{chr(10).join(f'  - {h}' for h in (market_data.get('recent_news') or ['No recent news']))}

Based on ALL data above, applying confluence rules, what is the optimal trade action?"""

        system_prompt = _SYSTEM_PROMPTS.get(asset_class, _PROMPT_STOCKS)

        # ── Gemini call with 429 retry ────────────────────────────────────────
        for attempt in range(3):
            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        response_schema=_RESPONSE_SCHEMA,
                        temperature=0.15,
                        max_output_tokens=300,
                    ),
                )
                return json.loads(response.text)

            except Exception as exc:
                exc_str = str(exc)
                if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str:
                    match = re.search(r"retryDelay.*?(\d+)s", exc_str)
                    wait  = int(match.group(1)) + 2 if match else 60
                    print(f"[Brain][RateLimit] 429 on {ticker} — "
                          f"waiting {wait}s (attempt {attempt + 1}/3)...")
                    time.sleep(wait)
                else:
                    print(f"[Brain][Error] Gemini failed for {ticker}: {exc}")
                    break   # Non-rate-limit error — don't retry

        return {
            "action":     "HOLD",
            "confidence": 0,
            "reasoning":  "Gemini API error or rate limit — defaulting to HOLD.",
        }


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    load_dotenv()
    b = TradingBrain()
    mock = {
        "current_price": 151.74, "price_change_5d": -3.2,
        "rsi_14": 29.5, "macd": -0.8, "macd_hist": 0.3,
        "sma_20": 158.0, "sma_50": 162.0,
        "bb_lower": 148.0, "bb_upper": 170.0, "bb_pct": 0.12,
        "atr_14": 4.5, "volume": 38_000_000, "volume_vs_avg": 1.7,
        "trailing_pe": 145, "forward_pe": 90,
        "eps_ttm": 0.22, "revenue_growth": 0.25,
        "earnings_growth": 0.40, "debt_to_equity": 18,
        "recommendation": "buy", "analyst_target": 195.0,
        "recent_news": [
            "Palantir wins major DoD AI contract worth $480M",
            "PLTR beats Q4 earnings, raises guidance",
        ],
    }
    d = b.analyze_asset("PLTR", mock, "stocks")
    print(json.dumps(d, indent=2))
