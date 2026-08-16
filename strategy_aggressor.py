# ==============================================================================
# STRATEGY_AGGRESSOR.PY - High-Frequency Volume Delta Scalper
# ==============================================================================

from collections import namedtuple

SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"]
)

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.45  # Volume Delta scalping typically ~45%
_RR_RATIO = 2.66            # 4.0 ATR / 1.5 ATR

def get_signal(df):
    """
    The Aggressor Strategy:
    - Analyzes Order Book Imbalance (Volume Delta)
    - BUY when extreme buying pressure + momentum trending up (RSI > 55)
    - SELL when extreme selling pressure + momentum trending down (RSI < 45)
    - Uses ultra-tight stop losses for high frequency
    Returns: SignalResult
    """
    if df is None or len(df) < 2:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    last = df.iloc[-1]

    # Required columns check
    if 'vol_delta' not in df.columns or 'rsi' not in df.columns:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    rsi = last["rsi"]
    close = last["close"]
    atr = last["atr"]
    vol_delta = last["vol_delta"]
    
    # Calculate average volume delta to find extremes
    avg_vol = df["volume"].tail(20).mean()
    extreme_vol_threshold = avg_vol * 0.5  # Delta must be 50% of avg volume
    
    # BUY: Massive buy volume delta AND RSI confirms upward momentum
    if vol_delta > extreme_vol_threshold and rsi > 55:
        sl = close - (atr * 1.5)  # Give a bit more breathing room
        tp = close + (atr * 4.0)  # Aim for higher R:R
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # SELL: Massive sell volume delta AND RSI confirms downward momentum
    if vol_delta < -extreme_vol_threshold and rsi < 45:
        sl = close + (atr * 1.5)
        tp = close - (atr * 4.0)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

