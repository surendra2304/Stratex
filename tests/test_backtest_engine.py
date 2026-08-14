import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from backtest_engine import BacktestEngine, DataValidator

class MockStrategy:
    def __init__(self, signals):
        self.signals = signals # list of (signal, sl, tp)
        self.idx = 0
        self.__name__ = "mock"
        
    def get_signal(self, df):
        if self.idx < len(self.signals):
            sig = self.signals[self.idx]
            self.idx += 1
            return sig
        return None, None, None

def create_mock_data(rows):
    """Helper to create sequential mocked OHLC data."""
    base_time = datetime(2024, 1, 1)
    data = []
    for r in rows:
        data.append({
            "timestamp": base_time,
            "open": r[0], "high": r[1], "low": r[2], "close": r[3],
            "volume": 100, "taker_buy_base": 50
        })
        base_time += timedelta(minutes=1)
    return pd.DataFrame(data)

def test_data_validator_duplicate_candles():
    df = create_mock_data([[100, 105, 95, 102], [100, 105, 95, 102]])
    df.iloc[1, df.columns.get_loc('timestamp')] = df.iloc[0]['timestamp'] # duplicate timestamp
    with pytest.raises(ValueError, match="duplicate"):
        DataValidator.validate(df)

def test_data_validator_invalid_ohlc():
    df = create_mock_data([[100, 90, 110, 102]]) # high < low
    with pytest.raises(ValueError, match="invalid OHLC"):
        DataValidator.validate(df)

def test_winning_buy_with_fees_and_slippage():
    # Setup a trend that guarantees a win
    # Warmup 200 bars, then bar 201 entry, bar 202 exit via TP
    rows = [[100, 100, 100, 100]] * 200 # warmup
    # Entry bar: close is 100
    rows.append([100, 105, 95, 100])
    # Next bar: high hits 110 (TP is 105)
    rows.append([100, 110, 95, 105])
    
    df = create_mock_data(rows)
    
    # Engine starts calling at i=200
    signals = [("BUY", 90, 105)]
    strat = MockStrategy(signals)
    
    engine = BacktestEngine(df, strat, fee_rate=0.01, slippage_rate=0.01, initial_balance=10000, risk_per_trade=0.01)
    trades, equity = engine.run()
    
    assert len(trades) == 1
    t = trades[0]
    
    assert t['result'] == 'WIN'
    assert t['reason'] == 'TP_HIT'
    assert t['entry_price'] == 101.0 # 100 * 1.01 (slippage)
    assert t['exit_price'] == 103.95 # 105 * 0.99 (slippage on TP)
    
def test_losing_sell():
    rows = [[100, 100, 100, 100]] * 200
    # Entry bar: close is 100
    rows.append([100, 105, 95, 100])
    # Next bar: high hits 110 (SL is 105) -> Loss
    rows.append([100, 110, 95, 105])
    
    df = create_mock_data(rows)
    signals = [("SELL", 105, 90)]
    strat = MockStrategy(signals)
    
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0)
    trades, equity = engine.run()
    
    assert len(trades) == 1
    assert trades[0]['result'] == 'LOSS'
    assert trades[0]['reason'] == 'SL_HIT'
    
def test_same_candle_resolution_conservative():
    """Test 7: Both SL and TP hit on same candle."""
    rows = [[100, 100, 100, 100]] * 200
    # Entry bar: close 100
    rows.append([100, 105, 95, 100])
    # Next bar: high 110 (hits TP 105), low 90 (hits SL 95)
    rows.append([100, 110, 90, 100])
    
    df = create_mock_data(rows)
    signals = [("BUY", 95, 105)]
    strat = MockStrategy(signals)
    
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0)
    trades, equity = engine.run()
    
    assert len(trades) == 1
    # Must assume SL was hit first
    assert trades[0]['result'] == 'LOSS'
    assert trades[0]['reason'] == 'SL_HIT'

def test_position_sizing_max_open():
    """Tests 10 & 11: Sizing and Max Open Positions"""
    rows = [[100, 100, 100, 100]] * 200
    rows.append([100, 105, 95, 100]) # Entry 1
    rows.append([100, 104, 95, 100]) # Bar where Entry 2 would happen if max_open=2
    rows.append([100, 110, 95, 105]) # TP for Entry 1
    
    df = create_mock_data(rows)
    signals = [("BUY", 90, 105), ("BUY", 90, 105), (None, None, None)]
    
    # Engine tracks index inside strategy. If it checks signal on bar 201, it would consume the 2nd BUY.
    class TrackingMockStrategy:
        def __init__(self, signals):
            self.signals = signals
            self.idx = 0
            self.__name__ = "mock"
            self.calls = 0
            
        def get_signal(self, df):
            self.calls += 1
            if self.idx < len(self.signals):
                sig = self.signals[self.idx]
                self.idx += 1
                return sig
            return None, None, None
            
    strat = TrackingMockStrategy(signals)
    
    engine = BacktestEngine(df, strat, max_open_trades=1, fee_rate=0, slippage_rate=0, initial_balance=10000, risk_per_trade=0.01)
    trades, equity = engine.run()
    
    # Bar 200: open_trades=0 -> get_signal called (calls=1). Returns BUY (idx=1). Enters trade.
    # Bar 201: open_trades=1 -> get_signal NOT called (calls=1).
    # Bar 202: TP hit! Trade closed. open_trades=0 -> get_signal called (calls=2). Returns 2nd BUY (idx=2). Enters trade.
    # End of sim: Trade 2 is force closed.
    
    assert strat.calls == 2 # Proves it skipped checking on bar 201
    assert len(trades) == 2 
    
    # Check sizing
    assert trades[0]['quantity'] == 10.0
    
    # Verify sizing:
    # risk = 10000 * 0.01 = 100
    # sl_dist = 100 - 90 = 10
    # qty = 100 / 10 = 10
    assert trades[0]['quantity'] == 10.0
