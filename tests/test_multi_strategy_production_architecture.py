"""
tests/test_multi_strategy_production_architecture.py
Regression test suite for the Multi-Strategy Production Execution Architecture.
"""

import numpy as np
import pandas as pd

from config_strategy import PRODUCTION_STRATEGY_REGISTRY
from strategy_adx_ema import SignalResult, add_features
from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.protection import compute_net_pnl
from testnet_engine.risk_gate import RiskGate


class TestProductionStrategyRegistry:
    def test_registry_classifications(self):
        """Ensure only strategies with defensible OOS proof are VALIDATED."""
        assert "adx_ema" in PRODUCTION_STRATEGY_REGISTRY
        assert PRODUCTION_STRATEGY_REGISTRY["adx_ema"]["status"] == "VALIDATED"
        assert PRODUCTION_STRATEGY_REGISTRY["adx_ema"]["timeframe"] == "4h"
        # V2-spot upgrade (2026-08): long-only crossover @ADX20 + BTC-regime gate,
        # 3×ATR SL/TP — see research/upgrade_2026_08/param_study.py.
        # OOS 2024-2026 (136 long trades, crossover+retest): win 0.551, PF 2.36.
        assert PRODUCTION_STRATEGY_REGISTRY["adx_ema"]["oos_win_rate_prior"] == 0.551
        assert PRODUCTION_STRATEGY_REGISTRY["adx_ema"]["rr_ratio"] == 1.0
        
        # Disabled strategies
        for strat in ["aggressor", "scalper", "supertrend", "swing", "ml"]:
            if strat in PRODUCTION_STRATEGY_REGISTRY:
                assert PRODUCTION_STRATEGY_REGISTRY[strat]["status"] == "DISABLED"

class TestStrategyEquivalenceAndIntegrity:
    def test_causal_indicators_no_lookahead(self):
        """Verify indicators do not look into future rows."""
        np.random.seed(123)
        n = 100
        close = 50000 + np.cumsum(np.random.randn(n) * 100)
        high = close + np.abs(np.random.randn(n) * 50)
        low = close - np.abs(np.random.randn(n) * 50)
        open_ = close - np.random.randn(n) * 20
        volume = np.random.randint(100, 1000, n).astype(float)
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
        df1 = add_features(df.iloc[:50].copy())
        df2 = add_features(df.copy())
        
        # Values on bar 49 should be identical whether we calculated 50 or 100 bars
        assert np.isclose(df1['ema_20'].iloc[49], df2['ema_20'].iloc[49])
        assert np.isclose(df1['ema_50'].iloc[49], df2['ema_50'].iloc[49])
        assert np.isclose(df1['ema_200'].iloc[49], df2['ema_200'].iloc[49])
        assert np.isclose(df1['atr_adx_ema'].iloc[49], df2['atr_adx_ema'].iloc[49])

    def test_rule_based_prior_preserved(self):
        """Rule-based strategy must output OOS win rate prior 0.494 and RR 1.5."""
        sig = SignalResult("BUY", 50000, 55000, "RULE_BASED", 0.494, 1.5)
        assert sig.strategy_type == "RULE_BASED"
        assert sig.win_rate_prior == 0.494
        assert sig.rr_ratio == 1.5

class TestProfitabilityGateMath:
    def test_profitability_exact_formula(self):
        gate = ProfitabilityGate()
        entry = 60000.0
        sl = 58200.0  # 2.0 * ATR where ATR = 900 (1.5% ATR)
        tp = 62700.0  # 3.0 * ATR
        
        sig = SignalResult("BUY", sl, tp, "RULE_BASED", 0.494, 1.5)
        passed, metrics = gate.evaluate_signal("BTCUSDT", "BUY", entry, sl, tp, sig)
        
        # Gross = (0.4940 * 0.0450) - (0.5060 * 0.0300) = 0.02223 - 0.01518 = +0.007050
        # Friction = 0.003100
        # Net = +0.007050 - 0.003100 = +0.003950 (+39.5 bps)
        assert passed is True
        assert np.isclose(metrics["expected_gross_return"], 0.007050, atol=1e-5)
        assert np.isclose(metrics["total_friction"], 0.003100, atol=1e-5)
        assert np.isclose(metrics["expected_net_return"], 0.003950, atol=1e-5)

    def test_negative_edge_rejection(self):
        gate = ProfitabilityGate()
        entry = 60000.0
        sl = 59850.0  # 0.25% risk (small ATR)
        tp = 60225.0  # 0.375% reward
        
        sig = SignalResult("BUY", sl, tp, "RULE_BASED", 0.494, 1.5)
        passed, metrics = gate.evaluate_signal("BTCUSDT", "BUY", entry, sl, tp, sig)
        
        # Low volatility creates net negative expectancy after 31 bps friction
        assert passed is False
        assert metrics["expected_net_return"] < 0.0005

class TestRiskGateLimits:
    def test_position_sizing_and_notional(self):
        rg = RiskGate()
        equity = 10000.0
        entry = 60000.0
        sl = 58200.0 # 1800 risk per unit
        
        filters = {"stepSize": 0.00001, "minNotional": 5.0}
        qty = rg.calculate_position_size(equity, entry, sl, filters)
        
        # Max risk = 10000 * 0.005 = $50. Qty = 50 / 1800 = 0.02777 BTC
        # Capped by single asset max exposure = 10000 * 0.02 = $200. Qty = 200 / 60000 = 0.00333 BTC
        notional = qty * entry
        assert notional <= 200.01
        assert notional >= 5.0

class TestProvenanceAndAccounting:
    def test_pnl_calculation_net_of_fees(self):
        gross_pnl, net_pnl = compute_net_pnl(
            entry_side="BUY",
            entry_qty=0.01,
            entry_price=60000.0,
            entry_fee=0.60,
            close_qty=0.01,
            close_price=63000.0,
            close_fee=0.63
        )
        assert np.isclose(gross_pnl, 30.0) # (63000 - 60000) * 0.01 = $30.00
        assert np.isclose(net_pnl, 28.77)  # 30.00 - 1.23 = $28.77
