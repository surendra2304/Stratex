from __future__ import annotations

import hashlib
import inspect
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class StrategySpec:
    name: str
    module: str
    version: str
    timeframes: tuple[str, ...]
    symbols: tuple[str, ...]
    status: str
    source_hash: str
    parameters: dict[str, Any] = field(default_factory=dict)


class StrategyRegistry:
    def __init__(self):
        self._items: dict[str, StrategySpec] = {}
        self._lock = threading.RLock()

    @staticmethod
    def source_hash(strategy_obj: Any) -> str:
        src = inspect.getsource(strategy_obj)
        return hashlib.sha256(src.encode()).hexdigest()

    def register(self, spec: StrategySpec) -> None:
        if spec.status not in {"VALIDATED", "OBSERVE_ONLY", "DISABLED"}:
            raise ValueError("invalid strategy status")
        with self._lock:
            self._items[spec.name] = spec

    def get(self, name: str) -> StrategySpec | None:
        with self._lock:
            return self._items.get(name)

    def executable(self, name: str, timeframe: str, symbol: str) -> tuple[bool, str]:
        spec = self.get(name)
        if spec is None:
            return False, "UNREGISTERED"
        if spec.status != "VALIDATED":
            return False, f"STATUS_{spec.status}"
        if timeframe not in spec.timeframes:
            return False, "TIMEFRAME_NOT_VALIDATED"
        if spec.symbols and symbol not in spec.symbols:
            return False, "SYMBOL_NOT_VALIDATED"
        return True, "OK"

    def snapshot(self) -> list[StrategySpec]:
        with self._lock:
            return list(self._items.values())
