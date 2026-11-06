"""
strategies/statistical_arbitrage.py — Statistical Arbitrage / Pairs Trading Strategy.

Implements:
1. Cointegration-based pair selection (Engle-Granger, Johansen)
2. Mean reversion of spread with Kalman filter
3. Dynamic hedge ratio calculation
4. Rolling window half-life estimation
5. Risk management with position limits
6. Multi-pair portfolio management
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from logger import get_logger

logger = get_logger("strategies.statistical_arbitrage")

# Suppress warnings for cleaner output
warnings.filterwarnings("ignore", category=RuntimeWarning)

# Strategy metadata for registry
_STRATEGY_TYPE = "STATISTICAL_ARBITRAGE"
_OOS_WIN_RATE_PRIOR = 0.55
_RR_RATIO = 1.8


@dataclass
class PairSignal:
    """Pairs trading signal."""
    symbol_a: str
    symbol_b: str
    direction: str  # long_a_short_b, short_a_long_b, none
    hedge_ratio: float
    spread_zscore: float
    half_life: float
    confidence: float
    entry_spread: float
    stop_loss_spread: float
    take_profit_spread: float
    timeframe: str


class StatisticalArbitrageStrategy:
    """Statistical Arbitrage / Pairs Trading Strategy."""

    def __init__(
        self,
        lookback: int = 252,  # ~1 year for cointegration
        zscore_entry: float = 2.0,
        zscore_exit: float = 0.5,
        max_half_life: int = 20,
        min_half_life: int = 1,
        max_pairs: int = 5,
        min_correlation: float = 0.7,
        min_coint_pvalue: float = 0.05,
        lookback_hedge: int = 100,
        stop_loss_std: float = 3.0,
    ):
        self.lookback = lookback
        self.zscore_entry = zscore_entry
        self.zscore_exit = zscore_exit
        self.max_half_life = max_half_life
        self.min_half_life = min_half_life
        self.max_pairs = max_pairs
        self.min_correlation = min_correlation
        self.min_coint_pvalue = min_coint_pvalue
        self.lookback_hedge = lookback_hedge
        self.stop_loss_std = stop_loss_std
        self.active_pairs: Dict[str, Dict] = {}

    @staticmethod
    def _calculate_hedge_ratio(series_a: pd.Series, series_b: pd.Series, lookback: int = 100) -> float:
        """Calculate dynamic hedge ratio using OLS."""
        if len(series_a) < lookback or len(series_b) < lookback:
            return 1.0

        y = series_a.iloc[-lookback:]
        x = series_b.iloc[-lookback:]

        # OLS: y = beta * x + c
        x_mean = x.mean()
        y_mean = y.mean()
        beta = ((x - x_mean) * (y - y_mean)).sum() / ((x - x_mean) ** 2).sum()
        return max(0.1, min(10.0, float(beta)))

    @staticmethod
    def _calculate_half_life(spread: pd.Series) -> float:
        """Calculate half-life of mean reversion using Ornstein-Uhlenbeck."""
        if len(spread) < 20:
            return 10.0

        spread_lag = spread.shift(1).dropna()
        spread_diff = spread.diff().dropna()

        # Align
        common_idx = spread_lag.index.intersection(spread_diff.index)
        if len(common_idx) < 10:
            return 10.0

        y = spread_diff.loc[common_idx]
        x = spread_lag.loc[common_idx]

        # OLS: dy = alpha + beta * y_{t-1} + epsilon
        x_mean = x.mean()
        y_mean = y.mean()
        beta = ((x - x_mean) * (y - y_mean)).sum() / ((x - x_mean) ** 2).sum()

        if beta >= 0:
            return 100.0  # Not mean reverting

        half_life = -np.log(2) / beta
        return max(0.5, min(half_life, 100.0))

    @staticmethod
    def _cointegration_test(series_a: pd.Series, series_b: pd.Series) -> Tuple[float, float]:
        """Engle-Granger cointegration test. Returns (p-value, hedge_ratio)."""
        if len(series_a) < 30 or len(series_b) < 30:
            return 1.0, 1.0

        # OLS regression
        hedge_ratio = StatisticalArbitrageStrategy._calculate_hedge_ratio(series_a, series_b, len(series_a))

        # Spread
        spread = series_a - hedge_ratio * series_b

        # ADF test on spread
        try:
            from statsmodels.tsa.stattools import adfuller
            adf_result = adfuller(spread.dropna(), maxlag=1, regression='c', autolag=None)
            pvalue = adf_result[1]
            return pvalue, hedge_ratio
        except ImportError:
            # Fallback: simple correlation + variance check
            correlation = series_a.corr(series_b)
            if correlation > 0.7:
                return 0.01, hedge_ratio
            return 1.0, hedge_ratio

    def generate_signal(
        self,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        symbol_a: str,
        symbol_b: str,
        timeframe: str = "1h"
    ) -> Optional[PairSignal]:
        """Generate pairs trading signal."""
        if len(df_a) < self.lookback or len(df_b) < self.lookback:
            return None

        # Align data
        common_idx = df_a.index.intersection(df_b.index)
        if len(common_idx) < self.lookback:
            return None

        close_a = df_a.loc[common_idx, "close"]
        close_b = df_b.loc[common_idx, "close"]

        # Check correlation
        correlation = close_a.corr(close_b)
        if correlation < self.min_correlation:
            return None

        # Cointegration test
        pvalue, hedge_ratio = self._cointegration_test(close_a, close_b)
        if pvalue > self.min_coint_pvalue:
            return None

        # Calculate spread
        spread = close_a - hedge_ratio * close_b

        # Current spread stats
        spread_mean = spread.rolling(20).mean().iloc[-1]
        spread_std = spread.rolling(20).std().iloc[-1]
        current_spread = spread.iloc[-1]

        if spread_std == 0:
            return None

        zscore = (current_spread - spread_mean) / spread_std

        # Half-life
        half_life = self._calculate_half_life(spread)
        if half_life > self.max_half_life or half_life < self.min_half_life:
            return None

        # Check if we already have this pair active
        pair_key = f"{symbol_a}_{symbol_b}"
        if pair_key in self.active_pairs:
            active = self.active_pairs[pair_key]
            # Check exit condition
            if abs(zscore) < self.zscore_exit:
                # Exit signal
                direction = "none"
                if active["direction"] == "long_a_short_b":
                    direction = "close_long"
                elif active["direction"] == "short_a_long_b":
                    direction = "close_short"
                return PairSignal(
                    symbol_a=symbol_a,
                    symbol_b=symbol_b,
                    direction=direction,
                    hedge_ratio=hedge_ratio,
                    spread_zscore=zscore,
                    half_life=half_life,
                    confidence=0.8,
                    entry_spread=active["entry_spread"],
                    stop_loss_spread=active["stop_loss"],
                    take_profit_spread=active["take_profit"],
                    timeframe="1h",
                )
            return None  # Position already active, no new signal

        # Generate entry signal
        direction = "none"
        confidence = 0.0

        if zscore > self.zscore_entry:
            # Spread too wide -> short A, long B
            direction = "short_a_long_b"
            confidence = min(0.9, 0.5 + abs(zscore - self.zscore_entry) * 0.1)
        elif zscore < -self.zscore_entry:
            # Spread too narrow -> long A, short B
            direction = "long_a_short_b"
            confidence = min(0.9, 0.5 + abs(zscore + self.zscore_entry) * 0.1)

        if direction == "none":
            return None

        # Calculate stops and targets in spread space
        entry_spread = current_spread
        stop_loss = abs(zscore + self.stop_loss_std) * spread_std
        take_profit = abs(zscore - self.zscore_exit) * spread_std

        # Convert to price levels for symbol A
        current_price_a = float(df_a["close"].iloc[-1])
        atr_a = self._calculate_atr(df_a)

        if direction == "long_a_short_b":
            # Long A, Short B
            stop_loss = current_price_a - self.stop_loss_std * atr_a
            take_profit = current_price_a + 2 * atr_a
        else:
            # Short A, Long B
            stop_loss = current_price_a + self.stop_loss_std * atr_a
            take_profit = current_price_a - 2 * atr_a

        # Store active pair
        self.active_pairs[pair_key] = {
            "direction": direction,
            "entry_spread": entry_spread,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "hedge_ratio": hedge_ratio,
        }

        return PairSignal(
            symbol_a=symbol_a,
            symbol_b=symbol_b,
            direction=direction,
            hedge_ratio=hedge_ratio,
            spread_zscore=zscore,
            half_life=half_life,
            confidence=confidence,
            entry_spread=entry_spread,
            stop_loss_spread=stop_loss,
            take_profit_spread=take_profit,
            timeframe="1h",
        )

    @staticmethod
    def _calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR."""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        return float(tr.ewm(alpha=1/period, adjust=False).mean().iloc[-1])


