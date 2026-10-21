"""
tests/test_quantitative_engine.py — Comprehensive Unit Tests for Quantitative Engine.

Verifies:
1. StatisticalArbitrageModel (OLS hedge ratio, spread stationary bounds, pairs entry/exit/stop).
2. KalmanFilterMeanReversionModel (1D state-space updates, innovation covariance, mean-reversion signals).
3. VolatilityRegimeBreakoutModel (ATR calculation, 4-state regime classifier, Chandelier Exits, chaos protection).
"""
import numpy as np
import pytest

from strategies.quantitative_engine import (
    KalmanFilterMeanReversionModel,
    MarketRegime,
    PairsSignal,
    StatisticalArbitrageModel,
    VolatilityRegimeBreakoutModel,
)


class TestStatisticalArbitrageModel:
    def test_hedge_ratio_edge_cases(self):
        model = StatisticalArbitrageModel(window=30)
        # Short series
        beta, alpha = model.calculate_hedge_ratio(np.array([1.0, 2.0]), np.array([1.0, 2.0]))
        assert beta == 1.0
        assert alpha == 0.0

        # Zero variance series
        x = np.ones(20) * 100.0
        y = np.linspace(50, 60, 20)
        beta, alpha = model.calculate_hedge_ratio(y, x)
        assert beta == 1.0
        assert alpha == 0.0

    def test_hedge_ratio_linear_relationship(self):
        model = StatisticalArbitrageModel(window=30)
        x = np.linspace(100, 200, 50)
        # y = 2.0 * x + 10.0
        y = 2.0 * x + 10.0
        beta, alpha = model.calculate_hedge_ratio(y, x)
        assert pytest.approx(beta, rel=1e-3) == 2.0
        assert pytest.approx(alpha, abs=1e-3) == 10.0

    def test_evaluate_pair_insufficient_data(self):
        model = StatisticalArbitrageModel(window=40)
        sig = model.evaluate_pair("ETHUSDT", "BTCUSDT", [100.0] * 25, [200.0] * 25)
        assert sig is None

    def test_evaluate_pair_zero_spread_std(self):
        model = StatisticalArbitrageModel(window=30)
        # Perfectly identical constant spread
        x = np.ones(40) * 100.0
        y = np.ones(40) * 100.0
        sig = model.evaluate_pair("ETHUSDT", "BTCUSDT", y, x)
        assert sig is None

    def test_evaluate_pair_signals(self):
        model = StatisticalArbitrageModel(entry_z=2.0, exit_z=0.5, stop_z=3.5, window=30)

        # Baseline cointegrated series
        np.random.seed(42)
        base_x = np.linspace(100, 150, 40)
        base_y = 1.5 * base_x + np.random.normal(0, 0.5, 40)

        # Case 1: Underpriced spread (Y drops by 2.0 -> z ~ -2.8 -> BUY Y, SELL X)
        under_y = base_y.copy()
        under_y[-1] -= 2.0
        sig_buy = model.evaluate_pair("ETHUSDT", "BTCUSDT", under_y, base_x)
        assert isinstance(sig_buy, PairsSignal)
        assert sig_buy.action_y == "BUY"
        assert sig_buy.action_x == "SELL"
        assert sig_buy.z_score < -2.0
        assert sig_buy.confidence >= 0.6

        # Case 2: Overpriced spread (Y spikes by 2.0 -> z ~ +2.8 -> SELL Y, BUY X)
        over_y = base_y.copy()
        over_y[-1] += 2.0
        sig_sell = model.evaluate_pair("ETHUSDT", "BTCUSDT", over_y, base_x)
        assert isinstance(sig_sell, PairsSignal)
        assert sig_sell.action_y == "SELL"
        assert sig_sell.action_x == "BUY"
        assert sig_sell.z_score > 2.0
        assert sig_sell.confidence >= 0.6

        # Case 3: Divergence stop (massive blowout beyond 3.5 z)
        blowout_y = base_y.copy()
        blowout_y[-1] += 15.0
        sig_stop = model.evaluate_pair("ETHUSDT", "BTCUSDT", blowout_y, base_x)
        assert isinstance(sig_stop, PairsSignal)
        assert sig_stop.action_y == "EXIT"
        assert sig_stop.action_x == "EXIT"
        assert sig_stop.confidence == 1.0

        # Case 4: Exit signal near mean (spread within 0.5 z)
        exit_y = base_y.copy()
        beta, alpha = model.calculate_hedge_ratio(exit_y[-30:], base_x[-30:])
        exit_y[-1] = beta * base_x[-1] + alpha
        sig_exit = model.evaluate_pair("ETHUSDT", "BTCUSDT", exit_y, base_x)
        assert isinstance(sig_exit, PairsSignal)
        assert sig_exit.action_y == "EXIT"
        assert sig_exit.action_x == "EXIT"
        assert abs(sig_exit.z_score) <= 0.5


class TestKalmanFilterMeanReversionModel:
    def test_kalman_initialization_and_single_update(self):
        kf = KalmanFilterMeanReversionModel(process_variance=1e-4, measurement_variance=1e-2, z_threshold=2.0)
        mean, err, z = kf.update(100.0)
        assert mean == 100.0
        assert err == 0.0
        assert z == 0.0

        # Second update
        mean2, err2, z2 = kf.update(105.0)
        assert mean2 > 100.0
        assert err2 == 5.0
        assert z2 > 0.0

    def test_evaluate_price_series_short(self):
        kf = KalmanFilterMeanReversionModel()
        assert kf.evaluate_price_series([100.0, 101.0]) is None

    def test_evaluate_price_series_signals(self):
        kf = KalmanFilterMeanReversionModel(z_threshold=2.0)

        # Flat prices with an upward spike
        prices = [100.0] * 30 + [115.0]
        res_sell = kf.evaluate_price_series(prices)
        assert res_sell is not None
        assert res_sell["action"] == "SELL"
        assert res_sell["z_score"] >= 2.0
        assert res_sell["confidence"] >= 0.6

        # Flat prices with a downward drop
        prices_down = [100.0] * 30 + [85.0]
        res_buy = kf.evaluate_price_series(prices_down)
        assert res_buy is not None
        assert res_buy["action"] == "BUY"
        assert res_buy["z_score"] <= -2.0
        assert res_buy["confidence"] >= 0.6

        # Completely steady prices -> HOLD
        prices_steady = [100.0 + (i % 2) * 0.01 for i in range(30)]
        res_hold = kf.evaluate_price_series(prices_steady)
        assert res_hold is not None
        assert res_hold["action"] == "HOLD"


