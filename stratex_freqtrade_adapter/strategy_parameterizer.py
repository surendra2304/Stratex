"""strategy_parameterizer.py — Parameterized Strategy Adapters for Stratex Optimization.

Provides clean parameterized wrappers for Stratex quantitative strategies,
allowing Optuna hyperoptimization to explore candidate parameters without
mutating production config_strategy.py defaults.
"""

from collections import namedtuple
from typing import Any
import numpy as np
import pandas as pd

from .parameters import IntParameter, RealParameter, CategoricalParameter

class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior


def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    plus_dm = df['high'].diff()
    minus_dm = df['low'].diff(-1).shift(1)
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)

    tr_smooth = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / (tr_smooth + 1e-9) * 100
    minus_di = pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / (tr_smooth + 1e-9) * 100
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


class ParameterizedADXEMA:
    """Parameterized ADX + EMA Trend Following Strategy for Hyperopt."""
    __name__ = "adx_ema"

    def __init__(self, **kwargs):
        self.adx_threshold = int(kwargs.get("ADX_THRESHOLD", 20))
        self.sl_atr_mult = float(kwargs.get("SL_ATR_MULTIPLIER", 3.0))
        self.tp_atr_mult = float(kwargs.get("TP_ATR_MULTIPLIER", 3.0))
        self.retest_window_bars = int(kwargs.get("RETEST_WINDOW_BARS", 10))
        self.enable_retest = bool(kwargs.get("ENABLE_RETEST_ENTRY", True))
        self.ema_fast = int(kwargs.get("EMA_FAST_PERIOD", 20))
        self.ema_slow = int(kwargs.get("EMA_SLOW_PERIOD", 50))
        self.ema_direction = int(kwargs.get("EMA_DIRECTION_PERIOD", 200))
        self.atr_period = int(kwargs.get("ATR_PERIOD", 14))
        self.adx_period = int(kwargs.get("ADX_PERIOD", 14))
        self.strategy_type = "RULE_BASED"
        self.win_rate_prior = float(kwargs.get("OOS_WIN_RATE_PRIOR", 0.551))

    @classmethod
    def get_search_space(cls) -> dict[str, Any]:
        return {
            "ADX_THRESHOLD": IntParameter(15, 35, default=20),
            "SL_ATR_MULTIPLIER": RealParameter(1.5, 4.5, default=3.0, step=0.25),
            "TP_ATR_MULTIPLIER": RealParameter(1.5, 5.0, default=3.0, step=0.25),
            "RETEST_WINDOW_BARS": IntParameter(4, 16, default=10),
            "EMA_FAST_PERIOD": IntParameter(10, 30, default=20, step=2),
            "EMA_SLOW_PERIOD": IntParameter(40, 70, default=50, step=5),
        }

    def set_params(self, params: dict):
        if "ADX_THRESHOLD" in params: self.adx_threshold = int(params["ADX_THRESHOLD"])
        if "SL_ATR_MULTIPLIER" in params: self.sl_atr_mult = float(params["SL_ATR_MULTIPLIER"])
        if "TP_ATR_MULTIPLIER" in params: self.tp_atr_mult = float(params["TP_ATR_MULTIPLIER"])
        if "RETEST_WINDOW_BARS" in params: self.retest_window_bars = int(params["RETEST_WINDOW_BARS"])
        if "ENABLE_RETEST_ENTRY" in params: self.enable_retest = bool(params["ENABLE_RETEST_ENTRY"])
        if "EMA_FAST_PERIOD" in params: self.ema_fast = int(params["EMA_FAST_PERIOD"])
        if "EMA_SLOW_PERIOD" in params: self.ema_slow = int(params["EMA_SLOW_PERIOD"])

    def get_signal(self, df: pd.DataFrame) -> SignalResult:
        _NO_SIGNAL = SignalResult(None, None, None, self.strategy_type, self.win_rate_prior, self.tp_atr_mult / self.sl_atr_mult)
        if df is None or len(df) < max(self.ema_direction, 50):
            return _NO_SIGNAL

        close = df['close']
        ema_fast = close.ewm(span=self.ema_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.ema_slow, adjust=False).mean()
        ema_dir = close.ewm(span=self.ema_direction, adjust=False).mean()
        atr = _compute_atr(df, self.atr_period)
        adx = _compute_adx(df, self.adx_period)

        last_idx = df.index[-1]
        prev_idx = df.index[-2]

        last_close = float(close.loc[last_idx])
        last_open = float(df['open'].loc[last_idx])
        last_low = float(df['low'].loc[last_idx])

        c_fast_last = float(ema_fast.loc[last_idx])
        c_fast_prev = float(ema_fast.loc[prev_idx])
        c_slow_last = float(ema_slow.loc[last_idx])
        c_slow_prev = float(ema_slow.loc[prev_idx])
        c_dir_last = float(ema_dir.loc[last_idx])
        c_adx_last = float(adx.loc[last_idx])
        c_atr_last = float(atr.loc[last_idx])

        if np.isnan([c_fast_last, c_slow_last, c_dir_last, c_adx_last, c_atr_last]).any():
            return _NO_SIGNAL

        cross_up = (c_fast_last > c_slow_last) and (c_fast_prev <= c_slow_prev)
        cross_dn = (c_fast_last < c_slow_last) and (c_fast_prev >= c_slow_prev)
        trend_strong = (c_adx_last > self.adx_threshold)

        rr = self.tp_atr_mult / self.sl_atr_mult if self.sl_atr_mult > 0 else 1.0

        def _buy():
            sl = last_close - (self.sl_atr_mult * c_atr_last)
            tp = last_close + (self.tp_atr_mult * c_atr_last)
            return SignalResult("BUY", sl, tp, self.strategy_type, self.win_rate_prior, rr)

        # 1. Fresh Golden Cross in Uptrend
        if cross_up and last_close > c_dir_last and trend_strong:
            return _buy()

        # 2. Qualified Retest entry
        if self.enable_retest and trend_strong and last_close > c_dir_last and c_fast_last > c_slow_last:
            if last_low <= c_fast_last * 1.002 and last_close > last_open and last_close >= c_fast_last:
                li = len(df) - 1
                for back in range(1, self.retest_window_bars + 1):
                    k = li - back
                    if k < 1:
                        break
                    b_fast = ema_fast.iloc[k]
                    b_slow = ema_slow.iloc[k]
                    p_fast = ema_fast.iloc[k - 1]
                    p_slow = ema_slow.iloc[k - 1]
                    if (b_fast > b_slow) and (p_fast <= p_slow):
                        if df['close'].iloc[k] > ema_dir.iloc[k] and adx.iloc[k] > self.adx_threshold:
                            between_lows = df['low'].iloc[k + 1:li]
                            between_emas = ema_fast.iloc[k + 1:li]
                            if len(between_lows) == 0 or (between_lows > between_emas * 1.002).all():
                                return _buy()
                        break

        # 3. Short / Bearish Crossover
        if cross_dn and last_close < c_dir_last and trend_strong:
            sl = last_close + (self.sl_atr_mult * c_atr_last)
            tp = last_close - (self.tp_atr_mult * c_atr_last)
            return SignalResult("SELL", sl, tp, self.strategy_type, self.win_rate_prior, rr)

        return _NO_SIGNAL


def get_parameterized_strategy(name: str, **kwargs):
    """Factory to retrieve parameterized strategy instances."""
    key = str(name).lower()
    if "adx" in key or "ema" in key:
        return ParameterizedADXEMA(**kwargs)
    raise ValueError(f"No parameterized adapter available for strategy: {name}")
