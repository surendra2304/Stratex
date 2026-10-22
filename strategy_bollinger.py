# strategy_bollinger.py - Bollinger Band mean reversion

from collections import namedtuple


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.48  # Approx win rate based on OOS testing
_RR_RATIO = 2.0

def get_signal(df):
    """Bollinger Band mean reversion strategy.
    - BUY when price closes below lower band and RSI is oversold (< 35).
    - SELL when price closes above upper band and RSI is overbought (> 65).
    Returns a SignalResult.
    """
    if df is None or len(df) < 20:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    if "bb_lower" not in df.columns or ("rsi" not in df.columns and "rsi_14" not in df.columns):
        try:
            import features
            df = features.add_features(df)
        except Exception:
            return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    last = df.iloc[-1]
    if "bb_lower" not in last or "bb_upper" not in last:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    close = float(last["close"])
    lower = float(last["bb_lower"])
    upper = float(last["bb_upper"])
    rsi = float(last.get("rsi", last.get("rsi_14", 50.0)))
    atr = float(last.get("atr", last.get("atr_14", close * 0.01)))
    if close < lower and rsi < 35:
        sl = close - atr * 1.5
        tp = close + atr * 3.0
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    if close > upper and rsi > 65:
        sl = close + atr * 1.5
        tp = close - atr * 3.0
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
