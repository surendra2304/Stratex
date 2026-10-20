"""
tests/test_risk_orchestrator.py — Tests for Risk Orchestrator, Strategy Coordinator, Drawdown Controller & Circuit Breakers.

Verifies:
1. RiskOrchestrator:
   - 95% Historical VaR and CVaR / Expected Shortfall calculation.
   - Correlation-adjusted portfolio heat calculation.
   - Dynamic sizing: Heat > 70% reduces sizing by 50%, Heat > 85% blocks new entries.
   - Drawdown protection: Drawdown > 5% reduces sizing by 30%, Drawdown > 12% flattens and halts.
2. StrategyCoordinator:
   - Sharpe-based allocation weights with 25% single-strategy cap.
   - Regime boosts (Trending boosts trend by 20%, Ranging boosts mean-reversion by 20%).
   - Signal conflict resolution: Higher-Sharpe strategy wins with 50% position sizing.
3. DrawdownController:
   - Warning (5%), Action (8%), Critical (12%) thresholds.
   - 48h clean paper validation recovery protocol and progressive 25% re-entry tiers.
4. CircuitBreakerEngine:
   - Volatility 4σ breaker (1h cooldown).
   - Correlation breakdown (< 0.20).
   - Execution slippage (3 consecutive breaches > 3x normal).
   - API latency (> 2.0s median).
"""

import json
import tempfile

import numpy as np
import pytest

from risk.circuit_breakers import CircuitBreakerEngine
from risk.drawdown_controller import DrawdownController
from risk.risk_orchestrator import RiskOrchestrator, build_measured_snapshot
from risk.strategy_coordinator import StrategyCoordinator


def test_circuit_breakers_engine():
    engine = CircuitBreakerEngine()

    # 1. Volatility Breaker (> 4 sigma)
    hist_vols = [0.02] * 20
    tripped = engine.check_volatility_circuit_breaker(current_24h_vol=0.15, historical_vols=hist_vols)
    assert tripped is True
    assert engine.breakers["volatility"].is_tripped is True

    # 2. Correlation Breakdown (< 0.20)
    cb_corr = engine.check_correlation_breakdown(avg_strategy_corr=0.15)
    assert cb_corr is True

    # 3. Execution Quality (3 consecutive slippages > 3x normal)
    assert engine.record_order_execution_slippage(18.0, normal_slippage_bps=5.0) is False
    assert engine.record_order_execution_slippage(16.0, normal_slippage_bps=5.0) is False
    assert engine.record_order_execution_slippage(20.0, normal_slippage_bps=5.0) is True  # 3rd consecutive
    assert engine.breakers["execution_quality"].is_tripped is True

    # 4. API Latency (> 2.0s median)
    for _ in range(5):
        engine.record_api_latency(2.5)
    assert engine.breakers["api_latency"].is_tripped is True


def test_drawdown_controller_levels_and_recovery():
    ctrl = DrawdownController(initial_equity=10000.0)

    # 1. Nominal
    st = ctrl.update_equity(10000.0)
    assert st.level == "NOMINAL"
    assert st.position_size_multiplier == 1.0

    # 2. Warning Level (5% DD -> 70% sizing)
    st = ctrl.update_equity(9450.0)  # 5.5% DD
    assert st.level == "WARNING_5PCT"
    assert st.position_size_multiplier == 0.70

    # 3. Action Level (8% DD -> 50% sizing, halt entries)
    st = ctrl.update_equity(9100.0)  # 9.0% DD
    assert st.level == "ACTION_8PCT"
    assert st.allow_new_entries is False

    # 4. Critical Level (12% DD -> 0% sizing, full halt)
    st = ctrl.update_equity(8700.0)  # 13.0% DD
    assert st.level == "CRITICAL_12PCT"
    assert st.in_recovery_mode is True

    # 5. Recovery Protocol (48h clean paper requirement)
    resumed, tier = ctrl.progress_recovery_paper_trading(hours_elapsed=24.0)
    assert resumed is False

    resumed, tier = ctrl.progress_recovery_paper_trading(hours_elapsed=25.0)  # Total 49h
    assert resumed is True
    assert tier == 0.25  # 25% progressive tier

    # Advance clean week
    new_tier = ctrl.advance_progressive_reentry()
    assert new_tier == 0.50


