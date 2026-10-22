"""In-memory, no-order processor for additional unvalidated paper candidates.

The caller must provide real OHLCV rows that are already confirmed closed.
This module intentionally performs no network, filesystem, account, or exchange
operations. It is a processor core only; durable storage and scheduling are a
separate future integration.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from research_phase9.cost_engine import CostEngine


EVIDENCE_STATUS = "UNVALIDATED_PAPER_SHADOW"
REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}

# Only observe-only research candidates are included. These are not production
# authorizations, and this module is not imported by the testnet engine.
SHADOW_CANDIDATES: dict[str, tuple[str, str]] = {
    "donchian20": ("strategy_donchian_observe_only", "get_signal"),
    "adx_ema": ("strategy_adx_ema", "get_signal"),
    "bb_reversion": ("strategy_bb_reversion", "get_signal"),
    "rsi_burst": ("strategy_rsi_burst", "get_signal"),
    "vwap_trend": ("strategy_vwap_trend", "get_signal"),
    "supertrend": ("strategy_supertrend", "get_signal"),
}

# A bounded starting observation matrix. It is a recommendation for a future
# scheduler; this pure module never fetches these markets itself.
DEFAULT_SHADOW_STREAMS = tuple(
    (candidate, symbol, timeframe)
    for candidate, timeframes in {
        "donchian20": ("1h", "4h"),
        "adx_ema": ("4h",),
        "bb_reversion": ("15m",),
        "rsi_burst": ("15m",),
        "vwap_trend": ("15m",),
        "supertrend": ("15m", "1h"),
    }.items()
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    for timeframe in timeframes
)


@dataclass
class ShadowPaperProcessor:
    """Process one actual closed candle at a time across isolated candidate keys.

    Signals formed at a close are filled at the following candle open. A
    deterministic signal id and per-stream monotonic cursor make repeated
    calls idempotent for the life of this in-memory object. State is purposely
    not durable; callers must not describe it as crash-safe or cloud-backed.
    """

    starting_capital: float = 10_000.0
    risk_fraction: float = 0.0025
    max_position_fraction: float = 0.10
    max_total_exposure_fraction: float = 1.0
    cost_engine: CostEngine = field(default_factory=CostEngine.get_binance_taker_config)
    realized_equity: float = field(init=False)
    positions: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    pending_entries: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    last_timestamps: dict[tuple[str, str, str], pd.Timestamp] = field(default_factory=dict)
    seen_signal_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not np.isfinite(self.starting_capital) or self.starting_capital <= 0:
            raise ValueError("starting_capital must be finite and positive")
        if not 0 < self.risk_fraction <= 0.005:
            raise ValueError("risk_fraction must be in (0, 0.005]")
        if not 0 < self.max_position_fraction <= 1:
            raise ValueError("max_position_fraction must be in (0, 1]")
        if not 0 < self.max_total_exposure_fraction <= 1:
            raise ValueError("max_total_exposure_fraction must be in (0, 1]")
        self.realized_equity = float(self.starting_capital)

    def process_closed_candle(
        self,
        strategy: str,
        symbol: str,
        timeframe: str,
        candles: pd.DataFrame,
    ) -> dict[str, Any]:
        """Consume history ending at one real closed candle; return its decision."""
        if strategy not in SHADOW_CANDIDATES:
            raise ValueError(f"strategy is not an observe-only shadow candidate: {strategy}")
        key = (strategy, symbol, timeframe)
        frame = _validate_candles(candles)
        bar = frame.iloc[-1]
        timestamp = pd.Timestamp(bar["timestamp"])
        prior = self.last_timestamps.get(key)
        signal_id = f"{strategy}:{symbol}:{timeframe}:{timestamp.isoformat()}"
        if signal_id in self.seen_signal_ids or (prior is not None and timestamp <= prior):
            return {
                "signal_id": signal_id,
                "decision": "DUPLICATE_OR_OUT_OF_ORDER",
                "evidence_status": EVIDENCE_STATUS,
            }

        self.seen_signal_ids.add(signal_id)
        self.last_timestamps[key] = timestamp
        decision: dict[str, Any] = {
            "signal_id": signal_id,
            "timestamp": timestamp.isoformat(),
            "strategy": strategy,
            "symbol": symbol,
            "timeframe": timeframe,
            "evidence_status": EVIDENCE_STATUS,
            "decision": "NO_SIGNAL",
            "side": None,
            "entry": None,
            "exit": None,
            "net_pnl": None,
        }

        feature_frame = _add_strategy_features(strategy, frame)
        # A signal generated on the previous close is filled at this bar's open.
        pending = self.pending_entries.pop(key, None)
        if pending is not None:
            opened = self._open_position(key, pending, bar, timestamp)
            if opened is not None:
                self.positions[key] = opened
                decision["decision"] = "PAPER_ENTRY"
                decision["side"] = opened["side"]
                decision["entry"] = opened["entry_price"]
                immediate_exit = _find_exit(opened, bar, feature_frame, strategy)
                if immediate_exit:
                    trade = self._close_position(key, opened, *immediate_exit, timestamp)
                    self.positions.pop(key, None)
                    decision["decision"] = "PAPER_ENTRY_AND_EXIT"
                    decision["exit"] = trade["exit_price"]
                    decision["net_pnl"] = trade["net_pnl"]
                    decision["trade"] = trade
            else:
                decision["decision"] = "ENTRY_REJECTED"
                decision["rejection_reason"] = pending.get("rejection_reason", "INVALID_STOP_OR_RISK")

        # Existing position exits are checked before considering a new signal.
        position = self.positions.get(key)
        if position is not None and not position.get("closed"):
            exit_info = _find_exit(position, bar, feature_frame, strategy)
            if exit_info:
                trade = self._close_position(key, position, *exit_info, timestamp)
                decision["decision"] = "PAPER_EXIT"
                decision["exit"] = trade["exit_price"]
                decision["net_pnl"] = trade["net_pnl"]
                decision["trade"] = trade
                self.positions.pop(key, None)

        # One position per candidate/symbol/timeframe stream. Signals are
        # derived strictly from the supplied history through this closed bar.
        if key not in self.positions and key not in self.pending_entries:
            signal = _get_signal(strategy, feature_frame)
            side = getattr(signal, "side", None)
            stop = _number_or_none(getattr(signal, "stop", getattr(signal, "sl", None)))
            target = _number_or_none(getattr(signal, "tp", None))
            if side in {"BUY", "SELL"}:
                pending = {
                    "signal_id": signal_id,
                    "signal_timestamp": timestamp.isoformat(),
                    "side": side,
                    "stop": stop,
                    "target": target,
                }
                if stop is None:
                    decision["decision"] = "SIGNAL_REJECTED"
                    decision["rejection_reason"] = "MISSING_OR_INVALID_STOP"
                else:
                    self.pending_entries[key] = pending
                    # Keep same-bar entry information when a prior trade closed.
                    if decision["decision"] == "PAPER_EXIT":
                        decision["next_signal"] = pending
                    else:
                        decision["decision"] = "SIGNAL_QUEUED_NEXT_OPEN"
                        decision["side"] = side
                        decision["stop"] = stop
                        decision["target"] = target
        return decision

    def _open_position(
        self,
        key: tuple[str, str, str],
        pending: dict[str, Any],
        bar: pd.Series,
        timestamp: pd.Timestamp,
    ) -> dict[str, Any] | None:
        entry = float(bar["open"])
        side = pending["side"]
        stop = float(pending["stop"])
        target = pending.get("target")
        is_long = side == "BUY"
        if (is_long and stop >= entry) or (not is_long and stop <= entry):
            pending["rejection_reason"] = "STOP_INVALID_AT_NEXT_OPEN"
            return None
        if target is not None and ((is_long and target <= entry) or (not is_long and target >= entry)):
            pending["rejection_reason"] = "TARGET_INVALID_AT_NEXT_OPEN"
            return None
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0 or self.realized_equity <= 0:
            pending["rejection_reason"] = "INVALID_RISK_OR_EQUITY"
            return None
        used_exposure = sum(p["entry_notional"] for p in self.positions.values())
        available_exposure = max(
            0.0,
            self.realized_equity * self.max_total_exposure_fraction - used_exposure,
        )
        risk_budget = self.realized_equity * self.risk_fraction
        quantity = min(
            risk_budget / risk_per_unit,
            self.realized_equity * self.max_position_fraction / entry,
            available_exposure / entry,
        )
        if not np.isfinite(quantity) or quantity <= 0:
            pending["rejection_reason"] = "EXPOSURE_LIMIT"
            return None
        direction = 1.0 if is_long else -1.0
        return {
            "signal_id": pending["signal_id"],
            "strategy": key[0],
            "symbol": key[1],
            "timeframe": key[2],
            "side": "LONG" if is_long else "SHORT",
            "signal_side": side,
            "stop": stop,
            "target": _number_or_none(target),
            "entry_price": entry,
            "entry_fill": entry * (1 + direction * self.cost_engine.entry_slip),
            "entry_notional": quantity * entry,
            "quantity": quantity,
            "entry_fee": self.cost_engine.entry_fee * quantity * entry,
            "entry_timestamp": timestamp.isoformat(),
            "evidence_status": EVIDENCE_STATUS,
        }

    def _close_position(
        self,
        key: tuple[str, str, str],
        position: dict[str, Any],
        raw_exit: float,
        reason: str,
        timestamp: pd.Timestamp,
    ) -> dict[str, Any]:
        direction = 1.0 if position["side"] == "LONG" else -1.0
        quantity = position["quantity"]
        exit_notional = quantity * raw_exit
        gross = quantity * direction * (raw_exit - position["entry_price"])
        exit_fee = self.cost_engine.exit_fee * exit_notional
        slippage = quantity * (
            abs(position["entry_fill"] - position["entry_price"])
            + abs(raw_exit * (1 - direction * self.cost_engine.exit_slip) - raw_exit)
        )
        spread = self.cost_engine.spread * (position["entry_notional"] + exit_notional)
        fees = position["entry_fee"] + exit_fee
        net = gross - fees - slippage - spread
        self.realized_equity += net
        position["closed"] = True
        return {
            "signal_id": position["signal_id"],
            "strategy": key[0],
            "symbol": key[1],
            "timeframe": key[2],
            "side": position["side"],
            "entry_timestamp": position["entry_timestamp"],
            "exit_timestamp": timestamp.isoformat(),
            "entry_price": position["entry_price"],
            "exit_price": raw_exit,
            "quantity": quantity,
            "exit_reason": reason,
            "gross_pnl": gross,
            "fees": fees,
            "slippage": slippage,
            "spread": spread,
            "net_pnl": net,
            "equity_after_close": self.realized_equity,
            "evidence_status": EVIDENCE_STATUS,
        }


def _validate_candles(candles: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(candles, pd.DataFrame) or candles.empty:
        raise ValueError("candles must be a non-empty DataFrame of real closed OHLCV data")
    missing = REQUIRED_COLUMNS.difference(candles.columns)
    if missing:
        raise ValueError(f"candles missing columns: {sorted(missing)}")
    frame = candles.copy().reset_index(drop=True)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    if (
        frame["timestamp"].isna().any()
        or frame["timestamp"].duplicated().any()
        or not frame["timestamp"].is_monotonic_increasing
    ):
        raise ValueError("candle timestamps must be unique and strictly chronological")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not np.isfinite(frame[column].to_numpy(dtype=float)).all():
            raise ValueError(f"{column} values must be finite")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("candle high is inconsistent with OHLC")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("candle low is inconsistent with OHLC")
    if (frame["volume"] < 0).any():
        raise ValueError("volume cannot be negative")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    return frame


def _add_strategy_features(strategy: str, frame: pd.DataFrame) -> pd.DataFrame:
    module_name, _ = SHADOW_CANDIDATES[strategy]
    module = importlib.import_module(module_name)
    if strategy == "donchian20":
        return module.add_candidate_features(frame)
    if strategy in {"adx_ema", "bb_reversion", "rsi_burst", "vwap_trend"}:
        return module.add_features(frame)
    from features import add_features
    return add_features(frame)


def _get_signal(strategy: str, frame: pd.DataFrame) -> Any:
    module_name, function_name = SHADOW_CANDIDATES[strategy]
    module = importlib.import_module(module_name)
    return getattr(module, function_name)(frame)


def _number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _find_exit(
    position: dict[str, Any],
    bar: pd.Series,
    feature_frame: pd.DataFrame,
    strategy: str,
) -> tuple[float, str] | None:
    side = position["side"]
    stop = position["stop"]
    target = position["target"]
    op, hi, lo = float(bar["open"]), float(bar["high"]), float(bar["low"])
    # Ambiguous stop and target on one OHLC candle is conservatively a stop.
    if side == "LONG":
        if op <= stop or lo <= stop:
            return (op if op <= stop else stop), "STOP"
        if target is not None and (op >= target or hi >= target):
            return (op if op >= target else target), "TARGET"
    else:
        if op >= stop or hi >= stop:
            return (op if op >= stop else stop), "STOP"
        if target is not None and (op <= target or lo <= target):
            return (op if op <= target else target), "TARGET"
    if strategy == "donchian20":
        module = importlib.import_module("strategy_donchian_observe_only")
        if module.get_channel_exit(feature_frame, position["signal_side"]):
            return float(bar["close"]), "CHANNEL"
    return None
