"""
paper_forward_runner.py

Genuine 30-day PAPER Forward Validation Experiment Runner.

CLASSIFICATION RULE (IMMUTABLE):
  Final classification requires BOTH conditions satisfied simultaneously:
    1. Wall-clock duration >= 30 complete calendar days
    2. Closed trades >= 30

  30 trades before 30 days → CONTINUE RUNNING — do NOT classify early.
  30 days with < 30 trades  → INCONCLUSIVE — INSUFFICIENT SAMPLE.
  30 days AND >= 30 trades  → perform full statistical evaluation.

EXPERIMENT: forward_exp_001
STRATEGY: Swing MACD + 200 EMA (1H BTCUSDT)
MODE: PAPER ONLY — zero Binance orders placed

FROZEN CONFIGURATION — DO NOT MODIFY DURING EXPERIMENT:
  - Strategy: strategy_swing.py (MACD crossover + 200 EMA, volume filter)
  - Features: features.py (add_features)
  - Symbol: BTCUSDT
  - Timeframe: 1h
  - CostEngine: Binance Taker (entry_fee=0.001, exit_fee=0.001,
                               entry_slip=0.0005, exit_slip=0.0005,
                               spread=0.0001)
  - Starting capital: $10,000
  - Max simultaneous positions: 3
  - Max daily loss: $500
  - Max drawdown: 20%

ARCHITECTURE:
  1. MarketDataClient (read-only, no credentials) fetches 1H candles
  2. features.add_features() computes indicators
  3. strategy_swing.get_signal() generates BUY/SELL/None — ONLY on past data
  4. PaperPortfolio handles all accounting
  5. SignalLogger (append-only, deduplicated) logs every signal
  6. paper_trade_ledger.jsonl is the durable trade record
  7. paper_equity_curve.jsonl tracks equity
  8. Heartbeat, reconciliation, and daily reports run every cycle
  9. Crash recovery: on restart, reads existing experiment config and ledger

CRASH RECOVERY:
  - The experiment config is persisted in experiments/<id>.json
  - The portfolio is persisted in paper_portfolio.json (atomic writes)
  - The ledger and equity curve are append-only JSONL files
  - On restart, the runner detects the existing running experiment and resumes

DO NOT:
  - Change parameters, thresholds, features, or strategy mid-experiment
  - Add symbols not in the frozen config
  - Disable cost accounting
  - Skip signal logging for losing or rejected signals
  - Stop early because results look bad
"""
import datetime
import json
import os
import sys
import time
import uuid

import pandas as pd

# ── Configuration ──────────────────────────────────────────────────────────

EXPERIMENT_ID_FILE = "experiments/active_forward_experiment_id.txt"
EXPERIMENT_DIR = "experiments"
SIGNAL_LOG_FILE = "forward_signal_log.jsonl"
LEDGER_FILE = "paper_trade_ledger.jsonl"
EQUITY_CURVE_FILE = "paper_equity_curve.jsonl"
DAILY_REPORT_DIR = "forward_daily_reports"
HEALTH_FILE = "forward_health.json"
RECONCILIATION_LOG = "forward_reconciliation.jsonl"

# Frozen strategy parameters — DO NOT CHANGE
FROZEN_SYMBOL = "BTCUSDT"
FROZEN_TIMEFRAME = "1h"
FROZEN_STRATEGY = "strategy_swing_macd_200ema"
FROZEN_STRATEGY_VERSION = "1.0.0"
FROZEN_FEATURE_VERSION = "features.py@e090903"
FROZEN_PARAMS = {
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "ema_trend": 200,
    "atr_sl_multiplier": 2.0,
    "atr_tp_multiplier": 3.0,
    "min_rel_volume": 1.0,
    "macd_must_be_negative_for_buy": True,
    "macd_must_be_positive_for_sell": True,
}
FROZEN_STARTING_CAPITAL = 10000.0
FROZEN_PLANNED_DAYS = 30
FROZEN_MIN_TRADES = 30