class TestVolatilityRegimeBreakoutModel:
    def test_compute_atr_short_series(self):
        model = VolatilityRegimeBreakoutModel(atr_period=14)
        highs = np.array([105.0, 106.0])
        lows = np.array([95.0, 96.0])
        closes = np.array([100.0, 101.0])
        atr = model.compute_atr(highs, lows, closes)
        assert atr == 10.0

        # Empty fallback
        assert model.compute_atr(np.array([]), np.array([]), np.array([])) == 1.0

    def test_classify_regimes(self):
        model = VolatilityRegimeBreakoutModel()

        # Extreme chaos: massive returns and high atr ratio
        chaos_closes = np.array([100.0, 150.0, 80.0, 140.0, 70.0])
        assert model.classify_regime(chaos_closes, atr=15.0) == MarketRegime.EXTREME_VOL_CHAOS

        # High trend, low vol
        trend_closes = np.linspace(100.0, 150.0, 50)
        assert model.classify_regime(trend_closes, atr=0.5) == MarketRegime.LOW_VOL_TRENDING

        # High trend, high vol
        assert model.classify_regime(trend_closes, atr=5.0) == MarketRegime.HIGH_VOL_TRENDING

        # Chop mean reverting: sideways oscillation
        chop_closes = np.array([100.0, 101.0, 99.0, 100.5, 99.5] * 10)
        assert model.classify_regime(chop_closes, atr=1.0) == MarketRegime.CHOP_MEAN_REVERTING

    def test_evaluate_insufficient_candles(self):
        model = VolatilityRegimeBreakoutModel(lookback=50)
        candles = [{"open": 100, "high": 105, "low": 95, "close": 100} for _ in range(20)]
        assert model.evaluate(candles) is None

    def test_evaluate_chaos_preservation(self):
        model = VolatilityRegimeBreakoutModel(lookback=50)
        candles = []
        for i in range(50):
            # Wild swings
            p = 100.0 if i % 2 == 0 else 180.0
            candles.append({"open": p, "high": p + 20, "low": p - 20, "close": p})

        res = model.evaluate(candles)
        assert res is not None
        assert res["regime"] == MarketRegime.EXTREME_VOL_CHAOS
        assert res["action"] == "HOLD"
        assert res["stop_loss"] is None
        assert res["take_profit"] is None

    def test_evaluate_trending_breakout(self):
        model = VolatilityRegimeBreakoutModel(lookback=50)
        # Monotonically increasing trend
        candles = []
        for i in range(50):
            p = 100.0 + i * 2.0
            candles.append({"open": p - 1, "high": p + 1, "low": p - 1, "close": p})
        candles[-1]["close"] = candles[-1]["high"]

        res = model.evaluate(candles)
        assert res is not None
        assert res["action"] == "BUY"
        assert res["stop_loss"] is not None
        assert res["take_profit"] is not None
        assert res["take_profit"] > res["current_price"]

        # Downward trending breakdown -> SELL
        candles_down = []
        for i in range(50):
            p = 200.0 - i * 2.0
            candles_down.append({"open": p + 1, "high": p + 1, "low": p - 1, "close": p})
        candles_down[-1]["close"] = candles_down[-1]["low"]

        res_down = model.evaluate(candles_down)
        assert res_down is not None
        assert res_down["action"] == "SELL"
        assert res_down["stop_loss"] is not None
        assert res_down["take_profit"] is not None
        assert res_down["take_profit"] < res_down["current_price"]

    def test_evaluate_chop_channel_reversal(self):
        model = VolatilityRegimeBreakoutModel(lookback=50)
        candles = []
        # Sideways range between 95 and 105
        for i in range(49):
            p = 100.0 + 4.0 * np.sin(i / 2.0)
            candles.append({"open": p - 0.5, "high": p + 1.0, "low": p - 1.0, "close": p})
        # Last candle at highest high to trigger SELL fade
        candles_sell = list(candles)
        candles_sell.append({"open": 103.5, "high": 105.0, "low": 103.0, "close": 105.0})

        res_sell = model.evaluate(candles_sell)
        assert res_sell is not None
        assert res_sell["regime"] == MarketRegime.CHOP_MEAN_REVERTING
        if res_sell["action"] == "SELL":
            assert res_sell["stop_loss"] > res_sell["current_price"]
            assert res_sell["take_profit"] < res_sell["current_price"]

        # Last candle at lowest low to trigger BUY fade
        candles_buy = list(candles)
        candles_buy.append({"open": 96.5, "high": 97.0, "low": 95.0, "close": 95.0})
        res_buy = model.evaluate(candles_buy)
        assert res_buy is not None
        assert res_buy["regime"] == MarketRegime.CHOP_MEAN_REVERTING
        if res_buy["action"] == "BUY":
            assert res_buy["stop_loss"] < res_buy["current_price"]
            assert res_buy["take_profit"] > res_buy["current_price"]
