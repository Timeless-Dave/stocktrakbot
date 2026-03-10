"""
brain.py – The Brain
Sends technical market data to the Gemini API and parses a strictly-typed
JSON response: { "action": "BUY"|"SELL"|"HOLD", "confidence": 1-100, "reasoning": "..." }
Uses response_mime_type + response_schema to guarantee machine-parseable output.
Uses the current google-genai SDK (google.genai).
"""
import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types


class TradingBrain:
    """Analyses market data snapshots with Gemini and returns structured trade signals."""

    _SYSTEM_PROMPT = (
        "You are a ruthless, highly logical quantitative trading algorithm. "
        "Your sole purpose is to analyse the provided technical indicators "
        "(RSI-14, MACD, SMA-20) and current price, then output a trade decision. "
        "Standard interpretation guidelines:\n"
        "  • RSI > 70  → overbought  (leaning SELL)\n"
        "  • RSI < 30  → oversold    (leaning BUY)\n"
        "  • MACD > 0  → bullish momentum\n"
        "  • MACD < 0  → bearish momentum\n"
        "  • Price > SMA_20 → uptrend\n"
        "You must output your decision strictly as a JSON object with no markdown, "
        "no prose, and no extra keys."
    )

    _RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["BUY", "SELL", "HOLD"],
                "description": "Trade action based on the technical analysis.",
            },
            "confidence": {
                "type": "integer",
                "description": "Conviction score from 1 (very uncertain) to 100 (extremely certain).",
            },
            "reasoning": {
                "type": "string",
                "description": "One concise sentence explaining the decision.",
            },
        },
        "required": ["action", "confidence", "reasoning"],
    }

    # ─────────────────────────────────────────────────────────────────────────
    def __init__(self) -> None:
        # Load .env if it hasn't been loaded yet
        load_dotenv()
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "[Brain] GEMINI_API_KEY environment variable is not set.\n"
                "  Windows : set GEMINI_API_KEY=your_key\n"
                "  Unix    : export GEMINI_API_KEY=your_key\n"
                "  Or fill in your .env file."
            )
        self._client = genai.Client(api_key=api_key)
        # gemini-2.0-flash → fast & cheap; ideal for a high-frequency loop
        self._model  = "gemini-2.0-flash"

    # ─────────────────────────────────────────────────────────────────────────
    def analyze_asset(self, ticker: str, market_data: dict) -> dict:
        """
        Send a ticker's market snapshot to Gemini and return a decision dict.

        Returns
        -------
        dict with keys: action (str), confidence (int), reasoning (str)
        Falls back to {"action": "HOLD", "confidence": 0, ...} on any error.
        """
        print(f"[Brain] Analysing {ticker}…")

        user_prompt = (
            f"Asset          : {ticker}\n"
            f"Current Price  : ${market_data.get('current_price', 'N/A')}\n"
            f"RSI (14)       : {market_data.get('rsi_14', 'N/A')}\n"
            f"MACD           : {market_data.get('macd', 'N/A')}\n"
            f"MACD Signal    : {market_data.get('macd_signal', 'N/A')}\n"
            f"MACD Histogram : {market_data.get('macd_hist', 'N/A')}\n"
            f"SMA (20)       : {market_data.get('sma_20', 'N/A')}\n"
            f"Volume         : {market_data.get('volume', 'N/A')}\n\n"
            "Based on standard momentum and mean-reversion analysis, "
            "what is the optimal move right now?"
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self._SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=self._RESPONSE_SCHEMA,
                    temperature=0.2,        # Low temp → more deterministic decisions
                    max_output_tokens=256,  # Keep responses tightly scoped
                ),
            )
            decision: dict = json.loads(response.text)
            return decision

        except Exception as exc:
            print(f"[Brain][Error] Gemini call failed for {ticker}: {exc}")
            # Failsafe: HOLD with zero confidence so the executor skips this tick
            return {
                "action": "HOLD",
                "confidence": 0,
                "reasoning": f"API error – defaulting to HOLD. ({exc})",
            }


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Automatically loads GEMINI_API_KEY from .env in this directory
    load_dotenv()

    try:
        brain = TradingBrain()
    except EnvironmentError as e:
        print(e)
        exit(1)

    # Mock data formatted exactly like data_fetcher.py output
    mock_scenarios = [
        ("PLTR", {"current_price": 330.83, "rsi_14": 57.74, "macd": 3.77,
                  "macd_signal": 4.63, "macd_hist": -0.85, "sma_20": 329.09, "volume": 34_070_000}),
        ("MCD",  {"current_price": 292.10, "rsi_14": 28.5,  "macd": -0.8,
                  "macd_signal": -0.3, "macd_hist": -0.5, "sma_20": 300.00, "volume": 3_200_000}),
    ]

    for sym, data in mock_scenarios:
        decision = brain.analyze_asset(sym, data)
        print(f"\n─── {sym} Decision ──────────────────────────────")
        print(json.dumps(decision, indent=4))
