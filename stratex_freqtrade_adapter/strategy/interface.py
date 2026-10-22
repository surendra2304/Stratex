"""stratex_freqtrade_adapter/strategy/interface.py

Defines the IStrategy base class matching Freqtrade's standard strategy contract.
Vectorized pandas DataFrame pipelines with standard buy/sell/exit tags.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import pandas as pd


class IStrategy(ABC):
    """Abstract Base Class for Freqtrade-compatible strategies in Stratex."""

    # Default strategy properties
    timeframe: str = "5m"
    minimal_roi: Dict[str, float] = {
        "0": 0.05,
        "30": 0.03,
        "60": 0.015,
        "120": 0.0,
    }
    stoploss: float = -0.05  # -5% default stoploss
    trailing_stop: bool = False
    trailing_stop_positive: float = 0.01
    trailing_stop_positive_offset: float = 0.02
    trailing_only_offset_is_reached: bool = False
    can_short: bool = True
    process_only_new_candles: bool = True
    use_custom_stoploss: bool = False

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    @abstractmethod
    def populate_indicators(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """Populate technical indicators into dataframe. Must return dataframe."""
        return dataframe

    @abstractmethod
    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """Populate entry signals.
        Sets 'enter_long' / 'buy', 'enter_short', and optionally 'enter_tag'.
        Must return dataframe.
        """
        return dataframe

    @abstractmethod
    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """Populate exit signals.
        Sets 'exit_long' / 'sell', 'exit_short', and optionally 'exit_tag'.
        Must return dataframe.
        """
        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Any,
        current_time: Any,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> float:
        """Custom stoploss hook. Return new stoploss percentage (negative, e.g. -0.02) or None."""
        return self.stoploss

    def custom_exit(
        self,
        pair: str,
        trade: Any,
        current_time: Any,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> Optional[str]:
        """Custom exit hook. Return string exit reason (e.g. 'custom_rsi_exit') or None."""
        return None
