# quantum/validation/backtest.py
"""Research Backtest Engine for walk-forward fold evaluation."""

import time
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

@dataclass
class TradeRecord:
    symbol: str
    entry_time: str
    exit_time: str
    side: str
    entry_price: float
    exit_price: float
    sl: float
    tp: float
    gross_pnl: float
    fees: float
    slippage: float
    net_pnl: float
    net_return_pct: float
    duration_candles: int
    exit_reason: str

@dataclass
class BacktestResult:
    strategy_name: str
    fold_idx: int
    trades: List[TradeRecord]
    equity_curve: List[float]
    total_trades: int
    win_rate_pct: float
    net_profit: float
    net_return_pct: float
    max_drawdown_pct: float
    profit_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    avg_trade_pnl: float
    total_fees: float
    total_slippage: float
    turnover: float
    avg_latency_ms: float
    backend_used: str

class BacktestRunner:
    """
    Simulates bar-by-bar execution with realistic fee (0.1%), slippage (0.05%), and trade resolution.
    Ensures 100% fair and identical trading environment across all 5 strategies.
    """
    def __init__(
        self,
        initial_capital: float = 10000.0,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        risk_per_trade: float = 0.01,
        max_open_trades: int = 1
    ):
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.risk_per_trade = risk_per_trade
        self.max_open_trades = max_open_trades

    def run_strategy(self, strategy: Any, df: pd.DataFrame, fold_idx: int = 1, is_optimizer_wrapper: bool = False, base_strategy: Any = None) -> BacktestResult:
        if df.empty or len(df) < 35:
            return BacktestResult(
                strategy_name=getattr(strategy, "name", "Strategy"),
                fold_idx=fold_idx,
                trades=[],
                equity_curve=[self.initial_capital],
                total_trades=0,
                win_rate_pct=0.0,
                net_profit=0.0,
                net_return_pct=0.0,
                max_drawdown_pct=0.0,
                profit_factor=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                avg_trade_pnl=0.0,
                total_fees=0.0,
                total_slippage=0.0,
                turnover=0.0,
                avg_latency_ms=0.0,
                backend_used=getattr(strategy, "backend_used", "Local_CPU")
            )

        try:
            from features import add_features
        except ImportError:
            from ..features import add_features

        df_feat = add_features(df.copy())
        capital = self.initial_capital
        equity_curve = [capital]
        trades: List[TradeRecord] = []
        open_trade: Optional[Dict[str, Any]] = None
        latencies: List[float] = []
        
        warmup = 30
        for i in range(warmup, len(df_feat)):
            current_bar = df_feat.iloc[i]
            ts_str = str(current_bar['timestamp'])
            high = float(current_bar['high'])
            low = float(current_bar['low'])
            close = float(current_bar['close'])
            
            # 1. Manage Active Trade
            if open_trade is not None:
                open_trade['duration'] += 1
                entry_p = open_trade['entry_price']
                sl = open_trade['sl']
                tp = open_trade['tp']
                qty = open_trade['qty']
                side = open_trade['side']
                
                exit_price = None
                exit_reason = None
                
                if side == "BUY":
                    if low <= sl:
                        exit_price = sl * (1 - self.slippage_rate)
                        exit_reason = "STOP_LOSS"
                    elif high >= tp:
                        exit_price = tp * (1 - self.slippage_rate)
                        exit_reason = "TAKE_PROFIT"
                    elif open_trade['duration'] >= 40:
                        exit_price = close * (1 - self.slippage_rate)
                        exit_reason = "TIME_EXPIRY"
                else: # SELL
                    if high >= sl:
                        exit_price = sl * (1 + self.slippage_rate)
                        exit_reason = "STOP_LOSS"
                    elif low <= tp:
                        exit_price = tp * (1 + self.slippage_rate)
                        exit_reason = "TAKE_PROFIT"
                    elif open_trade['duration'] >= 40:
                        exit_price = close * (1 + self.slippage_rate)
                        exit_reason = "TIME_EXPIRY"
                        
                if exit_price is not None:
                    if side == "BUY":
                        gross_pnl = (exit_price - entry_p) * qty
                    else:
                        gross_pnl = (entry_p - exit_price) * qty
                        
                    fee_entry = entry_p * qty * self.fee_rate
                    fee_exit = exit_price * qty * self.fee_rate
                    total_fee = fee_entry + fee_exit
                    total_slip = (entry_p * qty * self.slippage_rate) + (exit_price * qty * self.slippage_rate)
                    net_pnl = gross_pnl - total_fee
                    
                    capital += net_pnl
                    equity_curve.append(capital)
                    
                    trades.append(TradeRecord(
                        symbol="BTCUSDT",
                        entry_time=open_trade['entry_time'],
                        exit_time=ts_str,
                        side=side,
                        entry_price=entry_p,
                        exit_price=exit_price,
                        sl=sl,
                        tp=tp,
                        gross_pnl=gross_pnl,
                        fees=total_fee,
                        slippage=total_slip,
                        net_pnl=net_pnl,
                        net_return_pct=(net_pnl / max(10.0, entry_p * qty)) * 100.0,
                        duration_candles=open_trade['duration'],
                        exit_reason=exit_reason
                    ))
                    open_trade = None
            else:
                equity_curve.append(capital)

            # 2. Evaluate Signal Generation on Precomputed Feature Row
            if open_trade is None:
                window = df_feat.iloc[max(0, i - 100) : i + 1]
                t0 = time.perf_counter()
                
                if is_optimizer_wrapper and base_strategy is not None:
                    raw_sig = base_strategy.generate_signal(window)
                    if raw_sig.get("signal") in ["BUY", "SELL"]:
                        selected = strategy.select_best_opportunities([raw_sig], max_slots=1)
                        sig = selected[0] if selected else {"signal": "HOLD"}
                    else:
                        sig = raw_sig
                else:
                    sig = strategy.generate_signal(window)
                    
                latencies.append((time.perf_counter() - t0) * 1000.0)
                
                if sig.get("signal") in ["BUY", "SELL"]:
                    side = sig["signal"]
                    raw_entry = float(sig["entry"])
                    sl = float(sig["sl"])
                    tp = float(sig["tp"])
                    
                    entry_price = raw_entry * (1 + self.slippage_rate if side == "BUY" else 1 - self.slippage_rate)
                    sl_dist = abs(entry_price - sl)
                    if sl_dist > 0:
                        risk_amt = capital * self.risk_per_trade
                        qty = risk_amt / sl_dist
                        qty = min(qty, capital / entry_price)
                        
                        if qty * entry_price >= 10.0:
                            open_trade = {
                                "entry_time": ts_str,
                                "side": side,
                                "entry_price": entry_price,
                                "sl": sl,
                                "tp": tp,
                                "qty": qty,
                                "duration": 0
                            }

        # Calculate fold metrics
        from .metrics import calculate_performance_metrics
        perf = calculate_performance_metrics(trades, equity_curve, self.initial_capital)
        
        return BacktestResult(
            strategy_name=getattr(strategy, "name", "Strategy"),
            fold_idx=fold_idx,
            trades=trades,
            equity_curve=equity_curve,
            total_trades=perf.total_trades,
            win_rate_pct=perf.win_rate_pct,
            net_profit=perf.net_profit,
            net_return_pct=perf.net_return_pct,
            max_drawdown_pct=perf.max_drawdown_pct,
            profit_factor=perf.profit_factor,
            sharpe_ratio=perf.sharpe_ratio,
            sortino_ratio=perf.sortino_ratio,
            avg_trade_pnl=perf.avg_trade_pnl,
            total_fees=perf.total_fees,
            total_slippage=perf.total_slippage,
            turnover=perf.turnover,
            avg_latency_ms=float(np.mean(latencies)) if latencies else 0.0,
            backend_used=getattr(strategy, "backend_used", "Local_CPU")
        )
