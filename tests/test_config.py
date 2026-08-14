import pytest
import config

def test_valid_config():
    """Test that the current config passes validation."""
    try:
        config.validate_config()
    except ValueError as e:
        pytest.fail(f"validate_config() raised ValueError unexpectedly: {e}")

def test_invalid_strategy(monkeypatch):
    monkeypatch.setattr(config, "ACTIVE_STRATEGY", "invalid_strat")
    with pytest.raises(ValueError, match="Invalid ACTIVE_STRATEGY"):
        config.validate_config()

def test_invalid_qty(monkeypatch):
    monkeypatch.setattr(config, "TRADE_QTY", -1.0)
    with pytest.raises(ValueError, match="TRADE_QTY must be a positive number"):
        config.validate_config()

def test_invalid_timeframe(monkeypatch):
    monkeypatch.setattr(config, "TIMEFRAME", "2m") # Invalid
    with pytest.raises(ValueError, match="Invalid TIMEFRAME"):
        config.validate_config()
