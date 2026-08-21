"""
scripts/validate_strategies_oos.py — Multi-Asset Chronological OOS Validation
Evaluates candidate strategies with strict Binance Spot Taker friction (31 bps round-trip).
Chronological split: 60% Train, 20% Val, 20% OOS.
"""

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binance.client import Client

from data import add_indicators
from research_phase9.cost_engine import CostEngine

_data_cache = {}

def fetch_klines(symbol, interval, limit=1000):
    cache_key = f"{symbol}_{interval}"
    if cache_key in _data_cache:
        return _data_cache[cache_key].copy()
        
    cache_file = os.path.join("data_cache", f"{cache_key}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file, parse_dates=['timestamp'], index_col='timestamp')
            _data_cache[cache_key] = df
            return df.copy()
        except Exception:
            pass
            
    client = Client("", "", testnet=True)
    try:
        print(f"Fetching {symbol} {interval} klines...")
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume', 'taker_buy_base_asset_volume']:
            df[col] = df[col].astype(float)
        df['buy_vol'] = df['taker_buy_base_asset_volume']
        df['sell_vol'] = df['volume'] - df['buy_vol']
        df['vol_delta'] = df['buy_vol'] - df['sell_vol']
        df.set_index('timestamp', inplace=True)
        res = df[['open', 'high', 'low', 'close', 'volume', 'vol_delta', 'buy_vol', 'sell_vol']]
        _data_cache[cache_key] = res
        
        os.makedirs("data_cache", exist_ok=True)
        res.to_csv(cache_file)
        return res.copy()
    except Exception as e:
        print(f"Error fetching {symbol} {interval}: {e}")
        return pd.DataFrame()

def run_oos_evaluation(df, strat_module, cost_engine):
    """Computes features on the entire series, then simulates trading strictly on the OOS 20% window."""
    if df.empty or len(df) < 100:
        return []
        
    df = add_indicators(df.copy())
    if hasattr(strat_module, 'add_features'):
        df = strat_module.add_features(df)
        
    n = len(df)
    val_end = int(n * 0.80) # 80% mark: last 20% is true OOS
    
    trades = []
    in_pos = False
    pos_side = None
    entry_price = 0.0
    sl_price = 0.0
    tp_price = 0.0
    entry_idx = None
    entry_time = None
    
    total_friction = cost_engine.get_total_friction() # 0.0031
    
    for i in range(val_end, len(df) - 1):
        sub_df = df.iloc[:i+1]
        
        # If in position, check if current bar hits TP or SL
        if in_pos:
            curr_bar = df.iloc[i]
            high = curr_bar['high']
            low = curr_bar['low']
            
            exit_price = None
            exit_reason = None
            
            if pos_side == "BUY":
                if low <= sl_price:
                    exit_price = sl_price
                    exit_reason = "SL"
                elif high >= tp_price:
                    exit_price = tp_price
                    exit_reason = "TP"
            elif pos_side == "SELL":
                if high >= sl_price:
                    exit_price = sl_price
                    exit_reason = "SL"
                elif low <= tp_price:
                    exit_price = tp_price
                    exit_reason = "TP"
                    
            if exit_price is not None:
                if pos_side == "BUY":
                    gross_ret = (exit_price - entry_price) / entry_price
                else:
                    gross_ret = (entry_price - exit_price) / entry_price
                    
                net_ret = gross_ret - total_friction
                
                trades.append({
                    "entry_time": str(entry_time),
                    "exit_time": str(curr_bar.name),
                    "side": pos_side,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "gross_return": gross_ret,
                    "net_return": net_ret,
                    "friction": total_friction,
                    "bars_held": i - entry_idx
                })
                in_pos = False
                pos_side = None
                
        # If not in position, evaluate signal for entry on next bar
        if not in_pos:
            sig = strat_module.get_signal(sub_df)
            side = getattr(sig, 'side', sig[0] if sig else None)
            sl = getattr(sig, 'sl', sig[1] if sig else None)
            tp = getattr(sig, 'tp', sig[2] if sig else None)
            
            if side in ("BUY", "SELL") and sl and tp:
                next_bar = df.iloc[i+1]
                entry_price = next_bar['open']
                pos_side = side
                sl_price = sl
                tp_price = tp
                entry_idx = i + 1
                entry_time = next_bar.name
                in_pos = True
                
    return trades

def compute_metrics(trades):
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "net_pnl_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_bps": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_trade_pct": 0.0
        }
        
    net_returns = [t['net_return'] for t in trades]
    wins = [r for r in net_returns if r > 0]
    losses = [r for r in net_returns if r <= 0]
    
    total_trades = len(trades)
    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
    gross_gain = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = (gross_gain / gross_loss) if gross_loss > 0 else (float('inf') if gross_gain > 0 else 0.0)
    
    # Cumulative equity curve
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in net_returns:
        equity *= (1.0 + r)
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        max_dd = max(max_dd, dd)
            
    net_pnl_pct = equity - 1.0
    avg_trade = np.mean(net_returns) if net_returns else 0.0
    
    return {
        "total_trades": total_trades,
        "win_rate": round(win_rate * 100, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy_bps": round(avg_trade * 10000, 2),
        "net_pnl_pct": round(net_pnl_pct * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "avg_trade_pct": round(avg_trade * 100, 4)
    }

def main():
    import strategy_adx_ema
    import strategy_aggressor
    import strategy_scalper
    import strategy_supertrend
    import strategy_swing
    
    cost_engine = CostEngine.get_binance_taker_config()
    
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT", "ADAUSDT"]
    candidates = [
        ("adx_ema", "4h", strategy_adx_ema),
        ("adx_ema", "1h", strategy_adx_ema),
        ("supertrend", "15m", strategy_supertrend),
        ("supertrend", "1h", strategy_supertrend),
        ("swing", "1d", strategy_swing),
        ("swing", "4h", strategy_swing),
        ("scalper", "1m", strategy_scalper),
        ("aggressor", "1m", strategy_aggressor),
    ]
    
    report_rows = []
    
    for strat_name, tf, strat_mod in candidates:
        all_oos_trades = []
        symbol_details = {}
        
        for sym in symbols:
            df = fetch_klines(sym, tf, limit=1000)
            if df.empty or len(df) < 150:
                continue
                
            oos_trades = run_oos_evaluation(df, strat_mod, cost_engine)
            all_oos_trades.extend(oos_trades)
            sym_metrics = compute_metrics(oos_trades)
            symbol_details[sym] = sym_metrics
            
        agg_metrics = compute_metrics(all_oos_trades)
        
        is_validated = (
            agg_metrics["total_trades"] >= 5 and
            agg_metrics["profit_factor"] >= 1.05 and
            agg_metrics["expectancy_bps"] >= 5.0 and
            agg_metrics["net_pnl_pct"] > 0.0
        )
        
        status = "VALIDATED" if is_validated else "UNVALIDATED"
        
        report_rows.append({
            "strategy": strat_name,
            "timeframe": tf,
            "status": status,
            "oos_trades": agg_metrics["total_trades"],
            "oos_win_rate": f"{agg_metrics['win_rate']}%",
            "oos_profit_factor": agg_metrics["profit_factor"],
            "oos_expectancy_bps": agg_metrics["expectancy_bps"],
            "oos_net_return": f"{agg_metrics['net_pnl_pct']}%",
            "oos_max_dd": f"{agg_metrics['max_drawdown_pct']}%",
            "symbol_details": symbol_details
        })
        print(f"[{status}] {strat_name} ({tf}): {agg_metrics['total_trades']} trades | Win: {agg_metrics['win_rate']}% | PF: {agg_metrics['profit_factor']} | Exp: {agg_metrics['expectancy_bps']} bps | Net: {agg_metrics['net_pnl_pct']}%")

    os.makedirs("backtest_results", exist_ok=True)
    with open("backtest_results/oos_strategy_audit.json", "w") as f:
        json.dump(report_rows, f, indent=2)
        
    print("\nSaved OOS audit to backtest_results/oos_strategy_audit.json")

if __name__ == "__main__":
    main()
