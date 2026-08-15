import pytest
import os
import importlib
from unittest import mock

def reload_modules():
    import config
    import execution
    importlib.reload(config)
    importlib.reload(execution)
    return config, execution

class TestExecutionSafety:

    @mock.patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "True", "LIVE_TRADING_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy"})
    def test_testnet_mode_cannot_build_live_client(self):
        """Proof that when TRADING_MODE=TESTNET, the client is explicitly forced to use testnet endpoints."""
        config, execution = reload_modules()
        
        # Verify config resolution
        assert config.TRADING_MODE == "TESTNET"
        assert config.TESTNET_ENABLED is True
        assert config.LIVE_TRADING_ENABLED is False
        assert config.PAPER_SAFE_MODE is False
        
        # Ensure policy allows testnet
        allowed, reason = execution.ExecutionPolicy.can_place_order()
        assert allowed is True
        assert reason == "ALLOWED_TESTNET"
        
        # Build client and assert it's strictly testnet
        with mock.patch("binance.client.Client.ping"):
            client = execution.get_exchange_client()
        assert client is not None
        
        # In python-binance, client.testnet is True when built with testnet=True
        # We also verify it doesn't default to the production URL by ensuring testnet is set.
        assert getattr(client, 'testnet', False) is True
        
    @mock.patch.dict(os.environ, {"TRADING_MODE": "LIVE", "LIVE_TRADING_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy"})
    def test_live_mode_fails_if_not_explicitly_enabled(self):
        """Proof that LIVE mode fails to build client if LIVE_TRADING_ENABLED is False."""
        config, execution = reload_modules()
        
        assert config.TRADING_MODE == "LIVE"
        
        allowed, reason = execution.ExecutionPolicy.can_place_order()
        assert allowed is False
        assert reason == "LIVE_DISABLED"
        
        with pytest.raises(RuntimeError, match="CRITICAL ERROR: LIVE execution attempted but LIVE_TRADING_ENABLED is false."):
            execution.get_exchange_client()
            
    @mock.patch.dict(os.environ, {"TRADING_MODE": "PAPER", "PAPER_SAFE_MODE": "True", "API_KEY": "dummy", "SECRET_KEY": "dummy"})
    def test_paper_mode_makes_zero_exchange_calls(self):
        """Proof that PAPER mode explicitly returns None for the execution client."""
        config, execution = reload_modules()
        
        assert config.TRADING_MODE == "PAPER"
        
        client = execution.get_exchange_client()
        assert client is None
        
    @mock.patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy"})
    def test_testnet_fails_if_testnet_disabled(self):
        """Proof that TESTNET mode fails if TESTNET_ENABLED is False."""
        config, execution = reload_modules()
        
        assert config.TRADING_MODE == "TESTNET"
        
        allowed, reason = execution.ExecutionPolicy.can_place_order()
        assert allowed is False
        assert reason == "TESTNET_DISABLED"
        
        with pytest.raises(RuntimeError, match="CRITICAL ERROR: TESTNET execution attempted but TESTNET_ENABLED is false."):
            execution.get_exchange_client()
