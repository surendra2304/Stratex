import json
import os
import threading
import time
import uuid

from paper_engine.config import (
    MAX_DAILY_LOSS,
    MAX_DRAWDOWN_PCT,
    MAX_PORTFOLIO_EXPOSURE,
    MAX_SIMULTANEOUS_POSITIONS,
    STARTING_PAPER_CAPITAL,
)


class PaperPortfolio:
    """
    Central Portfolio for Capital Accounting.
    Tracks Cash, Realized PnL, Used Margin, Unrealized PnL.
    """
    def __init__(self, filename="paper_portfolio.json", ledger_file=None, equity_file=None):
        self.filename = filename
        self._lock = threading.RLock()
        self.starting_capital = STARTING_PAPER_CAPITAL
        self.cash = STARTING_PAPER_CAPITAL
        self.realized_pnl = 0.0
        self.used_margin = 0.0
        
        # Cumulative Cost Tracking
        self.cumulative_fees = 0.0
        self.cumulative_slippage = 0.0
        self.cumulative_spread = 0.0
        self.cumulative_funding = 0.0
        
        self.positions: dict[str, dict] = {} # position_id -> details
        self.processed_event_ids = set()
        
        self.peak_equity = STARTING_PAPER_CAPITAL
        self.daily_loss = 0.0
        self.daily_realized_pnl = 0.0
        self.daily_fees = 0.0
        self.daily_funding = 0.0
        self.last_day_ts = self._get_day_start(time.time())
        
        self.ledger_file = ledger_file or os.getenv("PAPER_LEDGER_FILE", "paper_trade_ledger.jsonl")
        self.equity_file = equity_file or os.getenv("PAPER_EQUITY_FILE", "paper_equity_curve.jsonl")
        
        self._load()
        
    def _get_day_start(self, ts):
        return int(ts) // 86400 * 86400

    def get_equity(self, current_market_prices: dict[str, float]) -> float:
        """
        Equity = Cash + Used Margin + Unrealized PnL

        When positions are opened via allocate_margin(), the full notional is
        deducted from cash and tracked in used_margin. The correct equity
        formula adds used_margin back so that (cash + used_margin) represents
        the true capital base, and unrealized_pnl captures the P&L change
        since entry. Without this correction, equity is understated by the
        total open position notional for the entire duration of every trade.
        """
        unrealized = self.get_unrealized_pnl(current_market_prices)
        return self.cash + self.used_margin + unrealized

    def get_unrealized_pnl(self, current_market_prices: dict[str, float]) -> float:
        unrealized = 0.0
        for pos in self.positions.values():
            if pos['status'] in ["OPEN", "OPENING", "REDUCING"]:
                sym = pos['symbol']
                if sym in current_market_prices:
                    current_price = current_market_prices[sym]
                    direction = pos.get('direction') or pos.get('side', 'LONG')
                    if str(direction).upper() in ["LONG", "BUY"]:
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

    def _check_daily_rollover(self):
        now = time.time()
        if now - self.last_day_ts >= 86400:
            self.daily_loss = 0.0
            self.daily_realized_pnl = 0.0
            self.daily_fees = 0.0
            self.daily_funding = 0.0
            self.last_day_ts = self._get_day_start(now)
            
    def add_realized_pnl(self, pnl: float, event_id: str):
        if event_id in self.processed_event_ids:
            return
            
        self._check_daily_rollover()
            
        self.cash += pnl
        self.realized_pnl += pnl
        self.daily_realized_pnl += pnl
        
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

    def close_position(self, pos_id: str, exit_price: float, exit_fee: float = 0.0, exit_time: float | None = None, funding_pnl: float = 0.0):
        if pos_id in self.positions:
            pos = self.positions[pos_id]
            pos['status'] = "CLOSED"
            pos['close_time'] = exit_time or time.time()
            pos['last_update_time'] = time.time()
            
            entry_price = pos['entry_price']
            qty = pos['quantity']
            direction = pos['direction']
            
            if direction in ["LONG", "BUY"]:
                gross_pnl = (exit_price - entry_price) * qty
            else:
                gross_pnl = (entry_price - exit_price) * qty
                
            net_pnl = gross_pnl - exit_fee + funding_pnl
            self.cumulative_fees += exit_fee
            self.cumulative_funding += funding_pnl
            
            trade_record = {
                "trade_id": pos_id,
                "symbol": pos['symbol'],
                "direction": direction,
                "entry_time": pos['open_time'],
                "exit_time": pos['close_time'],
                "entry_price": entry_price,
                "exit_price": exit_price,
                "quantity": qty,
                "gross_pnl": gross_pnl,
                "exit_fee": exit_fee,
                "funding_pnl": funding_pnl,
                "net_pnl": net_pnl,
                "status": "CLOSED"
            }
            
            self.record_completed_trade(trade_record)
            self._save()
            
    def record_completed_trade(self, trade_record: dict):
        """Append a closed trade to the durable JSONL ledger"""
        import json
        with open(self.ledger_file, "a") as f:
            f.write(json.dumps(trade_record) + "\n")
            
    def record_equity_snapshot(self, timestamp: float, current_market_prices: dict[str, float]):
        """Persist the equity curve to a JSONL file"""
        import json
        eq = self.get_equity(current_market_prices)
        ur_pnl = self.get_unrealized_pnl(current_market_prices)
        record = {
            "timestamp": timestamp,
            "cash": self.cash,
            "unrealized_pnl": ur_pnl,
            "realized_pnl": self.realized_pnl,
            "fees": self.cumulative_fees,
            "funding": self.cumulative_funding,
            "equity": eq
        }
        with open(self.equity_file, "a") as f:
            f.write(json.dumps(record) + "\n")
            
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
            self._save() # Save the new peak equity
            
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
            
    def get_max_drawdown(self) -> float:
        """Calculate Max Drawdown by reading the persisted equity curve."""
        if not os.path.exists(self.equity_file):
            return 0.0
            
        peak = 0.0
        max_dd = 0.0
        try:
            with open(self.equity_file, 'r') as f:
                for line in f:
                    record = json.loads(line)
                    eq = record.get("equity", 0.0)
                    peak = max(peak, eq)
                    if peak > 0:
                        dd = (peak - eq) / peak
                        max_dd = max(max_dd, dd)
        except Exception:
            pass
        return max_dd

    def _save(self):
        with self._lock:
            try:
                data = {
                    "starting_capital": self.starting_capital,
                    "cash": self.cash,
                    "realized_pnl": self.realized_pnl,
                    "used_margin": self.used_margin,
                    "cumulative_fees": self.cumulative_fees,
                    "cumulative_slippage": self.cumulative_slippage,
                    "cumulative_spread": self.cumulative_spread,
                    "cumulative_funding": self.cumulative_funding,
                    "positions": self.positions,
                    "peak_equity": self.peak_equity,
                    "daily_loss": self.daily_loss,
                    "daily_realized_pnl": self.daily_realized_pnl,
                    "daily_fees": self.daily_fees,
                    "daily_funding": self.daily_funding,
                    "last_day_ts": self.last_day_ts,
                    "processed_event_ids": list(self.processed_event_ids)
                }
                tmp_file = f"{self.filename}.{uuid.uuid4().hex}.tmp"
                with open(tmp_file, "w") as f:
                    json.dump(data, f, indent=4)
                os.replace(tmp_file, self.filename)
            except Exception as e:
                from paper_engine.exceptions import PersistenceError
                raise PersistenceError(f"Failed to atomically save portfolio: {e}")

    def _load(self):
        if not os.path.exists(self.filename):
            return
        try:
            with open(self.filename, 'r') as f:
                raw = f.read().strip()
            if not raw or raw in ("null", "[]", ""):
                from paper_engine.exceptions import StateCorruptionError
                raise StateCorruptionError(
                    f"Portfolio file '{self.filename}' is empty or null — "
                    "cannot distinguish between corruption and a genuine zero state."
                )
            data = json.loads(raw)
            if not isinstance(data, dict):
                from paper_engine.exceptions import StateCorruptionError
                raise StateCorruptionError(
                    f"Portfolio file '{self.filename}' does not contain a JSON object — "
                    f"got {type(data).__name__}."
                )
            self.starting_capital = data.get("starting_capital", STARTING_PAPER_CAPITAL)
            self.cash = data.get("cash", STARTING_PAPER_CAPITAL)
            self.realized_pnl = data.get("realized_pnl", 0.0)
            self.used_margin = data.get("used_margin", 0.0)

            self.cumulative_fees = data.get("cumulative_fees", 0.0)
            self.cumulative_slippage = data.get("cumulative_slippage", 0.0)
            self.cumulative_spread = data.get("cumulative_spread", 0.0)
            self.cumulative_funding = data.get("cumulative_funding", 0.0)

            self.positions = data.get("positions", {})
            self.peak_equity = data.get("peak_equity", STARTING_PAPER_CAPITAL)
            self.daily_loss = data.get("daily_loss", 0.0)
            self.daily_realized_pnl = data.get("daily_realized_pnl", 0.0)
            self.daily_fees = data.get("daily_fees", 0.0)
            self.daily_funding = data.get("daily_funding", 0.0)
            self.last_day_ts = data.get("last_day_ts", self._get_day_start(time.time()))
            self.processed_event_ids = set(data.get("processed_event_ids", []))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            from paper_engine.exceptions import StateCorruptionError
            raise StateCorruptionError(
                f"Portfolio file '{self.filename}' is corrupted and cannot be loaded safely: {e}"
            ) from e

