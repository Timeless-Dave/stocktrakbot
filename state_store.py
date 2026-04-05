"""
Simple JSON-backed persistence for bot state and trade history.
"""
from __future__ import annotations

import json
import os
from datetime import datetime


class BotStateStore:
    def __init__(self, state_file: str, ledger_file: str) -> None:
        self.state_file = state_file
        self.ledger_file = ledger_file

    def load(self) -> tuple[dict[str, float], dict[str, datetime], dict[str, float]]:
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return {}, {}, {}
        except Exception as exc:
            print(f"[State][Warning] Could not load state file '{self.state_file}': {exc}")
            return {}, {}, {}

        positions = {
            str(ticker).upper(): float(quantity)
            for ticker, quantity in (payload.get("positions") or {}).items()
            if quantity is not None
        }
        entry_times: dict[str, datetime] = {}
        for ticker, value in (payload.get("entry_times") or {}).items():
            try:
                entry_times[str(ticker).upper()] = datetime.fromisoformat(str(value))
            except ValueError:
                continue

        entry_prices = {
            str(ticker).upper(): float(price)
            for ticker, price in (payload.get("entry_prices") or {}).items()
            if price is not None
        }
        return positions, entry_times, entry_prices

    def save(
        self,
        positions: dict[str, float],
        entry_times: dict[str, datetime],
        entry_prices: dict[str, float],
    ) -> None:
        payload = {
            "positions": {
                ticker: quantity
                for ticker, quantity in sorted(positions.items())
                if float(quantity) > 0
            },
            "entry_times": {
                ticker: dt.isoformat(timespec="seconds")
                for ticker, dt in sorted(entry_times.items())
                if ticker in positions and float(positions.get(ticker, 0)) > 0
            },
            "entry_prices": {
                ticker: price
                for ticker, price in sorted(entry_prices.items())
                if ticker in positions and float(positions.get(ticker, 0)) > 0
            },
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }

        try:
            self._ensure_parent_dir(self.state_file)
            temp_file = f"{self.state_file}.tmp"
            with open(temp_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(temp_file, self.state_file)
        except Exception as exc:
            print(f"[State][Warning] Could not save state file '{self.state_file}': {exc}")

    def append_trade(self, trade: dict[str, object]) -> None:
        try:
            self._ensure_parent_dir(self.ledger_file)
            with open(self.ledger_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(trade, sort_keys=True) + "\n")
        except Exception as exc:
            print(f"[State][Warning] Could not append trade ledger '{self.ledger_file}': {exc}")

    @staticmethod
    def _ensure_parent_dir(path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
