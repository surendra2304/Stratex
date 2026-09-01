# ==============================================================================
# STRATEGY_AGGRESSOR.PY - Adaptive Volume Delta & Order Flow Surge Engine
# ==============================================================================

from collections import namedtuple


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.50  # 50% Win rate with order flow momentum confirmation
_RR_RATIO = 2.5             # 3.75 ATR TP / 1.5 ATR SL

def get_signal(df):
    """
    The Aggressor Strategy:
    - Analyzes Order Flow / Volume Delta Surges via adaptive Z-Scores
    - BUY when institutional buying pressure (Z-Score > 1.3 or Delta > 30% Vol) + Momentum Up (RSI > 50) + Green Candle
    - SELL when institutional selling pressure (Z-Score < -1.3 or Delta < -30% Vol) + Momentum Down (RSI < 50) + Red Candle
    Returns: SignalResult
    """
    if df is None or len(df) < 20:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # Required columns check
    if 'vol_delta' not in df.columns or 'rsi_14' not in df.columns:
        try:
            import features
            df = features.add_features(df)
        except Exception:
            return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if 'vol_delta' not in df.columns:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    last = df.iloc[-1]

    close     = float(last["close"])
    open_p    = float(last["open"])
    atr       = float(last.get("atr", last.get("atr_14", close * 0.01)))
    rsi       = float(last.get("rsi", last.get("rsi_14", 50.0)))
    vol_delta = float(last["vol_delta"])
    
    # Calculate rolling volume delta stats for z-score
    recent_deltas = df["vol_delta"].tail(20)
    delta_mean = float(recent_deltas.mean())
    delta_std = float(recent_deltas.std()) + 1e-9
    z_score = (vol_delta - delta_mean) / delta_std
    
    avg_vol = float(df["volume"].tail(20).mean())
    
    # BUY: Positive Volume Surge (Z-Score > 1.3 or > 30% avg vol) + Bullish Price Action + RSI Momentum
    is_buy_surge = (z_score > 1.3 or vol_delta > avg_vol * 0.3)
    if is_buy_surge and rsi > 50 and close > open_p:
        sl = close - (atr * 1.5)
        tp = close + (atr * 3.75)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # SELL: Negative Volume Surge (Z-Score < -1.3 or < -30% avg vol) + Bearish Price Action + RSI Momentum
    is_sell_surge = (z_score < -1.3 or vol_delta < -avg_vol * 0.3)
    if is_sell_surge and rsi < 50 and close < open_p:
        sl = close + (atr * 1.5)
        tp = close - (atr * 3.75)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
