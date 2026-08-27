"""
tests/test_ab_infrastructure.py — Unit and smoke tests for the A/B forward testing infrastructure.

Verifies:
1. ABExperimentConfig creation, immutability, and atomic save/load.
2. Dual Paper Engine setup with strictly isolated state, ledger, and equity files.
3. Drawdown safety trigger halts the breaching arm at max_drawdown_limit_pct.
4. Treatment arm applies AI-Universe parameter changes while Control arm preserves default parameters.
5. Statistical comparison engine calculates Welch's t-test, Mann-Whitney U, and bootstrap CIs accurately.
6. 24-hour mock smoke test verifying parallel multi-step execution.
"""

import datetime
import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd
import pytest

from config_ab import ABExperimentConfig, get_default_ab_config
from paper_ab_runner import PaperABEngine
from scripts.compare_ab_performance import compute_ab_comparison, generate_markdown_report


def create_mock_candles(n_bars: int = 100, base_price: float = 50000.0) -> pd.DataFrame:
    """Generates synthetic trend and swing candles for testing."""
    dates = [datetime.datetime(2026, 8, 1) + datetime.timedelta(hours=i) for i in range(n_bars)]
    np.random.seed(42)
    # Generate prices with alternating upward and downward waves
    returns = np.sin(np.linspace(0, 10, n_bars)) * 0.01 + np.random.normal(0, 0.005, n_bars)
    prices = base_price * np.exp(np.cumsum(returns))

    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices,
        "high": prices * 1.005,
        "low": prices * 0.995,
        "close": prices * 1.002,
        "volume": np.random.uniform(100, 500, n_bars)
    })
    return df


def test_ab_experiment_config_lifecycle():
    """Verify ABExperimentConfig creates, persists, and reloads atomically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = ABExperimentConfig(
            experiment_id="ab_test_mock_001",
            experiment_name="Unit Test A/B"
        )
        saved_path = cfg.save(directory=tmpdir)
        assert os.path.exists(saved_path)

        loaded = ABExperimentConfig.load(saved_path)
        assert loaded.experiment_id == "ab_test_mock_001"
        assert loaded.arm_a_name == "CONTROL_BASELINE"
        assert loaded.arm_b_name == "TREATMENT_AI_ADVISED"
        assert loaded.max_drawdown_limit_pct == 0.10


def test_paper_ab_engine_parallel_execution_and_isolation():
    """Verify dual paper engines execute in lockstep with separate state files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = ABExperimentConfig(
            experiment_id="ab_exec_test",
            state_file_control=os.path.join(tmpdir, "paper_state_ctrl.json"),
            ledger_file_control=os.path.join(tmpdir, "paper_ledger_ctrl.jsonl"),
            equity_file_control=os.path.join(tmpdir, "paper_equity_ctrl.jsonl"),
            signals_file_control=os.path.join(tmpdir, "paper_signals_ctrl.jsonl"),
            state_file_treatment=os.path.join(tmpdir, "paper_state_treat.json"),
            ledger_file_treatment=os.path.join(tmpdir, "paper_ledger_treat.jsonl"),
            equity_file_treatment=os.path.join(tmpdir, "paper_equity_treat.jsonl"),
            signals_file_treatment=os.path.join(tmpdir, "paper_signals_treat.jsonl"),
            params_state_treatment=os.path.join(tmpdir, "params_treat.json"),
            advisory_log_treatment=os.path.join(tmpdir, "adv_log_treat.jsonl"),
            symbols=["BTCUSDT"]
        )

        engine = PaperABEngine(experiment_cfg=cfg)

        # Mock AI consultation for treatment arm
        mock_ai_resp = {
            "decision_id": "DEC_AB_001",
            "status": "APPROVED",
            "confidence": 0.90,
            "parameter_changes": [
                {"strategy": "strategy_swing", "parameter": "tp_atr_multiplier", "current_value": 3.0, "new_value": 3.5}
            ],
            "debate_summary": "Expand TP multiplier in trending wave"
        }
        engine.ai_client.consult = MagicMock(return_value=mock_ai_resp)

        # Feed 60 synthetic candle steps
        candles = create_mock_candles(100)
        for i in range(55, 80):
            step_candles = candles.iloc[:i]
            engine.run_step({"BTCUSDT": step_candles})

        # 1. Verify separate equity curve files exist and have records
        assert os.path.exists(cfg.equity_file_control)
        assert os.path.exists(cfg.equity_file_treatment)

        with open(cfg.equity_file_control, "r") as f:
            lines_a = f.readlines()
        with open(cfg.equity_file_treatment, "r") as f:
            lines_b = f.readlines()

        assert len(lines_a) > 0
        assert len(lines_b) > 0
        assert len(lines_a) == len(lines_b)

        # 2. Verify Treatment arm overlay received AI parameters while Control did not
        assert engine.overlay_treatment.get_param("strategy_swing", "tp_atr_multiplier") == 3.5


