"""stratex_backtrader_adapter/strategy.py

Strategy base class and canonical strategies inspired by Backtrader:
- Strategy: lifecycle hooks (init, next, start, stop, notify_order, notify_trade)
- Built-in strategies: SMACrossStrategy, RSIStrategy
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any
import pandas as pd

from .models import (
    OrderSide,
    OrderType,
    OrderStatus,
    BacktestOrder,
    TradeRecord,
)

if TYPE_CHECKING:
    from .broker import BacktraderBroker
    from .sizers import BaseSizer


class Strategy:
    """Base Strategy class replicating Backtrader's declarative execution lifecycle."""

    def __init__(
        self,
        broker: BacktraderBroker,
        data_feeds: dict[str, pd.DataFrame],
        sizer: BaseSizer | None = None,
        **params,
    ) -> None:
        self.broker = broker
        self.data_feeds = data_feeds
        self.params = params
        self.sizer = sizer

        # Primary data feed alias
        self.symbol = list(data_feeds.keys())[0] if data_feeds else "DEFAULT"
        self.data = data_feeds.get(self.symbol, pd.DataFrame())

        # Current bar index
        self._current_idx: int = 0
        self._pending_orders: list[BacktestOrder] = []

    @property
    def position(self) -> float:
        """Returns current position in primary data symbol."""
        return self.broker.get_position(self.symbol)

    def start(self) -> None:
        """Hook called before the backtest simulation starts."""
        pass

    def stop(self) -> None:
        """Hook called after the backtest simulation finishes."""
        pass

    def init(self) -> None:
        """Hook called to initialize technical indicators or state."""
        pass

    def next(self) -> None:
        """Hook called on every new bar step. Implement entry/exit logic here."""
        raise NotImplementedError

    def notify_order(self, order: BacktestOrder) -> None:
        """Hook called whenever an order's status changes."""
        pass

    def notify_trade(self, trade: TradeRecord) -> None:
        """Hook called whenever a closed trade is recorded."""
        pass

    def buy(
        self,
        symbol: str | None = None,
        size: float | None = None,
        price: float | None = None,
        order_type: OrderType = OrderType.MARKET,
    ) -> BacktestOrder:
        """Submits a BUY order to the broker."""
        sym = (symbol or self.symbol).upper()
        current_close = self._get_current_price(sym)

        if size is None and self.sizer is not None:
            size = self.sizer.get_size(self.broker, sym, current_close)
        val_size = size or 1.0

        order = BacktestOrder(
            order_id=f"ord_{uuid.uuid4().hex[:8]}",
            symbol=sym,
            side=OrderSide.BUY,
            order_type=order_type,
            size=val_size,
            price=price or current_close,
            status=OrderStatus.SUBMITTED,
            created_idx=self._current_idx,
        )
        self.broker.submit_order(order)
        self._pending_orders.append(order)
        self.notify_order(order)
        return order

    def sell(
        self,
        symbol: str | None = None,
        size: float | None = None,
        price: float | None = None,
        order_type: OrderType = OrderType.MARKET,
    ) -> BacktestOrder:
        """Submits a SELL order to the broker."""
        sym = (symbol or self.symbol).upper()
        current_close = self._get_current_price(sym)

        if size is None and self.sizer is not None:
            size = self.sizer.get_size(self.broker, sym, current_close)
        val_size = size or 1.0

        order = BacktestOrder(
            order_id=f"ord_{uuid.uuid4().hex[:8]}",
            symbol=sym,
            side=OrderSide.SELL,
            order_type=order_type,
            size=val_size,
            price=price or current_close,
            status=OrderStatus.SUBMITTED,
            created_idx=self._current_idx,
        )
        self.broker.submit_order(order)
        self._pending_orders.append(order)
        self.notify_order(order)
        return order

    def close(self, symbol: str | None = None) -> BacktestOrder | None:
        """Closes the existing position for symbol."""
        sym = (symbol or self.symbol).upper()
        current_pos = self.broker.get_position(sym)
        if current_pos == 0.0:
            return None

        if current_pos > 0:
            return self.sell(symbol=sym, size=current_pos)
        else:
            return self.buy(symbol=sym, size=abs(current_pos))

    def _get_current_price(self, symbol: str) -> float:
        df = self.data_feeds.get(symbol)
        if df is not None and not df.empty and self._current_idx < len(df):
            return float(df["close"].iloc[self._current_idx])
        return 1.0


# ------------------------------------------------------------------------------
# Built-in Canonical Strategies
# ------------------------------------------------------------------------------
class SMACrossStrategy(Strategy):
    """Dual Simple Moving Average Crossover Strategy."""

    def init(self) -> None:
        fast_p = self.params.get("fast_period", 10)
        slow_p = self.params.get("slow_period", 30)
        self.fast_sma = self.data["close"].rolling(window=fast_p).mean()
        self.slow_sma = self.data["close"].rolling(window=slow_p).mean()

    def next(self) -> None:
        idx = self._current_idx
        if idx < 1 or pd.isna(self.fast_sma.iloc[idx]) or pd.isna(self.slow_sma.iloc[idx]):
            return

        fast_curr = self.fast_sma.iloc[idx]
        fast_prev = self.fast_sma.iloc[idx - 1]
        slow_curr = self.slow_sma.iloc[idx]
        slow_prev = self.slow_sma.iloc[idx - 1]

        # Golden Cross (Fast crosses above Slow) -> BUY
        if fast_prev <= slow_prev and fast_curr > slow_curr:
            if self.position <= 0:
                if self.position < 0:
                    self.close()
                self.buy()

        # Death Cross (Fast crosses below Slow) -> SELL
        elif fast_prev >= slow_prev and fast_curr < slow_curr:
            if self.position >= 0:
                if self.position > 0:
                    self.close()
                self.sell()


class RSIStrategy(Strategy):
    """RSI Mean Reversion Strategy."""

    def init(self) -> None:
        period = self.params.get("period", 14)
        delta = self.data["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-9)
        self.rsi = 100 - (100 / (1 + rs))

        self.lower_band = self.params.get("lower", 30)
        self.upper_band = self.params.get("upper", 70)

    def next(self) -> None:
        idx = self._current_idx
        if idx < 1 or pd.isna(self.rsi.iloc[idx]):
            return

        rsi_val = self.rsi.iloc[idx]

        # Oversold -> BUY
        if rsi_val < self.lower_band and self.position <= 0:
            if self.position < 0:
                self.close()
            self.buy()

        # Overbought -> SELL
        elif rsi_val > self.upper_band and self.position >= 0:
            if self.position > 0:
                self.close()
            self.sell()
