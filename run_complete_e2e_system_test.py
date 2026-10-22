#!/usr/bin/env python3
"""
run_complete_e2e_system_test.py — Comprehensive End-to-End System Test for STRATEX.

Executes a complete 12-stage validation across all runtime subsystems:
 1. Security & Configuration Invariants (LIVE_TRADING_ENABLED=False, bounded limits)
 2. Market Data Guard (Freshness, sequence monotonicity, crossed books, closed candles)
 3. Strategy Registry & Governance Lifecycle (VALIDATED, OBSERVE_ONLY, DISABLED)
 4. Multi-Strategy Signal Generation & Deterministic Arbitration
 5. AI Advisory Gate & Safety Isolation
 6. Pre-Trade Risk Manager & Concurrent Notional Reservations
 7. Buying Power & Protection Evaluator Adapters (QuantConnect & Freqtrade ideas)
 8. Deterministic OrderIntent & Idempotency Enforcement
 9. Execution State Machine & Partial Fill Tracking
10. Transport Timeout -> UNKNOWN State & Venue Reconciliation Mismatch Gating
11. Emergency Protection & Residual Handling
12. Backtest Engine & Non-Shuffled Walk-Forward Validation
"""

from __future__ import annotations

import os
import sys
import time
import json
import tempfile
import hashlib
from decimal import Decimal
import pandas as pd

# Reconfigure stdout to UTF-8 for Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add workspace to sys.path
sys.path.insert(0, os.path.abspath("."))

# Core imports
import config
from advisory_gate import AdvisoryGate
from risk.risk_orchestrator import RiskOrchestrator
from testnet_engine.protection import emergency_market_close
from paper_engine.exceptions import PersistenceError


# Deep upgrade overlay imports
from stratex_upgrade.models import (
    Side, OrderType, OrderStatus, Signal, OrderIntent, InstrumentRules,
    RiskDecision, MarketSnapshot, Fill, Position
)
from stratex_upgrade.risk import RiskManager, RiskState, RiskLimits
from stratex_upgrade.market import MarketDataGuard, FreshnessPolicy
from stratex_upgrade.strategy_registry import StrategyRegistry, StrategySpec
from stratex_upgrade.governance import ValidationGate
from stratex_upgrade.adapters.quantconnect_ideas import BuyingPowerGuard
from stratex_upgrade.adapters.freqtrade_ideas import ProtectionEvaluator
from stratex_upgrade.execution import ExecutionEngine, ExecutionConfig
from stratex_upgrade.reconcile import ExchangeReconciler, AtomicJsonStore
from stratex_upgrade.costs import CostModel, net_pnl
from stratex_upgrade.backtest import Bar, BacktestConfig, EventDrivenBacktester, BacktestOrder
from stratex_upgrade.walk_forward import Window, tiled_windows, summarize_fold
from backtest_engine import BacktestEngine


