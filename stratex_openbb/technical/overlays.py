"""
stratex_openbb/technical/overlays.py — Technical Overlays, Channels, and Pivot Points.
Implements OpenBB-grade technical overlays:
- Donchian Channels (Breakout detection)
- Keltner Channels (Volatility envelope)
- Standard, Fibonacci, and Camarilla Pivot Points
"""

from typing import Any, Dict
import numpy as np
import pandas as pd


def compute_donchian_channels(
    df: pd.DataFrame,
    window: int = 20
) -> pd.DataFrame:
    """
    Computes Donchian Channels:
    - Upper Band: Rolling maximum of High
    - Lower Band: Rolling minimum of Low
    - Middle Band: (Upper + Lower) / 2
    """
    res = df.copy()
    res["donchian_upper"] = res["high"].rolling(window=window).max()
    res["donchian_lower"] = res["low"].rolling(window=window).min()
    res["donchian_middle"] = (res["donchian_upper"] + res["donchian_lower"]) / 2.0
    return res


def compute_keltner_channels(
    df: pd.DataFrame,
    window: int = 20,
    atr_multiplier: float = 2.0
) -> pd.DataFrame:
    """
    Computes Keltner Channels:
    - Centerline: Exponential Moving Average of Close
    - ATR: Average True Range over window
    - Upper Channel: EMA + (atr_multiplier * ATR)
    - Lower Channel: EMA - (atr_multiplier * ATR)
    """
    res = df.copy()
    c = res["close"]
    h = res["high"]
    l = res["low"]

    # True range
    prev_c = c.shift(1)
    tr1 = h - l
    tr2 = (h - prev_c).abs()
    tr3 = (l - prev_c).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=window, adjust=False).mean()

    ema = c.ewm(span=window, adjust=False).mean()

    res["keltner_middle"] = ema
    res["keltner_upper"] = ema + (atr_multiplier * atr)
    res["keltner_lower"] = ema - (atr_multiplier * atr)
    res["keltner_width"] = (res["keltner_upper"] - res["keltner_lower"]) / ema
    return res


def compute_pivot_points(
    high: float,
    low: float,
    close: float
) -> Dict[str, Dict[str, float]]:
    """
    Computes multi-model Pivot Points (Standard, Fibonacci, Camarilla) from prior period H, L, C.
    """
    hl_range = high - low
    pp = (high + low + close) / 3.0

    # 1. Standard Classical
    std_r1 = (2.0 * pp) - low
    std_s1 = (2.0 * pp) - high
    std_r2 = pp + hl_range
    std_s2 = pp - hl_range
    std_r3 = high + 2.0 * (pp - low)
    std_s3 = low - 2.0 * (high - pp)

    # 2. Fibonacci
    fib_r1 = pp + (0.382 * hl_range)
    fib_s1 = pp - (0.382 * hl_range)
    fib_r2 = pp + (0.618 * hl_range)
    fib_s2 = pp - (0.618 * hl_range)
    fib_r3 = pp + (1.000 * hl_range)
    fib_s3 = pp - (1.000 * hl_range)

    # 3. Camarilla (Reversal / Mean-Reversion pivots)
    cam_r4 = close + (hl_range * 1.1 / 2.0)
    cam_r3 = close + (hl_range * 1.1 / 4.0)
    cam_s3 = close - (hl_range * 1.1 / 4.0)
    cam_s4 = close - (hl_range * 1.1 / 2.0)

    return {
        "standard": {
            "pivot": round(pp, 4),
            "r1": round(std_r1, 4),
            "s1": round(std_s1, 4),
            "r2": round(std_r2, 4),
            "s2": round(std_s2, 4),
            "r3": round(std_r3, 4),
            "s3": round(std_s3, 4),
        },
        "fibonacci": {
            "pivot": round(pp, 4),
            "r1": round(fib_r1, 4),
            "s1": round(fib_s1, 4),
            "r2": round(fib_r2, 4),
            "s2": round(fib_s2, 4),
            "r3": round(fib_r3, 4),
            "s3": round(fib_s3, 4),
        },
        "camarilla": {
            "pivot": round(pp, 4),
            "r4": round(cam_r4, 4),
            "r3": round(cam_r3, 4),
            "s3": round(cam_s3, 4),
            "s4": round(cam_s4, 4),
        }
    }