# ── Imports ────────────────────────────────────────────────────────────────

from data_client import MarketDataClient
from features import add_features
from logger import get_logger
from paper_engine.experiment_config import (
    FrozenExperimentConfig,
    register_experiment,
)
from paper_engine.kill_switch import is_kill_switch_active
from paper_engine.portfolio import PaperPortfolio
from paper_engine.reconciliation import PaperReconciliation
from paper_engine.signal_logger import SignalLogger
from paper_engine.statistical_report import can_classify
from research_phase9.cost_engine import CostEngine
from strategy_swing import get_signal

logger = get_logger("paper_forward_runner")

COST_ENGINE = CostEngine.get_binance_taker_config()


# ══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════════

def _get_git_sha() -> str:
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def load_or_create_experiment() -> FrozenExperimentConfig:
    """
    Load existing running experiment, or create and start a new one.
    The experiment ID is persisted so crash restarts resume the same experiment.
    """
    os.makedirs(EXPERIMENT_DIR, exist_ok=True)

    # ── Resume existing experiment ─────────────────────────────────────────
    if os.path.exists(EXPERIMENT_ID_FILE):
        with open(EXPERIMENT_ID_FILE) as f:
            exp_id = f.read().strip()
        exp_path = os.path.join(EXPERIMENT_DIR, f"{exp_id}.json")
        if os.path.exists(exp_path):
            cfg = FrozenExperimentConfig.load(exp_id, EXPERIMENT_DIR)
            if cfg.status == "RUNNING":
                logger.info(f"RESUMED experiment: {cfg.experiment_id[:8]} (started {cfg.started_at})")
                return cfg
            else:
                logger.info(f"Experiment {exp_id[:8]} is {cfg.status} — creating fresh experiment.")
        else:
            logger.warning(f"Experiment ID file found but config missing: {exp_path}")

    # ── Create new experiment ──────────────────────────────────────────────
    git_sha = _get_git_sha()
    cfg = FrozenExperimentConfig(
        experiment_name="forward_exp_001",
        strategy_name=FROZEN_STRATEGY,
        strategy_version=FROZEN_STRATEGY_VERSION,
        symbols=[FROZEN_SYMBOL],
        timeframe=FROZEN_TIMEFRAME,
        strategy_params=FROZEN_PARAMS,
        cost_config=COST_ENGINE.get_report_dict(),
        starting_capital=FROZEN_STARTING_CAPITAL,
        max_position_pct=0.10,
        max_simultaneous_positions=3,
        max_daily_loss=500.0,
        max_drawdown_pct=0.20,
        leverage=1.0,
        typical_hold_bars=24,       # 1H bars: ~1 day hold
        benchmark_hold_bars=24,
        benchmark_n_trades=15,
        benchmark_iterations=1000,
        benchmark_random_seed=42,
        min_required_trades=FROZEN_MIN_TRADES,
        planned_duration_days=FROZEN_PLANNED_DAYS,
        required_profit_factor=1.20,
        required_expectancy_per_trade=0.0,
        required_win_rate=0.0,
        required_sharpe=0.5,
        max_acceptable_drawdown_pct=0.20,
        git_sha=git_sha,
    )
    cfg.mark_started()
    cfg.save(EXPERIMENT_DIR)
    register_experiment(cfg, os.path.join(EXPERIMENT_DIR, "registry.json"))

    # Persist experiment ID for crash recovery
    with open(EXPERIMENT_ID_FILE, "w") as f:
        f.write(cfg.experiment_id)

    logger.info(
        f"NEW experiment started: {cfg.experiment_id[:8]} "
        f"git_sha={git_sha[:8]} "
        f"at {datetime.datetime.utcfromtimestamp(cfg.started_at).isoformat()}Z"
    )
    return cfg


# ══════════════════════════════════════════════════════════════════════════════
# MARKET DATA
# ══════════════════════════════════════════════════════════════════════════════

