import time
from datetime import datetime
import pandas as pd
import math
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
        
        # We track both the exchange's time and when we got it locally.
        self.last_received_time: float = 0.0
        self.last_market_time: float = 0.0
        
        self.current_prices: Dict[str, float] = {}
        self.current_spreads: Dict[str, Tuple[float, float, str]] = {} # (bid, ask, bbo_source)
        
    def _validate_data(self, price: float, bid: float, ask: float):
        if math.isnan(price) or math.isinf(price) or price <= 0:
            raise DataException(f"Invalid price: {price}")
        if math.isnan(bid) or math.isinf(bid) or bid <= 0:
            raise DataException(f"Invalid bid: {bid}")
        if math.isnan(ask) or math.isinf(ask) or ask <= 0:
            raise DataException(f"Invalid ask: {ask}")
        if bid > ask:
            raise DataException(f"Invalid spread: bid {bid} > ask {ask}")

    def push_tick(self, symbol: str, price: float, bid: float, ask: float, market_timestamp: float, bbo_source: str = "REAL"):
        """
        Receives live updates (e.g. from websocket or polling).
        """
        if market_timestamp is None or math.isnan(market_timestamp):
             raise DataException("Invalid market_timestamp")
             
        self._validate_data(price, bid, ask)

        if market_timestamp <= self.last_market_time and self.last_market_time > 0:
            raise DataException(f"Out of order or duplicate timestamp. Received: {market_timestamp}, Last: {self.last_market_time}")
            
        self.last_market_time = market_timestamp
        self.last_received_time = time.time()
        
        self.current_prices[symbol] = price
        self.current_spreads[symbol] = (bid, ask, bbo_source)
        
        try:
            from paper_engine.heartbeat import HeartbeatState
            HeartbeatState().ping_data()
        except:
            pass
        
    def get_price(self, symbol: str) -> float:
        """
        Gets current price, raising an error if data is too stale.
        """
        self._check_stale()
        if symbol not in self.current_prices:
            raise DataException(f"No price available for {symbol}")
        return self.current_prices[symbol]
        
    def get_bbo(self, symbol: str) -> Tuple[float, float, str]:
        """
        Gets Best Bid and Offer.
        """
        self._check_stale()
        if symbol not in self.current_spreads:
            raise DataException(f"No BBO available for {symbol}")
        return self.current_spreads[symbol]
        
    def _check_stale(self):
        now = time.time()
        if self.last_received_time == 0:
            raise DataStaleException("Market Data Feed uninitialized. No ticks received.")
        if now - self.last_received_time > self.max_stale_seconds:
            raise DataStaleException(f"Data is stale. Last update was {now - self.last_received_time:.1f}s ago locally.")

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
        elif isinstance(latest_time, (int, float)):
            ts = float(latest_time)
        else:
            raise DataException(f"Cannot parse market timestamp: {latest_time}")
            
        self.push_tick(symbol, latest_close, bid, ask, ts, bbo_source="ESTIMATED")