def test_strategy_coordinator_allocation_and_conflicts():
    coord = StrategyCoordinator()

    # 1. Rebalance Allocations under Trending Regime
    weights = coord.rebalance_allocations(current_regime="TRENDING")
    assert sum(weights.values()) <= 1.0
    assert all(w <= 0.25 for w in weights.values())  # Max single-strategy cap 25%

    # 2. Signal Conflict Resolution
    signals = {
        "strategy_supertrend": 1,  # BUY (Sharpe 1.85)
        "strategy_scalper": -1     # SELL (Sharpe 1.45)
    }
    direction, winner, mult = coord.resolve_signal_conflict("BTC/USDT", signals)
    assert direction == 1  # Supertrend won
    assert winner == "strategy_supertrend"
    assert mult == 0.50    # Sizing halved due to conflict


def test_risk_orchestrator_heat_and_gating():
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = f"{tmpdir}/risk_orch.jsonl"
        orch = RiskOrchestrator(log_file=log_file, initial_equity=5000.0)

        # 1. VaR & CVaR calculation
        returns = [-3.5, -2.1, -1.8, -0.5, 0.4, 1.2, 1.5, 2.0, 2.8, -4.2]
        var, cvar = orch.calculate_var_and_cvar(returns)
        assert var > 0
        assert cvar >= var

        # 2. Portfolio Heat calculation
        positions = [{"symbol": "BTC/USDT", "risk_pct": 2.0}, {"symbol": "ETH/USDT", "risk_pct": 1.5}]
        heat = orch.calculate_portfolio_heat(positions, correlation_matrix=np.array([[1.0, 0.8], [0.8, 1.0]]))
        assert heat > 0

        # 3. Dynamic Entry Sizing (Heat > 70% -> 50% size)
        allow, size, reason = orch.evaluate_new_entry_risk(
            symbol="BTC/USDT",
            strategy="supertrend",
            requested_size=1.0,
            current_equity=5000.0,
            portfolio_heat_pct=75.0
        )
        assert allow is True
        assert size == 0.50
        assert "REDUCED_50PCT" in reason

        # 4. Entry Block (Heat >= 85%)
        allow, size, reason = orch.evaluate_new_entry_risk(
            symbol="BTC/USDT",
            strategy="supertrend",
            requested_size=1.0,
            current_equity=5000.0,
            portfolio_heat_pct=88.0
        )
        assert allow is False
        assert size == 0.0
        assert "HEAT_EXCEEDED" in reason


def test_measured_snapshot_does_not_treat_missing_active_trades_as_zero_heat(tmp_path):
    equity_history = tmp_path / "equity.jsonl"
    equity_history.write_text('{"equity": 5000.0}\n', encoding="utf-8")

    snapshot = build_measured_snapshot(
        str(equity_history),
        str(tmp_path / "missing-active-trades.json"),
    )

    assert snapshot["portfolio_heat_pct"] is None
    assert snapshot["open_positions_measured"] == 0
    assert snapshot["unmeasured_reasons"]["portfolio_heat_pct"] == "Active trades file not found."


def test_measured_snapshot_keeps_heat_unknown_when_any_trade_is_malformed(tmp_path):
    equity_history = tmp_path / "equity.jsonl"
    equity_history.write_text('{"equity": 5000.0}\n', encoding="utf-8")
    active_trades = tmp_path / "active-trades.json"
    active_trades.write_text(
        '[{"quantity": 0.01, "entry_price": 50000.0, "sl_price": 49000.0},'
        '{"symbol": "ETHUSDT", "entry_price": 3000.0}]',
        encoding="utf-8",
    )

    snapshot = build_measured_snapshot(str(equity_history), str(active_trades))

    assert snapshot["portfolio_heat_pct"] is None
    assert snapshot["open_positions_measured"] == 1
    assert snapshot["unmeasured_reasons"]["portfolio_heat_pct"] == (
        "Active trades row 2 has invalid or missing quantity."
    )