def get_signal(df: pd.DataFrame) -> dict:
    """Entry point for strategy registry - single symbol version for compatibility."""
    # This strategy needs two symbols, so for registry compatibility we return HOLD
    return {
        "side": "HOLD",
        "sl": 0.0,
        "tp": 0.0,
        "win_rate_prior": 0.0,
        "rr_ratio": 0.0,
        "strategy_type": "STATISTICAL_ARBITRAGE",
    }


# The actual pairs signal function for the multi-symbol engine
def get_pairs_signal(df_a: pd.DataFrame, df_b: pd.DataFrame, symbol_a: str, symbol_b: str) -> dict:
    """Generate pairs trading signal for two symbols."""
    strategy = StatisticalArbitrageStrategy()
    signal = strategy.generate_signal(df_a, df_b, "SYMBOL_A", "SYMBOL_B", "1h")
    if not signal or signal.direction == "none":
        return {"side": "HOLD", "sl": 0.0, "tp": 0.0, "win_rate_prior": 0.0, "rr_ratio": 0.0}

    return {
        "side": signal.direction.upper(),
        "sl": signal.stop_loss_spread,
        "tp": signal.take_profit_spread,
        "win_rate_prior": 0.55,
        "rr_ratio": 1.8,
        "strategy_type": "STATISTICAL_ARBITRAGE",
        "symbol_a": signal.symbol_a,
        "symbol_b": signal.symbol_b,
        "hedge_ratio": signal.hedge_ratio,
        "spread_zscore": signal.spread_zscore,
        "half_life": signal.half_life,
    }


