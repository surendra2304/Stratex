import pytest
import pandas as pd
import numpy as np
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_phase10.funding_engine import FundingEngine
from research_phase10.pairs_engine import PairsEngine
from research_phase9.cost_engine import CostEngine

def test_funding_no_overlap():
    """
    Part 13: Regression test proving 100 overlapping trades don't magically become 100x capital.
    We pass overlapping funding events and verify the engine only enters once, holds, and ignores overlapping triggers.
    """
    cost = CostEngine(entry_fee=0.0, exit_fee=0.0, entry_slip=0.0, exit_slip=0.0)
    engine = FundingEngine(cost, max_leverage=3.0)
    
    # 10 sequential 8h epochs. Every single one has +1% funding.
    times = pd.date_range("2023-01-01", periods=10, freq="8h")
    df_funding = pd.DataFrame({
        "fundingTime": times,
        "fundingRate": [0.01] * 10
    })
    
    # Flat price to isolate funding PnL
    df_spot = pd.DataFrame({"timestamp": times, "close": [100.0] * 10, "high": [100.0] * 10, "low": [100.0] * 10, "open": [100.0]*10})
    df_perp = pd.DataFrame({"timestamp": times, "close": [100.0] * 10, "high": [100.0] * 10, "low": [100.0] * 10, "open": [100.0]*10})
    
    res = engine.simulate_funding_arbitrage(df_spot, df_perp, df_funding, hold_epochs=5)
    
    # Since we hold for 5 epochs, out of 10 epochs we can only trade ONCE. 
    # (Epochs 0-4 are trade 1, Epochs 5-9 are trade 2, but we loop len(df_funding)-hold_epochs = 5 times)
    # The loop should only find 1 complete non-overlapping trade in 5 start-epochs.
    assert res['trades'] == 1, "Engine allowed overlapping trades!"
    
    # Trade 1 held for 5 epochs * 1% = +5% return.
    assert np.isclose(res['total_return_pct'], 0.05), f"Expected 0.05, got {res['total_return_pct']}"

def test_two_leg_costs():
    """
    Part 5: Prove Pairs pays BOTH legs' costs.
    """
    cost = CostEngine(entry_fee=0.001, exit_fee=0.001, entry_slip=0.0005, exit_slip=0.0005)
    df1 = pd.DataFrame({"timestamp": pd.date_range("2023-01-01", periods=10, freq="1h"), "close": [100.0]*10, "open": [100.0]*10, "high": [100.0]*10, "low": [100.0]*10, "volume": [100.0]*10})
    df2 = pd.DataFrame({"timestamp": pd.date_range("2023-01-01", periods=10, freq="1h"), "close": [100.0]*10, "open": [100.0]*10, "high": [100.0]*10, "low": [100.0]*10, "volume": [100.0]*10})
    engine = PairsEngine(df1, df2, "A", "B", "1h")
    engine.cost_engine = cost
    
    # Total friction for ONE leg = entry_fee + exit_fee + entry_slip + exit_slip = 0.003
    # Pair trade has TWO legs. So total friction per notional = 0.003.
    # Wait, gross_dollar is calculated on notional_a + notional_b.
    # In simulate_fold: 
    # friction_a = notional_a * 0.003
    # friction_b = notional_b * 0.003
    # If beta=1, notional_a = notional_b. Total friction = 0.003 * Total_Notional.
    
    # Let's mock a perfect zero-PnL trade
    df = pd.DataFrame({
        "close_a": [100, 100],
        "close_b": [100, 100],
        "spread": [2.5, 0.0], # Z-score will mirror this roughly
        "z_score": [2.5, -0.6]
    })
    
    res = engine._simulate_fold(df, beta=1.0, entry_z=2.0, exit_z=0.5, available_capital=10000, is_test=True)
    
    # Capital = 10000. Notional_A = 5000, Notional_B = 5000.
    # Gross PnL = 0
    # Friction A = 5000 * 0.003 = 15
    # Friction B = 5000 * 0.003 = 15
    # Net = -30
    assert len(res['trades']) == 1
    assert np.isclose(res['net_pnl'], -30.0), f"Expected -30.0, got {res['net_pnl']}"
