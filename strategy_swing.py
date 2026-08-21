# ==============================================================================
# STRATEGY_SWING.PY - Multi-Timeframe MACD Trend & Momentum Expansion Engine
# ==============================================================================

from collections import namedtuple

class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.50  # 50% Win Rate on higher timeframes (2h/4h)
_RR_RATIO = 2.0             # 4.0 ATR TP / 2.0 ATR SL (1:2 RR)

def get_signal(df):
    """
    Swing Strategy:
    1. Trend Reversal / Zero-Line Cross: Price > 200 EMA + MACD crosses above signal line.
    2. Trend Continuation Momentum: Price > 50 EMA > 200 EMA + MACD Histogram expanding upward + Volume surge.
    Returns: SignalResult
    """
    if df is None or len(df) < 50:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # Required columns check
    if 'macd' not in df.columns or 'ema_200' not in df.columns:
        try:
            import features
            df = features.add_features(df)
        except Exception:
            return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if 'macd' not in df.columns or 'ema_200' not in df.columns:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close     = float(last["close"])
    open_p    = float(last["open"])
    ema_200   = float(last.get("ema_200", close))
    ema_50    = float(last.get("ema_50", close))
    atr       = float(last.get("atr", last.get("atr_14", close * 0.01)))
    macd_now  = float(last["macd"])
    sig_now   = float(last["macd_signal"])
    macd_prev = float(prev["macd"])
    sig_prev  = float(prev["macd_signal"])
    macd_hist = float(last.get("macd_hist", macd_now - sig_now))
    prev_hist = float(prev.get("macd_hist", macd_prev - sig_prev))
    rel_vol   = float(last.get("rel_volume", 1.0))

    # MACD crossover detection
    crossed_up   = (macd_prev <= sig_prev) and (macd_now > sig_now)
    crossed_down = (macd_prev >= sig_prev) and (macd_now < sig_now)

    # 1. Fresh MACD Bullish Crossover in Uptrend
    if close > ema_200 and crossed_up and (rel_vol >= 0.9 or close > open_p):
        sl = close - (atr * 2.0)
        tp = close + (atr * 4.0)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # 2. Strong Trend Momentum Expansion (MACD above signal line and histogram accelerating)
    strong_uptrend = (close > ema_50 > ema_200)
    hist_expanding = (macd_hist > prev_hist) and (macd_hist > 0)
    if strong_uptrend and hist_expanding and rel_vol > 1.1 and close > open_p:
        sl = close - (atr * 2.0)
        tp = close + (atr * 4.0)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # Bearish Signals
    if close < ema_200 and crossed_down:
        sl = close + (atr * 2.0)
        tp = close - (atr * 4.0)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
