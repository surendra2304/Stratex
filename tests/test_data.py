import pandas as pd

from data import add_indicators


def test_add_indicators_empty_df():
    """Test that add_indicators safely handles an empty DataFrame."""
    df = pd.DataFrame()
    result = add_indicators(df)
    assert result.empty

def test_add_indicators_none():
    """Test that add_indicators safely handles None."""
    result = add_indicators(None)
    assert result is None

def test_add_indicators_insufficient_data():
    """Test that add_indicators safely handles a df with too few rows."""
    df = pd.DataFrame({"close": [1, 2, 3]})
    result = add_indicators(df)
    assert len(result) == 3 # Should return unmodified
    assert "rsi" not in result.columns

def test_add_indicators_valid_data():
    """Test that add_indicators adds the correct columns to valid data."""
    # Create dummy data with 250 rows to satisfy window=200
    df = pd.DataFrame({
        "close": range(250),
        "high": range(1, 251),
        "low": range(-1, 249),
        "open": range(250),
        "volume": [100] * 250,
        "timestamp": pd.date_range("2024-01-01", periods=250)
    })
    
    result = add_indicators(df)
    
    assert not result.empty
    assert "ema_200" in result.columns
    assert "rsi" in result.columns
    assert "macd" in result.columns
    assert "atr" in result.columns
    
    # Check that rows with NaN from indicator warmup were dropped
    # e.g., ema_200 requires 200 rows
    assert len(result) < 250
