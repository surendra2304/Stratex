"""Isolated, historical-candle paper evaluation for observe-only strategies.

This module has no exchange client, forward-runner, account, or order imports.
It reads one provenance-verified OHLCV CSV and writes a self-contained report
under an explicitly supplied output directory. It never edits forward ledgers.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from research_phase9.cost_engine import CostEngine


STRATEGIES: dict[str, tuple[str, str]] = {
    "donchian20": ("strategy_donchian_observe_only", "get_signal"),
    "swing": ("strategy_swing", "get_signal"),
    "adx_ema": ("strategy_adx_ema", "get_signal"),
    "bb_reversion": ("strategy_bb_reversion", "get_signal"),
    "rsi_burst": ("strategy_rsi_burst", "get_signal"),
    "vwap_trend": ("strategy_vwap_trend", "get_signal"),
    "supertrend": ("strategy_supertrend", "get_signal"),
}
REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}
DEFAULT_STARTING_CAPITAL = 10_000.0
DEFAULT_RISK_FRACTION = 0.01


def _load_verified_candles(csv_path: Path, manifest_path: Path) -> tuple[pd.DataFrame, dict]:
    """Load real exchange candles only when their sidecar verifies provenance."""
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = csv_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if metadata.get("sha256") != digest:
        raise ValueError("candle CSV SHA-256 does not match provenance manifest")
    if not metadata.get("source") or metadata.get("source") in {"fixture", "synthetic", "unknown"}:
        raise ValueError("provenance manifest must identify a real market-data source")
    if not metadata.get("symbol") or not metadata.get("timeframe"):
        raise ValueError("provenance manifest must include symbol and timeframe")
    if not metadata.get("fetched_at_utc"):
        raise ValueError("provenance manifest must include fetched_at_utc")
    candles = pd.read_csv(csv_path)
    if candles.empty:
        raise ValueError("candle CSV must contain at least one market candle")
    missing = REQUIRED_COLUMNS.difference(candles.columns)
    if missing:
        raise ValueError(f"candle CSV missing columns: {sorted(missing)}")
    if "timestamp" not in candles:
        raise ValueError("candle CSV must include timestamp")
    candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True, errors="raise")
    for name in REQUIRED_COLUMNS:
        candles[name] = pd.to_numeric(candles[name], errors="raise")
        if not np.isfinite(candles[name].to_numpy(dtype=float)).all():
            raise ValueError(f"candle column {name} contains non-finite values")
    candles = candles.sort_values("timestamp", kind="stable").reset_index(drop=True)
    if candles["timestamp"].duplicated().any():
        raise ValueError("candle timestamps must be unique")
    if (candles["high"] < candles[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("candle high is inconsistent with OHLC")
    if (candles["low"] > candles[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("candle low is inconsistent with OHLC")
    metadata["sha256"] = digest
    return candles, metadata


def _signal_fields(signal) -> tuple[str | None, float | None, float | None]:
    side = getattr(signal, "side", None)
    stop = getattr(signal, "stop", getattr(signal, "sl", None))
    target = getattr(signal, "tp", None)
    if side not in {"BUY", "SELL"}:
        return None, None, None
    return side, _finite_optional(stop), _finite_optional(target)


def _finite_optional(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _exit_fill(position: dict, bar: pd.Series) -> tuple[float | None, str | None]:
    """Conservative intrabar stop/target handling; stop wins ambiguous bars."""
    side = position["side"]
    stop, target = position["stop"], position["target"]
    op, hi, lo = float(bar.open), float(bar.high), float(bar.low)
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
    return None, None


def evaluate_candidate(
    candles: pd.DataFrame,
    strategy_name: str,
    *,
    starting_capital: float = DEFAULT_STARTING_CAPITAL,
    risk_fraction: float = DEFAULT_RISK_FRACTION,
    cost_engine: CostEngine | None = None,
    signal_fn: Callable | None = None,
) -> dict:
    """Evaluate one strategy causally: close signal, next-open entry, OHLC exit.

    A single position is allowed. Stop/target touches use conservative stop
    priority. An open position is marked out at the final close. Sizing risks
    the configured fraction of current equity to the initial stop before costs.
    """
    if strategy_name not in STRATEGIES and signal_fn is None:
        raise ValueError(f"unknown strategy {strategy_name!r}")
    if starting_capital <= 0 or not 0 < risk_fraction <= 1:
        raise ValueError("capital must be positive and risk_fraction in (0, 1]")
    missing = REQUIRED_COLUMNS.difference(candles.columns)
    if missing:
        raise ValueError(f"candles missing columns: {sorted(missing)}")
    frame = candles.reset_index(drop=True)
    if frame.empty:
        raise ValueError("candles must contain at least one market candle")
    if signal_fn is None:
        module_name, fn_name = STRATEGIES[strategy_name]
        strategy_module = importlib.import_module(module_name)
        signal_fn = getattr(strategy_module, fn_name)
        if strategy_name == "donchian20":
            from strategy_donchian_observe_only import add_candidate_features
            frame = add_candidate_features(frame)
        elif strategy_name == "swing":
            from features import add_features
            frame = add_features(frame)
        elif strategy_name == "adx_ema":
            frame = strategy_module.add_features(frame)
        elif strategy_name in {"bb_reversion", "rsi_burst", "vwap_trend"}:
            frame = strategy_module.add_features(frame)
        elif strategy_name == "supertrend":
            from features import add_features
            frame = add_features(frame)
    costs = cost_engine or CostEngine.get_binance_taker_config()
    equity = float(starting_capital)
    peak = equity
    max_drawdown = 0.0
    position = None
    trades: list[dict] = []

    for i in range(len(frame)):
        bar = frame.iloc[i]
        if position is not None:
            raw_exit, reason = _exit_fill(position, bar)
            if raw_exit is None and strategy_name == "donchian20":
                from strategy_donchian_observe_only import get_channel_exit
                history = frame.iloc[: i + 1]
                if get_channel_exit(history, position["signal_side"]):
                    raw_exit, reason = float(bar.close), "CHANNEL"
            if raw_exit is not None:
                direction = 1.0 if position["side"] == "LONG" else -1.0
                exit_price = raw_exit * (1 - direction * costs.exit_slip)
                exit_notional = position["quantity"] * raw_exit
                gross = position["quantity"] * direction * (raw_exit - position["entry_raw"])
                fee = costs.entry_fee * position["entry_notional"] + costs.exit_fee * exit_notional
                slip_cost = position["quantity"] * (
                    abs(position["entry_fill"] - position["entry_raw"])
                    + abs(exit_price - raw_exit)
                )
                spread_cost = costs.spread * (position["entry_notional"] + exit_notional)
                net = gross - fee - slip_cost - spread_cost
                equity += net
                trades.append({
                    "entry_timestamp": position["entry_timestamp"],
                    "exit_timestamp": bar.timestamp.isoformat() if "timestamp" in frame else i,
                    "side": position["side"],
                    "entry_price": position["entry_raw"],
                    "exit_price": raw_exit,
                    "quantity": position["quantity"],
                    "exit_reason": reason,
                    "gross_pnl": gross,
                    "fees": fee,
                    "slippage": slip_cost,
                    "spread": spread_cost,
                    "net_pnl": net,
                })
                position = None
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, (peak - equity) / peak)

        # Signals are calculated after this candle closes; fills are on next open.
        if position is None and equity > 0 and i + 1 < len(frame):
            history = frame.iloc[max(0, i - 255): i + 1]
            signal = signal_fn(history)
            side, stop, target = _signal_fields(signal)
            if side:
                next_bar = frame.iloc[i + 1]
                entry = float(next_bar.open)
                long_side = side == "BUY"
                if stop is not None and ((long_side and stop < entry) or (not long_side and stop > entry)):
                    risk_per_unit = abs(entry - stop)
                    quantity = min(equity * risk_fraction / risk_per_unit, equity / entry)
                    direction = 1.0 if long_side else -1.0
                    entry_fill = entry * (1 + direction * costs.entry_slip)
                    position = {
                        "side": "LONG" if long_side else "SHORT",
                        "signal_side": side,
                        "stop": stop,
                        "target": target,
                        "entry_raw": entry,
                        "entry_fill": entry_fill,
                        "entry_notional": quantity * entry,
                        "quantity": quantity,
                        "entry_index": i + 1,
                        "entry_timestamp": next_bar.timestamp.isoformat() if "timestamp" in frame else i + 1,
                    }

        # Track mark-to-market drawdown on every completed candle while open.
        if position is not None and i >= position["entry_index"]:
            direction = 1.0 if position["side"] == "LONG" else -1.0
            mark = float(bar.close)
            marked_gross = position["quantity"] * direction * (mark - position["entry_raw"])
            marked_exit_notional = position["quantity"] * mark
            marked_fees = costs.entry_fee * position["entry_notional"] + costs.exit_fee * marked_exit_notional
            marked_slippage = position["quantity"] * (
                abs(position["entry_fill"] - position["entry_raw"]) + mark * costs.exit_slip
            )
            marked_spread = costs.spread * (position["entry_notional"] + marked_exit_notional)
            marked_equity = equity + marked_gross - marked_fees - marked_slippage - marked_spread
            peak = max(peak, marked_equity)
            max_drawdown = max(max_drawdown, (peak - marked_equity) / peak)

    if position is not None:
        bar = frame.iloc[-1]
        direction = 1.0 if position["side"] == "LONG" else -1.0
        raw_exit = float(bar.close)
        exit_price = raw_exit * (1 - direction * costs.exit_slip)
        exit_notional = position["quantity"] * raw_exit
        gross = position["quantity"] * direction * (raw_exit - position["entry_raw"])
        fee = costs.entry_fee * position["entry_notional"] + costs.exit_fee * exit_notional
        slip_cost = position["quantity"] * (abs(position["entry_fill"] - position["entry_raw"]) + abs(exit_price - raw_exit))
        spread_cost = costs.spread * (position["entry_notional"] + exit_notional)
        net = gross - fee - slip_cost - spread_cost
        equity += net
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
        trades.append({
            "entry_timestamp": position["entry_timestamp"],
            "exit_timestamp": bar.timestamp.isoformat() if "timestamp" in frame else len(frame) - 1,
            "side": position["side"], "entry_price": position["entry_raw"],
            "exit_price": raw_exit, "quantity": position["quantity"],
            "exit_reason": "END_OF_DATA", "gross_pnl": gross, "fees": fee,
            "slippage": slip_cost, "spread": spread_cost, "net_pnl": net,
        })
    wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses = [t["net_pnl"] for t in trades if t["net_pnl"] < 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    return {
        "strategy": strategy_name,
        "evidence_status": "HISTORICAL_PAPER_RESEARCH_ONLY",
        "starting_capital": starting_capital,
        "final_equity": equity,
        "net_pnl": equity - starting_capital,
        "return_pct": (equity / starting_capital - 1) * 100,
        "trade_count": len(trades),
        "win_rate_pct": (len(wins) / len(trades) * 100) if trades else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "max_drawdown_pct": max_drawdown * 100,
        "trades": trades,
    }


def _validate_output_dir(output_dir: Path) -> Path:
    output_dir = output_dir.resolve()
    protected = {"experiments", "forward_daily_reports"}
    if any(part.lower() in protected for part in output_dir.parts):
        raise ValueError("paper lab output cannot be written under forward experiment directories")
    if output_dir == Path(__file__).resolve().parent:
        raise ValueError("paper lab output must use a dedicated output directory")
    return output_dir


def run_lab(csv_path: Path, manifest_path: Path, output_dir: Path, strategies: list[str]) -> Path:
    unknown = set(strategies).difference(STRATEGIES)
    if unknown:
        raise ValueError(f"unknown strategies: {sorted(unknown)}")
    output_dir = _validate_output_dir(output_dir)
    candles, provenance = _load_verified_candles(csv_path, manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "chronology": "signal on completed close; entry at next candle open",
            "exit": "intrabar stop/target; stop has priority when both touched; end-of-data close",
            "position_limit": 1,
            "risk_fraction": DEFAULT_RISK_FRACTION,
            "leverage_limit": 1.0,
            "costs": CostEngine.get_binance_taker_config().get_report_dict(),
            "claim_limit": "historical paper research only; not forward evidence or profitability claim",
        },
        "data": {**provenance, "rows": len(candles), "first_timestamp": candles.timestamp.iloc[0].isoformat(), "last_timestamp": candles.timestamp.iloc[-1].isoformat()},
        "results": [evaluate_candidate(candles, strategy) for strategy in strategies],
    }
    target = output_dir / "paper_lab_report.json"
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing paper lab report: {target}")
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles", required=True, type=Path, help="CSV of real OHLCV candles")
    parser.add_argument("--manifest", required=True, type=Path, help="JSON provenance sidecar including sha256")
    parser.add_argument("--output-dir", required=True, type=Path, help="isolated paper lab output directory")
    parser.add_argument("--strategies", nargs="+", choices=sorted(STRATEGIES), default=sorted(STRATEGIES))
    args = parser.parse_args()
    print(run_lab(args.candles, args.manifest, args.output_dir, args.strategies))


if __name__ == "__main__":
    main()
