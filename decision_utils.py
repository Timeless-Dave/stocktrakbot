"""
Deterministic validation for model-generated trade decisions.
"""
from __future__ import annotations


def _parse_confidence(value: object) -> int:
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return 0


def sanitize_decisions(
    raw_decisions: list[dict] | None,
    market_matrix: dict[str, dict],
    owned_assets: list[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Normalize model output into one safe decision per asset.

    Guarantees:
    - exactly one decision per ticker in market_matrix
    - only BUY / SELL / HOLD actions
    - at most one BUY and one SELL survive
    - SELL is only allowed for owned assets
    """
    warnings: list[str] = []
    owned = {ticker.upper() for ticker in (owned_assets or [])}
    ordered_tickers = list(market_matrix.keys())

    sanitized: dict[str, dict] = {
        ticker: {
            "ticker": ticker,
            "action": "HOLD",
            "confidence": 45,
            "reasoning": "No validated trade signal.",
        }
        for ticker in ordered_tickers
    }

    if not raw_decisions:
        warnings.append("Model returned no decisions; defaulting every asset to HOLD.")
        return [sanitized[ticker] for ticker in ordered_tickers], warnings

    seen: set[str] = set()
    for index, raw in enumerate(raw_decisions):
        if not isinstance(raw, dict):
            warnings.append(f"Ignored non-dict decision at index {index}.")
            continue

        ticker = str(raw.get("ticker", "")).strip().upper()
        if ticker not in sanitized:
            warnings.append(f"Ignored decision for unknown ticker '{ticker or '?'}'.")
            continue

        if ticker in seen:
            warnings.append(f"Ignored duplicate decision for {ticker}.")
            continue
        seen.add(ticker)

        action = str(raw.get("action", "HOLD")).strip().upper()
        confidence = _parse_confidence(raw.get("confidence", 0))
        reasoning = str(raw.get("reasoning", "")).strip() or "No reasoning provided."

        if action not in {"BUY", "SELL", "HOLD"}:
            warnings.append(f"{ticker}: invalid action '{action}', converted to HOLD.")
            action = "HOLD"

        if action == "SELL" and ticker not in owned:
            warnings.append(f"{ticker}: SELL rejected because the asset is not currently owned.")
            action = "HOLD"

        if action == "HOLD":
            confidence = 45

        sanitized[ticker] = {
            "ticker": ticker,
            "action": action,
            "confidence": confidence,
            "reasoning": reasoning,
        }

    missing = [ticker for ticker in ordered_tickers if ticker not in seen]
    if missing:
        warnings.append(
            f"Model omitted {len(missing)} asset(s); missing tickers defaulted to HOLD."
        )

    # Allow up to 2 BUYs and 2 SELLs per cycle — matches MAX_BUYS/SELLS_PER_CYCLE in config.
    # If the model somehow returns 3+, keep the top 2 by confidence.
    _MAX_PER_ACTION = 2
    for action_name in ("BUY", "SELL"):
        candidates = [
            sanitized[ticker]
            for ticker in ordered_tickers
            if sanitized[ticker]["action"] == action_name
        ]
        if len(candidates) <= _MAX_PER_ACTION:
            continue

        # Keep the top-N by confidence; demote the rest to HOLD
        top_n = sorted(
            candidates,
            key=lambda item: (item["confidence"], -ordered_tickers.index(item["ticker"])),
            reverse=True,
        )[:_MAX_PER_ACTION]
        keep_tickers = {item["ticker"] for item in top_n}

        for item in candidates:
            if item["ticker"] in keep_tickers:
                continue
            sanitized[item["ticker"]] = {
                "ticker": item["ticker"],
                "action": "HOLD",
                "confidence": 45,
                "reasoning": (
                    f"Converted to HOLD: more than {_MAX_PER_ACTION} {action_name} signals returned; "
                    f"kept top-{_MAX_PER_ACTION} by confidence."
                ),
            }
        kept_str = ", ".join(t["ticker"] for t in top_n)
        warnings.append(
            f"Model returned {len(candidates)} {action_name} signals; kept top-{_MAX_PER_ACTION}: {kept_str}."
        )

    return [sanitized[ticker] for ticker in ordered_tickers], warnings
