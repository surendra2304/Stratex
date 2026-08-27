"""
paper_ab_runner.py — Dual Paper Engine Runner for AI Advisory A/B Forward Testing.

Runs two parallel, strictly isolated paper execution loops against identical market data:
- Arm A (CONTROL): Static strategy parameters, zero AI advisory.
- Arm B (TREATMENT): Dynamic strategy parameters modulated by AI-Universe (applied within bounds).

Safety Invariants:
1. Max drawdown hard limit (10% per arm) -> Halts arm immediately if breached.
2. Independent state, ledger, and equity files.
3. Zero live order placement.
4. Identical timestamps, candle feeds, and cost accounting.
"""

import datetime
import json
import os
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from advisory_gate import AdvisoryGate
from advisory_ledger import append_advisory_entry
from advisory_params import AdvisoryParameterOverlay
from advisory_telemetry import build_telemetry_payload
from ai_universe_client import AIUniverseClient
import config
from config_ab import ABExperimentConfig, get_default_ab_config
from data_client import MarketDataClient
import features
from logger import get_logger
from metrics import calculate_drawdown, calculate_metrics
from paper_engine.portfolio import PaperPortfolio
import strategy_swing

logger = get_logger("paper_ab_runner")


class CostModel:
    def __init__(self, cfg: Dict[str, float]):
        self.entry_fee = cfg.get("entry_fee", 0.001)
        self.exit_fee = cfg.get("exit_fee", 0.001)
        self.entry_slip = cfg.get("entry_slip", 0.0005)
        self.exit_slip = cfg.get("exit_slip", 0.0005)
        self.spread = cfg.get("spread", 0.0001)