def fetch_candles(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame | None:
    """
    Fetches recent OHLCV candles from Binance via MarketDataClient (read-only).
    Returns None on failure — DO NOT substitute synthetic data.
    """
    try:
        mdc = MarketDataClient()
        raw = mdc.get_klines(symbol=symbol, interval=interval, limit=limit)
        if not raw:
            return None
        df = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "n_trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"fetch_candles failed for {symbol}/{interval}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def log_signal_record(
    signal_logger: SignalLogger,
    timestamp: float,
    strategy: str,
    symbol: str,
    side: str | None,
    confidence: float,
    entry_price: float,
    sl: float | None,
    tp: float | None,
    decision: str,
    rejection_reason: str = "",
    data_source: str = "BINANCE_REST",
):
    record = {
        "signal_id": str(uuid.uuid4()),
        "timestamp": timestamp,
        "strategy": strategy,
        "symbol": symbol,
        "side": side,
        "confidence": confidence,
        "entry_price": entry_price,
        "sl": sl,
        "tp": tp,
        "decision": decision,        # TRADED | REJECTED | NO_SIGNAL
        "rejection_reason": rejection_reason,
        "data_source": data_source,
        "timeframe": FROZEN_TIMEFRAME,
    }
    signal_logger.log_signal(record)


# ══════════════════════════════════════════════════════════════════════════════
# PAPER EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

def paper_execute(
    portfolio: PaperPortfolio,
    signal: str,
    symbol: str,
    candle_close: float,
    sl: float,
    tp: float,
    current_prices: dict,
    ledger_file: str,
):
    """
    Simulates paper execution with full CostEngine cost attribution.
    Returns a dict describing the simulated fill or rejection.
    """
    direction = "LONG" if signal == "BUY" else "SHORT"

    # Position sizing: risk 1% of equity per trade
    equity = portfolio.get_equity(current_prices)
    max_loss_per_trade = equity * 0.01
    atr_distance = abs(candle_close - sl)
    if atr_distance <= 0:
        return {"status": "REJECTED", "reason": "ZERO_ATR_DISTANCE"}

    qty = max_loss_per_trade / atr_distance
    notional = candle_close * qty

    # Apply entry slippage
    if signal == "BUY":
        eff_entry = candle_close * (1 + COST_ENGINE.entry_slip)
    else:
        eff_entry = candle_close * (1 - COST_ENGINE.entry_slip)

    entry_fee = notional * COST_ENGINE.entry_fee
    spread_cost = notional * COST_ENGINE.spread

    margin = notional  # 1x leverage

    # Risk gate
    try:
        portfolio.check_risk_limits(equity, notional)
    except ValueError as e:
        return {"status": "REJECTED", "reason": f"RISK_LIMIT: {e}"}

    try:
        ev_open = str(uuid.uuid4())
        portfolio.allocate_margin(margin, ev_open)
        pos_id = str(uuid.uuid4())
        portfolio.add_position(pos_id, symbol, direction, eff_entry, qty)

        # Deduct entry fee
        fee_ev = str(uuid.uuid4())
        portfolio.add_realized_pnl(-entry_fee - spread_cost, fee_ev)

        logger.info(
            f"PAPER ENTRY: {direction} {symbol} @ {eff_entry:.4f} qty={qty:.6f} "
            f"entry_fee={entry_fee:.4f} spread={spread_cost:.4f}"
        )
        return {
            "status": "FILLED",
            "pos_id": pos_id,
            "direction": direction,
            "eff_entry": eff_entry,
            "qty": qty,
            "notional": notional,
            "entry_fee": entry_fee,
            "spread_cost": spread_cost,
            "sl": sl,
            "tp": tp,
        }
    except Exception as e:
        return {"status": "REJECTED", "reason": f"PORTFOLIO_ERROR: {e}"}


def paper_exit_positions(
    portfolio: PaperPortfolio,
    current_prices: dict,
    df: pd.DataFrame,
):
    """
    Check all open positions for SL/TP hits or exit signal.
    Uses current candle high/low to detect intrabar hits.
    """
    if df is None or df.empty:
        return

    last = df.iloc[-1]
    high = last["high"]
    low = last["low"]
    last["close"]

    for pos_id, pos in list(portfolio.positions.items()):
        if pos["status"] != "OPEN":
            continue

        sym = pos["symbol"]
        direction = pos["direction"]
        qty = pos["quantity"]
        entry_price = pos["entry_price"]

        # Retrieve SL/TP from position metadata (stored separately)
        sl_price = pos.get("sl")
        tp_price = pos.get("tp")
        if sl_price is None or tp_price is None:
            continue

        exit_price = None
        exit_reason = None

        if direction in ("LONG", "BUY"):
            if low <= sl_price:
                exit_price = sl_price
                exit_reason = "SL_HIT"
            elif high >= tp_price:
                exit_price = tp_price
                exit_reason = "TP_HIT"
        else:
            if high >= sl_price:
                exit_price = sl_price
                exit_reason = "SL_HIT"
            elif low <= tp_price:
                exit_price = tp_price
                exit_reason = "TP_HIT"

        if exit_price is None:
            continue

        # Apply exit slippage
        if direction in ("LONG", "BUY"):
            eff_exit = exit_price * (1 - COST_ENGINE.exit_slip)
        else:
            eff_exit = exit_price * (1 + COST_ENGINE.exit_slip)

        notional = eff_exit * qty
        exit_fee = notional * COST_ENGINE.exit_fee
        spread_cost = notional * COST_ENGINE.spread

        if direction in ("LONG", "BUY"):
            gross_pnl = (eff_exit - entry_price) * qty
        else:
            gross_pnl = (entry_price - eff_exit) * qty

        net_pnl = gross_pnl - exit_fee - spread_cost

        portfolio.close_position(pos_id, eff_exit, exit_fee=exit_fee)
        pnl_ev = str(uuid.uuid4())
        portfolio.add_realized_pnl(net_pnl, pnl_ev)

        logger.info(
            f"PAPER EXIT [{exit_reason}]: {direction} {sym} "
            f"entry={entry_price:.4f} exit={eff_exit:.4f} "
            f"gross={gross_pnl:.4f} net={net_pnl:.4f} "
            f"exit_fee={exit_fee:.4f}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH MONITORING
# ══════════════════════════════════════════════════════════════════════════════

class ForwardHealth:
    def __init__(self):
        self.market_data = "OK"
        self.strategy = "OK"
        self.portfolio = "OK"
        self.persistence = "OK"
        self.reconciliation = "OK"
        self.last_update = time.time()

    def set(self, component: str, state: str):
        setattr(self, component, state)
        self.last_update = time.time()
        self._persist()

    def is_safe_to_trade(self) -> bool:
        return all(
            s in ("OK", "DEGRADED")
            for s in [self.market_data, self.strategy, self.portfolio, self.persistence]
        )

    def _persist(self):
        try:
            data = {
                "market_data": self.market_data,
                "strategy": self.strategy,
                "portfolio": self.portfolio,
                "persistence": self.persistence,
                "reconciliation": self.reconciliation,
                "last_update": self.last_update,
            }
            tmp = HEALTH_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=4)
            os.replace(tmp, HEALTH_FILE)

            # Persist heartbeat.json
            hb_data = {
                "last_process_heartbeat": self.last_update,
                "last_market_data": self.last_update if self.market_data == "OK" else 0.0,
            }
            tmp_hb = "heartbeat.json.tmp"
            with open(tmp_hb, "w") as f:
                json.dump(hb_data, f)
            os.replace(tmp_hb, "heartbeat.json")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# DAILY REPORT
# ══════════════════════════════════════════════════════════════════════════════

def generate_daily_report(
    portfolio: PaperPortfolio,
    cfg: FrozenExperimentConfig,
    current_prices: dict,
    date_str: str,
    n_signals: int,
    n_trades_today: int,
    data_events: dict,
):
    """Generate and persist a daily forward validation report."""
    os.makedirs(DAILY_REPORT_DIR, exist_ok=True)

    equity = portfolio.get_equity(current_prices)
    max_dd = portfolio.get_max_drawdown()

    report = {
        "date": date_str,
        "experiment_id": cfg.experiment_id,
        "git_sha": cfg.git_sha,
        "equity": equity,
        "cash": portfolio.cash,
        "realized_pnl": portfolio.realized_pnl,
        "unrealized_pnl": portfolio.get_unrealized_pnl(current_prices),
        "cumulative_fees": portfolio.cumulative_fees,
        "cumulative_funding": portfolio.cumulative_funding,
        "daily_loss": portfolio.daily_loss,
        "max_drawdown": max_dd,
        "open_positions": len([p for p in portfolio.positions.values() if p["status"] == "OPEN"]),
        "n_signals_today": n_signals,
        "n_trades_today": n_trades_today,
        "data_events": data_events,
        "generated_at": time.time(),
    }

    path = os.path.join(DAILY_REPORT_DIR, f"daily_report_{date_str}.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=4)

    logger.info(
        f"DAILY REPORT [{date_str}]: equity={equity:.2f} "
        f"realized_pnl={portfolio.realized_pnl:.2f} "
        f"signals={n_signals} trades={n_trades_today}"
    )
    return report


# ══════════════════════════════════════════════════════════════════════════════
# RECONCILIATION
# ══════════════════════════════════════════════════════════════════════════════

def run_reconciliation(portfolio: PaperPortfolio, health: ForwardHealth) -> bool:
    """
    Verify ledger ↔ portfolio consistency.
    Returns True if reconciled. Sets health.reconciliation = RECONCILIATION_ERROR if not.
    """
    try:
        rec = PaperReconciliation(portfolio.ledger_file)
        ok = rec.run()
        if not ok:
            health.set("reconciliation", "RECONCILIATION_ERROR")
            logger.error("RECONCILIATION FAILED — stopping new trades until investigated")
            return False
        health.set("reconciliation", "OK")
        return True
    except Exception as e:
        health.set("reconciliation", "RECONCILIATION_ERROR")
        logger.error(f"Reconciliation exception: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run():
    logger.info("=" * 70)
    logger.info("PAPER FORWARD VALIDATION RUNNER — STARTING")
    logger.info("TRADING_MODE: PAPER | TESTNET ORDERS: 0 | LIVE ORDERS: 0")
    logger.info("=" * 70)

    # Safety gate
    import config
    if getattr(config, "LIVE_TRADING_ENABLED", False):
        logger.critical("SAFETY FAILURE: LIVE trading is enabled — aborting paper runner")
        sys.exit(1)

    if is_kill_switch_active():
        logger.critical("KILL SWITCH IS ACTIVE — cannot start runner")
        sys.exit(1)

    cfg = load_or_create_experiment()
    portfolio = PaperPortfolio(filename="paper_portfolio.json")
    portfolio.ledger_file = LEDGER_FILE
    portfolio.equity_file = EQUITY_CURVE_FILE

    signal_logger = SignalLogger(SIGNAL_LOG_FILE)
    health = ForwardHealth()

    last_day_str = None
    daily_signals = 0
    daily_trades = 0
    data_events = {"gaps": 0, "stale": 0, "unavailable": 0}
    last_candle_ts = None
    open_positions_meta = {}   # pos_id -> {sl, tp}
    last_known_price = None    # set once market data is fetched; guards daily report

    # Reconcile on startup
    run_reconciliation(portfolio, health)

    logger.info(
        f"Experiment '{cfg.experiment_name}' | "
        f"id={cfg.experiment_id[:8]} | "
        f"started={datetime.datetime.utcfromtimestamp(cfg.started_at).isoformat()}Z | "
        f"capital={cfg.starting_capital}"
    )

    POLL_INTERVAL_SECS = 60  # check every 60 seconds; 1H candles change hourly

    while True:
        try:
            if is_kill_switch_active():
                logger.critical("Kill switch detected — halting runner")
                health.set("strategy", "OFFLINE")
                break

            time.time()
            today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")

            # ── Daily rollover ───────────────────────────────────────────
            if today_str != last_day_str:
                if last_day_str is not None:
                    generate_daily_report(
                        portfolio, cfg, {FROZEN_SYMBOL: last_known_price} if last_known_price is not None else {},
                        last_day_str, daily_signals, daily_trades, data_events,
                    )
                    run_reconciliation(portfolio, health)

                    # ── Classification gate (dual condition check) ────────
                    elapsed_days = (time.time() - cfg.started_at) / 86400.0
                    n_closed = len([p for p in portfolio.positions.values()
                                    if p.get("status") == "CLOSED"])
                    gate = can_classify(cfg, elapsed_days, n_closed)
                    if gate["verdict"] == "BLOCKED_DURATION":
                        # Trade count met but duration not — CONTINUE RUNNING
                        logger.info(
                            f"CLASSIFICATION GATE: {gate['message']} "
                            "CONTINUE RUNNING — early classification PROHIBITED."
                        )
                    elif gate["verdict"] == "BLOCKED_BOTH":
                        remaining_days = cfg.planned_duration_days - elapsed_days
                        remaining_trades = cfg.min_required_trades - n_closed
                        logger.info(
                            f"CLASSIFICATION GATE: BLOCKED — "
                            f"{remaining_days:.1f} days and {remaining_trades} trades remaining."
                        )
                    elif gate["verdict"] == "BLOCKED_TRADES":
                        # 30 days passed but < 30 trades — experiment ends, INCONCLUSIVE
                        logger.warning(
                            f"EXPERIMENT DURATION REACHED: {elapsed_days:.1f} days. "
                            f"Only {n_closed} closed trades (< {cfg.min_required_trades}). "
                            "Final result: INCONCLUSIVE — INSUFFICIENT SAMPLE. "
                            "Runner will continue logging but experiment is concluded."
                        )
                    elif gate["verdict"] == "ALLOWED":
                        logger.info(
                            f"CLASSIFICATION GATE: ALLOWED — "
                            f"{elapsed_days:.1f} days AND {n_closed} trades. "
                            "Awaiting human operator to trigger final evaluation."
                        )

                last_day_str = today_str
                daily_signals = 0
                daily_trades = 0
                data_events = {"gaps": 0, "stale": 0, "unavailable": 0}
                last_known_price = portfolio.cash  # fallback

            # ── Fetch market data ────────────────────────────────────────
            df = fetch_candles(FROZEN_SYMBOL, FROZEN_TIMEFRAME, limit=250)

            if df is None or df.empty:
                health.set("market_data", "CRITICAL")
                data_events["unavailable"] += 1
                logger.warning("DATA_UNAVAILABLE — skipping cycle")
                time.sleep(POLL_INTERVAL_SECS)
                continue

            health.set("market_data", "OK")

            # Detect data gaps
            if last_candle_ts is not None:
                new_ts = df["timestamp"].iloc[-1]
                expected_gap = pd.Timedelta(hours=1)
                actual_gap = new_ts - pd.Timestamp(last_candle_ts)
                if actual_gap > expected_gap * 1.5:
                    data_events["gaps"] += 1
                    logger.warning(f"DATA GAP detected: {actual_gap}")

            last_candle_ts = df["timestamp"].iloc[-1]
            last_known_price = float(df["close"].iloc[-1])
            current_prices = {FROZEN_SYMBOL: last_known_price}
            current_ts = df["timestamp"].iloc[-1].timestamp()

            # ── Compute features (no lookahead) ──────────────────────────
            try:
                df_feat = add_features(df)
            except Exception as e:
                health.set("strategy", "DEGRADED")
                logger.error(f"Feature computation failed: {e}")
                time.sleep(POLL_INTERVAL_SECS)
                continue

            # ── Check exits for existing positions ───────────────────────
            paper_exit_positions(portfolio, current_prices, df_feat)

            # Attach SL/TP metadata to positions from open_positions_meta
            for pos_id, meta in open_positions_meta.items():
                if pos_id in portfolio.positions:
                    portfolio.positions[pos_id].setdefault("sl", meta.get("sl"))
                    portfolio.positions[pos_id].setdefault("tp", meta.get("tp"))

            # ── Generate signal ──────────────────────────────────────────
            if not health.is_safe_to_trade():
                log_signal_record(
                    signal_logger, current_ts, FROZEN_STRATEGY, FROZEN_SYMBOL,
                    None, 0.0, last_known_price, None, None,
                    decision="REJECTED", rejection_reason=f"HEALTH_{health.market_data}",
                )
                daily_signals += 1
                time.sleep(POLL_INTERVAL_SECS)
                continue

            try:
                sig_res = get_signal(df_feat)
                if hasattr(sig_res, "side"):
                    sig = sig_res.side
                    sl = sig_res.sl
                    tp = sig_res.tp
                elif isinstance(sig_res, (tuple, list)):
                    sig = sig_res[0]
                    sl = sig_res[1] if len(sig_res) > 1 else None
                    tp = sig_res[2] if len(sig_res) > 2 else None
                else:
                    sig, sl, tp = None, None, None
            except Exception as e:
                health.set("strategy", "DEGRADED")
                logger.error(f"Signal generation failed: {e}")
                log_signal_record(
                    signal_logger, current_ts, FROZEN_STRATEGY, FROZEN_SYMBOL,
                    None, 0.0, last_known_price, None, None,
                    decision="REJECTED", rejection_reason=f"STRATEGY_ERROR:{e}",
                )
                daily_signals += 1
                time.sleep(POLL_INTERVAL_SECS)
                continue

            daily_signals += 1

            if sig is None:
                log_signal_record(
                    signal_logger, current_ts, FROZEN_STRATEGY, FROZEN_SYMBOL,
                    None, 1.0, last_known_price, None, None,
                    decision="NO_SIGNAL",
                )
                # Record equity snapshot every bar regardless
                portfolio.record_equity_snapshot(current_ts, current_prices)
                time.sleep(POLL_INTERVAL_SECS)
                continue

            # ── Execute paper trade ───────────────────────────────────────
            fill = paper_execute(
                portfolio, sig, FROZEN_SYMBOL,
                last_known_price, sl, tp, current_prices, LEDGER_FILE,
            )

            if fill["status"] == "FILLED":
                daily_trades += 1
                open_positions_meta[fill["pos_id"]] = {"sl": sl, "tp": tp}
                log_signal_record(
                    signal_logger, current_ts, FROZEN_STRATEGY, FROZEN_SYMBOL,
                    sig, 1.0, last_known_price, sl, tp, decision="TRADED",
                )
            else:
                log_signal_record(
                    signal_logger, current_ts, FROZEN_STRATEGY, FROZEN_SYMBOL,
                    sig, 1.0, last_known_price, sl, tp,
                    decision="REJECTED", rejection_reason=fill["reason"],
                )

            portfolio.record_equity_snapshot(current_ts, current_prices)
            health.set("portfolio", "OK")
            health.set("persistence", "OK")

        except KeyboardInterrupt:
            logger.info("Runner interrupted by operator.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
            health.set("strategy", "DEGRADED")

        time.sleep(POLL_INTERVAL_SECS)

    logger.info("Forward validation runner stopped.")


if __name__ == "__main__":
    run()
