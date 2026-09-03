"""Unit and integration tests for stratex_freqtrade_adapter."""

import os
import pytest
from datetime import datetime, timedelta, timezone

from stratex_freqtrade_adapter.parameters import (
    BaseParameter, IntParameter, RealParameter, CategoricalParameter
)
from stratex_freqtrade_adapter.protections import ProtectionManager, ProtectionDecision
from stratex_freqtrade_adapter.optimizer import StrategyOptimizer, OptimizationConfig
from stratex_freqtrade_adapter.walkforward import WalkForwardValidator, Window
from stratex_freqtrade_adapter.stratex_bridge import StratexStrategyBridge
from stratex_freqtrade_adapter.strategy_parameterizer import ParameterizedADXEMA


# ==============================================================================
# 1. PARAMETERS TESTS
# ==============================================================================

def test_int_parameter_valid_and_bounds():
    p = IntParameter(10, 30, default=20, step=2)
    assert p.value == 20
    assert p.low == 10
    assert p.high == 30
    assert p.step == 2
    assert 20 in p.range

    d = p.to_dict()
    assert d["type"] == "int"
    assert d["low"] == 10
    assert d["high"] == 30

    with pytest.raises(ValueError):
        IntParameter(50, 20, default=30)  # low > high


def test_real_parameter_valid_and_bounds():
    p = RealParameter(1.5, 4.5, default=3.0, step=0.25)
    assert p.value == 3.0
    assert p.low == 1.5
    assert p.high == 4.5
    assert p.step == 0.25

    d = p.to_dict()
    assert d["type"] == "real"

    with pytest.raises(ValueError):
        RealParameter(5.0, 2.0, default=3.0)


def test_categorical_parameter():
    p = CategoricalParameter(["1h", "4h", "1d"], default="4h")
    assert p.value == "4h"
    assert p.choices == ["1h", "4h", "1d"]

    d = p.to_dict()
    assert d["type"] == "categorical"

    with pytest.raises(ValueError):
        CategoricalParameter([], default="4h")  # empty choices

    with pytest.raises(ValueError):
        CategoricalParameter(["1h", "4h"], default="15m")  # default not in choices


# ==============================================================================
# 2. PROTECTIONS TESTS
# ==============================================================================

def test_protection_normal_passes():
    pm = ProtectionManager()
    res = pm.evaluate("BTCUSDT", history=[], equity=10000.0, peak_equity=10000.0)
    assert res.allowed is True
    assert res.reason == "PROTECTION_OK"