class PaperABEngine:
    """
    Manages execution of both Arm A (Control) and Arm B (Treatment) in lockstep.
    """

    def __init__(self, experiment_cfg: Optional[ABExperimentConfig] = None):
        self.cfg = experiment_cfg or get_default_ab_config()
        self.cost_model = CostModel(self.cfg.cost_config)

        # 1. Initialize Arm A (Control)
        self.portfolio_control = PaperPortfolio(
            filename=self.cfg.state_file_control,
            ledger_file=self.cfg.ledger_file_control,
            equity_file=self.cfg.equity_file_control
        )
        self.portfolio_control.starting_capital = self.cfg.initial_capital_per_arm

        # 2. Initialize Arm B (Treatment)
        self.portfolio_treatment = PaperPortfolio(
            filename=self.cfg.state_file_treatment,
            ledger_file=self.cfg.ledger_file_treatment,
            equity_file=self.cfg.equity_file_treatment
        )
        self.portfolio_treatment.starting_capital = self.cfg.initial_capital_per_arm

        # Treatment Advisory Overlay (Dynamic overrides for Arm B)
        self.overlay_treatment = AdvisoryParameterOverlay(state_file=self.cfg.params_state_treatment)
        self.advisory_gate = AdvisoryGate()
        self.ai_client = AIUniverseClient(
            base_url=getattr(config, "AI_UNIVERSE_BASE_URL", "http://localhost:8000"),
            timeout=int(getattr(config, "ADVISORY_TIMEOUT_SECONDS", 120)),
            api_key=getattr(config, "AI_UNIVERSE_API_KEY", "")
        )

        self.last_ai_consultation_time: Optional[datetime.datetime] = None
        self.market_client = MarketDataClient()
        self._stop_event = threading.Event()
        self.arm_a_halted = False
        self.arm_b_halted = False

    def check_drawdown_safeties(self, current_prices: Dict[str, float]) -> Tuple[bool, bool]:
        """
        Calculates current drawdown for both arms.
        Halts an arm if drawdown breaches max_drawdown_limit_pct (10%).
        """
        # Arm A
        eq_a = self.portfolio_control.get_equity(current_prices)
        peak_a = max(self.portfolio_control.peak_equity, eq_a)
        self.portfolio_control.peak_equity = peak_a
        dd_a = (peak_a - eq_a) / peak_a if peak_a > 0 else 0.0

        if dd_a >= self.cfg.max_drawdown_limit_pct and not self.arm_a_halted:
            logger.critical(f"[AB_RUNNER] 🚨 Arm A (Control) breached max drawdown limit ({dd_a*100:.2f}% >= {self.cfg.max_drawdown_limit_pct*100:.1f}%). Halting Arm A.")
            self.arm_a_halted = True

        # Arm B
        eq_b = self.portfolio_treatment.get_equity(current_prices)
        peak_b = max(self.portfolio_treatment.peak_equity, eq_b)
        self.portfolio_treatment.peak_equity = peak_b
        dd_b = (peak_b - eq_b) / peak_b if peak_b > 0 else 0.0

        if dd_b >= self.cfg.max_drawdown_limit_pct and not self.arm_b_halted:
            logger.critical(f"[AB_RUNNER] 🚨 Arm B (Treatment) breached max drawdown limit ({dd_b*100:.2f}% >= {self.cfg.max_drawdown_limit_pct*100:.1f}%). Halting Arm B.")
            self.arm_b_halted = True

        return self.arm_a_halted, self.arm_b_halted

    def consult_ai_for_treatment_arm(self, current_df: Optional[pd.DataFrame] = None) -> None:
        """
        Queries AI-Universe and applies validated parameter changes ONLY to Arm B overlay.
        """
        now = datetime.datetime.utcnow()
        if self.last_ai_consultation_time is not None:
            elapsed_hours = (now - self.last_ai_consultation_time).total_seconds() / 3600.0
            if elapsed_hours < 4.0:
                return  # Cooldown

        try:
            telemetry = build_telemetry_payload(
                trading_mode="PAPER",
                consultation_reason="SCHEDULED_AB_TREATMENT",
                current_df=current_df
            )
            # Override portfolio telemetry with Arm B specific portfolio stats
            telemetry["portfolio"]["equity"] = round(self.portfolio_treatment.cash + self.portfolio_treatment.used_margin, 2)
            telemetry["portfolio"]["realized_pnl"] = round(self.portfolio_treatment.realized_pnl, 2)
            telemetry["portfolio"]["open_positions"] = len(self.portfolio_treatment.positions)
            telemetry["runtime_overlay_active"] = self.overlay_treatment.get_state().get("active_overrides", {})

            decision = self.ai_client.consult(telemetry)
            if not decision:
                logger.warning("[AB_RUNNER] AI-Universe returned no decision for Treatment Arm. Retaining current parameters.")
                return

            current_params = self.overlay_treatment.get_current_params("strategy_swing")
            validation_result = self.advisory_gate.validate(
                decision=decision,
                current_params=current_params,
                last_applied_time=self.overlay_treatment._last_applied_time,
                shadow_mode=False  # Live application to Arm B Paper overlay
            )

            # Append to Arm B advisory ledger
            ledger_entry = {
                "timestamp": now.isoformat() + "Z",
                "decision_id": validation_result.decision_id,
                "consultation_reason": "SCHEDULED_AB_TREATMENT",
                "ai_status": decision.get("status", "UNKNOWN"),
                "confidence": decision.get("confidence", 0.0),
                "requested_changes": decision.get("parameter_changes", []),
                "verdict": validation_result.verdict,
                "applied_changes": validation_result.applied_changes,
                "rejected_changes": validation_result.rejected_changes,
                "ai_debate_summary": decision.get("debate_summary", ""),
                "regime_analysis": telemetry.get("market_regime", {}),
                "latency_ms": decision.get("latency_ms", 0.0),
                "shadow_mode": False,
                "bounds_checked": validation_result.bounds_checked
            }
            append_advisory_entry(ledger_entry, filepath=self.cfg.advisory_log_treatment)

            if validation_result.verdict == "APPLY" and validation_result.applied_changes:
                self.overlay_treatment.apply_changes(
                    decision_id=validation_result.decision_id,
                    changes=validation_result.applied_changes
                )
                logger.info(f"[AB_RUNNER] Applied {len(validation_result.applied_changes)} changes to Arm B (Treatment) overlay.")

            self.last_ai_consultation_time = now

        except Exception as e:
            logger.error(f"[AB_RUNNER] Error during Treatment Arm AI consultation: {e}")

    def evaluate_signals_for_arm(
        self,
        symbol: str,
        df: pd.DataFrame,
        is_treatment: bool
    ) -> Tuple[Optional[str], Optional[float], Optional[float], float]:
        """
        Computes trading signals.
        - If Control (is_treatment=False): Uses default strategy parameters.
        - If Treatment (is_treatment=True): Uses overlay-modified parameters.
        """
        if df is None or len(df) < 50:
            return None, None, None, 0.0

        df_feat = features.add_features(df.copy())
        
        # Determine strategy SL/TP multipliers
        if is_treatment:
            sl_mult = float(self.overlay_treatment.get_param("strategy_swing", "sl_atr_multiplier", 2.0))
            tp_mult = float(self.overlay_treatment.get_param("strategy_swing", "tp_atr_multiplier", 3.0))
        else:
            sl_mult = 2.0
            tp_mult = 3.0

        signal_res = strategy_swing.get_signal(df_feat)
        if not signal_res or not signal_res.side:
            return None, None, None, 0.0

        side = signal_res.side
        close_p = float(df_feat.iloc[-1]["close"])
        atr_val = float(df_feat.iloc[-1].get("atr", close_p * 0.01))

        if side in ["BUY", "LONG"]:
            sl = round(close_p - (atr_val * sl_mult), 4)
            tp = round(close_p + (atr_val * tp_mult), 4)
        else:
            sl = round(close_p + (atr_val * sl_mult), 4)
            tp = round(close_p - (atr_val * tp_mult), 4)

        conf = getattr(signal_res, "confidence", 0.5)
        return side, sl, tp, conf

    def process_order(
        self,
        portfolio: PaperPortfolio,
        symbol: str,
        direction: str,
        price: float,
        sl: Optional[float],
        tp: Optional[float]
    ) -> Optional[str]:
        """Executes a simulated paper order with fee and slippage attribution."""
        try:
            equity = portfolio.cash + portfolio.used_margin
            notional = min(equity * self.cfg.max_position_pct, 1000.0)
            if notional < 10.0 or portfolio.cash < notional:
                return None

            if len(portfolio.positions) >= self.cfg.max_simultaneous_positions:
                return None

            slip = price * self.cost_model.entry_slip
            eff_entry = price + slip if direction in ["BUY", "LONG"] else price - slip
            qty = notional / eff_entry

            entry_fee = notional * self.cost_model.entry_fee
            spread_cost = notional * self.cost_model.spread

            ev_id = str(uuid.uuid4())
            portfolio.allocate_margin(notional, ev_id)
            pos_id = str(uuid.uuid4())

            pos_data = {
                "position_id": pos_id,
                "symbol": symbol,
                "direction": direction,
                "entry_price": eff_entry,
                "quantity": qty,
                "notional": notional,
                "entry_time": time.time(),
                "sl": sl,
                "tp": tp,
                "status": "OPEN",
                "entry_fee": entry_fee,
                "spread_cost": spread_cost
            }
            portfolio.positions[pos_id] = pos_data
            portfolio.add_realized_pnl(-entry_fee - spread_cost, ev_id)
            return pos_id
        except Exception as e:
            logger.warning(f"[AB_RUNNER] Order processing error: {e}")
            return None

    def manage_positions_for_portfolio(
        self,
        portfolio: PaperPortfolio,
        current_prices: Dict[str, float],
        df_map: Dict[str, pd.DataFrame]
    ) -> None:
        """Evaluates open positions for SL, TP, or bar closure exits."""
        now = time.time()
        for pos_id, pos in list(portfolio.positions.items()):
            if pos.get("status") != "OPEN":
                continue

            sym = pos["symbol"]
            curr_p = current_prices.get(sym)
            if not curr_p and sym in df_map:
                curr_p = float(df_map[sym].iloc[-1]["close"])

            if not curr_p:
                continue

            direction = pos["direction"]
            entry_p = pos["entry_price"]
            qty = pos["quantity"]
            sl = pos.get("sl")
            tp = pos.get("tp")

            hit_exit = False
            exit_reason = ""
            exit_price = curr_p

            if direction in ["BUY", "LONG"]:
                if sl and curr_p <= sl:
                    hit_exit = True
                    exit_reason = "STOP_LOSS"
                    exit_price = sl
                elif tp and curr_p >= tp:
                    hit_exit = True
                    exit_reason = "TAKE_PROFIT"
                    exit_price = tp
            else:
                if sl and curr_p >= sl:
                    hit_exit = True
                    exit_reason = "STOP_LOSS"
                    exit_price = sl
                elif tp and curr_p <= tp:
                    hit_exit = True
                    exit_reason = "TAKE_PROFIT"
                    exit_price = tp

            # Maximum hold bars exit (e.g. 48 hours = 48 1h bars)
            if not hit_exit and (now - pos["entry_time"]) > (48 * 3600):
                hit_exit = True
                exit_reason = "TIME_LIMIT"
                exit_price = curr_p

            if hit_exit:
                slip = exit_price * self.cost_model.exit_slip
                eff_exit = exit_price - slip if direction in ["BUY", "LONG"] else exit_price + slip
                gross_pnl = (eff_exit - entry_p) * qty if direction in ["BUY", "LONG"] else (entry_p - eff_exit) * qty
                exit_fee = (eff_exit * qty) * self.cost_model.exit_fee
                net_pnl = gross_pnl - exit_fee

                # Settle in portfolio
                ev_close = str(uuid.uuid4())
                portfolio.release_margin(pos["notional"], ev_close)
                portfolio.add_realized_pnl(net_pnl, ev_close)
                pos["status"] = "CLOSED"
                pos["exit_price"] = eff_exit
                pos["exit_time"] = now
                pos["exit_reason"] = exit_reason
                pos["gross_pnl"] = gross_pnl
                pos["exit_fee"] = exit_fee
                pos["net_pnl"] = net_pnl

                # Append to trade ledger
                ledger_entry = {
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "position_id": pos_id,
                    "symbol": sym,
                    "direction": direction,
                    "entry_price": entry_p,
                    "exit_price": eff_exit,
                    "quantity": qty,
                    "gross_pnl": round(gross_pnl, 4),
                    "net_pnl": round(net_pnl, 4),
                    "exit_reason": exit_reason,
                    "hold_duration_sec": round(now - pos["entry_time"], 1)
                }
                with open(portfolio.ledger_file, "a", encoding="utf-8") as lf:
                    lf.write(json.dumps(ledger_entry) + "\n")

    def run_step(self, market_candles_map: Optional[Dict[str, pd.DataFrame]] = None) -> None:
        """
        Executes one unified market step for both Arm A and Arm B.
        """
        df_map: Dict[str, pd.DataFrame] = {}
        current_prices: Dict[str, float] = {}

        # 1. Fetch / Ingest identical candle data
        for sym in self.cfg.symbols:
            if market_candles_map and sym in market_candles_map:
                df = market_candles_map[sym]
            else:
                try:
                    df = self.market_client.get_klines_df(sym, self.cfg.timeframe, limit=100)
                except Exception:
                    df = None

            if df is not None and not df.empty:
                df_map[sym] = df
                current_prices[sym] = float(df.iloc[-1]["close"])

        if not df_map:
            logger.warning("[AB_RUNNER] No market data available for A/B step.")
            return

        # 2. Check Drawdown Safeties
        self.check_drawdown_safeties(current_prices)

        # 3. Consult AI for Treatment Arm (Arm B)
        first_df = next(iter(df_map.values()))
        self.consult_ai_for_treatment_arm(current_df=first_df)

        # 4. Position Lifecycle Management
        if not self.arm_a_halted:
            self.manage_positions_for_portfolio(self.portfolio_control, current_prices, df_map)
        if not self.arm_b_halted:
            self.manage_positions_for_portfolio(self.portfolio_treatment, current_prices, df_map)

        # 5. Signal Evaluation & Execution
        for sym, df in df_map.items():
            price = current_prices[sym]

            # Arm A (Control)
            if not self.arm_a_halted:
                side_a, sl_a, tp_a, conf_a = self.evaluate_signals_for_arm(sym, df, is_treatment=False)
                if side_a:
                    self.process_order(self.portfolio_control, sym, side_a, price, sl_a, tp_a)

            # Arm B (Treatment)
            if not self.arm_b_halted:
                side_b, sl_b, tp_b, conf_b = self.evaluate_signals_for_arm(sym, df, is_treatment=True)
                if side_b:
                    self.process_order(self.portfolio_treatment, sym, side_b, price, sl_b, tp_b)

        # 6. Record Synchronized Equity Snapshots
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        eq_a = self.portfolio_control.get_equity(current_prices)
        eq_b = self.portfolio_treatment.get_equity(current_prices)

        # Write Arm A equity
        with open(self.portfolio_control.equity_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": now_iso, "equity": round(eq_a, 2), "cash": round(self.portfolio_control.cash, 2)}) + "\n")

        # Write Arm B equity
        with open(self.portfolio_treatment.equity_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": now_iso, "equity": round(eq_b, 2), "cash": round(self.portfolio_treatment.cash, 2)}) + "\n")

    def run_live_loop(self, poll_interval_sec: int = 60) -> None:
        """Continuous live execution loop."""
        self.cfg.mark_started()
        self.cfg.save()
        logger.info(f"[AB_RUNNER] Started Dual A/B Paper Experiment '{self.cfg.experiment_id}'...")

        while not self._stop_event.is_set():
            try:
                self.run_step()
                if self.arm_a_halted and self.arm_b_halted:
                    logger.critical("[AB_RUNNER] Both arms have halted. Terminating experiment.")
                    self.cfg.mark_ended("BOTH_ARMS_HALTED_DRAWDOWN")
                    self.cfg.save()
                    break
            except Exception as e:
                logger.error(f"[AB_RUNNER] Error in A/B execution loop: {e}", exc_info=True)

            self._stop_event.wait(poll_interval_sec)

        logger.info("[AB_RUNNER] Dual A/B Paper Engine stopped.")

    def stop(self) -> None:
        self._stop_event.set()


# Module-level singleton supervisor
_ab_engine_instance: Optional[PaperABEngine] = None
_ab_engine_lock = threading.Lock()


def get_ab_engine() -> PaperABEngine:
    global _ab_engine_instance
    if _ab_engine_instance is None:
        with _ab_engine_lock:
            if _ab_engine_instance is None:
                _ab_engine_instance = PaperABEngine()
    return _ab_engine_instance
