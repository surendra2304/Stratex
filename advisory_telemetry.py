"""
advisory_telemetry.py — Telemetry collector for AI-Universe Advisory Intelligence.

Assembles comprehensive trading performance metrics, strategy parameters,
recent trades, risk states, and regime classifications for AI-Universe analysis.
"""

import datetime
import json
import os
from typing import Any, Dict, List, Optional

import config
import config_strategy
from advisory_params import get_advisory_overlay
from logger import get_logger
import pandas as pd

logger = get_logger("advisory_telemetry")


def _read_recent_trades_from_ledger(ledger_file: str, limit: int = 15) -> List[Dict[str, Any]]:
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
    trading_mode: Optional[str] = None,
    consultation_reason: str = "SCHEDULED",
    current_df: Optional[pd.DataFrame] = None
) -> Dict[str, Any]:
    """
    Constructs the structured telemetry dictionary sent to AI-Universe.
    """
    mode = (trading_mode or getattr(config, "TRADING_MODE", "PAPER")).upper()
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    # 1. Gather Portfolio Metrics
    equity = 10000.0
    cash = 10000.0
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    max_drawdown = 0.0
    open_positions_count = 0
    consecutive_losses = 0

    portfolio_file = "testnet_portfolio.json" if mode in ["TESTNET", "FUTURES"] else "paper_portfolio.json"
    if os.path.exists(portfolio_file):
        try:
            with open(portfolio_file, "r", encoding="utf-8") as pf:
                pdata = json.load(pf)
                equity = float(pdata.get("equity", pdata.get("current_equity", 10000.0)))
                cash = float(pdata.get("cash", pdata.get("usdt_cash", equity)))
                realized_pnl = float(pdata.get("realized_pnl", 0.0))
                unrealized_pnl = float(pdata.get("unrealized_pnl", 0.0))
                max_drawdown = float(pdata.get("max_drawdown", 0.0))
                positions = pdata.get("positions", {})
                open_positions_count = len(positions) if isinstance(positions, (dict, list)) else 0
        except Exception as e:
            logger.warning(f"[ADVISORY_TELEMETRY] Could not read {portfolio_file}: {e}")

    # 2. Gather Ledger Trades and Compute Win Rate & Profit Factor
    ledger_file = "testnet_trade_ledger.jsonl" if mode in ["TESTNET", "FUTURES"] else "paper_trade_ledger.jsonl"
    recent_closed_trades = _read_recent_trades_from_ledger(ledger_file, limit=15)

    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    gross_profit = 0.0
    gross_loss = 0.0
    streak_losses = 0

    # Parse full ledger for accurate statistics
    if os.path.exists(ledger_file):
        try:
            with open(ledger_file, "r", encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        rec = json.loads(line_str)
                        pnl = float(rec.get("net_pnl", rec.get("pnl", rec.get("realized_pnl", 0.0))))
                        total_trades += 1
                        if pnl > 0:
                            winning_trades += 1
                            gross_profit += pnl
                            streak_losses = 0
                        elif pnl < 0:
                            losing_trades += 1
                            gross_loss += abs(pnl)
                            streak_losses += 1
                        else:
                            pass
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"[ADVISORY_TELEMETRY] Error calculating metrics from {ledger_file}: {e}")

    consecutive_losses = streak_losses
    win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (1.0 if gross_profit > 0 else 0.0)

    # 3. Market Regime Analysis
    regime_data = {"regime": "RANGE", "volatility_state": "LOW_VOL"}
    if current_df is not None and not current_df.empty:
        try:
            from regime import classify_regimes
            classified = classify_regimes(current_df)
            if classified is not None and not classified.empty:
                last_row = classified.iloc[-1]
                regime_data = {
                    "regime": str(last_row.get("regime", "RANGE")),
                    "volatility_state": str(last_row.get("volatility_state", "LOW_VOL"))
                }
        except Exception as e:
            logger.warning(f"[ADVISORY_TELEMETRY] Regime classification error: {e}")

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
                reg.forecast_accuracy_pct = float(acc_info.get("accuracy_pct", 100.0))
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[ADVISORY_TELEMETRY] Futuris context lookup skipped: {e}")

    # 6. Normalize consultation reason to valid Enum
    valid_reasons = {"SCHEDULED", "DRAWDOWN_EVENT", "LOSS_STREAK", "MANUAL"}
    reason_clean = consultation_reason.upper().strip()
    if "STARTUP" in reason_clean or reason_clean not in valid_reasons:
        reason_clean = "SCHEDULED"

    # 7. Normalize trading mode to PAPER or TESTNET
    normalized_mode = "TESTNET" if mode in ["TESTNET", "FUTURES"] else "PAPER"

    # 8. Assemble Payload adhering strictly to TradingConsultRequest schema
    payload = {
        "bot_id": "stratex_bot_01",
        "trading_mode": normalized_mode,
        "consultation_reason": reason_clean,
        "telemetry": {
            "equity": round(equity, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "win_rate": round(min(max(win_rate / 100.0 if win_rate > 1.0 else win_rate, 0.0), 1.0), 4),
            "profit_factor": round(max(profit_factor, 0.0), 2),
            "max_drawdown_pct": round(max_drawdown * 100 if max_drawdown <= 1.0 else max_drawdown, 2),
            "consecutive_losses": int(max(consecutive_losses, 0)),
            "total_trades": int(max(total_trades, 0)),
            "sharpe_ratio": None
        },
        "strategy_performance": [
            {
                "strategy_name": current_strategy,
                "trade_count": int(max(total_trades, 0)),
                "win_rate": round(min(max(win_rate / 100.0 if win_rate > 1.0 else win_rate, 0.0), 1.0), 4),
                "profit_factor": round(max(profit_factor, 0.0), 2),
                "net_pnl": round(realized_pnl, 2),
                "avg_win": round(gross_profit / max(winning_trades, 1), 2),
                "avg_loss": round(gross_loss / max(losing_trades, 1), 2),
                "consecutive_losses": int(max(consecutive_losses, 0))
            }
        ],
        "current_parameters": {
            current_strategy: current_params if isinstance(current_params, dict) else {}
        },
        "regime_data": regime_data,
        "recent_trades": recent_closed_trades,
        "testnet_specific": {
            "testnet_equity": round(equity, 2),
            "testnet_drawdown_pct": round(max_drawdown * 100 if max_drawdown <= 1.0 else max_drawdown, 2),
            "testnet_daily_loss": 0.0,
            "testnet_open_positions": open_positions_count,
            "testnet_margin_level": 100.0
        } if normalized_mode == "TESTNET" else None
    }

    return payload