def test_protection_cooldown_blocks_and_expires():
    pm = ProtectionManager(cooldown_minutes=10)
    # Simulate SL_HIT trade
    trade = {"symbol": "BTCUSDT", "reason": "SL_HIT", "net_pnl": -50.0}
    pm.on_trade_closed(trade)

    res = pm.evaluate("BTCUSDT", history=[], equity=10000.0, peak_equity=10000.0)
    assert res.allowed is False
    assert res.reason == "COOLDOWN"
    assert res.cooldown_until is not None

    # Other symbol should pass
    res_other = pm.evaluate("ETHUSDT", history=[], equity=10000.0, peak_equity=10000.0)
    assert res_other.allowed is True

    # Manually expire cooldown
    pm._cooldowns["BTCUSDT"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    res_after = pm.evaluate("BTCUSDT", history=[], equity=10000.0, peak_equity=10000.0)
    assert res_after.allowed is True


def test_protection_stoploss_guard():
    pm = ProtectionManager(stoploss_guard_lookback=4, stoploss_guard_max_losses=2)
    history = [
        {"symbol": "BTCUSDT", "reason": "SL_HIT", "net_pnl": -30.0},
        {"symbol": "BTCUSDT", "reason": "TP_HIT", "net_pnl": 50.0},
        {"symbol": "BTCUSDT", "reason": "SL_HIT", "net_pnl": -25.0},
    ]
    res = pm.evaluate("BTCUSDT", history=history, equity=10000.0, peak_equity=10000.0)
    assert res.allowed is False
    assert res.reason == "STOPLOSS_GUARD"


def test_protection_low_profit_guard():
    pm = ProtectionManager(low_profit_lookback=5, low_profit_min_trades=3, low_profit_threshold=0.0)
    history = [
        {"symbol": "SOLUSDT", "reason": "TIME_EXIT", "net_pnl": -10.0},
        {"symbol": "SOLUSDT", "reason": "TIME_EXIT", "net_pnl": -15.0},
        {"symbol": "SOLUSDT", "reason": "TIME_EXIT", "net_pnl": 5.0},
    ]
    # Sum is -20 <= 0.0, trades count is 3 >= 3
    res = pm.evaluate("SOLUSDT", history=history, equity=10000.0, peak_equity=10000.0)
    assert res.allowed is False
    assert res.reason == "LOW_PROFIT_PAIR"


def test_protection_drawdown_guard():
    pm = ProtectionManager(max_drawdown_pct=0.05)  # 5% max drawdown
    # Current equity 9400, peak 10000 -> 6% drawdown
    res = pm.evaluate("BTCUSDT", history=[], equity=9400.0, peak_equity=10000.0)
    assert res.allowed is False
    assert res.reason == "MAX_DRAWDOWN"

    # Within drawdown limit (9700/10000 = 3% drawdown)
    res_pass = pm.evaluate("BTCUSDT", history=[], equity=9700.0, peak_equity=10000.0)
    assert res_pass.allowed is True


# ==============================================================================
# 3. OPTIMIZER OBJECTIVE TESTS
# ==============================================================================

def test_optimizer_objective_trade_count_penalty():
    cfg = OptimizationConfig(min_trades=20)
    # Result with only 5 trades
    res = {"trade_count": 5, "profit_factor": 2.5, "net_pnl": 500.0, "max_drawdown_pct": 0.02, "sharpe": 1.5}
    score = StrategyOptimizer.objective_from_result(res, cfg)
    assert score < -500000  # heavily penalized for < 20 trades


def test_optimizer_objective_drawdown_penalty():
    cfg = OptimizationConfig(min_trades=10, max_drawdown_pct=0.05)
    # Good PF and PnL, but unacceptable 15% drawdown
    res_bad_dd = {"trade_count": 15, "profit_factor": 1.5, "net_pnl": 200.0, "max_drawdown_pct": 0.15, "sharpe": 1.0}
    # Acceptable 3% drawdown
    res_good_dd = {"trade_count": 15, "profit_factor": 1.5, "net_pnl": 200.0, "max_drawdown_pct": 0.03, "sharpe": 1.0}

    score_bad = StrategyOptimizer.objective_from_result(res_bad_dd, cfg)
    score_good = StrategyOptimizer.objective_from_result(res_good_dd, cfg)
    assert score_good > score_bad


def test_optimizer_objective_weak_pf_penalty():
    cfg = OptimizationConfig(min_trades=10, target_profit_factor=1.20)
    res_weak = {"trade_count": 15, "profit_factor": 0.90, "net_pnl": -50.0, "max_drawdown_pct": 0.02, "sharpe": -0.5}
    res_strong = {"trade_count": 15, "profit_factor": 1.40, "net_pnl": 200.0, "max_drawdown_pct": 0.02, "sharpe": 1.2}

    score_weak = StrategyOptimizer.objective_from_result(res_weak, cfg)
    score_strong = StrategyOptimizer.objective_from_result(res_strong, cfg)
    assert score_strong > score_weak


# ==============================================================================
# 4. WALK-FORWARD VALIDATOR TESTS
# ==============================================================================

def test_walkforward_window_generation():
    wf = WalkForwardValidator(train_size=100, test_size=25, step_size=25)
    windows = wf.windows(175)
    assert len(windows) == 3
    assert windows[0] == Window(0, 100, 100, 125)
    assert windows[1] == Window(25, 125, 125, 150)
    assert windows[2] == Window(50, 150, 150, 175)


def test_walkforward_invalid_sizes():
    with pytest.raises(ValueError):
        WalkForwardValidator(train_size=-10, test_size=20)
    with pytest.raises(ValueError):
        WalkForwardValidator(train_size=100, test_size=0)
    with pytest.raises(ValueError):
        WalkForwardValidator(train_size=100, test_size=50, step_size=-5)


# ==============================================================================
# 5. BRIDGE INTEGRATION TESTS
# ==============================================================================

def test_bridge_delegates_to_backtest_engine(monkeypatch):
    class MockEngine:
        def __init__(self, df, strategies, **kwargs):
            self.kwargs = kwargs
            self.strategies = strategies
        def run(self):
            return [{"trade_id": "1", "net_pnl": 10.0, "gross_pnl": 12.0}], None

    strat = ParameterizedADXEMA()
    bridge = StratexStrategyBridge(strat, MockEngine)
    trades, _ = bridge.run(df=None, fee_rate=0.002, slippage_rate=0.001)
    assert len(trades) == 1
    assert trades[0]["net_pnl"] == 10.0


# ==============================================================================
# 6. SECURITY & SAFETY INVARIANT TESTS
# ==============================================================================

def test_research_mode_blocks_execution_policy():
    from execution import ExecutionPolicy
    os.environ["RESEARCH_MODE"] = "1"
    allowed, reason = ExecutionPolicy.can_place_order()
    assert allowed is False
    assert reason == "RESEARCH_BLOCKED"


def test_live_trading_permanently_forbidden(monkeypatch):
    import execution
    monkeypatch.setattr(execution, "TRADING_MODE", "LIVE")
    os.environ.pop("RESEARCH_MODE", None)
    allowed, reason = execution.ExecutionPolicy.can_place_order()
    assert allowed is False
    assert "LIVE" in reason or "FORBIDDEN" in reason

