import json
import os
from datetime import datetime


class PaperTradingEngine:
    """
    Part 26: Forward Paper-Trading Architecture
    Logs theoretical signals and theoretical fills without interacting with the Binance Order API.
    """
    def __init__(self, strategy_name, log_file="backtest_results/phase9/paper_trading_log.json"):
        self.strategy_name = strategy_name
        self.log_file = log_file
        self.ledger = []
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        
        # Load existing
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    self.ledger = json.load(f)
            except Exception:
                self.ledger = []
                
    def record_signal(self, symbol, timeframe, direction, confidence, entry_price, sl_price, tp_price):
        """
        Logs an intended trade.
        """
        trade_id = f"{self.strategy_name}_{int(datetime.utcnow().timestamp())}"
        
        record = {
            "trade_id": trade_id,
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy": self.strategy_name,
            "direction": direction,
            "confidence": confidence,
            "theoretical_entry": entry_price,
            "theoretical_sl": sl_price,
            "theoretical_tp": tp_price,
            "status": "OPEN",
            "actual_outcome": None,
            "net_pnl": 0.0
        }
        
        self.ledger.append(record)
        self._save()
        print(f"[PAPER TRADING] Logged Signal: {direction} {symbol} @ {entry_price}")
        return trade_id
        
    def resolve_trade(self, trade_id, outcome, net_pnl):
        """
        Updates an open paper trade after the market has moved.
        outcome: "HIT_PT", "HIT_SL", "TIMEOUT"
        """
        for record in self.ledger:
            if record['trade_id'] == trade_id:
                record['status'] = "CLOSED"
                record['actual_outcome'] = outcome
                record['net_pnl'] = net_pnl
                self._save()
                print(f"[PAPER TRADING] Resolved Trade {trade_id}: {outcome} (PnL: {net_pnl})")
                return True
        return False
        
    def _save(self):
        with open(self.log_file, 'w') as f:
            json.dump(self.ledger, f, indent=4)