def test_measured_snapshot_treats_an_empty_trade_list_as_zero_heat(tmp_path):
    equity_history = tmp_path / "equity.jsonl"
    equity_history.write_text('{"equity": 5000.0}\n', encoding="utf-8")
    active_trades = tmp_path / "active-trades.json"
    active_trades.write_text("[]", encoding="utf-8")

    snapshot = build_measured_snapshot(str(equity_history), str(active_trades))

    assert snapshot["portfolio_heat_pct"] == 0.0
    assert snapshot["open_positions_measured"] == 0
    assert "portfolio_heat_pct" not in snapshot["unmeasured_reasons"]


@pytest.mark.parametrize(
    ("trade_row", "expected_reason"),
    [
        (
            {"quantity": 1, "sl_price": 90},
            "Active trades row 1 has invalid or missing entry_price.",
        ),
        (
            {"quantity": 1, "entry_price": "invalid"},
            "Active trades row 1 has invalid or missing entry_price.",
        ),
        (
            {"quantity": 1, "entry_price": 100, "sl_price": "invalid"},
            "Active trades row 1 has invalid sl_price.",
        ),
        (None, "Active trades row 1 is not an object."),
    ],
)
def test_measured_snapshot_fails_closed_for_malformed_trade_rows(
    tmp_path, trade_row, expected_reason
):
    equity_history = tmp_path / "equity.jsonl"
    equity_history.write_text('{"equity": 5000.0}\n', encoding="utf-8")
    active_trades = tmp_path / "active-trades.json"
    active_trades.write_text(json.dumps([trade_row]), encoding="utf-8")

    snapshot = build_measured_snapshot(str(equity_history), str(active_trades))

    assert snapshot["portfolio_heat_pct"] is None
    assert snapshot["open_positions_measured"] == 0
    assert snapshot["unmeasured_reasons"]["portfolio_heat_pct"] == expected_reason


@pytest.mark.parametrize("stop", [None, 0])
def test_measured_snapshot_uses_full_notional_for_unprotected_positions(tmp_path, stop):
    equity_history = tmp_path / "equity.jsonl"
    equity_history.write_text('{"equity": 5000.0}\n', encoding="utf-8")
    active_trades = tmp_path / "active-trades.json"
    active_trades.write_text(
        json.dumps([{"quantity": 1, "entry_price": 100, "sl_price": stop}]),
        encoding="utf-8",
    )

    snapshot = build_measured_snapshot(str(equity_history), str(active_trades))

    assert snapshot["open_positions_measured"] == 1
    assert snapshot["portfolio_heat_pct"] == 18.4


@pytest.mark.parametrize(
    ("bad_record", "expected_skipped"),
    [("corrupt", 1), ('{"equity": 0}', 0), ('{"balance": "invalid"}', 0)],
)
def test_measured_snapshot_does_not_bridge_invalid_equity_history_records(
    tmp_path, bad_record, expected_skipped
):
    equity_history = tmp_path / "equity.jsonl"
    equity_history.write_text(
        f'{{"equity": 10000}}\n{{"equity": 20000}}\n{bad_record}\n{{"equity": 10000}}\n',
        encoding="utf-8",
    )
    active_trades = tmp_path / "active-trades.json"
    active_trades.write_text("[]", encoding="utf-8")

    snapshot = build_measured_snapshot(str(equity_history), str(active_trades))

    assert snapshot["equity_history_skipped_lines"] == expected_skipped
    assert snapshot["var_95_pct"] is None
    assert snapshot["cvar_95_pct"] is None
    assert snapshot["drawdown_metrics"] is None
    assert snapshot["unmeasured_reasons"]["var_95_pct"] == (
        "Equity history contains skipped lines or invalid equity records; returns and drawdown are not measured."
    )
    assert snapshot["unmeasured_reasons"]["drawdown_metrics"] == (
        "Equity history contains skipped lines or invalid equity records; returns and drawdown are not measured."
    )