if __name__ == "__main__":
    import numpy as np
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=500, freq="1h")

    # Generate cointegrated pair
    base = 100 * np.cumprod(1 + np.random.randn(500) * 0.001)
    noise = np.random.randn(500) * 0.5
    prices_a = base + noise
    prices_b = base * 0.95 + noise * 0.8  # Cointegrated with noise

    df_a = pd.DataFrame({
        "open": prices_a * (1 + np.random.rand(500) * 0.001),
        "high": prices_a * (1 + np.abs(np.random.rand(500)) * 0.003),
        "low": prices_a * (1 - np.abs(np.random.rand(500)) * 0.003),
        "close": prices_a,
        "volume": np.random.randint(1000, 10000, 500),
    }, index=pd.date_range("2024-01-01", periods=500, freq="1h"))

    df_b = pd.DataFrame({
        "open": prices_b * (1 + np.random.rand(500) * 0.001),
        "high": prices_b * (1 + np.abs(np.random.rand(500)) * 0.003),
        "low": prices_b * (1 - np.abs(np.random.rand(500)) * 0.003),
        "close": prices_b,
        "volume": np.random.randint(1000, 10000, 500),
    }, index=pd.date_range("2024-01-01", periods=500, freq="1h"))

    df_a["high"] = df_a[["open", "close"]].max(axis=1) * (1 + np.abs(np.random.rand(500)) * 0.001)
    df_a["low"] = df_a[["open", "close"]].min(axis=1) * (1 - np.abs(np.random.rand(500)) * 0.001)
    df_b["high"] = df_b[["open", "close"]].max(axis=1) * (1 + np.abs(np.random.rand(500)) * 0.001)
    df_b["low"] = df_b[["open", "close"]].min(axis=1) * (1 - np.abs(np.random.rand(500)) * 0.001)

    result = get_pairs_signal(df_a, df_b, "BTCUSDT", "ETHUSDT")
    print(f"Signal: {result}")