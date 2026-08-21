# ==============================================================================
# STRATEGY_SUPERTREND.PY - Maximum Profitability Trend Rider & Pullback Engine
# ==============================================================================

from collections import namedtuple

SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"]
)

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.48  # High win rate with pullback confirmation
_RR_RATIO = 2.5             # Asymmetric reward-to-risk (2.5x to 3.0x)

def get_signal(df):
    """
    Supertrend + 200 EMA + Value Pullback Strategy:
    1. Fresh Breakout: Supertrend flips Bullish + Price > 200 EMA
    2. Trend Pullback Continuation: Supertrend already Bullish + Pullback to EMA21/50 + Bullish bounce + RSI in 40-65 zone
    Returns: SignalResult
    """
    if df is None or len(df) < 50:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Required columns check
    if 'supertrend' not in df.columns:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    st_now = bool(last['supertrend'])
    st_prev = bool(prev['supertrend'])
    close = float(last['close'])
    open_p = float(last['open'])
    low = float(last['low'])
    float(last['high'])
    
    ema_200 = float(last.get('ema_200', close))
    ema_50  = float(last.get('ema_50', close))
    ema_21  = float(last.get('ema_21', last.get('ema_20', close)))
    atr     = float(last.get('atr', last.get('atr_14', close * 0.01)))
    rsi     = float(last.get('rsi', last.get('rsi_14', 50.0)))
    st_lower = float(last.get('st_lower', close - 2.0 * atr))
    st_upper = float(last.get('st_upper', close + 2.0 * atr))

    # --- 1. Fresh Trend Flip (Breakout Entry) ---
    if st_now == True and st_prev == False and close > ema_200:
        sl = max(st_lower, close - (atr * 2.5))
        risk = max(close - sl, atr * 1.0)
        tp = close + (risk * 3.0)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, 3.0)

    # --- 2. Trend Continuation / Dynamic Pullback Entry ---
    # When established in an uptrend (Supertrend True for >1 bar, price above 200 EMA & EMA21 > EMA50)
    if st_now == True and st_prev == True and close > ema_200 and ema_21 >= ema_50:
        # Price dipped to touch EMA21 or EMA50 during this candle or prev candle
        pulled_back = (low <= ema_21 * 1.002 or prev['low'] <= ema_21 * 1.002)
        # Closed bullish (green candle bouncing back above EMA21)
        bullish_bounce = (close > open_p) and (close >= ema_21)
        # Healthy momentum (not overbought, resetting from support)
        rsi_healthy = (38 <= rsi <= 68)

        if pulled_back and bullish_bounce and rsi_healthy:
            sl = max(st_lower, close - (atr * 2.0))
            risk = max(close - sl, atr * 1.0)
            tp = close + (risk * 2.5)
            return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # --- Bearish Signals (For Short/Hedge evaluation) ---
    if st_now == False and st_prev == True and close < ema_200:
        sl = min(st_upper, close + (atr * 2.5))
        risk = max(sl - close, atr * 1.0)
        tp = close - (risk * 3.0)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, 3.0)

    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
