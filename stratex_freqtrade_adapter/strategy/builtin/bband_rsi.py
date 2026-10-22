"""stratex_freqtrade_adapter/strategy/builtin/bband_rsi.py

Classic Bollinger Bands + RSI Mean Reversion Strategy.
"""

from __future__ import annotations
from typing import Any, Dict, Optional
import pandas as pd

from ..interface import IStrategy
from ..registry import registry


@registry.register("bband_rsi")
class BbandRsiStrategy(IStrategy):
    """Mean Reversion Strategy exploiting Bollinger Band extremes confirmed by RSI."""

    timeframe = "15m"
    minimal_roi = {
        "0": 0.03,
        "20": 0.015,
        "40": 0.005,
        "60": 0.0,
    }
    stoploss = -0.035
    trailing_stop = False
    can_short = True

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        df = dataframe
        # Bollinger Bands (20, 2.0)
        mid = df["close"].rolling(20).mean()
        std = df["close"].rolling(20).std()
        df["bb_lower"] = mid - (2.0 * std)
        df["bb_middle"] = mid
        df["bb_upper"] = mid + (2.0 * std)

        # RSI (14)
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / (loss + 1e-9)
        df["rsi"] = 100 - (100 / (1 + rs))

        # Volume rolling mean
        df["volume_mean"] = df["volume"].rolling(24).mean() if "volume" in df.columns else 1.0

        return df

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        df = dataframe
        df["enter_long"] = 0
        df["enter_short"] = 0
        df["enter_tag"] = ""

        # Long: Price pierces below lower band and RSI < 32 (oversold)
        long_cond = (
            (df["close"] <= df["bb_lower"]) &
            (df["rsi"] < 32)
        )
        df.loc[long_cond, "enter_long"] = 1
        df.loc[long_cond, "enter_tag"] = "bb_oversold_long"

        # Short: Price pierces above upper band and RSI > 68 (overbought)
        short_cond = (
            (df["close"] >= df["bb_upper"]) &
            (df["rsi"] > 68)
        )
        df.loc[short_cond, "enter_short"] = 1
        df.loc[short_cond, "enter_tag"] = "bb_overbought_short"

        return df

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        df = dataframe
        df["exit_long"] = 0
        df["exit_short"] = 0
        df["exit_tag"] = ""

        # Exit long when price reaches middle band or RSI > 60
        exit_long_cond = (df["close"] >= df["bb_middle"]) | (df["rsi"] > 60)
        df.loc[exit_long_cond, "exit_long"] = 1
        df.loc[exit_long_cond, "exit_tag"] = "bb_mid_or_rsi_normalized"

        # Exit short when price reaches middle band or RSI < 40
        exit_short_cond = (df["close"] <= df["bb_middle"]) | (df["rsi"] < 40)
        df.loc[exit_short_cond, "exit_short"] = 1
        df.loc[exit_short_cond, "exit_tag"] = "bb_mid_or_rsi_normalized"

        return df
