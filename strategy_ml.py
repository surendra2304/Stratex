# ==============================================================================
# STRATEGY: REGIME-FILTERED ENSEMBLE (formerly ML)
# ==============================================================================
# This advanced strategy uses the ADX to detect if the market is trending or ranging.
# If Trending: Uses MACD Trend Following
# If Ranging: Uses RSI Mean Reversion
# ==============================================================================
import pandas as pd
import ta

def get_signal(df: pd.DataFrame):
    """
    Returns (signal, stop_loss, take_profit)
    signal: 'BUY', 'SELL', or None
    """
    if len(df) < 50:
        return None, None, None

    # Calculate ADX (Average Directional Index) for Regime Filtering
    adx_indicator = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14)
    df['adx'] = adx_indicator.adx()
    
    current = df.iloc[-1]
    prev = df.iloc[-2]
    
    adx_val = current['adx']
    
    signal = None
    
    # 1. MARKET REGIME: TRENDING (ADX > 25)
    if adx_val > 25:
        # Strategy: MACD Trend Following
        if current['macd'] > current['macd_signal'] and prev['macd'] <= prev['macd_signal']:
            # Bullish Crossover
            signal = "BUY"
        elif current['macd'] < current['macd_signal'] and prev['macd'] >= prev['macd_signal']:
            # Bearish Crossover
            signal = "SELL"
            
    # 2. MARKET REGIME: RANGING (ADX <= 25)
    else:
        # Strategy: RSI Mean Reversion
        if current['rsi'] < 30:
            signal = "BUY"
        elif current['rsi'] > 70:
            signal = "SELL"

    # 3. RISK MANAGEMENT
    if signal:
        price = current['close']
        atr = current['atr']
        
        # 1.5 ATR for Stop Loss, 3.0 ATR for Take Profit (1:2 Risk Reward)
        if signal == "BUY":
            sl = price - (atr * 1.5)
            tp = price + (atr * 3.0)
        else:
            sl = price + (atr * 1.5)
            tp = price - (atr * 3.0)
            
        return signal, sl, tp

    return None, None, None