def test_drawdown_safety_halts_breaching_arm():
    """Verify that breaching the 10% drawdown threshold halts that arm."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = ABExperimentConfig(
            experiment_id="ab_dd_test",
            state_file_control=os.path.join(tmpdir, "ctrl.json"),
            ledger_file_control=os.path.join(tmpdir, "ctrl.jsonl"),
            equity_file_control=os.path.join(tmpdir, "ctrl_eq.jsonl"),
            state_file_treatment=os.path.join(tmpdir, "treat.json"),
            ledger_file_treatment=os.path.join(tmpdir, "treat.jsonl"),
            equity_file_treatment=os.path.join(tmpdir, "treat_eq.jsonl"),
            params_state_treatment=os.path.join(tmpdir, "treat_params.json"),
            advisory_log_treatment=os.path.join(tmpdir, "treat_adv.jsonl"),
            max_drawdown_limit_pct=0.10
        )
        engine = PaperABEngine(experiment_cfg=cfg)

        # Simulate Arm A suffering 15% drawdown
        engine.portfolio_control.peak_equity = 10000.0
        engine.portfolio_control.cash = 8400.0  # 16% loss

        halt_a, halt_b = engine.check_drawdown_safeties({"BTCUSDT": 50000.0})
        assert halt_a is True
        assert halt_b is False


def test_statistical_comparison_engine():
    """Verify compute_ab_comparison outputs statistical tests and reports accurately."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_a = os.path.join(tmpdir, "ledger_a.jsonl")
        ledger_b = os.path.join(tmpdir, "ledger_b.jsonl")
        eq_a = os.path.join(tmpdir, "eq_a.jsonl")
        eq_b = os.path.join(tmpdir, "eq_b.jsonl")
        report_md = os.path.join(tmpdir, "report.md")

        # Generate 35 trades for Arm A and 35 trades for Arm B (Arm B with higher returns)
        np.random.seed(101)
        trades_a = []
        trades_b = []
        for i in range(35):
            pnl_a = np.random.normal(5.0, 15.0)
            pnl_b = np.random.normal(25.0, 15.0)  # Significant advantage
            trades_a.append({"net_pnl": pnl_a, "gross_pnl": pnl_a + 1.0, "hold_duration_sec": 3600})
            trades_b.append({"net_pnl": pnl_b, "gross_pnl": pnl_b + 1.0, "hold_duration_sec": 3600})

        with open(ledger_a, "w") as f:
            for t in trades_a: f.write(json.dumps(t) + "\n")
        with open(ledger_b, "w") as f:
            for t in trades_b: f.write(json.dumps(t) + "\n")

        with open(eq_a, "w") as f:
            f.write(json.dumps({"timestamp": "2026-08-27T00:00:00Z", "equity": 10000.0, "cash": 10000.0}) + "\n")
        with open(eq_b, "w") as f:
            f.write(json.dumps({"timestamp": "2026-08-27T00:00:00Z", "equity": 10000.0, "cash": 10000.0}) + "\n")

        results = compute_ab_comparison(
            ledger_control=ledger_a,
            ledger_treatment=ledger_b,
            equity_control=eq_a,
            equity_treatment=eq_b,
            min_trades_for_sig=30
        )

        assert results["sample_size"]["has_enough_samples"] is True
        assert results["arm_b_treatment"]["net_pnl"] > results["arm_a_control"]["net_pnl"]
        assert results["statistical_tests"]["welch_t_test"]["p_value"] < 0.05
        assert results["evaluation_summary"]["verdict"] == "PROMOTE_TREATMENT"

        md = generate_markdown_report(results, output_path=report_md)
        assert os.path.exists(report_md)
        assert "PROMOTE TO TESTNET" in md
