import json
import os
import time
import uuid
from typing import Dict, List, Optional
from paper_engine.config import STARTING_PAPER_CAPITAL, MAX_PORTFOLIO_EXPOSURE, MAX_SIMULTANEOUS_POSITIONS, MAX_DAILY_LOSS, MAX_DRAWDOWN_PCT

class PaperPortfolio:
    """
    Central Portfolio for Capital Accounting.
    Tracks Cash, Realized PnL, Used Margin, Unrealized PnL.
    """
    def __init__(self, filename="paper_portfolio.json"):
        self.filename = filename
        self.starting_capital = STARTING_PAPER_CAPITAL
        self.cash = STARTING_PAPER_CAPITAL
        self.realized_pnl = 0.0
        self.used_margin = 0.0
        
        self.positions: Dict[str, dict] = {} # position_id -> details
        self.processed_event_ids = set()
        
        self.peak_equity = STARTING_PAPER_CAPITAL
        self.daily_loss = 0.0
        self.last_day_ts = self._get_day_start(time.time())
        
        self._load()
        
    def _get_day_start(self, ts):
        return int(ts) // 86400 * 86400

    def get_equity(self, current_market_prices: Dict[str, float]) -> float:
        """
        Equity = Cash + Unrealized PnL
        """
        unrealized = self.get_unrealized_pnl(current_market_prices)
        return self.cash + unrealized

    def get_unrealized_pnl(self, current_market_prices: Dict[str, float]) -> float:
        unrealized = 0.0
        for pos_id, pos in self.positions.items():
            if pos['status'] in ["OPEN", "OPENING", "REDUCING"]:
                sym = pos['symbol']
                if sym in current_market_prices:
                    current_price = current_market_prices[sym]
                    if pos['direction'] in ["LONG", "BUY"]:
                        unrealized += (current_price - pos['entry_price']) * pos['quantity']
                    else:
                        unrealized += (pos['entry_price'] - current_price) * pos['quantity']
        return unrealized

    def allocate_margin(self, amount: float, event_id: str):
        if event_id in self.processed_event_ids:
            return
        
        if self.cash - amount < 0:
            raise ValueError("Insufficient cash for margin allocation.")
            
        self.cash -= amount
        self.used_margin += amount
        self.processed_event_ids.add(event_id)
        self._save()

    def release_margin(self, amount: float, event_id: str):
        if event_id in self.processed_event_ids:
            return
            
        self.used_margin -= amount
        self.cash += amount
        self.processed_event_ids.add(event_id)
        self._save()

    def add_realized_pnl(self, pnl: float, event_id: str):
        if event_id in self.processed_event_ids:
            return
            
        # Reset daily loss if new day
        now = time.time()
        if now - self.last_day_ts >= 86400:
            self.daily_loss = 0.0
            self.last_day_ts = self._get_day_start(now)
            
        self.cash += pnl
        self.realized_pnl += pnl
        
        if pnl < 0:
            self.daily_loss += abs(pnl)
            
        self.processed_event_ids.add(event_id)
        self._save()

    def add_position(self, pos_id: str, symbol: str, direction: str, entry_price: float, quantity: float):
        if pos_id in self.positions:
            return
            
        self.positions[pos_id] = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "quantity": quantity,
            "status": "OPEN",
            "open_time": time.time(),
            "last_update_time": time.time()
        }
        self._save()

    def close_position(self, pos_id: str):
        if pos_id in self.positions:
            self.positions[pos_id]['status'] = "CLOSED"
            self.positions[pos_id]['close_time'] = time.time()
            self.positions[pos_id]['last_update_time'] = time.time()
            self._save()
            
    def mark_position_stale(self, pos_id: str):
        if pos_id in self.positions:
            self.positions[pos_id]['status'] = "STALE"
            self._save()

    def check_risk_limits(self, current_equity: float, new_notional_exposure: float):
        """
        Throws exception if a risk limit is violated.
        """
        if self.daily_loss >= MAX_DAILY_LOSS:
            raise ValueError(f"Risk Block: Max daily loss exceeded ({self.daily_loss})")
            
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            
        if self.peak_equity > 0:
            dd = (self.peak_equity - current_equity) / self.peak_equity
            if dd >= MAX_DRAWDOWN_PCT:
                raise ValueError(f"Risk Block: Max drawdown exceeded ({dd*100:.1f}%)")
                
        open_count = len([p for p in self.positions.values() if p['status'] == "OPEN"])
        if open_count >= MAX_SIMULTANEOUS_POSITIONS:
            raise ValueError(f"Risk Block: Max simultaneous positions exceeded ({open_count})")
            
        total_exposure = sum([p['entry_price'] * p['quantity'] for p in self.positions.values() if p['status'] == "OPEN"])
        if total_exposure + new_notional_exposure > MAX_PORTFOLIO_EXPOSURE:
            raise ValueError(f"Risk Block: Max portfolio exposure exceeded ({total_exposure + new_notional_exposure})")

    def _save(self):
        data = {
            "starting_capital": self.starting_capital,
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "used_margin": self.used_margin,
            "positions": self.positions,
            "peak_equity": self.peak_equity,
            "daily_loss": self.daily_loss,
            "last_day_ts": self.last_day_ts,
            "processed_event_ids": list(self.processed_event_ids)
        }
        os.makedirs(os.path.dirname(self.filename) or '.', exist_ok=True)
        # atomic write
        tmp = self.filename + ".tmp"
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, self.filename)

    def _load(self):
        if not os.path.exists(self.filename):
            return
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                self.starting_capital = data.get("starting_capital", STARTING_PAPER_CAPITAL)
                self.cash = data.get("cash", STARTING_PAPER_CAPITAL)
                self.realized_pnl = data.get("realized_pnl", 0.0)
                self.used_margin = data.get("used_margin", 0.0)
                self.positions = data.get("positions", {})
                self.peak_equity = data.get("peak_equity", STARTING_PAPER_CAPITAL)
                self.daily_loss = data.get("daily_loss", 0.0)
                self.last_day_ts = data.get("last_day_ts", self._get_day_start(time.time()))
                self.processed_event_ids = set(data.get("processed_event_ids", []))
        except:
            pass
