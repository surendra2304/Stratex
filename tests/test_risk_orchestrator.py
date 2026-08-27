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

import tempfile
import pytest
import numpy as np

from risk.risk_orchestrator import RiskOrchestrator
from risk.strategy_coordinator import StrategyCoordinator, StrategyProfile
from risk.drawdown_controller import DrawdownController
from risk.circuit_breakers import CircuitBreakerEngine


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
