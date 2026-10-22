"""stratex_freqtrade_adapter/strategy/builtin/sample_strategy.py

Canonical sample Freqtrade strategy using EMA crossover, RSI filter, and Bollinger Bands.
"""

from __future__ import annotations
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd

from ..interface import IStrategy
from ..registry import registry


@registry.register("sample_strategy")
class SampleFreqtradeStrategy(IStrategy):
    """Canonical EMA Crossover + RSI + Bollinger Bands strategy."""

    timeframe = "5m"
    minimal_roi = {
        "0": 0.04,
        "30": 0.02,
        "60": 0.01,
        "120": 0.0,
    }
    stoploss = -0.04
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.025
    trailing_only_offset_is_reached = True
    can_short = True

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        df = dataframe
        # EMAs
        df["ema_fast"] = df["close"].ewm(span=12, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=26, adjust=False).mean()

        # RSI (14)
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / (loss + 1e-9)
        df["rsi"] = 100 - (100 / (1 + rs))

        # Bollinger Bands (20, 2)
        mid = df["close"].rolling(20).mean()
        std = df["close"].rolling(20).std()
        df["bb_lower"] = mid - (2 * std)
        df["bb_middle"] = mid
        df["bb_upper"] = mid + (2 * std)

        return df

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        df = dataframe
        df["enter_long"] = 0
        df["enter_short"] = 0
        df["enter_tag"] = ""

        # Long: EMA Golden Cross + RSI not overbought
        long_cond = (
            (df["ema_fast"] > df["ema_slow"]) &
            (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1)) &
            (df["rsi"] < 65) &
            (df["close"] > df["ema_fast"])
        )
        df.loc[long_cond, "enter_long"] = 1
        df.loc[long_cond, "enter_tag"] = "ema_cross_long"

        # Short: EMA Death Cross + RSI not oversold
        short_cond = (
            (df["ema_fast"] < df["ema_slow"]) &
            (df["ema_fast"].shift(1) >= df["ema_slow"].shift(1)) &
            (df["rsi"] > 35) &
            (df["close"] < df["ema_fast"])
        )
        df.loc[short_cond, "enter_short"] = 1
        df.loc[short_cond, "enter_tag"] = "ema_cross_short"

        return df

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        df = dataframe
        df["exit_long"] = 0
        df["exit_short"] = 0
        df["exit_tag"] = ""

        # Long Exit: RSI extreme overbought or price breakdown
        exit_long_cond = (df["rsi"] > 75) | (df["close"] < df["ema_slow"])
        df.loc[exit_long_cond, "exit_long"] = 1
        df.loc[exit_long_cond, "exit_tag"] = "rsi_high_or_ema_break"

        # Short Exit: RSI extreme oversold or price breakout
        exit_short_cond = (df["rsi"] < 25) | (df["close"] > df["ema_slow"])
        df.loc[exit_short_cond, "exit_short"] = 1
        df.loc[exit_short_cond, "exit_tag"] = "rsi_low_or_ema_break"

        return df
