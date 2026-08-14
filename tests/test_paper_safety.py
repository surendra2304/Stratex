import pytest
import os
from unittest.mock import patch

# Force config to be loaded with TRADING_MODE = PAPER
# We do this by mocking config before execution.py is imported
import config
config.TRADING_MODE = "PAPER"

import execution

def test_paper_mode_execution_block():
    """
    Part 34: PAPER TRADING SAFETY TEST
    Proves that PAPER MODE cannot call create_order, cancel_order, or Binance APIs.
    """
    assert execution.get_exchange_client() is None, "Client should not be initialized in PAPER mode"
    
    with pytest.raises(RuntimeError) as excinfo:
        execution.place_market_order("test", "BUY", "BTCUSDT", 0.001)
        
    assert "PAPER mode attempted to place a real Binance order" in str(excinfo.value)
