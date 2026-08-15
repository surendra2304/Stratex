import pytest
import pandas as pd
import strategy_scalper
import strategy_swing
import strategy_ml
import strategy_aggressor

strategies = [
    strategy_scalper,
    strategy_swing,
    strategy_ml,
    strategy_aggressor
]

@pytest.mark.parametrize("strat", strategies)
def test_strategy_empty_df(strat):
    """Test that all strategies handle an empty DataFrame safely."""
    df = pd.DataFrame()
    out = strat.get_signal(df)
    assert out[0] is None

@pytest.mark.parametrize("strat", strategies)
def test_strategy_none_df(strat):
    """Test that all strategies handle None safely."""
    out = strat.get_signal(None)
    assert out[0] is None

@pytest.mark.parametrize("strat", strategies)
def test_strategy_insufficient_data(strat):
    """Test that all strategies handle DataFrames with < 2 rows (or < 50 for ML) safely."""
    df = pd.DataFrame({"close": [1]})
    out = strat.get_signal(df)
    assert out[0] is None