@pytest.mark.parametrize(
    ("tail", "terminated", "expected_measured"),
    [("{torn", False, True), ("{corrupt}", True, False)],
)
def test_measured_snapshot_only_recovers_unterminated_corrupt_tail(
    tmp_path, tail, terminated, expected_measured
):
    history = tmp_path / "equity.jsonl"
    records = [json.dumps({"equity": 10000 - i * 5}) for i in range(15)]
    history.write_text(
        "\n".join(records + [tail]) + ("\n" if terminated else ""),
        encoding="utf-8",
    )
    active_trades = tmp_path / "active-trades.json"
    active_trades.write_text("[]", encoding="utf-8")

    snapshot = build_measured_snapshot(str(history), str(active_trades))

    assert snapshot["equity_history_skipped_lines"] == 1
    assert (snapshot["var_95_pct"] is not None) is expected_measured
    assert (snapshot["cvar_95_pct"] is not None) is expected_measured
    assert (snapshot["drawdown_metrics"] is not None) is expected_measured
    assert snapshot["portfolio_heat_pct"] == (0.0 if expected_measured else None)


def test_measured_snapshot_does_not_recover_interior_corruption_with_torn_tail(tmp_path):
    history = tmp_path / "equity.jsonl"
    records = [json.dumps({"equity": 10000 - i * 5}) for i in range(15)]
    records.insert(5, "{interior corruption}")
    history.write_text("\n".join(records + ["{torn tail}"]), encoding="utf-8")
    active_trades = tmp_path / "active-trades.json"
    active_trades.write_text("[]", encoding="utf-8")

    snapshot = build_measured_snapshot(str(history), str(active_trades))

    assert snapshot["equity_history_skipped_lines"] == 2
    assert snapshot["var_95_pct"] is None
    assert snapshot["cvar_95_pct"] is None
    assert snapshot["drawdown_metrics"] is None
    assert snapshot["portfolio_heat_pct"] is None


def test_measured_snapshot_ignores_corruption_before_selected_recent_window(tmp_path):
    history = tmp_path / "equity.jsonl"
    records = [json.dumps({"equity": value}) for value in (10000, 9900)]
    records.extend(
        ["{old corruption}"]
        + [json.dumps({"equity": value}) for value in (9800, 9700, 9600, 9500, 9400)]
    )
    history.write_text("\n".join(records) + "\n", encoding="utf-8")
    active_trades = tmp_path / "active-trades.json"
    active_trades.write_text("[]", encoding="utf-8")

    snapshot = build_measured_snapshot(
        str(history), str(active_trades), max_points=3
    )

    assert snapshot["equity_points"] == 3
    assert snapshot["equity_history_skipped_lines"] == 1
    assert snapshot["drawdown_metrics"] is not None
    assert snapshot["portfolio_heat_pct"] == 0.0


def test_measured_snapshot_rejects_corruption_inside_selected_recent_window(tmp_path):
    history = tmp_path / "equity.jsonl"
    records = [json.dumps({"equity": value}) for value in (10000, 9900, 9800)]
    records.extend(
        ["{recent corruption}"]
        + [json.dumps({"equity": value}) for value in (9700, 9600)]
    )
    history.write_text("\n".join(records) + "\n", encoding="utf-8")
    active_trades = tmp_path / "active-trades.json"
    active_trades.write_text("[]", encoding="utf-8")

    snapshot = build_measured_snapshot(
        str(history), str(active_trades), max_points=3
    )

    assert snapshot["equity_history_skipped_lines"] == 1
    assert snapshot["var_95_pct"] is None
    assert snapshot["cvar_95_pct"] is None
    assert snapshot["drawdown_metrics"] is None
    assert snapshot["portfolio_heat_pct"] is None
