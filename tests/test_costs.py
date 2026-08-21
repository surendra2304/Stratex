import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_phase9.cost_engine import CostEngine


def test_cost_decoupling():
    """
    Part 4: Test deterministic cost evaluation.
    gross PnL - entry fee - exit fee - entry slippage - exit slippage - spread = net PnL
    """
    engine = CostEngine(
        entry_fee=0.001,
        exit_fee=0.001,
        entry_slip=0.0005,
        exit_slip=0.0005,
        spread=0.0001
    )
    
    gross_pnl = 0.01 # 1% move
    expected_friction = 0.001 + 0.001 + 0.0005 + 0.0005 + 0.0001 # 0.0031
    expected_net = gross_pnl - expected_friction # 0.0069
    
    net_pnl = engine.calculate_net_pnl(gross_pnl)
    assert abs(net_pnl - expected_net) < 1e-9, f"Net PnL calculation failed. Expected {expected_net}, got {net_pnl}"
    print("Cost Engine test passed.")

if __name__ == "__main__":
    test_cost_decoupling()
