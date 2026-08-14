import pytest
import os
import importlib
import execution
from unittest.mock import patch

def test_paper_safe_mode_blocks_execution():
    """Proves that PAPER mode blocks execution."""
    with patch("execution.TRADING_MODE", "PAPER"), patch("execution.PAPER_SAFE_MODE", True):
        with pytest.raises(RuntimeError, match="PAPER mode"):
            execution.place_market_order("test", "BUY", "BTCUSDT")

def test_research_mode_blocks_execution():
    """Proves that RESEARCH_MODE blocks execution even if config is somehow TESTNET."""
    with patch("execution.TRADING_MODE", "TESTNET"), patch("execution.TESTNET_ENABLED", True), patch("execution.PAPER_SAFE_MODE", False):
        with patch.dict(os.environ, {"RESEARCH_MODE": "1"}):
            with pytest.raises(RuntimeError, match="research script"):
                execution.place_market_order("test", "BUY", "BTCUSDT")

def test_testnet_requires_explicit_enable():
    """Proves that TESTNET without TESTNET_ENABLED fails."""
    with patch("execution.TRADING_MODE", "TESTNET"), patch("execution.TESTNET_ENABLED", False), patch("execution.PAPER_SAFE_MODE", False):
        with pytest.raises(RuntimeError, match="TESTNET_ENABLED is false"):
            execution.place_market_order("test", "BUY", "BTCUSDT")

def test_live_requires_explicit_enable():
    """Proves that LIVE without LIVE_TRADING_ENABLED fails."""
    with patch("execution.TRADING_MODE", "LIVE"), patch("execution.LIVE_TRADING_ENABLED", False), patch("execution.PAPER_SAFE_MODE", False):
        with pytest.raises(RuntimeError, match="LIVE_TRADING_ENABLED is false"):
            execution.place_market_order("test", "BUY", "BTCUSDT")
