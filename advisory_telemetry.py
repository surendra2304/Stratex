"""
advisory_telemetry.py — Telemetry collector for AI-Universe Advisory Intelligence.

Assembles comprehensive trading performance metrics, strategy parameters,
recent trades, risk states, and regime classifications for AI-Universe analysis.
"""

import datetime
import json
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd

import config
from advisory_params import get_advisory_overlay
from atomic_io import read_jsonl
from logger import get_logger

logger = get_logger("advisory_telemetry")

_CLOSED_TRADE_STATUSES = {"CLOSED", "EXIT_FILLED", "CLOSE_FILLED", "CLOSED_FILLED"}
_CLOSED_TIME_FIELDS = ("closed_at", "exit_timestamp", "exit_time", "close_timestamp", "timestamp")
_CLOSED_PNL_FIELDS = ("net_pnl", "pnl", "realized_pnl")
_REQUIRED_ADVISORY_TRADES = 10
_MAX_PORTFOLIO_AGE_SECONDS = 300.0


def _finite_number(value: Any) -> float | None:
    """Accept finite numbers only; booleans and overflow are not measurements."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _parse_timestamp(value: Any) -> datetime.datetime | None:
    """Parse a timezone-aware UTC instant without inventing a fallback time."""
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            number = _finite_number(value)
            if number is None:
                return None
            if abs(number) >= 100_000_000_000:
                number /= 1000.0
            return datetime.datetime.fromtimestamp(number, tz=datetime.timezone.utc)
        if not isinstance(value, str) or not value.strip():
            return None
        parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _closed_trade(row: dict[str, Any]) -> bool:
    if row.get("synthetic") is True or row.get("census_synthetic") is True:
        return False
    status = row.get("status")
    status = status.strip().upper() if isinstance(status, str) else ""
    if status in _CLOSED_TRADE_STATUSES:
        return True
    if any(row.get(key) is not None for key in _CLOSED_TIME_FIELDS[:-1]):
        return True
    action = row.get("action")
    return isinstance(action, str) and "CLOSE" in action.upper()


def _load_closed_ledger(path_value: str | None) -> tuple[list[tuple[dict[str, Any], float, datetime.datetime]], str, int]:
    """Load complete JSONL ledger evidence; never compute from a partial file."""
    if not path_value:
        return [], "UNSUPPORTED_MODE", 0
    path = Path(path_value)
    if not path.is_file():
        return [], "MISSING", 0
    try:
        result = read_jsonl(path, expected_type=dict)
    except (OSError, UnicodeError, ValueError):
        return [], "UNREADABLE", 0
    if not result.clean:
        return [], "INCOMPLETE", result.skipped
    output: list[tuple[dict[str, Any], float, datetime.datetime]] = []
    invalid_closed_rows = 0
    for row in result.records:
        if not _closed_trade(row):
            continue
        pnl_value = next((row[key] for key in _CLOSED_PNL_FIELDS if key in row), None)
        pnl = _finite_number(pnl_value)
        stamp = next((_parse_timestamp(row.get(key)) for key in _CLOSED_TIME_FIELDS if _parse_timestamp(row.get(key)) is not None), None)
        if pnl is None or stamp is None:
            invalid_closed_rows += 1
            continue
        output.append((row, pnl, stamp))
    if invalid_closed_rows:
        return [], "INCOMPLETE_CLOSED_TRADES", invalid_closed_rows
    output.sort(key=lambda item: item[2])
    return output, "COMPLETE" if result.records else "EMPTY", 0



def _read_recent_trades_from_ledger(ledger_file: str, limit: int = 15) -> list[dict[str, Any]]:
    """Reads the last N closed trades from a JSONL trade ledger."""
    if not os.path.exists(ledger_file):
        return []

    trades = []
    try:
        with open(ledger_file, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    rec = json.loads(line_str)
                    # Filter for closed trades or trades with net_pnl
                    if rec.get("status") in ["CLOSED", "EXIT_FILLED"] or "net_pnl" in rec or "pnl" in rec:
                        trades.append(rec)
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"[ADVISORY_TELEMETRY] Error reading trades from {ledger_file}: {e}")
        return []

    return trades[-limit:]


def build_telemetry_payload(
    trading_mode: str | None = None,
    consultation_reason: str = "SCHEDULED",
    current_df: pd.DataFrame | None = None
) -> dict[str, Any]:
    """
    Constructs the structured telemetry dictionary sent to AI-Universe.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    mode = (trading_mode or getattr(config, "TRADING_MODE", "PAPER")).strip().upper()
    mode_supported = mode in {"PAPER", "TESTNET", "FUTURES"}
    normalized_mode = "TESTNET" if mode in {"TESTNET", "FUTURES"} else "PAPER" if mode == "PAPER" else "UNKNOWN"

    # Missing sources remain missing. The downstream scheduler checks the
    # telemetry_quality contract before allowing any model consultation.
    equity = None
    cash = None
    realized_pnl = None
    unrealized_pnl = None
    max_drawdown = None
    max_drawdown_pct = None
    open_positions_count = None
    portfolio_raw: dict[str, Any] = {}
    portfolio_file = (
        os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
        if mode in {"TESTNET", "FUTURES"}
        else os.getenv("PAPER_PORTFOLIO_FILE", "paper_portfolio.json")
        if mode == "PAPER"
        else None
    )
    portfolio_missing_fields: list[str] = []
    portfolio_status = "UNSUPPORTED_MODE" if not mode_supported else "MISSING"
    snapshot_dt: datetime.datetime | None = None

    if portfolio_file and os.path.isfile(portfolio_file):
        try:
            with open(portfolio_file, "r", encoding="utf-8") as pf:
                loaded = json.load(pf)
            if not isinstance(loaded, dict):
                portfolio_status = "INVALID"
                portfolio_missing_fields.append("json_object")
            else:
                portfolio_raw = loaded
                equity = _finite_number(loaded.get("equity", loaded.get("current_equity")))
                if equity is None or equity <= 0.0:
                    portfolio_missing_fields.append("equity")
                cash = _finite_number(loaded.get("cash", loaded.get("usdt_cash")))
                realized_pnl = _finite_number(loaded.get("realized_pnl"))
                unrealized_pnl = _finite_number(loaded.get("unrealized_pnl"))

                if "max_drawdown_pct" in loaded:
                    max_drawdown_pct = _finite_number(loaded.get("max_drawdown_pct"))
                    max_drawdown = max_drawdown_pct
                else:
                    legacy_dd = _finite_number(loaded.get("max_drawdown"))
                    max_drawdown = legacy_dd
                    max_drawdown_pct = (
                        legacy_dd * 100.0
                        if legacy_dd is not None and 0.0 <= legacy_dd <= 1.0
                        else legacy_dd
                    )
                if max_drawdown_pct is None or not 0.0 <= max_drawdown_pct <= 100.0:
                    portfolio_missing_fields.append("max_drawdown_pct")

                if isinstance(loaded.get("positions"), (dict, list)):
                    open_positions_count = len(loaded["positions"])
                else:
                    open_count = _finite_number(loaded.get("open_positions", loaded.get("open_positions_count")))
                    if open_count is None or not open_count.is_integer() or open_count < 0:
                        portfolio_missing_fields.append("positions")
                    else:
                        open_positions_count = int(open_count)

                timestamp_raw = loaded.get("timestamp", loaded.get("updated_at", loaded.get("captured_at")))
                snapshot_dt = _parse_timestamp(timestamp_raw)
                if snapshot_dt is None:
                    portfolio_missing_fields.append("snapshot_timestamp")
                if unrealized_pnl is None:
                    portfolio_missing_fields.append("unrealized_pnl")
                portfolio_status = "MEASURED" if not portfolio_missing_fields else "INCOMPLETE"
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            portfolio_status = "UNREADABLE"
            portfolio_missing_fields.append("valid_portfolio_snapshot")
            logger.warning("[ADVISORY_TELEMETRY] Portfolio snapshot unavailable (%s)", type(exc).__name__)

    # The ledger has an all-or-nothing integrity boundary. Only explicitly
    # closed trades with finite PnL and a parseable close time are counted.
    ledger_file = (
        os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
        if mode in {"TESTNET", "FUTURES"}
        else os.getenv("PAPER_LEDGER_FILE", os.getenv("PAPER_TRADE_LEDGER_FILE", "paper_trade_ledger.jsonl"))
        if mode == "PAPER"
        else None
    )
    closed_trades, ledger_status, ledger_issues = _load_closed_ledger(ledger_file)
    ledger_complete = ledger_status in {"COMPLETE", "EMPTY"}
    if not ledger_complete:
        closed_trades = []

    total_trades = len(closed_trades) if ledger_complete else None
    winning_pnls = [pnl for _, pnl, _ in closed_trades if pnl > 0.0]
    losing_pnls = [pnl for _, pnl, _ in closed_trades if pnl < 0.0]
    winning_trades = len(winning_pnls) if ledger_complete else None
    losing_trades = len(losing_pnls) if ledger_complete else None
    gross_profit = sum(winning_pnls) if ledger_complete else None
    gross_loss = abs(sum(losing_pnls)) if ledger_complete else None
    win_rate = (len(winning_pnls) / total_trades) if total_trades and total_trades > 0 else None
    profit_factor = (
        gross_profit / gross_loss
        if gross_profit is not None and gross_loss is not None and gross_loss > 0.0
        else None
    )
    consecutive_losses = 0 if ledger_complete else None
    if ledger_complete:
        for _, pnl, _ in reversed(closed_trades):
            if pnl < 0.0:
                consecutive_losses += 1
            else:
                break
    realized_from_ledger = sum(pnl for _, pnl, _ in closed_trades) if ledger_complete else None
    if realized_pnl is None and realized_from_ledger is not None:
        realized_pnl = realized_from_ledger

    # A source timestamp is measured separately from the time this function runs.
    snapshot_age_sec = (now - snapshot_dt).total_seconds() if snapshot_dt is not None else None
    freshness_status = (
        "FRESH"
        if snapshot_age_sec is not None and 0.0 <= snapshot_age_sec <= _MAX_PORTFOLIO_AGE_SECONDS
        else "STALE"
        if snapshot_age_sec is not None and snapshot_age_sec > _MAX_PORTFOLIO_AGE_SECONDS
        else "UNKNOWN"
    )
    recent_closed_trades = [row for row, _, _ in closed_trades[-15:]] if ledger_complete else []
    # 3. Market Regime Analysis. Unknown is materially different from RANGE/LOW_VOL.
    regime_data = {"regime": "UNKNOWN", "volatility_state": "UNKNOWN"}
    regime_status = "UNAVAILABLE"
    if current_df is not None and not current_df.empty:
        try:
            from regime import classify_regimes
            classified = classify_regimes(current_df)
            if classified is not None and not classified.empty:
                last_row = classified.iloc[-1]
                regime = last_row.get("regime")
                volatility = last_row.get("volatility_state")
                if isinstance(regime, str) and regime.strip() and isinstance(volatility, str) and volatility.strip():
                    regime_data = {"regime": regime.strip(), "volatility_state": volatility.strip()}
                    regime_status = "MEASURED"
                else:
                    regime_status = "INVALID"
        except Exception as e:
            logger.warning(f"[ADVISORY_TELEMETRY] Regime classification unavailable ({type(e).__name__})")

    # 4. Strategy Parameters Snapshot
    overlay = get_advisory_overlay()
    current_strategy = getattr(config, "ACTIVE_STRATEGY", "aggressive_scalper")
    current_params = overlay.get_current_params(current_strategy)

    # 5. Integrate IntelX Market Context if available
    market_context = None
    try:
        from intelligence.intelx_client import get_intelx_client
        intelx = get_intelx_client()
        market_context = intelx.get_latest_market_context()
        if market_context:
            try:
                from monitoring.metrics import get_metrics_registry
                get_metrics_registry().market_context_enriched_consultations_total += 1
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[ADVISORY_TELEMETRY] IntelX context lookup skipped: {e}")

    # 5b. Integrate Futuris Market Forecast Context if available
    futuris_context = None
    try:
        from intelligence.futuris_client import get_futuris_client
        futuris = get_futuris_client()
        futuris_context = futuris.get_latest_futuris_context()
        if futuris_context:
            try:
                from monitoring.metrics import get_metrics_registry
                reg = get_metrics_registry()
                reg.futuris_context_included_consultations_total += 1
                acc_info = futuris.get_accuracy_metrics()
                accuracy = _finite_number(acc_info.get("accuracy_pct")) if isinstance(acc_info, dict) else None
                if accuracy is not None and 0.0 <= accuracy <= 100.0:
                    reg.forecast_accuracy_pct = accuracy
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[ADVISORY_TELEMETRY] Futuris context lookup skipped: {e}")

    # 6. Normalize consultation reason to valid Enum
    valid_reasons = {"SCHEDULED", "DRAWDOWN_EVENT", "LOSS_STREAK", "MANUAL"}
    reason_clean = consultation_reason.upper().strip()
    if "STARTUP" in reason_clean or reason_clean not in valid_reasons:
        reason_clean = "SCHEDULED"

    # 7. Build evidence and readiness status before producing the compatible payload.
    if realized_pnl is None and ledger_status in {"COMPLETE", "EMPTY"}:
        realized_pnl = sum(pnl for _, pnl, _ in closed_trades)

    if realized_pnl is None:
        portfolio_missing_fields.append("realized_pnl")
    if unrealized_pnl is None:
        portfolio_missing_fields.append("unrealized_pnl")
    portfolio_missing_fields = sorted(set(portfolio_missing_fields))
    portfolio_status = "MEASURED" if not portfolio_missing_fields and portfolio_file else (
        "UNSUPPORTED_MODE" if not mode_supported else "INCOMPLETE"
    )
    if snapshot_dt is not None:
        snapshot_age_sec = (now - snapshot_dt).total_seconds()
    else:
        snapshot_age_sec = None
    freshness_status = (
        "FRESH"
        if snapshot_age_sec is not None and 0.0 <= snapshot_age_sec <= _MAX_PORTFOLIO_AGE_SECONDS
        else "STALE"
        if snapshot_age_sec is not None and snapshot_age_sec > _MAX_PORTFOLIO_AGE_SECONDS
        else "UNKNOWN"
    )

    win_rate_fraction = win_rate
    win_rate_pct = win_rate_fraction * 100.0 if win_rate_fraction is not None else None
    normalized_mode = "TESTNET" if mode in {"TESTNET", "FUTURES"} else "PAPER" if mode == "PAPER" else "UNKNOWN"

    unavailable_fields: list[str] = []
    if not mode_supported:
        unavailable_fields.append("trading_mode")
    if portfolio_status != "MEASURED":
        unavailable_fields.append("complete_portfolio_snapshot")
    if freshness_status != "FRESH":
        unavailable_fields.append("fresh_portfolio_snapshot")
    if ledger_status != "COMPLETE":
        unavailable_fields.append("complete_trade_ledger")
    if total_trades is None or total_trades < _REQUIRED_ADVISORY_TRADES:
        unavailable_fields.append("sufficient_closed_trade_history")
    for field_name, field_value in (
        ("equity", equity),
        ("realized_pnl", realized_pnl),
        ("unrealized_pnl", unrealized_pnl),
        ("max_drawdown_pct", max_drawdown_pct),
        ("win_rate", win_rate_fraction),
        ("profit_factor", profit_factor),
    ):
        if field_value is None:
            unavailable_fields.append(field_name)
    advisory_ready = (
        mode_supported
        and portfolio_status == "MEASURED"
        and freshness_status == "FRESH"
        and ledger_status == "COMPLETE"
        and total_trades is not None
        and total_trades >= _REQUIRED_ADVISORY_TRADES
        and all(value is not None for value in (equity, realized_pnl, unrealized_pnl, max_drawdown_pct, win_rate_fraction, profit_factor))
    )
    telemetry_quality = {
        "schema_version": 1,
        "advisory_ready": advisory_ready,
        "blocking_reasons": sorted(set(unavailable_fields)),
        "trading_mode_status": "SUPPORTED" if mode_supported else "UNSUPPORTED",
        "portfolio_status": portfolio_status,
        "portfolio_missing_fields": portfolio_missing_fields,
        "portfolio_file": os.path.basename(portfolio_file) if portfolio_file else None,
        "portfolio_snapshot_age_seconds": round(snapshot_age_sec, 3) if snapshot_age_sec is not None else None,
        "portfolio_freshness_status": freshness_status,
        "ledger_status": ledger_status,
        "ledger_file": os.path.basename(ledger_file) if ledger_file else None,
        "ledger_skipped_rows": ledger_issues,
        "closed_trade_count": total_trades,
        "required_closed_trade_count": _REQUIRED_ADVISORY_TRADES,
        "win_rate_status": "MEASURED" if win_rate_fraction is not None else "UNAVAILABLE",
        "profit_factor_status": "MEASURED" if profit_factor is not None else "UNAVAILABLE_OR_ZERO_GROSS_LOSS",
        "market_regime_status": regime_status,
        "testnet_context_status": "MEASURED" if normalized_mode == "TESTNET" and isinstance(portfolio_raw.get("daily_loss"), (int, float)) and isinstance(portfolio_raw.get("margin_level"), (int, float)) else "UNAVAILABLE",
    }

    # Required schema values stay absent when evidence is absent. The scheduler
    # refuses the consultation unless all required model-facing metrics are present.
    telemetry_stats = {
        "equity": round(equity, 2) if equity is not None else None,
        "unrealized_pnl": round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
        "realized_pnl": round(realized_pnl, 2) if realized_pnl is not None else None,
        "win_rate": round(win_rate_fraction, 4) if win_rate_fraction is not None else None,
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "max_drawdown_pct": round(max_drawdown_pct, 2) if max_drawdown_pct is not None else None,
        "consecutive_losses": consecutive_losses,
        "total_trades": total_trades,
        "sharpe_ratio": None,
    }
    portfolio_compat = {
        "equity": round(equity, 2) if equity is not None else None,
        "cash": round(cash, 2) if cash is not None else None,
        "realized_pnl": round(realized_pnl, 2) if realized_pnl is not None else None,
        "unrealized_pnl": round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
        "max_drawdown_pct": round(max_drawdown_pct, 2) if max_drawdown_pct is not None else None,
        "open_positions": open_positions_count,
        "snapshot_timestamp": snapshot_dt.isoformat().replace("+00:00", "Z") if snapshot_dt else None,
    }
    performance_metrics_compat = {
        "total_trades": total_trades,
        "win_rate": round(win_rate_pct, 2) if win_rate_pct is not None else None,
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "consecutive_losses": consecutive_losses,
    }

    testnet_daily_loss = _finite_number(portfolio_raw.get("daily_loss", portfolio_raw.get("daily_realized_pnl")))
    testnet_margin_level = _finite_number(portfolio_raw.get("margin_level"))
    testnet_context_complete = (
        normalized_mode == "TESTNET"
        and equity is not None
        and max_drawdown_pct is not None
        and open_positions_count is not None
        and testnet_daily_loss is not None
        and testnet_margin_level is not None
    )
    testnet_specific = {
        "testnet_equity": round(equity, 2),
        "testnet_drawdown_pct": round(max_drawdown_pct, 2),
        "testnet_daily_loss": round(testnet_daily_loss, 2),
        "testnet_open_positions": open_positions_count,
        "testnet_margin_level": round(testnet_margin_level, 2),
    } if testnet_context_complete else None

    snapshot_iso = snapshot_dt.isoformat().replace("+00:00", "Z") if snapshot_dt else None
    payload = {
        "bot_id": "stratex_bot_01",
        "trading_mode": normalized_mode,
        "consultation_reason": reason_clean,
        "timestamp": now_iso,
        "telemetry_quality": telemetry_quality,
        "telemetry_freshness": {
            "timestamp": snapshot_iso,
            "max_staleness_seconds": _MAX_PORTFOLIO_AGE_SECONDS,
            "clock_skew_ms": round(abs(snapshot_age_sec) * 1000.0, 3) if snapshot_age_sec is not None else None,
            "status": freshness_status,
        },
        "exchange_status": {
            "connectivity": "UNKNOWN",
            "exchange": "binance_testnet" if normalized_mode == "TESTNET" else "paper_simulator",
            "latency_ms": None,
        },
        "positions": {
            "open_count": open_positions_count,
            "reconciliation_status": "UNKNOWN",
            "active_symbols": [],
        },
        "telemetry": telemetry_stats,
        "portfolio": portfolio_compat,
        "performance_metrics": performance_metrics_compat,
        "market_context": market_context,
        "futuris_context": futuris_context,
        "market_regime": regime_data,
        "strategy_performance": [
            {
                "strategy_name": current_strategy,
                "trade_count": total_trades,
                "win_rate": round(win_rate_fraction, 4) if win_rate_fraction is not None else None,
                "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
                "net_pnl": round(realized_pnl, 2) if realized_pnl is not None else None,
                "avg_win": round(gross_profit / len(winning_pnls), 2) if winning_pnls and gross_profit is not None else None,
                "avg_loss": round(gross_loss / len(losing_pnls), 2) if losing_pnls and gross_loss is not None else None,
                "consecutive_losses": consecutive_losses,
            }
        ] if ledger_complete and closed_trades else [],
        "current_parameters": {
            current_strategy: current_params if isinstance(current_params, dict) else {}
        },
        "regime_data": regime_data,
        "recent_trades": recent_closed_trades if ledger_complete else None,
        "testnet_specific": testnet_specific,
    }

    return payload
