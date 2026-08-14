import time
import uuid
from typing import Dict, Tuple, Optional
from paper_engine.config import LATENCY_MODEL, LIMIT_FILL_MODEL
from paper_engine.market_data import MarketDataFeed, DataException
from paper_engine.portfolio import PaperPortfolio
from research_phase9.cost_engine import CostEngine

# Realistic execution delay assumptions
LATENCY_MAP = {
    "LOW": 0.05,
    "BASE": 0.5,
    "HIGH": 2.0
}

class PaperSimulator:
    """
    Paper Execution Simulator.
    Simulates sending orders to an exchange, accounting for:
    - Latency
    - Cost (Spread, Slippage, Fees)
    - Limit vs Market assumptions
    """
    def __init__(self, portfolio: PaperPortfolio, market_data: MarketDataFeed, cost_engine: CostEngine):
        self.portfolio = portfolio
        self.market_data = market_data
        self.cost_engine = cost_engine
        
        self.orders: Dict[str, dict] = {}
        
    def submit_market_order(self, symbol: str, direction: str, quantity: float, signal_time: float) -> str:
        """
        Submits a market order.
        Fill is processed synchronously here for simplicity, but respects latency time.
        """
        # Calculate latency
        latency = LATENCY_MAP.get(LATENCY_MODEL, 0.5)
        order_time = time.time()
        fill_time = order_time + latency
        
        # We fetch the exact price at fill_time in a real live loop. 
        # For this simulator, we just use the current BBO.
        try:
            bid, ask = self.market_data.get_bbo(symbol)
        except DataException as e:
            raise ValueError(f"Order failed due to data issue: {e}")
            
        order_id = str(uuid.uuid4())
        
        # In a real engine, spread is dynamic. CostEngine has entry_slip.
        # But we actually want to execute against Bid/Ask.
        # Market BUY crosses the spread and pays Ask.
        # Market SELL pays Bid.
        if direction == "BUY":
            base_price = ask
        else:
            base_price = bid
            
        # Apply Slippage from CostEngine
        slippage_bps = self.cost_engine.entry_slip
        if direction == "BUY":
            fill_price = base_price * (1 + slippage_bps)
        else:
            fill_price = base_price * (1 - slippage_bps)
            
        # Fees
        notional = fill_price * quantity
        fee = notional * self.cost_engine.entry_fee
        
        self.orders[order_id] = {
            "symbol": symbol,
            "direction": direction,
            "quantity": quantity,
            "type": "MARKET",
            "status": "FILLED",
            "signal_time": signal_time,
            "order_time": order_time,
            "fill_time": fill_time,
            "fill_price": fill_price,
            "fee": fee
        }
        
        # Settle fees in portfolio immediately
        fee_event = f"fee_{order_id}"
        self.portfolio.add_realized_pnl(-fee, fee_event)
        
        return order_id
        
    def submit_limit_order(self, symbol: str, direction: str, quantity: float, limit_price: float, signal_time: float) -> str:
        """
        Submits a limit order. Requires a heartbeat/reconciler to tick it against market data to fill.
        """
        order_id = str(uuid.uuid4())
        
        self.orders[order_id] = {
            "symbol": symbol,
            "direction": direction,
            "quantity": quantity,
            "limit_price": limit_price,
            "type": "LIMIT",
            "status": "NEW",
            "signal_time": signal_time,
            "order_time": time.time(),
            "fill_time": None,
            "fill_price": None,
            "fee": 0.0
        }
        
        return order_id

    def tick_limit_orders(self):
        """
        Checks open limit orders against current market data.
        """
        for order_id, order in self.orders.items():
            if order['status'] != "NEW":
                continue
                
            try:
                bid, ask = self.market_data.get_bbo(order['symbol'])
            except DataException:
                continue
                
            # Check Limit Logic
            fill = False
            lp = order['limit_price']
            
            if LIMIT_FILL_MODEL == "OPTIMISTIC":
                if order['direction'] == "BUY" and ask <= lp:
                    fill = True
                elif order['direction'] == "SELL" and bid >= lp:
                    fill = True
            elif LIMIT_FILL_MODEL == "CONSERVATIVE":
                if order['direction'] == "BUY" and ask < lp:
                    fill = True
                elif order['direction'] == "SELL" and bid > lp:
                    fill = True
                    
            if fill:
                order['status'] = "FILLED"
                order['fill_time'] = time.time()
                # Maker fee usually for limit
                order['fill_price'] = lp
                
                notional = lp * order['quantity']
                fee = notional * self.cost_engine.entry_fee # Use entry fee to be conservative
                order['fee'] = fee
                
                fee_event = f"fee_{order_id}"
                self.portfolio.add_realized_pnl(-fee, fee_event)
