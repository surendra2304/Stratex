import time
from datetime import datetime
import pandas as pd
from typing import Dict, Optional, Tuple

class DataException(Exception):
    pass

class DataStaleException(DataException):
    pass

class DataGapException(DataException):
    pass

class MarketDataFeed:
    """
    Paper Trading Market Data Layer.
    Provides decoupled access to recent OHLCV, ticks, and Bid/Ask, 
    ensuring time ordering and preventing stale data trades.
    """
    def __init__(self, max_stale_seconds=60):
        self.max_stale_seconds = max_stale_seconds
        self.last_update_time: float = 0.0
        self.last_candle_time: pd.Timestamp = None
        self.current_prices: Dict[str, float] = {}
        self.current_spreads: Dict[str, Tuple[float, float]] = {} # (bid, ask)
        
    def push_tick(self, symbol: str, price: float, bid: float, ask: float, timestamp_sec: float):
        """
        Receives live updates (e.g. from websocket or polling).
        """
        if timestamp_sec <= self.last_update_time and self.last_update_time > 0:
            # Ignore out of order or duplicate
            return
            
        self.last_update_time = timestamp_sec
        self.current_prices[symbol] = price
        self.current_spreads[symbol] = (bid, ask)
        
    def get_price(self, symbol: str) -> float:
        """
        Gets current price, raising an error if data is too stale.
        """
        self._check_stale()
        if symbol not in self.current_prices:
            raise DataException(f"No price available for {symbol}")
        return self.current_prices[symbol]
        
    def get_bbo(self, symbol: str) -> Tuple[float, float]:
        """
        Gets Best Bid and Offer.
        """
        self._check_stale()
        if symbol not in self.current_spreads:
            raise DataException(f"No BBO available for {symbol}")
        return self.current_spreads[symbol]
        
    def _check_stale(self):
        now = time.time()
        if self.last_update_time == 0:
            raise DataStaleException("Market Data Feed uninitialized. No ticks received.")
        if now - self.last_update_time > self.max_stale_seconds:
            raise DataStaleException(f"Data is stale. Last update was {now - self.last_update_time:.1f}s ago.")

    def push_candle_df(self, symbol: str, df: pd.DataFrame):
        """
        In a purely paper setup without websockets, we might just poll REST for candles.
        This updates the 'current price' based on the latest candle close.
        """
        if df.empty:
            raise DataException("Empty DataFrame pushed to MarketDataFeed")
            
        latest_time = df['timestamp'].iloc[-1]
        latest_close = df['close'].iloc[-1]
        
        # Estimate spread if we don't have BBO (e.g., 0.02% spread)
        spread_bps = 0.0002
        bid = latest_close * (1 - spread_bps/2)
        ask = latest_close * (1 + spread_bps/2)
        
        # Convert timestamp to seconds if needed
        if isinstance(latest_time, pd.Timestamp):
            ts = latest_time.timestamp()
        else:
            ts = time.time() # fallback if parsing fails
            
        self.push_tick(symbol, latest_close, bid, ask, ts)
