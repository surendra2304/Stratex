import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from backtest_engine import BacktestEngine, DataValidator
from metrics import calculate_metrics, calculate_drawdown

class MockStrategy:
    def __init__(self, signals):
        # signals is a list of (signal, sl, tp) generated on subsequent calls
        self.signals = signals
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

def test_A_buy_reaches_tp():
    # TEST A: BUY entry -> price reaches TP -> verify profit.
    rows = [[100, 100, 100, 100]] * 200
    rows.append([100, 100, 100, 100]) # Bar 200: Signal generated at close 100
    rows.append([100, 100, 100, 100]) # Bar 201: Trade entered at Open 100
    rows.append([100, 115, 95, 110])  # Bar 202: High hits 115 (TP=110 hit)
    
    df = create_mock_data(rows)
    strat = MockStrategy([("BUY", 90, 110)])
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0)
    trades, equity = engine.run()
    
    assert len(trades) == 1
    assert trades[0]['result'] == 'WIN'
    assert trades[0]['reason'] == 'TP_HIT'
    assert trades[0]['exit_price'] == 110.0
    assert trades[0]['gross_pnl'] > 0

def test_B_buy_reaches_sl():
    # TEST B: BUY entry -> price reaches SL -> verify loss.
    rows = [[100, 100, 100, 100]] * 200
    rows.append([100, 100, 100, 100]) # Bar 200: Signal
    rows.append([100, 100, 100, 100]) # Bar 201: Entry at 100
    rows.append([100, 105, 85, 90])   # Bar 202: Low hits 85 (SL=90 hit)
    
    df = create_mock_data(rows)
    strat = MockStrategy([("BUY", 90, 110)])
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0)
    trades, equity = engine.run()
    
    assert len(trades) == 1
    assert trades[0]['result'] == 'LOSS'
    assert trades[0]['reason'] == 'SL_HIT'
    assert trades[0]['exit_price'] == 90.0

def test_C_same_candle_sl_and_tp():
    # TEST C: BUY entry -> both SL and TP occur in the same candle -> verify conservative mode.
    rows = [[100, 100, 100, 100]] * 200
    rows.append([100, 100, 100, 100]) # Bar 200: Signal
    rows.append([100, 115, 85, 100])  # Bar 201: Entry at 100, High=115 (TP=110), Low=85 (SL=90)
    
    df = create_mock_data(rows)
    strat = MockStrategy([("BUY", 90, 110)])
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0, intrabar_resolution="conservative")
    trades, equity = engine.run()
    
    assert len(trades) == 1
    assert trades[0]['result'] == 'LOSS'
    assert trades[0]['reason'] == 'SL_HIT'

def test_D_open_trade_remains_open():
    # TEST D: BUY entry -> neither SL nor TP occurs -> verify open position remains open (until end).
    rows = [[100, 100, 100, 100]] * 200
    rows.append([100, 100, 100, 100]) # Bar 200: Signal
    rows.append([100, 105, 95, 100])  # Bar 201: Entry at 100
    rows.append([100, 105, 95, 102])  # Bar 202: No SL/TP hit.
    
    df = create_mock_data(rows)
    strat = MockStrategy([("BUY", 90, 110)])
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0)
    trades, equity = engine.run()
    
    assert len(trades) == 1
    assert trades[0]['reason'] == 'TIME_EXIT'
    assert trades[0]['exit_price'] == 102.0

def test_E_time_exit_pnl():
    # TEST E: Final open trade -> time exit -> verify correct PnL.
    rows = [[100, 100, 100, 100]] * 200
    rows.append([100, 100, 100, 100]) # Bar 200: Signal
    rows.append([100, 105, 95, 100])  # Bar 201: Entry at 100
    rows.append([100, 108, 95, 108])  # Bar 202: Time exit at Close=108
    
    df = create_mock_data(rows)
    strat = MockStrategy([("BUY", 90, 110)])
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0)
    trades, equity = engine.run()
    
    assert trades[0]['gross_pnl'] == (108.0 - 100.0) * trades[0]['quantity']

def test_F_fee_only_accounting():
    # TEST F: Fee-only trade -> verify exact fee accounting.
    rows = [[100, 100, 100, 100]] * 200
    rows.append([100, 100, 100, 100]) # Bar 200: Signal
    rows.append([100, 100, 100, 100]) # Bar 201: Entry at 100
    rows.append([100, 100, 100, 100]) # Bar 202: Exit at 100
    
    df = create_mock_data(rows)
    strat = MockStrategy([("BUY", 90, 110)])
    # 0.1% fee
    engine = BacktestEngine(df, strat, fee_rate=0.001, slippage_rate=0, risk_per_trade=0.01)
    trades, equity = engine.run()
    
    t = trades[0]
    expected_entry_fee = 100.0 * t['quantity'] * 0.001
    expected_exit_fee = 100.0 * t['quantity'] * 0.001
    
    assert t['fees'] == expected_entry_fee + expected_exit_fee
    assert t['net_pnl'] == -(expected_entry_fee + expected_exit_fee)

def test_G_known_slippage():
    # TEST G: Known slippage -> verify exact executed price.
    rows = [[100, 100, 100, 100]] * 200
    rows.append([100, 100, 100, 100]) # Bar 200: Signal
    rows.append([100, 100, 100, 100]) # Bar 201: Entry at Open=100
    rows.append([100, 110, 100, 100]) # Bar 202: Exit at TP=110
    
    df = create_mock_data(rows)
    strat = MockStrategy([("BUY", 90, 110)])
    
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0.01)
    trades, equity = engine.run()
    
    t = trades[0]
    # Slippage 1% on BUY entry -> 100 * 1.01 = 101.0
    assert t['entry_price'] == 101.0
    # Slippage 1% on SELL exit -> 110 * 0.99 = 108.9
    assert t['exit_price'] == 108.9

