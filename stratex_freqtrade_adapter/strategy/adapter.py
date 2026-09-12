"""stratex_freqtrade_adapter/strategy/adapter.py

Bridges any Freqtrade IStrategy into Stratex's execution pipeline.
Exposes standard get_signal(df) -> SignalResult compatible with:
- testnet_engine
- paper_forward_runner
- backtest_engine
- FRIDAY supervision
"""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
import numpy as np
import pandas as pd

from .interface import IStrategy
from ..roi.roi_engine import ROIEngine
from ..roi.trailing_engine import TrailingStopEngine

class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    """Stratex-standard SignalResult tuple."""
    @property
    def confidence(self) -> float:
        return float(self.win_rate_prior)


class FreqtradeStrategyAdapter:
    """Wraps an IStrategy instance so it acts as a native Stratex strategy."""

    def __init__(self, strategy: IStrategy, name: Optional[str] = None):
        self.strategy = strategy
        self.__name__ = name or getattr(strategy, "__name__", strategy.__class__.__name__)
        self.roi_engine = ROIEngine(strategy.minimal_roi)
        self.trailing_engine = TrailingStopEngine(
            trailing_stop=strategy.trailing_stop,
            trailing_stop_positive=strategy.trailing_stop_positive,
            trailing_stop_positive_offset=strategy.trailing_stop_positive_offset,
            trailing_only_offset_is_reached=strategy.trailing_only_offset_is_reached,
        )
        self.strategy_type = "FREQTRADE_RULE"
        self.win_rate_prior = 0.55  # Baseline prior

    def get_signal(self, df: pd.DataFrame, pair: str = "BTCUSDT") -> SignalResult:
        """Evaluates indicators, entry signals, and returns Stratex SignalResult."""
        _NO_SIGNAL = SignalResult(None, None, None, self.strategy_type, self.win_rate_prior, 1.5)

        if df is None or len(df) < 5:
            return _NO_SIGNAL

        # Clone dataframe to avoid mutating caller's data
        work_df = df.copy()
        metadata = {"pair": pair, "timeframe": self.strategy.timeframe}

        try:
            work_df = self.strategy.populate_indicators(work_df, metadata)
            work_df = self.strategy.populate_entry_trend(work_df, metadata)
            work_df = self.strategy.populate_exit_trend(work_df, metadata)
        except Exception as e:
            return _NO_SIGNAL

        last_idx = work_df.index[-1]
        last_row = work_df.loc[last_idx]

        # Check for enter signals (supports both Freqtrade modern and legacy column names)
        enter_long = bool(last_row.get("enter_long", last_row.get("buy", 0)))
        enter_short = bool(last_row.get("enter_short", 0)) and self.strategy.can_short

        current_price = float(last_row.get("close", 0.0))
        if current_price <= 0:
            return _NO_SIGNAL

        # Calculate initial Stop Loss from strategy.stoploss (negative float, e.g. -0.05)
        sl_pct = abs(float(self.strategy.stoploss))

        # Calculate Take Profit from lowest tier of minimal_roi, or fallback to 1.5x SL
        initial_roi_target = self.roi_engine.get_initial_target()
        tp_pct = initial_roi_target if initial_roi_target > 0 else (sl_pct * 1.5)
        rr_ratio = round(tp_pct / sl_pct, 2) if sl_pct > 0 else 1.5

        if enter_long and not enter_short:
            sl = current_price * (1.0 - sl_pct)
            tp = current_price * (1.0 + tp_pct)
            return SignalResult("BUY", round(sl, 4), round(tp, 4), self.strategy_type, self.win_rate_prior, rr_ratio)

        if enter_short and not enter_long:
            sl = current_price * (1.0 + sl_pct)
            tp = current_price * (1.0 - tp_pct)
            return SignalResult("SELL", round(sl, 4), round(tp, 4), self.strategy_type, self.win_rate_prior, rr_ratio)

        return _NO_SIGNAL

    def check_exit(
        self,
        trade: Dict[str, Any],
        current_rate: float,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """Evaluates whether an active trade should exit based on Freqtrade rules:
        1. Custom exit hook
        2. Time-decayed minimal_roi
        3. Trailing stop / custom stoploss
        """
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        open_rate = float(trade.get("entry_price", trade.get("open_rate", current_rate)))
        side = str(trade.get("side", "BUY")).upper()
        open_time = trade.get("open_time", trade.get("timestamp"))

        # Calculate profit ratio
        if side in ("BUY", "LONG"):
            current_profit = (current_rate - open_rate) / open_rate if open_rate > 0 else 0.0
        else:
            current_profit = (open_rate - current_rate) / open_rate if open_rate > 0 else 0.0

        # 1. Custom Exit hook
        pair = str(trade.get("symbol", trade.get("pair", "BTCUSDT")))
        custom_exit_reason = self.strategy.custom_exit(
            pair=pair,
            trade=trade,
            current_time=current_time,
            current_rate=current_rate,
            current_profit=current_profit,
        )
        if custom_exit_reason:
            return True, f"custom_exit_{custom_exit_reason}"

        # 2. ROI Exit
        if open_time:
            if isinstance(open_time, (int, float)):
                open_dt = datetime.fromtimestamp(open_time / 1000.0 if open_time > 1e11 else open_time, tz=timezone.utc)
            elif isinstance(open_time, str):
                try:
                    open_dt = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
                except Exception:
                    open_dt = current_time
            else:
                open_dt = open_time

            duration_minutes = max(0.0, (current_time - open_dt).total_seconds() / 60.0)
            should_roi_exit, roi_target = self.roi_engine.should_exit(duration_minutes, current_profit)
            if should_roi_exit:
                return True, f"roi_target_{round(roi_target * 100, 2)}pct"

        # 3. Trailing Stop
        max_rate = float(trade.get("max_rate", max(open_rate, current_rate)))
        should_trailing_exit, stop_price = self.trailing_engine.should_exit(
            side=side,
            open_rate=open_rate,
            current_rate=current_rate,
            max_rate=max_rate,
        )
        if should_trailing_exit:
            return True, "trailing_stop_loss"

        return False, "NONE"
