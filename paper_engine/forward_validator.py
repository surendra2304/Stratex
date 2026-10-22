import json
import os
import time
import uuid

import pandas as pd

from logger import get_logger
from paper_engine.benchmark import BenchmarkComparators
from paper_engine.daily_report import DailyReportGenerator
from paper_engine.portfolio import PaperPortfolio

logger = get_logger("forward_validator")

class ForwardValidator:
    """
    Simulates or orchestrates a forward validation paper-trading session.
    Since real-time paper data isn't available right now, this orchestrator
    can be fed held-out data to simulate the exact forward-looking mechanics.
    """
    
    def __init__(self, experiment_name: str, cache_dir="experiments"):
        self.experiment_name = experiment_name
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.portfolio = PaperPortfolio(filename=f"{self.cache_dir}/{experiment_name}_portfolio.json")
        self.portfolio.ledger_file = f"{self.cache_dir}/{experiment_name}_ledger.jsonl"
        self.benchmark = BenchmarkComparators()
        self.report_gen = DailyReportGenerator(self.portfolio, self.benchmark)
        
    def _register_experiment(self):
        reg_file = f"{self.cache_dir}/registry.json"
        try:
            with open(reg_file, 'r') as f:
                data = json.load(f)
        except:
            data = {"experiments": []}
            
        data["experiments"].append({
            "name": self.experiment_name,
            "start_time": time.time(),
            "status": "RUNNING"
        })
        
        with open(reg_file, 'w') as f:
            json.dump(data, f, indent=4)
            
    def run_simulated_forward(self, df: pd.DataFrame, strategy_func):
        """
        Runs the forward validation on a held-out DataFrame.
        """
        self._register_experiment()
        logger.info(f"Starting simulated forward validation: {self.experiment_name}")
        
        # We just iterate row by row to simulate forward time
        # This prevents any lookahead bias in the validator itself.
        for idx in range(len(df)):
            window = df.iloc[:idx+1]
            if len(window) < 50: # warm up
                continue
                
            current_row = window.iloc[-1]
            current_row['timestamp']
            
            # strategy_func MUST only see 'window' (past data)
            signal = strategy_func(window)
            
            if signal:
                price = current_row['close']
                ev = str(uuid.uuid4())
                qty = 0.01 # mock sizing
                
                if signal == "BUY":
                    try:
                        self.portfolio.allocate_margin(price * qty, ev)
                        self.portfolio.add_position(ev, "BTCUSDT", "LONG", price, qty)
                    except Exception as e:
                        logger.warning(f"Rejected signal: {e}")
                
                # Mock a simplified hold logic (close on opposite signal)
                if signal == "SELL":
                    for pos_id, pos in list(self.portfolio.positions.items()):
                        if pos['status'] == "OPEN" and pos['direction'] == "LONG":
                            self.portfolio.close_position(pos_id, price, exit_fee=price * qty * 0.0005)
                            
        # Final generation
        rep = self.report_gen.generate(df)
        logger.info(f"Forward validation complete. Net PnL: {self.portfolio.realized_pnl}")
        
        # Mark registry DONE
        return rep
