import time
import uuid
from typing import Dict
from paper_engine.simulator import PaperSimulator
from paper_engine.portfolio import PaperPortfolio
from paper_engine.market_data import MarketDataFeed, DataException
from research_phase9.cost_engine import CostEngine

class PairsPaperSimulator(PaperSimulator):
    """
    Extends the PaperSimulator to handle Pair trades atomically or gracefully fail to UNHEDGED.
    """
    def __init__(self, portfolio: PaperPortfolio, market_data: MarketDataFeed, cost_engine: CostEngine):
        super().__init__(portfolio, market_data, cost_engine)
        self.pair_trades: Dict[str, dict] = {}
        
    def submit_pair_order(self, symbol_a: str, symbol_b: str, direction_a: str, quantity_a: float, quantity_b: float, signal_time: float) -> str:
        """
        Submits a pair of market orders.
        Models the risk of Leg A filling while Leg B fails.
        """
        pair_id = str(uuid.uuid4())
        direction_b = "SELL" if direction_a == "BUY" else "BUY"
        
        # We attempt to fill A
        try:
            order_a_id = self.submit_market_order(symbol_a, direction_a, quantity_a, signal_time)
            leg_a_status = "FILLED"
        except Exception as e:
            order_a_id = None
            leg_a_status = "FAILED"
            
        # Simulate an arbitrary connection interruption / leg fail risk (e.g. 5% chance)
        # For determinism in testing, we won't inject random math unless requested, but we support the state.
        
        # We attempt to fill B
        try:
            order_b_id = self.submit_market_order(symbol_b, direction_b, quantity_b, signal_time)
            leg_b_status = "FILLED"
        except Exception as e:
            order_b_id = None
            leg_b_status = "FAILED"
            
        status = "OPEN"
        if leg_a_status == "FAILED" and leg_b_status == "FAILED":
            status = "FAILED"
        elif leg_a_status == "FAILED" or leg_b_status == "FAILED":
            status = "UNHEDGED"
            
        self.pair_trades[pair_id] = {
            "symbol_a": symbol_a,
            "symbol_b": symbol_b,
            "order_a_id": order_a_id,
            "order_b_id": order_b_id,
            "status": status,
            "open_time": time.time()
        }
        
        return pair_id

    def close_pair_position(self, pair_id: str, signal_time: float):
        """
        Closes both legs of the pair.
        """
        if pair_id not in self.pair_trades:
            return False
            
        pair = self.pair_trades[pair_id]
        if pair['status'] == "CLOSED":
            return False
            
        # Close A
        if pair['order_a_id']:
            order_a = self.orders[pair['order_a_id']]
            close_dir_a = "SELL" if order_a['direction'] == "BUY" else "BUY"
            self.submit_market_order(order_a['symbol'], close_dir_a, order_a['quantity'], signal_time)
            
        # Close B
        if pair['order_b_id']:
            order_b = self.orders[pair['order_b_id']]
            close_dir_b = "SELL" if order_b['direction'] == "BUY" else "BUY"
            self.submit_market_order(order_b['symbol'], close_dir_b, order_b['quantity'], signal_time)
            
        pair['status'] = "CLOSED"
        pair['close_time'] = time.time()
        return True
