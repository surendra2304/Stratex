"""
signal_quality.py — Real-data entry quality filters (v3 upgrade).

Applied AFTER a strategy produces a raw directional signal and BEFORE the
decay smoother / profitability gate. Every filter uses ONLY real market
data from the evaluated candle frame — no randomness, no synthetic data,
no invented probabilities.

Design goal: fewer, higher-conviction entries. Each filter removes a
specific historically net-negative entry class:

  1. RISK_REWARD_GEOMETRY  — TP/SL geometry below the minimum reward-to-risk.
  2. TREND_ALIGNMENT       — counter-trend entries against the EMA structure.
  3. CANDLE_CONFIRMATION   — signal candle that closed against the signal.
  4. VOLUME_CONFIRMATION   — entries on dead / illiquid volume.
  5. VOLATILITY_BAND       — dead-flat markets (ATR% floor) and chaos
                             whipsaw regimes (ATR% ceiling).
  6. RSI_CHASING           — buying blow-off tops / selling capitulation
                             bottoms.

Every rejection returns a stable machine-readable reason token that is
logged through log_opportunity(), so the dashboard funnel shows exactly
why each trade was skipped. Indicator-missing conditions fail OPEN (with
a detail note) rather than blocking all trading on partial data — the
same philosophy as the BTC regime gate.
"""

import pandas as pd

from logger import get_logger

logger = get_logger("signal_quality")


def _cfg(name, default):
    """Config lookup that never crashes on import-time ordering."""
    import config
    return getattr(config, name, default)


def _f(row, col, default=0.0):
    """Safe float extraction from a pandas row."""
    try:
        val = row.get(col, default)
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def compute_atr_from_df(df, period=14):
    """True ATR (Wilder) computed from a real candle frame. None if insufficient."""
    try:
        if df is None or len(df) < period + 1:
            return None
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]
        val = float(atr)
        return val if val > 0 else None
    except Exception:
        return None


def evaluate_signal_quality(df, side, entry_price, sl_price, tp_price, strategy_name=""):
    """Run all enabled quality filters on the latest closed candle.

    Returns (passed: bool, reason: str, detail: dict) where reason is a
    stable token: "QUALITY_OK" or "QUALITY_<ISSUE>".
    """
    detail = {"strategy": strategy_name, "side": side}

    if df is None or df.empty or len(df) < 21:
        detail["note"] = "insufficient_history"
        return True, "QUALITY_OK", detail

    last = df.iloc[-1]
    close = _f(last, "close")
    open_ = _f(last, "open")
    high = _f(last, "high")
    low = _f(last, "low")
    volume = _f(last, "volume")
    ema20 = _f(last, "ema_20")
    ema50 = _f(last, "ema_50")
    ema200 = _f(last, "ema_200")
    rsi = _f(last, "rsi", 50.0)
    atr = _f(last, "atr", 0.0)
    if atr <= 0:
        atr = _f(last, "atr_14", 0.0)
    if atr <= 0:
        atr = _f(last, "atr_adx_ema", 0.0)

    detail.update({
        "close": close, "open": open_, "rsi": round(rsi, 2),
        "atr": atr, "volume": volume,
        "ema_20": ema20, "ema_50": ema50, "ema_200": ema200,
    })

    if close <= 0 or entry_price is None or float(entry_price) <= 0:
        detail["note"] = "no_valid_price"
        return False, "QUALITY_NO_VALID_PRICE", detail
    entry_price = float(entry_price)

    # 1. Minimum reward-to-risk geometry (most profitable-trade filter)
    min_rr = float(_cfg("SQ_MIN_RISK_REWARD", 1.5))
    if sl_price and tp_price:
        try:
            sl_dist = abs(entry_price - float(sl_price))
            tp_dist = abs(float(tp_price) - entry_price)
            if sl_dist <= 0:
                return False, "QUALITY_ZERO_RISK_DISTANCE", detail
            rr = tp_dist / sl_dist
            detail["risk_reward"] = round(rr, 3)
            detail["min_risk_reward"] = min_rr
            if rr < min_rr:
                return False, "QUALITY_RR_TOO_LOW", detail
        except (TypeError, ValueError):
            detail["note"] = "invalid_sl_tp"

    # 2. Trend alignment (fail-open when EMA200 is missing)
    mode = str(_cfg("SQ_TREND_ALIGNMENT", "partial")).lower()
    if mode != "off" and ema200 > 0:
        if side == "BUY":
            aligned_full = (ema20 > ema50 > ema200) and close > ema200
            aligned_partial = close > ema200
        elif side == "SELL":
            aligned_full = (0 < ema20 < ema50 < ema200) and close < ema200
            aligned_partial = close < ema200
        else:
            aligned_full = aligned_partial = True
        ok = aligned_full if mode == "full" else aligned_partial
        detail["trend_alignment"] = mode
        if not ok:
            return False, "QUALITY_COUNTER_TREND", detail

    # 3. Candle confirmation — the signal candle itself must agree
    if bool(_cfg("SQ_CANDLE_CONFIRMATION", True)) and high > low > 0 and open_ > 0:
        body_ok = (close > open_) if side == "BUY" else (close < open_)
        mid = (high + low) / 2.0
        pos_ok = (close >= mid) if side == "BUY" else (close <= mid)
        detail["candle_confirmed"] = bool(body_ok and pos_ok)
        if not (body_ok and pos_ok):
            return False, "QUALITY_CANDLE_NOT_CONFIRMED", detail

    # 4. Volume confirmation vs real 20-bar average
    if bool(_cfg("SQ_VOLUME_CONFIRMATION", True)):
        vol_mult = float(_cfg("SQ_VOLUME_MULT", 0.9))
        try:
            vol_sma20 = float(pd.Series(df["volume"].astype(float).iloc[-21:-1]).mean())
        except Exception:
            vol_sma20 = 0.0
        if vol_sma20 > 0 and volume > 0:
            ratio = volume / vol_sma20
            detail["volume_ratio"] = round(ratio, 3)
            detail["volume_min_mult"] = vol_mult
            if ratio < vol_mult:
                return False, "QUALITY_LOW_VOLUME", detail

    # 5. ATR% volatility band (dead-market floor, chaos ceiling)
    if atr > 0:
        atr_pct = atr / close if close > 0 else 0.0
        detail["atr_pct"] = round(atr_pct, 5)
        lo = float(_cfg("SQ_ATR_PCT_MIN", 0.0015))
        hi = float(_cfg("SQ_ATR_PCT_MAX", 0.035))
        if atr_pct < lo:
            return False, "QUALITY_ATR_TOO_LOW", detail
        if atr_pct > hi:
            return False, "QUALITY_ATR_TOO_HIGH", detail

    # 6. RSI chasing guard
    if bool(_cfg("SQ_RSI_GUARD", True)) and rsi > 0:
        max_buy = float(_cfg("SQ_RSI_MAX_BUY", 78.0))
        min_sell = float(_cfg("SQ_RSI_MIN_SELL", 22.0))
        detail["rsi"] = round(rsi, 2)
        if side == "BUY" and rsi > max_buy:
            return False, "QUALITY_RSI_OVERBOUGHT", detail
        if side == "SELL" and rsi < min_sell:
            return False, "QUALITY_RSI_OVERSOLD", detail

    return True, "QUALITY_OK", detail

