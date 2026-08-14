import pandas as pd
import numpy as np
import uuid

class DataValidator:
    """Validates the chronological integrity of backtest data."""
    @staticmethod
    def validate(df):
        if df is None or df.empty:
            raise ValueError("Data is empty")
        
        core_cols = ['open', 'high', 'low', 'close', 'volume', 'timestamp']
        if df[core_cols].isnull().any().any():
            raise ValueError("Data contains NaNs in core OHLCV columns.")
            
        if not df['timestamp'].is_monotonic_increasing:
            raise ValueError("Data is not in chronological order.")
            
        if df['timestamp'].duplicated().any():
            raise ValueError("Data contains duplicate timestamps.")
            
        invalid_ohlc = df[(df['high'] < df['low']) | 
                          (df['open'] > df['high']) | (df['open'] < df['low']) | 
                          (df['close'] > df['high']) | (df['close'] < df['low'])]
        if not invalid_ohlc.empty:
            raise ValueError(f"Data contains {len(invalid_ohlc)} invalid OHLC relationships.")
            
        return True

class BacktestEngine:
    def __init__(self, df, strategies, fee_rate=0.001, slippage_rate=0.0005, 
                 initial_balance=10000.0, risk_per_trade=0.01, max_open_trades=1,
                 symbol="SIM", long_only=True, intrabar_resolution="conservative"):
        self.df = df
        self.strategies = strategies if isinstance(strategies, list) else [strategies]
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.risk_per_trade = risk_per_trade
        self.max_open_trades = max_open_trades
        self.symbol = symbol
        self.long_only = long_only
        self.intrabar_resolution = intrabar_resolution
        
        self.open_trades = []
        self.trade_history = []
        self.equity_curve = []
        self.rejected_trades = []
        
    def _calculate_qty(self, entry_price, sl_price):
        """Risk-based position sizing."""
        risk_amount = self.equity * self.risk_per_trade
        sl_distance = abs(entry_price - sl_price)
        
        if sl_distance == 0:
            return 0.0
            
        raw_qty = risk_amount / sl_distance
        qty = np.floor(raw_qty * 10000) / 10000.0
        
        max_spot_qty = self.equity / entry_price
        qty = min(qty, max_spot_qty)
        
        if qty * entry_price < 10.0:
            return 0.0
            
        return qty

    def run(self):
        """Runs the bar-by-bar simulation."""
        DataValidator.validate(self.df)
        
        warmup = 200
        if len(self.df) <= warmup:
            return [], pd.DataFrame()
            
        for i in range(warmup, len(self.df)):
            current_bar = self.df.iloc[i]
            timestamp = current_bar['timestamp']
            
            # 1. Update Open Trades
            self._update_open_trades(current_bar, timestamp)
            
            # 2. Update Equity
            self._update_equity(current_bar)
            self.equity_curve.append({'timestamp': timestamp, 'equity': self.equity})
            
            # 3. Generate Signals
            if len(self.open_trades) < self.max_open_trades:
                window = self.df.iloc[i-100 : i+1]
                
                best_signal = None
                best_sl = None
                best_tp = None
                source_strat = None
                
                for strat in self.strategies:
                    sig, sl, tp = strat.get_signal(window)
                    if sig:
                        best_signal = sig
                        best_sl = sl
                        best_tp = tp
                        source_strat = strat.__name__.split('_')[-1]
                        break
                
                if best_signal:
                    if self.long_only and best_signal == 'SELL':
                        self.rejected_trades.append({"time": timestamp, "reason": "LONG_ONLY active. Ignored SELL."})
                        continue
                        
                    theoretical_entry = current_bar['close']
                    
                    if best_signal == 'BUY':
                        actual_entry = theoretical_entry * (1 + self.slippage_rate)
                        # Validation
                        if not (best_sl < actual_entry < best_tp):
                            self.rejected_trades.append({"time": timestamp, "reason": "Invalid SL/TP after BUY slippage."})
                            continue
                    else:
                        actual_entry = theoretical_entry * (1 - self.slippage_rate)
                        # Validation
                        if not (best_sl > actual_entry > best_tp):
                            self.rejected_trades.append({"time": timestamp, "reason": "Invalid SL/TP after SELL slippage."})
                            continue
                            
                    qty = self._calculate_qty(actual_entry, best_sl)
                    
                    if qty > 0:
                        fee = actual_entry * qty * self.fee_rate
                        
                        trade = {
                            'trade_id': str(uuid.uuid4())[:8],
                            'strategy': source_strat,
                            'symbol': self.symbol,
                            'side': best_signal,
                            'entry_time': timestamp,
                            'entry_price': actual_entry,
                            'quantity': qty,
                            'stop_loss': best_sl,
                            'take_profit': best_tp,
                            'entry_fee': fee
                        }
                        
                        self.balance -= fee
                        self.open_trades.append(trade)

        if self.open_trades:
            last_bar = self.df.iloc[-1]
            last_time = last_bar['timestamp']
            for trade in list(self.open_trades):
                self._close_trade(trade, last_bar['close'], last_time, 'TIME_EXIT')

        return self.trade_history, pd.DataFrame(self.equity_curve)

    def _update_open_trades(self, bar, timestamp):
        high = bar['high']
        low = bar['low']
        
        for trade in list(self.open_trades):
            side = trade['side']
            sl = trade['stop_loss']
            tp = trade['take_profit']
            
            sl_hit = False
            tp_hit = False
            
            if side == 'BUY':
                if low <= sl: sl_hit = True
                if high >= tp: tp_hit = True
            elif side == 'SELL':
                if high >= sl: sl_hit = True
                if low <= tp: tp_hit = True
                
            if sl_hit and tp_hit:
                if self.intrabar_resolution == "conservative":
                    self._close_trade(trade, sl, timestamp, 'SL_HIT')
                elif self.intrabar_resolution == "optimistic":
                    self._close_trade(trade, tp, timestamp, 'TP_HIT')
                else:
                    self._close_trade(trade, sl, timestamp, 'SL_HIT')
            elif sl_hit:
                self._close_trade(trade, sl, timestamp, 'SL_HIT')
            elif tp_hit:
                self._close_trade(trade, tp, timestamp, 'TP_HIT')

    def _update_equity(self, bar):
        close = bar['close']
        unrealized_pnl = 0
        for t in self.open_trades:
            if t['side'] == 'BUY':
                unrealized_pnl += (close - t['entry_price']) * t['quantity']
            else:
                unrealized_pnl += (t['entry_price'] - close) * t['quantity']
                
        self.equity = self.balance + unrealized_pnl

    def _close_trade(self, trade, exit_price, timestamp, reason):
        if trade['side'] == 'BUY':
            exit_price *= (1 - self.slippage_rate)
        else:
            exit_price *= (1 + self.slippage_rate)
            
        exit_fee = exit_price * trade['quantity'] * self.fee_rate
        total_fees = trade['entry_fee'] + exit_fee
        
        if trade['side'] == 'BUY':
            gross_pnl = (exit_price - trade['entry_price']) * trade['quantity']
        else:
            gross_pnl = (trade['entry_price'] - exit_price) * trade['quantity']
            
        net_pnl = gross_pnl - total_fees
        
        # Deduct ONLY the exit fee from balance, as entry fee was already deducted
        self.balance += (gross_pnl - exit_fee)
        
        sl_distance = abs(trade['entry_price'] - trade['stop_loss'])
        r_multiple = (exit_price - trade['entry_price']) / sl_distance if sl_distance > 0 else 0
        if trade['side'] == 'SELL':
            r_multiple = -r_multiple
            
        slippage_cost = (trade['entry_price'] * trade['quantity'] * self.slippage_rate) + (exit_price * trade['quantity'] * self.slippage_rate)
            
        completed_trade = {
            'trade_id': trade['trade_id'],
            'strategy': trade['strategy'],
            'symbol': trade['symbol'],
            'side': trade['side'],
            'entry_time': trade['entry_time'],
            'exit_time': timestamp,
            'entry_price': trade['entry_price'],
            'exit_price': exit_price,
            'quantity': trade['quantity'],
            'stop_loss': trade['stop_loss'],
            'take_profit': trade['take_profit'],
            'gross_pnl': gross_pnl,
            'fees': total_fees,
            'slippage': slippage_cost,
            'net_pnl': net_pnl,
            'result': 'WIN' if net_pnl > 0 else 'LOSS',
            'reason': reason,
            'holding_time': (timestamp - trade['entry_time']).total_seconds() / 60.0,
            'r_multiple': r_multiple
        }
        
        self.trade_history.append(completed_trade)
        self.open_trades.remove(trade)
