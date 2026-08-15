import os
import json
import pytest

DASHBOARD_FILE = "d:/MT5/python_bot/dashboard.py"

class TestDashboardDataIsolation:
    """
    Tests proving that the dashboard strictly isolates legacy paper data 
    from the current forward experiment metrics.
    """
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        # Create a mock active experiment ID file
        os.makedirs("experiments", exist_ok=True)
        with open("experiments/active_forward_experiment_id.txt", "w") as f:
            f.write("test_exp_4ba0d007")
            
        # Create a mock paper_trade_ledger.jsonl
        with open("paper_trade_ledger.jsonl", "w") as f:
            # Legacy trade (missing ID -> LEGACY_UNASSIGNED)
            f.write(json.dumps({"net_pnl": 50, "direction": "LONG", "symbol": "BTCUSDT", "entry_price": 50000, "quantity": 1}) + "\n")
            # Legacy trade (different ID)
            f.write(json.dumps({"experiment_id": "old_exp", "net_pnl": -20, "direction": "SHORT", "symbol": "BTCUSDT", "entry_price": 51000, "quantity": 1}) + "\n")
            # Current experiment trade
            f.write(json.dumps({"experiment_id": "test_exp_4ba0d007", "net_pnl": 100, "direction": "LONG", "symbol": "BTCUSDT", "entry_price": 52000, "quantity": 1}) + "\n")
            
        # Do not create paper_portfolio.json to test missing portfolio state (fallback to 10000)
        if os.path.exists("paper_portfolio.json"):
            os.remove("paper_portfolio.json")
            
        yield
        
        # Cleanup
        if os.path.exists("experiments/active_forward_experiment_id.txt"):
            os.remove("experiments/active_forward_experiment_id.txt")
        if os.path.exists("paper_trade_ledger.jsonl"):
            os.remove("paper_trade_ledger.jsonl")

    def test_legacy_trades_are_excluded(self):
        """Old trades are excluded. Current experiment trades are included."""
        import dashboard
        with dashboard.app.app_context():
            res = dashboard.get_trades()
            data = json.loads(res.get_data())
            
            # Out of 3 trades, only 1 belongs to the current experiment.
            assert data["total_trades"] == 1
            assert data["net_pnl"] == 0.0 # From missing portfolio, so 0 realized
            
    def test_win_rate_profit_factor_exclude_legacy(self):
        """Win rate and profit factor exclude legacy trades."""
        import dashboard
        with dashboard.app.app_context():
            res = dashboard.get_trades()
            data = json.loads(res.get_data())
            
            # The only included trade is a win
            assert data["win_rate"] == 100.0
            assert data["profit_factor"] == "Infinity"

    def test_no_trade_state_defaults(self):
        """
        No-trade state shows:
        Equity = $10,000
        Closed trades = 0
        Win rate = N/A
        Profit factor = N/A
        Drawdown = 0%
        """
        # Overwrite ledger to be empty for this active experiment
        with open("paper_trade_ledger.jsonl", "w") as f:
            f.write(json.dumps({"experiment_id": "old_exp", "net_pnl": 50}) + "\n")
            
        import dashboard
        with dashboard.app.app_context():
            # Check trades
            res_trades = dashboard.get_trades()
            t_data = json.loads(res_trades.get_data())
            
            assert t_data["total_trades"] == 0
            assert t_data["win_rate"] == "N/A"
            assert t_data["profit_factor"] == "N/A"
            
            # Check status (portfolio)
            res_status = dashboard.get_status()
            s_data = json.loads(res_status.get_data())
            
            assert s_data["equity"] == 10000.0
            assert s_data["cash"] == 10000.0
            assert s_data["fees"] == 0.0
            assert s_data["funding"] == 0.0
            assert s_data["max_drawdown"] == 0.0
            assert s_data["open_positions"] == 0
