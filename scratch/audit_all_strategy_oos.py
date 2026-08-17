"""
scratch/audit_all_strategy_oos.py
Strict out-of-sample empirical validation across all candidate strategy families.
"""

import sys
import os
import pandas as pd
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_client import MarketDataClient

def fetch_data(symbol, interval, limit=1000):
    cache_path = f"cache_{symbol}_{interval}.csv"
    if os.path.exists(cache_path):
        df = pd.read_csv(cache_path)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df
        
    client = MarketDataClient()
    raw = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw, columns=[
        "timestamp","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.to_csv(cache_path, index=False)
    return df

def compute_atr(df, period=14):
    tr = pd.concat([
        df['high'] - df['low'], 
        abs(df['high'] - df['close'].shift(1)), 
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def compute_adx(df, period=14):
    plus_dm = df['high'].diff()
    minus_dm = df['low'].diff(-1).shift(1)
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
    
    tr = pd.concat([
        df['high'] - df['low'], 
        abs(df['high'] - df['close'].shift(1)), 
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    
    tr_smooth = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = pd.Series(plus_dm).ewm(alpha=1/period, adjust=False).mean() / tr_smooth * 100
    minus_di = pd.Series(minus_dm).ewm(alpha=1/period, adjust=False).mean() / tr_smooth * 100
    
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
    return dx.ewm(alpha=1/period, adjust=False).mean()

def backtest_strategy(df, signals, roundtrip_friction=0.0031):
    """
    Simulates strict next-bar execution for signals list:
    each signal is (idx, side, sl, tp)
    """
    if not signals:
        return {"trades": 0, "win_rate": 0.0, "net_pnl": 0.0, "profit_factor": 0.0, "expectancy": 0.0, "max_dd": 0.0}
        
    wins, losses = 0, 0
    gross_profit, gross_loss = 0.0, 0.0
    equity_curve = [10000.0]
    
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    n = len(df)
    
    for idx, side, sl, tp in signals:
        if idx >= n - 1:
            continue
        entry_price = closes[idx]
        
        trade_closed = False
        for j in range(idx + 1, min(idx + 150, n)):
            h = highs[j]
            l = lows[j]
            
            if side == "BUY":
                if l <= sl and h >= tp: # Ambiguous bar: conservative loss
                    losses += 1
                    loss_pct = abs((entry_price - sl) / entry_price) + roundtrip_friction
                    gross_loss += loss_pct
                    equity_curve.append(equity_curve[-1] * (1 - loss_pct * 0.02))
                    trade_closed = True
                    break
                elif h >= tp:
                    wins += 1
                    win_pct = abs((tp - entry_price) / entry_price) - roundtrip_friction
                    gross_profit += win_pct
                    equity_curve.append(equity_curve[-1] * (1 + win_pct * 0.02))
                    trade_closed = True
                    break
                elif l <= sl:
                    losses += 1
                    loss_pct = abs((entry_price - sl) / entry_price) + roundtrip_friction
                    gross_loss += loss_pct
                    equity_curve.append(equity_curve[-1] * (1 - loss_pct * 0.02))
                    trade_closed = True
                    break
            elif side == "SELL":
                if h >= sl and l <= tp:
                    losses += 1
                    loss_pct = abs((sl - entry_price) / entry_price) + roundtrip_friction
                    gross_loss += loss_pct
                    equity_curve.append(equity_curve[-1] * (1 - loss_pct * 0.02))
                    trade_closed = True
                    break
                elif l <= tp:
                    wins += 1
                    win_pct = abs((entry_price - tp) / entry_price) - roundtrip_friction
                    gross_profit += win_pct
                    equity_curve.append(equity_curve[-1] * (1 + win_pct * 0.02))
                    trade_closed = True
                    break
                elif h >= sl:
                    losses += 1
                    loss_pct = abs((sl - entry_price) / entry_price) + roundtrip_friction
                    gross_loss += loss_pct
                    equity_curve.append(equity_curve[-1] * (1 - loss_pct * 0.02))
                    trade_closed = True
                    break
                    
    total_trades = wins + losses
    if total_trades == 0:
        return {"trades": 0, "win_rate": 0.0, "net_pnl": 0.0, "profit_factor": 0.0, "expectancy": 0.0, "max_dd": 0.0}
        
    win_rate = wins / total_trades
    net_pnl = gross_profit - gross_loss
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0
    expectancy = net_pnl / total_trades
    
    # Max Drawdown
    eq = np.array(equity_curve)
    peaks = np.maximum.accumulate(eq)
    dds = (peaks - eq) / peaks
    max_dd = np.max(dds)
    
    return {
        "trades": total_trades,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "max_dd": max_dd
    }

def run_all_strategy_audits():
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    results = []
    
    print("=" * 80)
    print("EMPIRICAL OOS AUDIT ACROSS CANDIDATE STRATEGIES (Friction: 31 bps)")
    print("=" * 80)
    
    for sym in symbols:
        # 1. ADX+EMA on 4H
        df_4h = fetch_data(sym, "4h", limit=1000)
        if not df_4h.empty and len(df_4h) >= 200:
            df_4h['ema_20'] = df_4h['close'].ewm(span=20, adjust=False).mean()
            df_4h['ema_50'] = df_4h['close'].ewm(span=50, adjust=False).mean()
            df_4h['ema_200'] = df_4h['close'].ewm(span=200, adjust=False).mean()
            df_4h['atr'] = compute_atr(df_4h, 14)
            df_4h['adx'] = compute_adx(df_4h, 14)
            
            signals_adx_ema = []
            for i in range(1, len(df_4h)):
                last = df_4h.iloc[i]
                prev = df_4h.iloc[i-1]
                cross_up = last['ema_20'] > last['ema_50'] and prev['ema_20'] <= prev['ema_50']
                cross_dn = last['ema_20'] < last['ema_50'] and prev['ema_20'] >= prev['ema_50']
                if cross_up and last['close'] > last['ema_200'] and last['adx'] > 25:
                    sl = last['close'] - 2.0 * last['atr']
                    tp = last['close'] + 3.0 * last['atr']
                    signals_adx_ema.append((i, "BUY", sl, tp))
                elif cross_dn and last['close'] < last['ema_200'] and last['adx'] > 25:
                    sl = last['close'] + 2.0 * last['atr']
                    tp = last['close'] - 3.0 * last['atr']
                    signals_adx_ema.append((i, "SELL", sl, tp))
                    
            r = backtest_strategy(df_4h, signals_adx_ema)
            r["strategy"] = "ADX_EMA (4h)"
            r["symbol"] = sym
            results.append(r)

        # 2. Swing MACD on 1H
        df_1h = fetch_data(sym, "1h", limit=1000)
        if not df_1h.empty and len(df_1h) >= 200:
            df_1h['ema_12'] = df_1h['close'].ewm(span=12, adjust=False).mean()
            df_1h['ema_26'] = df_1h['close'].ewm(span=26, adjust=False).mean()
            df_1h['macd'] = df_1h['ema_12'] - df_1h['ema_26']
            df_1h['macd_sig'] = df_1h['macd'].ewm(span=9, adjust=False).mean()
            df_1h['ema_200'] = df_1h['close'].ewm(span=200, adjust=False).mean()
            df_1h['atr'] = compute_atr(df_1h, 14)
            df_1h['vol_ma'] = df_1h['volume'].rolling(20).mean()
            
            signals_swing = []
            for i in range(1, len(df_1h)):
                last = df_1h.iloc[i]
                prev = df_1h.iloc[i-1]
                crossed_up = prev['macd'] < prev['macd_sig'] and last['macd'] > last['macd_sig']
                crossed_dn = prev['macd'] < prev['macd_sig'] and last['macd'] < last['macd_sig']
                rel_vol = last['volume'] / last['vol_ma'] if last['vol_ma'] > 0 else 1.0
                
                if last['close'] > last['ema_200'] and crossed_up and rel_vol > 1.0 and last['macd'] < 0:
                    sl = last['close'] - 2.0 * last['atr']
                    tp = last['close'] + 3.0 * last['atr']
                    signals_swing.append((i, "BUY", sl, tp))
                elif last['close'] < last['ema_200'] and crossed_dn and rel_vol > 1.0 and last['macd'] > 0:
                    sl = last['close'] + 2.0 * last['atr']
                    tp = last['close'] - 3.0 * last['atr']
                    signals_swing.append((i, "SELL", sl, tp))
                    
            r = backtest_strategy(df_1h, signals_swing)
            r["strategy"] = "SWING_MACD (1h)"
            r["symbol"] = sym
            results.append(r)

    res_df = pd.DataFrame(results)
    print(res_df.to_string(index=False))

if __name__ == "__main__":
    run_all_strategy_audits()