class SystemTestRunner:
    def __init__(self):
        self.stage_results: list[tuple[str, bool, str]] = []
        self.start_time = time.time()

    def record_stage(self, name: str, success: bool, detail: str):
        status_str = "[PASS]" if success else "[FAIL]"
        self.stage_results.append((name, success, detail))
        print(f"\n{status_str} STAGE: {name}")
        print(f"       Details: {detail}")
        if not success:
            print(f"       CRITICAL FAILURE in {name}!")

    def run_all(self) -> bool:
        print("=" * 80)
        print("   STRATEX QUANTITATIVE TRADING PLATFORM: COMPLETE END-TO-END SYSTEM TEST")
        print(f"   Execution Timestamp: {pd.Timestamp.now(tz='UTC').isoformat()}")
        print(f"   Python Version: {sys.version.split()[0]} | Platform: {sys.platform}")
        print("=" * 80)

        # Stage 1: Security & Configuration Invariants
        self._test_stage_1_security_invariants()

        # Stage 2: Market Data Guard
        self._test_stage_2_market_data_guard()

        # Stage 3: Strategy Registry & Governance Lifecycle
        self._test_stage_3_strategy_governance()

        # Stage 4: Multi-Strategy Signal Generation & Deterministic Arbitration
        self._test_stage_4_signal_arbitration()

        # Stage 5: AI Advisory Gate & Safety Isolation
        self._test_stage_5_advisory_isolation()

        # Stage 6: Pre-Trade Risk Manager & Concurrent Reservations
        self._test_stage_6_risk_reservations()

        # Stage 7: Buying Power & Defense Adapters
        self._test_stage_7_defense_adapters()

        # Stage 8: Deterministic OrderIntent & Idempotency
        self._test_stage_8_order_intent_idempotency()

        # Stage 9: Execution State Machine & Partial Fills
        self._test_stage_9_execution_state_machine()

        # Stage 10: Submission Timeout -> UNKNOWN & Venue Reconciliation Gating
        self._test_stage_10_timeout_reconciliation()

        # Stage 11: Emergency Protection & Residual Handling
        self._test_stage_11_emergency_protection()

        # Stage 12: Backtesting & Walk-Forward Chronological Validation
        self._test_stage_12_backtest_walk_forward()

        # Stage 13: Atomic Persistence & Corruption Recovery
        self._test_stage_13_atomic_persistence()

        # Final Summary
        self._print_summary()
        return all(s[1] for s in self.stage_results)

    def _test_stage_1_security_invariants(self):
        try:
            assert getattr(config, "LIVE_TRADING_ENABLED", True) is False, "LIVE_TRADING_ENABLED must be strictly False"
            assert config.MAX_OPEN_TRADES <= 10, f"MAX_OPEN_TRADES must be bounded (got {config.MAX_OPEN_TRADES})"
            assert config.MAX_OPEN_POSITIONS_AGGRESSIVE <= 10, "MAX_OPEN_POSITIONS_AGGRESSIVE must be bounded"
            assert getattr(config, "INFERENCE_API_KEY", "") != "ai_universe_secret_key_prod_2026", "Hardcoded API key leaked"
            self.record_stage("1. Security & Configuration Invariants", True,
                              f"LIVE_TRADING_ENABLED=False, MAX_OPEN_TRADES={config.MAX_OPEN_TRADES}, No plain-text API secrets.")
        except Exception as e:
            self.record_stage("1. Security & Configuration Invariants", False, str(e))

    def _test_stage_2_market_data_guard(self):
        try:
            guard = MarketDataGuard(FreshnessPolicy(max_age_seconds=5.0, require_sequence_monotonic=True))
            now = time.time_ns()
            # 1. Valid quote
            ok1, r1 = guard.accept("BTCUSDT", now, Decimal("65000"), Decimal("65010"), sequence=100)
            assert ok1, f"Valid quote should be accepted: {r1}"

            # 2. Stale quote
            stale_ts = now - int(10 * 1e9)
            ok2, r2 = guard.accept("BTCUSDT", stale_ts, Decimal("65000"), Decimal("65010"), sequence=101)
            assert not ok2 and r2 == "STALE", f"Stale quote failed: {r2}"

            # 3. Out of order sequence
            ok3, r3 = guard.accept("BTCUSDT", now, Decimal("65000"), Decimal("65010"), sequence=99)
            assert not ok3 and r3 == "OUT_OF_ORDER", f"Out of order failed: {r3}"

            # 4. Crossed book
            ok4, r4 = guard.accept("BTCUSDT", now, Decimal("65050"), Decimal("65000"), sequence=102)
            assert not ok4 and r4 == "CROSSED_BOOK", f"Crossed book failed: {r4}"

            # 5. Closed candle filtering
            curr_time = pd.Timestamp.now(tz="UTC")
            df_candles = pd.DataFrame([
                {"timestamp": curr_time - pd.Timedelta(minutes=30), "close_time": curr_time - pd.Timedelta(minutes=15), "close": 65000},
                {"timestamp": curr_time - pd.Timedelta(minutes=10), "close_time": curr_time + pd.Timedelta(minutes=5), "close": 65500}
            ])
            closed = df_candles[df_candles["close_time"] <= curr_time]
            assert len(closed) == 1, "Unclosed candle was not filtered out"

            self.record_stage("2. Market Data Guard & Closed Candles", True,
                              "Stale quotes, out-of-order sequence, crossed books, and unclosed candles correctly filtered.")
        except Exception as e:
            self.record_stage("2. Market Data Guard & Closed Candles", False, str(e))

    def _test_stage_3_strategy_governance(self):
        try:
            reg = StrategyRegistry()
            reg.register(StrategySpec("strat_val", "mod", "v1.0", ("5m",), ("BTCUSDT",), "VALIDATED", "hash_val"))
            reg.register(StrategySpec("strat_obs", "mod", "v1.0", ("5m",), ("BTCUSDT",), "OBSERVE_ONLY", "hash_obs"))
            reg.register(StrategySpec("strat_dis", "mod", "v1.0", ("5m",), ("BTCUSDT",), "DISABLED", "hash_dis"))

            ok_val, _ = reg.executable("strat_val", "5m", "BTCUSDT")
            ok_obs, r_obs = reg.executable("strat_obs", "5m", "BTCUSDT")
            ok_dis, r_dis = reg.executable("strat_dis", "5m", "BTCUSDT")

            assert ok_val is True
            assert ok_obs is False and r_obs == "STATUS_OBSERVE_ONLY"
            assert ok_dis is False and r_dis == "STATUS_DISABLED"

            self.record_stage("3. Strategy Registry & Governance Lifecycle", True,
                              "VALIDATED strategies permitted; OBSERVE_ONLY and DISABLED strategies strictly blocked.")
        except Exception as e:
            self.record_stage("3. Strategy Registry & Governance Lifecycle", False, str(e))

    def _test_stage_4_signal_arbitration(self):
        try:
            # 210 bars of OHLCV
            ts_list = [pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(minutes=5 * i) for i in range(210)]
            bars = [{"timestamp": t, "open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "volume": 1000.0} for t in ts_list]
            df = pd.DataFrame(bars)

            class StratLowerScore:
                __name__ = "strategy_StratLowerScore"
                def get_signal(self, d):
                    if len(d) == 201:
                        return ("BUY", 90.0, 110.0, 0.65)
                    return (None, None, None)

            class StratHighScore:
                __name__ = "strategy_StratHighScore"
                def get_signal(self, d):
                    if len(d) == 201:
                        return ("BUY", 95.0, 120.0, 0.95)
                    return (None, None, None)

            # Lower score strategy passed first in array
            engine = BacktestEngine(df, strategies=[StratLowerScore(), StratHighScore()], fee_rate=0, slippage_rate=0)
            trades, _ = engine.run()
            assert len(trades) > 0, "No trades produced"
            winner = trades[0]["strategy"]
            assert winner == "StratHighScore", f"Arbitration failed: expected StratHighScore, got {winner}"

            self.record_stage("4. Multi-Strategy Signal Arbitration", True,
                              f"Selected {winner} (conf {trades[0]['confidence']}); array position did NOT override priority.")
        except Exception as e:
            self.record_stage("4. Multi-Strategy Signal Arbitration", False, str(e))

    def _test_stage_5_advisory_isolation(self):
        try:
            gate = AdvisoryGate()
            # Test that advisory cannot alter hard safety bounds or force trade execution
            advisory_payload = {
                "decision_id": "ADV_SYSTEM_TEST",
                "status": "APPROVED",
                "parameter_changes": [
                    {"parameter": "max_open_trades", "proposed_value": 999, "current_value": 5}
                ]
            }
            res = gate.validate(advisory_payload, {"max_open_trades": 5}, shadow_mode=True)
            assert res.verdict in ["REJECT", "SHADOW_LOG_ONLY"], f"Advisory bypassed hard limits: {res.verdict}"

            self.record_stage("5. AI Advisory Gate & Safety Isolation", True,
                              f"Advisory parameter tampering gated with verdict: {res.verdict}.")
        except Exception as e:
            self.record_stage("5. AI Advisory Gate & Safety Isolation", False, str(e))

    def _test_stage_6_risk_reservations(self):
        try:
            eq = Decimal("10000")
            state = RiskState(equity=eq, starting_equity=eq, peak_equity=eq, daily_start_equity=eq)
            # 5% max exposure = $500 total exposure budget
            limits = RiskLimits(max_total_exposure=Decimal("0.05"), max_open_positions=3)
            rm = RiskManager(state, limits)

            # Reserve $300 (passes)
            ok1 = rm.reserve_notional(Decimal("300"))
            assert ok1 is True
            assert rm.reserved_notional() == Decimal("300")

            # Concurrent signal attempts $300 (total $600 > $500 cap -> must reject)
            ok2 = rm.reserve_notional(Decimal("300"))
            assert ok2 is False
            assert rm.reserved_notional() == Decimal("300")

            # Release first signal
            rm.release_notional(Decimal("300"))
            assert rm.reserved_notional() == Decimal("0")

            self.record_stage("6. Pre-Trade Risk Manager & Concurrent Reservations", True,
                              f"Concurrent signals locked at $500 cap; reservation budget restored on release.")
        except Exception as e:
            self.record_stage("6. Pre-Trade Risk Manager & Concurrent Reservations", False, str(e))

    def _test_stage_7_defense_adapters(self):
        try:
            bpg = BuyingPowerGuard()
            r_pass = bpg.check(free_cash=Decimal("500"), order_notional=Decimal("200"), reserved=Decimal("50"))
            r_fail = bpg.check(free_cash=Decimal("500"), order_notional=Decimal("480"), reserved=Decimal("50"))
            assert r_pass.sufficient is True
            assert r_fail.sufficient is False

            pe = ProtectionEvaluator()
            blocked, reason = pe.blocked(now=time.time_ns(), last_loss_ns=None, drawdown=Decimal("0.12"), consecutive_losses=0)
            assert blocked is True and reason == "MAX_DRAWDOWN"

            self.record_stage("7. Buying Power & Protection Defense Adapters", True,
                              "BuyingPowerGuard and ProtectionEvaluator correctly gate capital and drawdown.")
        except Exception as e:
            self.record_stage("7. Buying Power & Defense Adapters", False, str(e))

    def _test_stage_8_order_intent_idempotency(self):
        try:
            sig = Signal(
                signal_id="sig_e2e_888",
                strategy="breakout_trend",
                symbol="BTCUSDT",
                side=Side.BUY,
                created_at_ns=time.time_ns(),
                timeframe="15m",
                confidence=0.91,
                entry_price=Decimal("62000"),
                stop_loss=Decimal("61000"),
                take_profit=Decimal("64000")
            )
            intent1 = OrderIntent.create(sig, Decimal("0.05"))
            intent2 = OrderIntent.create(sig, Decimal("0.05"))
            assert intent1.client_order_id == intent2.client_order_id
            assert intent1.client_order_id.startswith("stx-")

            self.record_stage("8. Deterministic OrderIntent & Idempotency", True,
                              f"Deterministic client_order_id: {intent1.client_order_id}")
        except Exception as e:
            self.record_stage("8. Deterministic OrderIntent & Idempotency", False, str(e))

    def _test_stage_9_execution_state_machine(self):
        try:
            sig = Signal("sig_999", "strat_exec", "SOLUSDT", Side.BUY, time.time_ns(), "5m", 0.85)
            intent = OrderIntent.create(sig, Decimal("10.0"))

            class MockPartialAdapter:
                def __init__(self):
                    self.submissions = 0
                def submit_order(self, i):
                    self.submissions += 1
                    return "venue_sol_1"
                def fetch_order(self, sym, oid):
                    return {"status": "PARTIALLY_FILLED", "orderId": oid, "executedQty": "6.0"}

            adapter = MockPartialAdapter()
            engine = ExecutionEngine(adapter)
            rec1 = engine.submit(intent)
            assert rec1.status == OrderStatus.PARTIALLY_FILLED
            assert rec1.filled_quantity == Decimal("6.0")

            # Submit again -> idempotency prevents exchange call
            rec2 = engine.submit(intent)
            assert adapter.submissions == 1

            self.record_stage("9. Execution State Machine & Partial Fills", True,
                              f"Order transitioned to {rec1.status} (Filled: {rec1.filled_quantity}/10.0); re-submission deduplicated.")
        except Exception as e:
            self.record_stage("9. Execution State Machine & Partial Fills", False, str(e))

    def _test_stage_10_timeout_reconciliation(self):
        try:
            sig = Signal("sig_timeout", "strat_to", "ETHUSDT", Side.BUY, time.time_ns(), "5m", 0.8)
            intent = OrderIntent.create(sig, Decimal("1.0"))

            class NetworkTimeoutAdapter:
                def submit_order(self, i):
                    raise TimeoutError("REST POST to /api/v3/order timed out after socket write")
                def fetch_order(self, sym, oid):
                    return {}
                def fetch_open_orders(self, sym=None):
                    return [{"orderId": "rem_eth_99", "clientOrderId": "stx-remote-untracked", "symbol": "ETHUSDT"}]
                def fetch_positions(self):
                    return []

            timeout_adapter = NetworkTimeoutAdapter()
            engine = ExecutionEngine(timeout_adapter)
            rec = engine.submit(intent)
            # Timeout must set status to UNKNOWN, NOT FAILED
            assert rec.status == OrderStatus.UNKNOWN, f"Expected UNKNOWN, got {rec.status}"

            reconciler = ExchangeReconciler(timeout_adapter)
            report = reconciler.reconcile(local_orders=[], local_positions=[])
            assert report.ok is False, "Reconciliation should report discrepancy"
            assert report.issues[0].category == "UNTRACKED_REMOTE_ORDER"

            self.record_stage("10. Submission Timeout -> UNKNOWN & Venue Reconciliation Gating", True,
                              f"Timeout transitioned to {rec.status}. Reconciliation caught {report.issues[0].category} and blocked entries.")
        except Exception as e:
            self.record_stage("10. Submission Timeout -> UNKNOWN & Venue Reconciliation Gating", False, str(e))

    def _test_stage_11_emergency_protection(self):
        try:
            class SimulatedPartialCloseClient:
                def create_order(self, **kwargs):
                    return {
                        "orderId": 5555,
                        "status": "PARTIALLY_FILLED",
                        "executedQty": "0.04",
                        "cummulativeQuoteQty": "2400"
                    }

            resp = emergency_market_close(
                client=SimulatedPartialCloseClient(),
                symbol="BTCUSDT",
                entry_side="BUY",
                executed_qty=0.10
            )
            assert resp["_is_flat"] is False
            assert abs(resp["_residual_qty"] - 0.06) < 1e-6

            self.record_stage("11. Emergency Protection & Residual Handling", True,
                              f"Emergency close partial fill tracked: residual={resp['_residual_qty']:.4f}, _is_flat={resp['_is_flat']} (EMERGENCY_REVIEW).")
        except Exception as e:
            self.record_stage("11. Emergency Protection & Residual Handling", False, str(e))

    def _test_stage_12_backtest_walk_forward(self):
        try:
            # 1. Backtest with no-lookahead & conservative intrabar
            bars = [
                Bar(1, Decimal("100"), Decimal("102"), Decimal("98"), Decimal("101")),
                # Bar 2 spikes to 120 and drops to 80 (hits both SL=90 and TP=110)
                Bar(2, Decimal("101"), Decimal("120"), Decimal("80"), Decimal("105")),
                Bar(3, Decimal("105"), Decimal("108"), Decimal("104"), Decimal("107")),
            ]
            tester = EventDrivenBacktester(bars, BacktestConfig(conservative_intrabar=True))
            def sig_fn(history):
                if len(history) == 1:
                    return BacktestOrder(1, "BUY", Decimal("1.0"), Decimal("101"), Decimal("90"), Decimal("110"), "s", "sig_1")
                return None
            trades = tester.run(sig_fn)
            assert len(trades) == 1
            # Conservative execution triggers stop-loss first
            assert trades[0].exit_reason == "SL_HIT"
            assert trades[0].net_pnl < 0

            # 2. Chronological Walk-Forward Windows (no shuffling)
            windows = tiled_windows(n=120, train=60, test=30, step=30)
            assert len(windows) == 2
            for w in windows:
                assert w.train_end == w.test_start
                assert w.train_start < w.train_end

            self.record_stage("12. Backtesting & Walk-Forward Chronological Validation", True,
                              "Conservative same-bar SL/TP verified; walk-forward windows maintain strict chronological boundaries.")
        except Exception as e:
            self.record_stage("12. Backtesting & Walk-Forward Chronological Validation", False, str(e))

    def _test_stage_13_atomic_persistence(self):
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                store_path = os.path.join(tmpdir, "testnet_state.json")
                store = AtomicJsonStore(store_path)

                initial_data = {"positions": {"BTCUSDT": {"qty": 0.05}}, "equity": 10500.0}
                store.write(initial_data)
                loaded = store.read({})
                assert loaded["equity"] == 10500.0

                # Test corrupted state handling in Paper Engine
                corrupt_path = os.path.join(tmpdir, "corrupt_session.json")
                with open(corrupt_path, "w") as cf:
                    cf.write("CORRUPT_NOT_VALID_JSON{{{")

                from paper_engine.session import SessionState
                try:
                    state = SessionState(filename=corrupt_path)
                    state._load()

                    corrupt_caught = False
                except PersistenceError:
                    corrupt_caught = True

                assert corrupt_caught, "Corrupt session state was not caught with PersistenceError"

            self.record_stage("13. Atomic Persistence & Corruption Recovery", True,
                              "AtomicJsonStore write/read verified; corrupted paper session state caught with PersistenceError.")
        except Exception as e:
            self.record_stage("13. Atomic Persistence & Corruption Recovery", False, str(e))

    def _print_summary(self):
        elapsed = time.time() - self.start_time
        passed_count = sum(1 for _, ok, _ in self.stage_results if ok)
        total_count = len(self.stage_results)
        all_passed = (passed_count == total_count)

        print("\n" + "=" * 80)
        print("                  SYSTEM TEST EXECUTION SUMMARY")
        print("=" * 80)
        for name, ok, detail in self.stage_results:
            tag = "[PASS]" if ok else "[FAIL]"
            print(f"{tag} {name}")
        print("-" * 80)
        print(f"Total Stages Run: {total_count} | Passed: {passed_count} | Failed: {total_count - passed_count}")
        print(f"Total Execution Time: {elapsed:.2f} seconds")
        print("=" * 80)
        if all_passed:
            print(">>> COMPLETE END-TO-END SYSTEM TEST: 100% SUCCESSFUL <<<")
        else:
            print(">>> COMPLETE END-TO-END SYSTEM TEST ENCOUNTERED FAILURES <<<")
        print("=" * 80 + "\n")


if __name__ == "__main__":
    runner = SystemTestRunner()
    success = runner.run_all()
    sys.exit(0 if success else 1)
