# ==============================================================================
# STRATEGY_SUPERTREND.PY - Maximum Profitability Trend Rider
# ==============================================================================

from collections import namedtuple

SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"]
)

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.40  # Trend followers typically have lower win rates (~40%)
_RR_RATIO = 2.0             # But higher reward-to-risk (letting winners run)

def get_signal(df):
    """
    Supertrend + 200 EMA Strategy:
    - BUY when Supertrend turns bullish AND price is above 200 EMA
    - SELL when Supertrend turns bearish AND price is below 200 EMA
    - Close signals are handled by the execution engine, but we will provide
      a dynamic SL that matches the supertrend band.
    Returns: SignalResult
    """
    if df is None or len(df) < 200:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Required columns from features.py: 'supertrend', 'ema_200', 'st_lower', 'st_upper'
    if 'supertrend' not in df.columns:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    st_now = last['supertrend']
    st_prev = prev['supertrend']
    close = last['close']
    ema = last['ema_200']

    # Trend changed to BULLISH
    if st_now == True and st_prev == False and close > ema:
        # Initial Stop Loss at the lower band
        sl = last['st_lower']
        # Trailing stop rider. Realistic expected target is ~3.0x to 5.0x the initial risk.
        risk = close - sl
        tp = close + (risk * 3.0)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # Trend changed to BEARISH
    if st_now == False and st_prev == True and close < ema:
        # Initial Stop Loss at the upper band
        sl = last['st_upper']
        risk = sl - close
        tp = close - (risk * 3.0)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