def test_H_known_position_size():
    # TEST H: Known position size -> verify exact risk amount.
    rows = [[100, 100, 100, 100]] * 200
    rows.append([100, 100, 100, 100]) # Bar 200: Signal
    rows.append([100, 100, 100, 100]) # Bar 201: Entry Open=100
    
    df = create_mock_data(rows)
    strat = MockStrategy([("BUY", 90, 110)])
    
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0, initial_balance=10000, risk_per_trade=0.01)
    trades, equity = engine.run()
    
    # Balance = 10000. Risk = 1%. Risk amount = 100.
    # Entry = 100. SL = 90. Distance = 10.
    # Qty = Risk / Distance = 100 / 10 = 10.0
    assert trades[0]['quantity'] == 10.0

def test_I_consecutive_trades():
    # TEST I: Two consecutive trades -> verify balance/equity transitions.
    rows = [[100, 100, 100, 100]] * 200
    rows.append([100, 100, 100, 100]) # Bar 200: Signal 1
    rows.append([100, 100, 100, 100]) # Bar 201: Entry 1 Open=100
    rows.append([100, 110, 100, 110]) # Bar 202: Exit 1 TP=110. Signal 2!
    rows.append([110, 110, 110, 110]) # Bar 203: Entry 2 Open=110
    rows.append([110, 120, 110, 120]) # Bar 204: Exit 2 TP=120
    
    df = create_mock_data(rows)
    strat = MockStrategy([("BUY", 90, 110), ("BUY", 100, 120)])
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0, initial_balance=10000, risk_per_trade=0.01)
    trades, equity = engine.run()
    
    assert len(trades) == 2
    
    # Trade 1
    # Risk = 100, Dist = 10 -> Qty = 10
    # Profit = (110 - 100) * 10 = +100. New balance = 10100.
    assert trades[0]['net_pnl'] == 100.0
    
    # Trade 2
    # Risk = 10100 * 0.01 = 101, Dist = 10 -> Qty = 10.1
    # Profit = (120 - 110) * 10.1 = +101. New balance = 10201.
    assert trades[1]['net_pnl'] == 101.0
    assert engine.balance == 10201.0

def test_K_no_trades():
    # TEST K: No trades -> verify metrics.
    rows = [[100, 100, 100, 100]] * 205
    df = create_mock_data(rows)
    strat = MockStrategy([])
    engine = BacktestEngine(df, strat, fee_rate=0, slippage_rate=0)
    trades, equity = engine.run()
    
    assert len(trades) == 0
    metrics = calculate_metrics(trades, equity, 10000)
    assert metrics['net_pnl'] == 0.0
    assert metrics['profit_factor'] == 0.0

def test_L_M_all_winners_losers():
    # TEST L: All winners -> verify metrics.
    trades_w = [
        {'net_pnl': 100, 'holding_time': 5, 'r_multiple': 1.0},
        {'net_pnl': 200, 'holding_time': 5, 'r_multiple': 2.0}
    ]
    eq_df = pd.DataFrame({'timestamp': pd.to_datetime(['2024-01-01', '2024-01-02']), 'equity': [10000, 10300]})
    metrics_w = calculate_metrics(trades_w, eq_df, 10000)
    assert metrics_w['win_rate'] == 100.0
    assert metrics_w['profit_factor'] == float('inf') # All winners = inf PF
    
    # TEST M: All losers -> verify metrics.
    trades_l = [
        {'net_pnl': -100, 'holding_time': 5, 'r_multiple': -1.0},
        {'net_pnl': -200, 'holding_time': 5, 'r_multiple': -2.0}
    ]
    eq_df_l = pd.DataFrame({'timestamp': pd.to_datetime(['2024-01-01', '2024-01-02']), 'equity': [10000, 9700]})
    metrics_l = calculate_metrics(trades_l, eq_df_l, 10000)
    assert metrics_l['win_rate'] == 0.0
    assert metrics_l['profit_factor'] == 0.0

def test_N_known_drawdown():
    # TEST N: Known drawdown sequence -> verify exact max drawdown.
    # Peak: 10000 -> drops to 5000 -> rises to 10000
    eq_df = pd.DataFrame({'equity': [10000, 9000, 5000, 8000, 10000]})
    max_dd, max_dd_pct = calculate_drawdown(eq_df)
    assert max_dd == 5000
    assert max_dd_pct == 50.0

def test_O_known_returns():
    # TEST O: Known returns -> verify Sharpe/Sortino calculations.
    dates = pd.date_range(start='2024-01-01', periods=4, freq='D')
    eq_df = pd.DataFrame({
        'timestamp': dates,
        'equity': [100, 105, 99.75, 105] # Daily returns approx: +5%, -5%, +5.2%
    })
    
    # Simulate single trade to generate valid metric structure
    trades = [{'net_pnl': 5.0, 'holding_time': 1, 'r_multiple': 1}]
    metrics = calculate_metrics(trades, eq_df, 100)
    
    assert metrics['sharpe'] != 0.0
    assert metrics['sortino'] != 0.0
    assert metrics['calmar'] != 0.0
