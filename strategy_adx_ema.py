import pandas as pd
import numpy as np

def compute_atr(df, period=14):
    tr = pd.concat([
        df['high'] - df['low'], 
        abs(df['high'] - df['close'].shift(1)), 
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def true_range(df):
    return pd.concat([
        df['high'] - df['low'], 
        abs(df['high'] - df['close'].shift(1)), 
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)

def compute_adx(df, period=14):
    plus_dm = df['high'].diff()
    minus_dm = df['low'].diff(-1).shift(1)
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
    
    tr = true_range(df)
    
    tr_smooth = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / tr_smooth * 100
    minus_di = pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / tr_smooth * 100
    
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
    adx = dx.ewm(alpha=1/period, adjust=False).mean()
    return adx

def add_features(df):
    """
    Computes all necessary features for the ADX+EMA strategy.
    Called by the execution engine prior to get_signal().
    """
    df = df.copy()
    df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['atr_adx_ema'] = compute_atr(df, 14)
    df['adx'] = compute_adx(df, 14)
    return df

def get_signal(df):
    """
    ADX + EMA Trend Following Strategy:
    - Fast EMA=20, Slow EMA=50, Direction=200 EMA
    - Buy on EMA20 crossing above EMA50, if price > EMA200 AND ADX > 25
    - Sell on EMA20 crossing below EMA50, if price < EMA200 AND ADX > 25
    """
    if df is None or len(df) < 200:
        return None, None, None

    # Calculate strategy-specific features if they don't exist
    if 'atr_adx_ema' not in df.columns or 'adx' not in df.columns:
        df = add_features(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    cross_up = last['ema_20'] > last['ema_50'] and prev['ema_20'] <= prev['ema_50']
    cross_dn = last['ema_20'] < last['ema_50'] and prev['ema_20'] >= prev['ema_50']

    # ADX strength requirement
    trend_strong = pd.notna(last['adx']) and last['adx'] > 25

    if cross_up and last['close'] > last['ema_200'] and trend_strong:
        sl = last['close'] - (2 * last['atr_adx_ema'])
        tp = last['close'] + (3 * last['atr_adx_ema'])
        return "BUY", sl, tp

    if cross_dn and last['close'] < last['ema_200'] and trend_strong:
        sl = last['close'] + (2 * last['atr_adx_ema'])
        tp = last['close'] - (3 * last['atr_adx_ema'])
        return "SELL", sl, tp

    return None, None, None
