import pytest
import os
from unittest.mock import patch

import importlib

def reload_modules():
    import config
    import execution
    importlib.reload(config)
    importlib.reload(execution)
    return config, execution

@patch.dict(os.environ, {"TRADING_MODE": "PAPER", "PAPER_SAFE_MODE": "True"})

def test_paper_mode_execution_block():
    """
    Part 34: PAPER TRADING SAFETY TEST
    Proves that PAPER MODE cannot call create_order, cancel_order, or Binance APIs.
    """
    config, execution = reload_modules()
    assert execution.get_exchange_client() is None, "Client should not be initialized in PAPER mode"
    
    with pytest.raises(RuntimeError) as excinfo:
        execution.place_market_order("test", "BUY", "BTCUSDT", 0.001)
        
    assert "PAPER mode attempted to place a real Binance order" in str(excinfo.value)
