"""
strategies/quantitative_engine.py — Advanced Quantitative Trading Models for Stratex.

Implements production-grade mathematical trading strategies:
1. Statistical Arbitrage / Pairs Trading Model (`StatisticalArbitrageModel`):
   - Rolling OLS hedge ratio estimation (beta).
   - Stationary spread tracking with rolling Z-score.
   - Mean-reverting entry, exit, and divergence stop-loss levels.
2. Kalman Filter Dynamic State-Space Model (`KalmanFilterMeanReversionModel`):
   - 1D state-space tracking of hidden equilibrium price without fixed-window lag.
   - Innovation error covariance and normalized Z-score.
   - Adaptive mean-reversion signals with real-time error bounds.
3. Volatility Regime Adaptive Breakout Model (`VolatilityRegimeBreakoutModel`):
   - Four-state regime classifier (LOW_VOL_TRENDING, HIGH_VOL_TRENDING, CHOP_MEAN_REVERTING, EXTREME_VOL_CHAOS).
   - Chandelier Exits derived dynamically from ATR.
   - Automatic defense / cash mode in chaos regimes.

All strategies emit normalized `TradeIntent` dictionaries compatible with `StratexOrderManager`.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from logger import get_logger

logger = get_logger("quantitative_engine")


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


# ==============================================================================
# 1. STATISTICAL ARBITRAGE / PAIRS TRADING MODEL
# ==============================================================================

@dataclass
class PairsSignal:
    timestamp: str
    asset_y: str
    asset_x: str
    hedge_ratio: float
    spread: float
    z_score: float
    action_y: str  # "BUY", "SELL", "HOLD"
    action_x: str  # "SELL", "BUY", "HOLD"
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


class StatisticalArbitrageModel:
    """
    Pairs trading and statistical arbitrage model using cointegrated price spreads.
    Computes rolling OLS hedge ratio and Z-score of spread.
    """

    def __init__(
        self,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        stop_z: float = 3.5,
        window: int = 50,
    ):
        self.entry_z = abs(entry_z)
        self.exit_z = abs(exit_z)
        self.stop_z = abs(stop_z)
        self.window = max(20, window)

    def calculate_hedge_ratio(self, prices_y: np.ndarray, prices_x: np.ndarray) -> tuple[float, float]:
        """Compute rolling OLS beta and alpha: Y = beta * X + alpha."""
        if len(prices_y) < 10 or len(prices_x) < 10:
            return 1.0, 0.0
        # Simple linear regression: cov(X, Y) / var(X)
        cov = np.cov(prices_x, prices_y)
        var_x = cov[0, 0]
        if var_x <= 1e-12:
            return 1.0, 0.0
        beta = float(cov[0, 1] / var_x)
        alpha = float(np.mean(prices_y) - beta * np.mean(prices_x))
        return beta, alpha

    def evaluate_pair(
        self,
        asset_y: str,
        asset_x: str,
        series_y: list[float] | np.ndarray,
        series_x: list[float] | np.ndarray,
    ) -> PairsSignal | None:
        """Evaluate two cointegrated assets and return a PairsSignal."""
        y = np.asarray(series_y, dtype=float)
        x = np.asarray(series_x, dtype=float)
        n = min(len(y), len(x))
        if n < self.window:
            return None

        # Use recent window
        y_win = y[-self.window:]
        x_win = x[-self.window:]

        beta, alpha = self.calculate_hedge_ratio(y_win, x_win)
        spread = y_win - (beta * x_win + alpha)
        spread_mean = float(np.mean(spread))
        spread_std = float(np.std(spread))

        if spread_std <= 1e-12:
            return None

        current_spread = float(spread[-1])
        z_score = (current_spread - spread_mean) / spread_std

        action_y = "HOLD"
        action_x = "HOLD"
        confidence = 0.0

        if abs(z_score) >= self.stop_z:
            # Divergence stop: spread has blown out beyond statistical bounds
            action_y = "EXIT"
            action_x = "EXIT"
            confidence = 1.0
        elif z_score <= -self.entry_z:
            # Spread is underpriced -> Long Y, Short X
            action_y = "BUY"
            action_x = "SELL"
            confidence = min(0.95, 0.6 + abs(z_score) * 0.1)
        elif z_score >= self.entry_z:
            # Spread is overpriced -> Short Y, Long X
            action_y = "SELL"
            action_x = "BUY"
            confidence = min(0.95, 0.6 + abs(z_score) * 0.1)
        elif abs(z_score) <= self.exit_z:
            action_y = "EXIT"
            action_x = "EXIT"
            confidence = 0.8

        return PairsSignal(
            timestamp=_utc_now_iso(),
            asset_y=asset_y,
            asset_x=asset_x,
            hedge_ratio=beta,
            spread=current_spread,
            z_score=z_score,
            action_y=action_y,
            action_x=action_x,
            confidence=confidence,
            metadata={"spread_mean": spread_mean, "spread_std": spread_std, "alpha": alpha},
        )


# ==============================================================================
# 2. KALMAN FILTER ADAPTIVE MEAN REVERSION MODEL
# ==============================================================================

class KalmanFilterMeanReversionModel:
    """
    Online 1D Kalman Filter state-space tracker for unobserved equilibrium price.
    Estimates the true latent mean and innovation error in real-time.
    """

    def __init__(self, process_variance: float = 1e-4, measurement_variance: float = 1e-2, z_threshold: float = 2.0):
        self.q = process_variance      # State transition covariance
        self.r = measurement_variance  # Measurement noise covariance
        self.z_threshold = abs(z_threshold)

        # State estimates
        self.state_mean = None
        self.state_cov = 1.0

    def update(self, price: float) -> tuple[float, float, float]:
        """
        Update Kalman filter with observation price.
        Returns: (state_mean, error, z_score).
        """
        if self.state_mean is None:
            self.state_mean = price
            self.state_cov = 1.0
            return self.state_mean, 0.0, 0.0

        # 1. Time Update (Predict)
        predicted_mean = self.state_mean
        predicted_cov = self.state_cov + self.q

        # 2. Measurement Update (Correct)
        innovation = price - predicted_mean
        innovation_cov = predicted_cov + self.r
        kalman_gain = predicted_cov / innovation_cov

        self.state_mean = predicted_mean + kalman_gain * innovation
        self.state_cov = (1.0 - kalman_gain) * predicted_cov

        z_score = innovation / math.sqrt(max(1e-12, innovation_cov))
        return self.state_mean, innovation, z_score

    def evaluate_price_series(self, prices: list[float] | np.ndarray) -> dict[str, Any] | None:
        """Process price series through the Kalman Filter and return current signal."""
        if len(prices) < 5:
            return None

        # Reset state and run through history
        self.state_mean = None
        self.state_cov = 1.0

        last_z = 0.0
        last_error = 0.0
        for p in prices:
            _, last_error, last_z = self.update(float(p))

        action = "HOLD"
        confidence = 0.0
        if last_z <= -self.z_threshold:
            action = "BUY"
            confidence = min(0.95, 0.6 + abs(last_z) * 0.1)
        elif last_z >= self.z_threshold:
            action = "SELL"
            confidence = min(0.95, 0.6 + abs(last_z) * 0.1)

        return {
            "strategy": "kalman_mean_reversion",
            "action": action,
            "latent_price": self.state_mean,
            "innovation_error": last_error,
            "z_score": last_z,
            "confidence": confidence,
            "timestamp": _utc_now_iso(),
        }


# ==============================================================================
# 3. VOLATILITY REGIME ADAPTIVE BREAKOUT MODEL
# ==============================================================================

class MarketRegime:
    LOW_VOL_TRENDING = "LOW_VOL_TRENDING"
    HIGH_VOL_TRENDING = "HIGH_VOL_TRENDING"
    CHOP_MEAN_REVERTING = "CHOP_MEAN_REVERTING"
    EXTREME_VOL_CHAOS = "EXTREME_VOL_CHAOS"


class VolatilityRegimeBreakoutModel:
    """
    Volatility-normalized regime classifier with dynamic ATR Chandelier Exits.
    Adapts strategy behavior to prevailing market regime.
    """

    def __init__(self, atr_period: int = 14, chandelier_mult: float = 3.0, lookback: int = 50):
        self.atr_period = atr_period
        self.chandelier_mult = chandelier_mult
        self.lookback = lookback

    def compute_atr(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> float:
        """Calculate Average True Range over atr_period."""
        if len(closes) < self.atr_period + 1:
            return float(np.mean(highs - lows)) if len(highs) > 0 else 1.0

        tr_list = []
        for i in range(1, len(closes)):
            h_l = highs[i] - lows[i]
            h_pc = abs(highs[i] - closes[i - 1])
            l_pc = abs(lows[i] - closes[i - 1])
            tr_list.append(max(h_l, h_pc, l_pc))

        return float(np.mean(tr_list[-self.atr_period:]))

    def classify_regime(self, closes: np.ndarray, atr: float) -> str:
        """Classify current regime based on realized volatility and trend strength."""
        returns = np.diff(np.log(closes))
        realized_vol = float(np.std(returns)) if len(returns) > 0 else 0.01

        # Normalized ATR ratio: ATR / price
        current_price = closes[-1]
        atr_ratio = atr / current_price if current_price > 0 else 0.01

        # Trend strength: ratio of net price change to sum of absolute changes
        net_change = abs(closes[-1] - closes[0])
        gross_change = np.sum(np.abs(np.diff(closes)))
        trend_ratio = net_change / max(1e-12, gross_change)

        if atr_ratio > 0.06 or realized_vol > 0.05:
            return MarketRegime.EXTREME_VOL_CHAOS
        elif trend_ratio > 0.4:
            if atr_ratio > 0.02:
                return MarketRegime.HIGH_VOL_TRENDING
            return MarketRegime.LOW_VOL_TRENDING
        else:
            return MarketRegime.CHOP_MEAN_REVERTING

    def evaluate(self, candles: list[dict[str, Any]]) -> dict[str, Any] | None:
        """
        Evaluate candle series and return regime-aware breakout signals.
        """
        if len(candles) < self.lookback:
            return None

        closes = np.array([float(c["close"]) for c in candles[-self.lookback:]])
        highs = np.array([float(c["high"]) for c in candles[-self.lookback:]])
        lows = np.array([float(c["low"]) for c in candles[-self.lookback:]])

        atr = self.compute_atr(highs, lows, closes)
        regime = self.classify_regime(closes, atr)

        current_price = closes[-1]
        highest_high = float(np.max(highs[-20:]))
        lowest_low = float(np.min(lows[-20:]))

        # Chandelier Exit levels
        chandelier_long_stop = highest_high - (self.chandelier_mult * atr)
        chandelier_short_stop = lowest_low + (self.chandelier_mult * atr)

        action = "HOLD"
        confidence = 0.0
        stop_loss = 0.0
        take_profit = 0.0

        if regime == MarketRegime.EXTREME_VOL_CHAOS:
            # Defensive preservation: do not trade in chaos regime
            action = "HOLD"
            confidence = 0.0
        elif regime in {MarketRegime.LOW_VOL_TRENDING, MarketRegime.HIGH_VOL_TRENDING}:
            # Breakout signals
            if current_price >= highest_high * 0.999:
                action = "BUY"
                confidence = 0.85 if regime == MarketRegime.LOW_VOL_TRENDING else 0.70
                stop_loss = chandelier_long_stop
                take_profit = current_price + (2.0 * (current_price - stop_loss))
            elif current_price <= lowest_low * 1.001:
                action = "SELL"
                confidence = 0.85 if regime == MarketRegime.LOW_VOL_TRENDING else 0.70
                stop_loss = chandelier_short_stop
                take_profit = current_price - (2.0 * (stop_loss - current_price))
        elif regime == MarketRegime.CHOP_MEAN_REVERTING:
            # Fade extremes within channel
            mid = (highest_high + lowest_low) / 2.0
            if current_price >= highest_high * 0.995:
                action = "SELL"
                confidence = 0.75
                stop_loss = highest_high + (1.0 * atr)
                take_profit = mid
            elif current_price <= lowest_low * 1.005:
                action = "BUY"
                confidence = 0.75
                stop_loss = lowest_low - (1.0 * atr)
                take_profit = mid

        return {
            "strategy": "volatility_regime_breakout",
            "action": action,
            "regime": regime,
            "current_price": current_price,
            "atr": atr,
            "stop_loss": stop_loss if action != "HOLD" else None,
            "take_profit": take_profit if action != "HOLD" else None,
            "confidence": confidence,
            "timestamp": _utc_now_iso(),
        }
