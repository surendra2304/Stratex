"""stratex_freqtrade_adapter/strategy/builtin/awesome_macd.py

Trend following strategy using MACD and Awesome Oscillator momentum.
"""

from __future__ import annotations
from typing import Any, Dict, Optional
import pandas as pd

from ..interface import IStrategy
from ..registry import registry


@registry.register("awesome_macd")
class AwesomeMacdStrategy(IStrategy):
    """Trend Momentum Strategy combining MACD crossover with Awesome Oscillator."""

    timeframe = "1h"
    minimal_roi = {
        "0": 0.06,
        "60": 0.03,
        "180": 0.015,
        "360": 0.0,
    }
    stoploss = -0.05
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.035
    trailing_only_offset_is_reached = True
    can_short = True

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        df = dataframe
        # MACD (12, 26, 9)
        fast_ema = df["close"].ewm(span=12, adjust=False).mean()
        slow_ema = df["close"].ewm(span=26, adjust=False).mean()
        df["macd"] = fast_ema - slow_ema
        df["macdsignal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["macdhist"] = df["macd"] - df["macdsignal"]

        # Awesome Oscillator = SMA(median_price, 5) - SMA(median_price, 34)
        median_price = (df["high"] + df["low"]) / 2.0
        df["ao"] = median_price.rolling(5).mean() - median_price.rolling(34).mean()

        return df

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        df = dataframe
        df["enter_long"] = 0
        df["enter_short"] = 0
        df["enter_tag"] = ""

        # Long: MACD crosses above Signal and AO is positive
        long_cond = (
            (df["macd"] > df["macdsignal"]) &
            (df["macd"].shift(1) <= df["macdsignal"].shift(1)) &
            (df["ao"] > 0)
        )
        df.loc[long_cond, "enter_long"] = 1
        df.loc[long_cond, "enter_tag"] = "macd_cross_ao_positive"

        # Short: MACD crosses below Signal and AO is negative
        short_cond = (
            (df["macd"] < df["macdsignal"]) &
            (df["macd"].shift(1) >= df["macdsignal"].shift(1)) &
            (df["ao"] < 0)
        )
        df.loc[short_cond, "enter_short"] = 1
        df.loc[short_cond, "enter_tag"] = "macd_cross_ao_negative"

        return df

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        df = dataframe
        df["exit_long"] = 0
        df["exit_short"] = 0
        df["exit_tag"] = ""

        # Exit long when MACD crosses below signal
        exit_long_cond = (df["macd"] < df["macdsignal"]) & (df["macd"].shift(1) >= df["macdsignal"].shift(1))
        df.loc[exit_long_cond, "exit_long"] = 1
        df.loc[exit_long_cond, "exit_tag"] = "macd_cross_down"

        # Exit short when MACD crosses above signal
        exit_short_cond = (df["macd"] > df["macdsignal"]) & (df["macd"].shift(1) <= df["macdsignal"].shift(1))
        df.loc[exit_short_cond, "exit_short"] = 1
        df.loc[exit_short_cond, "exit_tag"] = "macd_cross_up"

        return df
