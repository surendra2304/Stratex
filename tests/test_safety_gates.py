import pytest
import os
import execution
from unittest.mock import patch

def test_safety_matrix():
    cases = [
        # PAPER tests
        ("PAPER", True, "0", False, False, False, "PAPER_BLOCKED"),
        ("PAPER", False, "0", True, True, False, "PAPER_BLOCKED"),
        ("PAPER", False, "1", True, True, False, "PAPER_BLOCKED"),
        
        # TESTNET tests
        ("TESTNET", False, "0", False, True, False, "TESTNET_DISABLED"),
        ("TESTNET", False, "0", True, False, True, "ALLOWED_TESTNET"),
        ("TESTNET", True, "1", True, False, False, "PAPER_BLOCKED"), # PAPER_SAFE_MODE=True blocks first
        
        # LIVE tests
        ("LIVE", False, "1", True, True, False, "RESEARCH_BLOCKED"),
        ("LIVE", True, "0", False, True, False, "PAPER_BLOCKED"), 
        ("LIVE", False, "0", False, False, False, "LIVE_DISABLED"),
        ("LIVE", False, "0", False, True, True, "ALLOWED_LIVE"),
    ]
    
    for mode, safe_mode, res_mode, testnet_en, live_en, exp_allow, exp_reason in cases:
        with patch("execution.TRADING_MODE", mode), \
             patch("execution.PAPER_SAFE_MODE", safe_mode), \
             patch.dict(os.environ, {"RESEARCH_MODE": res_mode}), \
             patch("execution.TESTNET_ENABLED", testnet_en), \
             patch("execution.LIVE_TRADING_ENABLED", live_en):
            
            allowed, reason = execution.ExecutionPolicy.can_place_order()
            
            assert allowed == exp_allow, f"Failed allowed check for config: {mode}, {safe_mode}, {res_mode}, {testnet_en}, {live_en}"
            assert reason == exp_reason, f"Failed reason check for config: {mode}, {safe_mode}, {res_mode}, {testnet_en}, {live_en}"

def test_paper_mode_execution_block():
    with patch("execution.TRADING_MODE", "PAPER"), \
         patch("execution.PAPER_SAFE_MODE", True):
        if "RESEARCH_MODE" in os.environ:
            del os.environ["RESEARCH_MODE"]
            
        with pytest.raises(RuntimeError, match="PAPER mode attempted to place"):
            execution.place_market_order("test", "BUY", "BTCUSDT")

def test_live_requires_explicit_enable():
    with patch("execution.TRADING_MODE", "LIVE"), \
         patch("execution.LIVE_TRADING_ENABLED", False), \
         patch("execution.PAPER_SAFE_MODE", False):
        if "RESEARCH_MODE" in os.environ:
            del os.environ["RESEARCH_MODE"]
            
        with pytest.raises(RuntimeError, match="LIVE execution attempted"):
            execution.place_market_order("test", "BUY", "BTCUSDT")
