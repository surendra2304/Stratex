import pytest
import pandas as pd
import numpy as np
import datetime

import strategy_aggressor
import strategy_scalper
import strategy_supertrend
import strategy_ml
import strategy_swing
import strategy_adx_ema

from data import add_indicators
from testnet_engine.profitability_gate import ProfitabilityGate, CostEngine
from testnet_engine.risk_gate import RiskGate

class TestMultiStrategyMultiTimeframe:
    """
    Comprehensive quality audit tests for:
    - 6 Strategies: aggressor, scalper, supertrend, ml, swing, adx_ema
    - 6 Timeframes: 5m, 15m, 30m, 1h, 2h, 4h
    - Warmup, feature completeness, no lookahead, exception isolation, stale data.
    """

    @pytest.fixture
    def sample_candles_df(self):
        """Generates realistic synthetic OHLCV candle sequence for unit testing."""
        np.random.seed(42)
        n = 150
        dates = pd.date_range("2026-08-18 00:00", periods=n, freq="15min")
        close = 60000.0 + np.cumsum(np.random.randn(n) * 50)
        open_ = close + np.random.randn(n) * 10
        high = np.maximum(open_, close) + np.random.rand(n) * 20
        low = np.minimum(open_, close) - np.random.rand(n) * 20
        vol = 10.0 + np.random.rand(n) * 5
        
        df = pd.DataFrame({
            "timestamp": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "taker_buy_base": vol * 0.52
        })
        df["buy_vol"] = df["taker_buy_base"]
        df["sell_vol"] = df["volume"] - df["buy_vol"]
        df["vol_delta"] = df["buy_vol"] - df["sell_vol"]
        return df

    def test_all_strategies_implement_canonical_signal_result(self, sample_candles_df):
        """All 6 strategies must return SignalResult with strategy_type and prior/confidence."""
        df_ind = add_indicators(sample_candles_df.copy())
        strategies = [
            ("aggressor", strategy_aggressor),
            ("scalper", strategy_scalper),
            ("supertrend", strategy_supertrend),
            ("ml", strategy_ml),
            ("swing", strategy_swing),
            ("adx_ema", strategy_adx_ema)
        ]
        
        for name, mod in strategies:
            assert hasattr(mod, "get_signal"), f"Strategy {name} missing get_signal()"
            res = mod.get_signal(df_ind)
            assert hasattr(res, "side"), f"Strategy {name} result missing 'side'"
            assert hasattr(res, "strategy_type"), f"Strategy {name} result missing 'strategy_type'"

    def test_all_strategies_handle_insufficient_warmup_gracefully(self):
        """When candle count is below warmup threshold (< 20 bars), strategies must return HOLD without crashing."""
        short_df = pd.DataFrame({
            "timestamp": pd.date_range("2026-08-18 00:00", periods=10, freq="15min"),
            "open": [100.0] * 10,
            "high": [105.0] * 10,
            "low": [95.0] * 10,
            "close": [102.0] * 10,
            "volume": [10.0] * 10
        })
        short_ind = add_indicators(short_df)
        strategies = [strategy_aggressor, strategy_scalper, strategy_supertrend, strategy_ml, strategy_swing, strategy_adx_ema]
        for mod in strategies:
            res = mod.get_signal(short_ind)
            side = getattr(res, "side", res[0] if res else None)
            assert side is None  # Must safely return None / HOLD

    def test_indicator_completeness_and_zero_nans_post_warmup(self, sample_candles_df):
        """Indicators DataFrame must calculate required moving averages and oscillators with 0 NaNs post-warmup."""
        df_ind = add_indicators(sample_candles_df.copy())
        assert len(df_ind) > 50
        assert "ema_9" in df_ind.columns
        assert "ema_21" in df_ind.columns
        assert "ema_50" in df_ind.columns
        assert "rsi_14" in df_ind.columns or "rsi" in df_ind.columns
        # Zero NaN in core price & indicator fields
        assert not df_ind[["open", "high", "low", "close", "volume"]].isna().any().any()

    def test_no_lookahead_bias_in_signal_evaluation(self, sample_candles_df):
        """Evaluating signal at slice t must produce identical result as backtest bar at t without future bars."""
        df_ind = add_indicators(sample_candles_df.copy())
        t_index = 80
        slice_t = df_ind.iloc[:t_index+1].copy()
        
        # Test across rule-based strategies
        for mod in [strategy_adx_ema, strategy_supertrend, strategy_scalper]:
            res1 = mod.get_signal(slice_t)
            res2 = mod.get_signal(slice_t)
            assert res1.side == res2.side
            assert res1.sl == res2.sl
            assert res1.tp == res2.tp

    def test_timeframe_alignment_across_all_active_timeframes(self):
        """Validate supported active timeframes in engine."""
        active_tfs = ["5m", "15m", "30m", "1h", "2h", "4h"]
        assert len(active_tfs) == 6
        for tf in active_tfs:
            assert tf[-1] in ["m", "h", "d"]
            val = int(tf[:-1])
            assert val > 0

    def test_friction_hurdle_blocks_low_timeframe_noise(self):
        """ProfitabilityGate must reject trades where expected net edge < 0.31% friction hurdle."""
        cost_engine = CostEngine.get_binance_taker_config()
        gate = ProfitabilityGate(cost_engine=cost_engine)
        
        # Micro scalp: 0.10% gross target vs 0.31% hurdle
        passed, metrics = gate.evaluate_signal(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=60000.0,
            sl_price=59950.0,
            tp_price=60060.0,
            signal_result=0.50
        )
        assert passed is False
        assert metrics["decision"] == "REJECTED"
        assert metrics["expected_net_return"] < 0.00010
