import pytest
import time
import pandas as pd
from paper_engine.market_data import MarketDataFeed, DataStaleException, DataException

def test_paper_market_data_stale():
    """
    Part 35: DATA QUALITY TEST
    Proves that stale data throws an exception.
    """
    feed = MarketDataFeed(max_stale_seconds=60)
    
    with pytest.raises(DataStaleException) as excinfo:
        feed.get_price("BTCUSDT")
    assert "uninitialized" in str(excinfo.value).lower()
        
    # Push old data
    feed.push_tick("BTCUSDT", 100.0, 99.9, 100.1, time.time() - 100)
    with pytest.raises(DataStaleException) as excinfo:
        feed.get_price("BTCUSDT")
    assert "stale" in str(excinfo.value).lower()
    
def test_paper_market_data_duplicate():
    """
    Proves duplicate/out of order timestamps are ignored.
    """
    feed = MarketDataFeed(max_stale_seconds=60)
    feed.push_tick("BTCUSDT", 100.0, 99.9, 100.1, time.time() - 10)
    assert feed.get_price("BTCUSDT") == 100.0
    
    # Push older data
    feed.push_tick("BTCUSDT", 90.0, 89.9, 90.1, time.time() - 20)
    # The price should still be 100.0 because the older tick was rejected
    assert feed.get_price("BTCUSDT") == 100.0
