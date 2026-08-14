import time
import uuid
from typing import Dict
from paper_engine.simulator import PaperSimulator
from paper_engine.portfolio import PaperPortfolio
from paper_engine.market_data import MarketDataFeed
from research_phase9.cost_engine import CostEngine

class FundingPaperSimulator(PaperSimulator):
    """
    Extends PaperSimulator for explicit Funding Arbitrage support.
    Tracks spot and perpetual positions separately, allowing funding rate income accumulation.
    """
    def __init__(self, portfolio: PaperPortfolio, market_data: MarketDataFeed, cost_engine: CostEngine):
        super().__init__(portfolio, market_data, cost_engine)
        self.funding_trades: Dict[str, dict] = {}
        
    def submit_funding_arbitrage(self, symbol: str, quantity: float, signal_time: float) -> str:
        """
        Submits Long Spot + Short Perp
        """
        trade_id = str(uuid.uuid4())
        
        # Spot is Long
        order_spot_id = self.submit_market_order(symbol, "BUY", quantity, signal_time)
        # Perp is Short
        order_perp_id = self.submit_market_order(symbol, "SELL", quantity, signal_time)
        
        self.funding_trades[trade_id] = {
            "symbol": symbol,
            "order_spot_id": order_spot_id,
            "order_perp_id": order_perp_id,
            "status": "OPEN",
            "accumulated_funding": 0.0,
            "open_time": time.time()
        }
        
        return trade_id

    def apply_funding_payment(self, symbol: str, funding_rate: float, mark_price: float, event_id: str):
        """
        Calculates and applies funding payment to any open Perp Short positions.
        Short pays funding if rate < 0, receives if rate > 0.
        """
        for trade_id, trade in self.funding_trades.items():
            if trade['status'] == "OPEN" and trade['symbol'] == symbol:
                order_perp = self.orders.get(trade['order_perp_id'])
                if order_perp and order_perp['status'] == "FILLED":
                    qty = order_perp['quantity']
                    notional = qty * mark_price
                    # Short position receives positive funding
                    payment = notional * funding_rate
                    
                    trade['accumulated_funding'] += payment
                    
                    # Update portfolio
                    self.portfolio.add_realized_pnl(payment, f"fund_{event_id}_{trade_id}")

    def close_funding_arbitrage(self, trade_id: str, signal_time: float):
        if trade_id not in self.funding_trades:
            return False
            
        trade = self.funding_trades[trade_id]
        if trade['status'] == "CLOSED":
            return False
            
        # Close Spot (Sell)
        order_spot = self.orders[trade['order_spot_id']]
        self.submit_market_order(order_spot['symbol'], "SELL", order_spot['quantity'], signal_time)
        
        # Close Perp (Buy)
        order_perp = self.orders[trade['order_perp_id']]
        self.submit_market_order(order_perp['symbol'], "BUY", order_perp['quantity'], signal_time)
        
        trade['status'] = "CLOSED"
        trade['close_time'] = time.time()
        return True
