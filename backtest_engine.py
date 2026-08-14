import pandas as pd
import numpy as np
import uuid
from datetime import datetime

class DataValidator:
    """Validates the chronological integrity of backtest data."""
    @staticmethod
    def validate(df):
        if df is None or df.empty:
            raise ValueError("Data is empty")
        
        # Check for missing values in core columns
        core_cols = ['open', 'high', 'low', 'close', 'volume', 'timestamp']
        if df[core_cols].isnull().any().any():
            raise ValueError("Data contains NaNs in core OHLCV columns.")
            
        # Check chronological ordering
        if not df['timestamp'].is_monotonic_increasing:
            raise ValueError("Data is not in chronological order.")
            
        # Check for duplicate timestamps
        if df['timestamp'].duplicated().any():
            raise ValueError("Data contains duplicate timestamps.")
            
        # Check valid OHLC relationships
        invalid_ohlc = df[(df['high'] < df['low']) | 
                          (df['open'] > df['high']) | (df['open'] < df['low']) | 
                          (df['close'] > df['high']) | (df['close'] < df['low'])]
        if not invalid_ohlc.empty:
            raise ValueError(f"Data contains {len(invalid_ohlc)} invalid OHLC relationships.")
            
        print("[VALIDATOR] Data integrity verified successfully.")
        return True

class BacktestEngine:
    def __init__(self, df, strategies, fee_rate=0.001, slippage_rate=0.0005, 
                 initial_balance=10000.0, risk_per_trade=0.01, max_open_trades=1):
        self.df = df
        self.strategies = strategies if isinstance(strategies, list) else [strategies]
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.risk_per_trade = risk_per_trade
        self.max_open_trades = max_open_trades
        
        self.open_trades = []
        self.trade_history = []
        self.equity_curve = []
        
    def _calculate_qty(self, entry_price, sl_price):
        """Risk-based position sizing."""
        risk_amount = self.equity * self.risk_per_trade
        sl_distance = abs(entry_price - sl_price)
        
        if sl_distance == 0:
            # Fallback if SL is exactly entry price (which shouldn't happen)
            sl_distance = entry_price * 0.01
            
        raw_qty = risk_amount / sl_distance
        
        # In a real bot, we'd apply Binance lot size step filters here.
        # For backtesting, we truncate to 4 decimal places.
        qty = np.floor(raw_qty * 10000) / 10000.0
        
        # Max leverage check (prevent buying more than we have equity for on spot, or limit leverage)
        # Assuming spot trading (no leverage): max qty = equity / entry_price
        max_spot_qty = self.equity / entry_price
        qty = min(qty, max_spot_qty)
        
        # Min qty check (e.g. $10 min notional)
        if qty * entry_price < 10.0:
            return 0.0
            
        return qty

    def run(self):
        """Runs the bar-by-bar simulation."""
        print(f"[ENGINE] Starting backtest. Initial Balance: ${self.initial_balance:.2f}")
        DataValidator.validate(self.df)
        
        # Warmup period for indicators
        warmup = 200
        if len(self.df) <= warmup:
            print("[ENGINE] Dataset too short for warmup.")
            return
            
        for i in range(warmup, len(self.df)):
            current_bar = self.df.iloc[i]
            timestamp = current_bar['timestamp']
            
            # 1. Update Open Trades (Check for SL/TP hits on CURRENT bar)
            self._update_open_trades(current_bar, timestamp)
            
            # 2. Update Equity
            self._update_equity(current_bar)
            self.equity_curve.append({'timestamp': timestamp, 'equity': self.equity})
            
            # 3. Generate Signals if we have capacity
            if len(self.open_trades) < self.max_open_trades:
                # We strictly use data UP TO the current bar (inclusive) for signal generation
                # In live trading, this simulates evaluating at the close of the bar.
                window = self.df.iloc[i-100 : i+1]
                
                best_signal = None
                best_sl = None
                best_tp = None
                source_strat = None
                
                for strat in self.strategies:
                    sig, sl, tp = strat.get_signal(window)
                    if sig:
                        # In this simple iteration, we take the first signal generated
                        # Multi-strategy can handle its own logic
                        best_signal = sig
                        best_sl = sl
                        best_tp = tp
                        source_strat = strat.__name__.split('_')[-1]
                        break
                
                if best_signal:
                    entry_price = current_bar['close']
                    
                    # Apply slippage on entry
                    if best_signal == 'BUY':
                        entry_price *= (1 + self.slippage_rate)
                    else:
                        entry_price *= (1 - self.slippage_rate)
                        
                    qty = self._calculate_qty(entry_price, best_sl)
                    
                    if qty > 0:
                        fee = entry_price * qty * self.fee_rate
                        
                        trade = {
                            'trade_id': str(uuid.uuid4())[:8],
                            'strategy': source_strat,
                            'symbol': 'SIM', # Replace if tracking multiple
                            'side': best_signal,
                            'entry_time': timestamp,
                            'entry_price': entry_price,
                            'quantity': qty,
                            'stop_loss': best_sl,
                            'take_profit': best_tp,
                            'entry_fee': fee
                        }
                        
                        # Deduct entry fee immediately from balance
                        self.balance -= fee
                        self.open_trades.append(trade)

        # Close out any remaining trades at the last close price
        if self.open_trades:
            last_bar = self.df.iloc[-1]
            last_time = last_bar['timestamp']
            # We copy the list because we modify it in the loop
            for trade in list(self.open_trades):
                self._close_trade(trade, last_bar['close'], last_time, 'TIME_EXIT')

        print(f"[ENGINE] Backtest complete. Final Balance: ${self.balance:.2f}")
        return self.trade_history, pd.DataFrame(self.equity_curve)

    def _update_open_trades(self, bar, timestamp):
        """Evaluates SL/TP against the high/low of the bar."""
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
                
            # Resolution logic
            if sl_hit and tp_hit:
                # Conservative: assume SL was hit first
                self._close_trade(trade, sl, timestamp, 'SL_HIT')
            elif sl_hit:
                self._close_trade(trade, sl, timestamp, 'SL_HIT')
            elif tp_hit:
                self._close_trade(trade, tp, timestamp, 'TP_HIT')

    def _update_equity(self, bar):
        """Updates MTM equity based on open positions and current close."""
        close = bar['close']
        unrealized_pnl = 0
        for t in self.open_trades:
            if t['side'] == 'BUY':
                unrealized_pnl += (close - t['entry_price']) * t['quantity']
            else:
                unrealized_pnl += (t['entry_price'] - close) * t['quantity']
                
        self.equity = self.balance + unrealized_pnl

    def _close_trade(self, trade, exit_price, timestamp, reason):
        """Closes a trade, applies fees/slippage, calculates PnL and R-multiple."""
        # Apply slippage on exit (worse price)
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
        
        self.balance += net_pnl
        
        # Calculate R-Multiple
        sl_distance = abs(trade['entry_price'] - trade['stop_loss'])
        r_multiple = (exit_price - trade['entry_price']) / sl_distance if sl_distance > 0 else 0
        if trade['side'] == 'SELL':
            r_multiple = -r_multiple
            
        # Slippage accounting for record
        # Approximation of slippage dollar cost:
        # Slippage cost = qty * entry_price * slippage_rate + qty * exit_price * slippage_rate
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
            'holding_time': (timestamp - trade['entry_time']).total_seconds() / 60.0, # minutes
            'r_multiple': r_multiple
        }
        
        self.trade_history.append(completed_trade)
        self.open_trades.remove(trade)
