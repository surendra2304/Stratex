from research_phase9.cost_engine import CostEngine
from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.risk_gate import RiskGate


class TestTestnetEngine:

    def test_profitability_gate_rejection_negative_expected_return(self):
        """Test that the profitability gate rejects signals with negative expected net return."""
        # Using Maker config (lower fees) just to test logic
        cost_engine = CostEngine.get_binance_maker_config() 
        gate = ProfitabilityGate(cost_engine=cost_engine)
        
        # Symbol, Side, Entry, SL, TP, Confidence
        # With 50% confidence and equal risk/reward, friction makes it negative
        passed, metrics = gate.evaluate_signal("BTCUSDT", "BUY", 50000, 49500, 50500, 0.5)
        
        assert not passed
        assert metrics["decision"] == "REJECTED"
        assert metrics["expected_net_return"] < 0
        
    def test_profitability_gate_acceptance_positive_edge(self):
        """Test that the profitability gate accepts highly confident, strong R:R signals."""
        cost_engine = CostEngine.get_binance_maker_config()
        gate = ProfitabilityGate(cost_engine=cost_engine)
        
        # High confidence (0.7) and good R:R (1% reward, 0.5% risk)
        passed, metrics = gate.evaluate_signal("BTCUSDT", "BUY", 50000, 49750, 50500, 0.7)
        
        assert passed
        assert metrics["decision"] == "ACCEPTED"
        assert metrics["expected_net_return"] > 0
        
    def test_risk_gate_daily_loss_limit(self):
        """Test that the risk gate correctly rejects trades when daily loss is breached."""
        gate = RiskGate(starting_balance=10000.0)
        
        # Simulate a massive loss beyond the daily limit
        gate.update_after_trade(-300.0, 9700.0) # 3% loss > 2% limit
        
        passed, reason, _ = gate.evaluate_risk("BTCUSDT", "LONG", 9700.0, {}, 0.001, 50000.0, "OK")
        
        assert not passed
        assert reason == "DAILY_LOSS_LIMIT"
        
    def test_risk_gate_consecutive_losses(self):
        """Test that the risk gate halts after 3 consecutive losses."""
        gate = RiskGate(starting_balance=10000.0)
        
        gate.update_after_trade(-10.0, 9990.0)
        gate.update_after_trade(-10.0, 9980.0)
        gate.update_after_trade(-10.0, 9970.0)
        
        passed, reason, _ = gate.evaluate_risk("BTCUSDT", "LONG", 9970.0, {}, 0.1, 50000.0, "OK")
        
        assert not passed
        assert reason == "CONSECUTIVE_LOSS_LIMIT"
        
    def test_risk_gate_position_sizing(self):
        """Test that position size correctly respects the max risk limit and single asset exposure."""
        import config
        config.MAX_TESTNET_RISK_PER_TRADE = 0.005 # 10000 * 0.005 = 50 max risk
        config.MAX_SINGLE_ASSET_EXPOSURE = 0.02   # 10000 * 0.02 = 200 max position
        
        gate = RiskGate(starting_balance=10000.0)
        filters = {"stepSize": 0.001, "minNotional": 10.0, "tickSize": 0.01}
        
        # Entry 50000, SL 45000 -> Risk is 5000 per BTC. 
        # Max risk allowed is 50. Max risk-based qty = 50 / 5000 = 0.01 BTC.
        # But max position value is 200. Max exposure-based qty = 200 / 50000 = 0.004 BTC.
        # So it should cap at 0.004
        qty = gate.calculate_position_size(10000.0, 50000.0, 45000.0, filters)
        assert qty == 0.004
        
        # Test capping heavily by max exposure
        # Entry 50000, SL 49950 -> Risk 50 per BTC.
        # Max risk allowed is 50. Risk-based qty = 1 BTC.
        # But 1 BTC = $50,000, which exceeds max exposure ($200).
        # Exposure-based qty = 200 / 50000 = 0.004 BTC.
        qty_capped = gate.calculate_position_size(10000.0, 50000.0, 49950.0, filters)
        assert qty_capped == 0.004
