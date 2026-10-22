import json
import time

from logger import get_logger

logger = get_logger("daily_report")

class DailyReportGenerator:
    """
    Generates a daily paper trading session report.
    Aggregates ledger and signal logs to produce actionable insights.
    """
    
    def __init__(self, portfolio, benchmark):
        self.portfolio = portfolio
        self.benchmark = benchmark
        
    def generate(self, market_data_df=None):
        logger.info("Generating Daily Forward Validation Report...")
        
        rep = {
            "timestamp": time.time(),
            "starting_capital": self.portfolio.starting_capital,
            "current_cash": self.portfolio.cash,
            "realized_pnl": self.portfolio.realized_pnl,
            "cumulative_fees": self.portfolio.cumulative_fees,
            "daily_loss": self.portfolio.daily_loss,
            "open_positions": len([p for p in self.portfolio.positions.values() if p['status'] == 'OPEN']),
        }
        
        if market_data_df is not None and not market_data_df.empty:
            bh = self.benchmark.buy_and_hold(market_data_df, self.portfolio.starting_capital)
            rep["benchmark_bh_pnl"] = bh["net_pnl"]
            
        with open("paper_daily_report.json", "w") as f:
            json.dump(rep, f, indent=4)
            
        logger.info(f"Daily report generated. Cash: {rep['current_cash']:.2f}")
        return rep
