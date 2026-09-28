"""Unregistered Donchian breakout candidate for isolated paper evaluation only.

This module is deliberately not imported by production/testnet strategy
registries or by the frozen ``paper_forward_runner`` experiment. Its output is
an unvalidated rule signal, not a win probability or recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CandidateSignal:
    side: str | None
    stop: float | None
    reason: str
    strategy_type: str = "OBSERVE_ONLY"
    confidence: None = None


ENTRY_LOOKBACK = 20
EXIT_LOOKBACK = 10
TREND_EMA = 200
ATR_PERIOD = 14
STOP_ATR_MULTIPLIER = 2.5


def add_candidate_features(candles: pd.DataFrame) -> pd.DataFrame:
    """Return backward-looking Donchian/EMA/ATR columns for OHLCV candles."""
    required = {"open", "high", "low", "close"}
    missing = required.difference(candles.columns)
    if missing:
        raise ValueError(f"OHLC candles missing required columns: {sorted(missing)}")
    if candles.empty:
        return candles.assign(
            candidate_ema=pd.Series(dtype="float64"),
            candidate_atr=pd.Series(dtype="float64"),
            candidate_entry_high=pd.Series(dtype="float64"),
            candidate_entry_low=pd.Series(dtype="float64"),
            candidate_exit_high=pd.Series(dtype="float64"),
            candidate_exit_low=pd.Series(dtype="float64"),
        )

    frame = candles.copy()
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not np.isfinite(frame[column].to_numpy(dtype=float)).all():
            raise ValueError(f"OHLC column {column} contains non-finite values")
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Every channel excludes the current candle. Signal generation at a close
    # therefore cannot use the high/low of a candle that has not closed yet.
    frame["candidate_ema"] = frame["close"].ewm(span=TREND_EMA, adjust=False).mean()
    frame["candidate_atr"] = true_range.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    frame["candidate_entry_high"] = frame["high"].shift(1).rolling(ENTRY_LOOKBACK).max()
    frame["candidate_entry_low"] = frame["low"].shift(1).rolling(ENTRY_LOOKBACK).min()
    frame["candidate_exit_high"] = frame["high"].shift(1).rolling(EXIT_LOOKBACK).max()
    frame["candidate_exit_low"] = frame["low"].shift(1).rolling(EXIT_LOOKBACK).min()
    return frame


def get_signal(candles: pd.DataFrame) -> CandidateSignal:
    """Create a completed-candle breakout signal; callers must fill next open.

    Buy breakouts require close above the prior 20-candle high and EMA200;
    sell breakouts require close below the prior 20-candle low and EMA200.
    The fixed initial stop is 2.5 ATR from the signal close. No take-profit or
    win-rate prior is supplied; exits are a 10-candle opposite-channel break
    or the stop, whichever occurs first in a conservative candle simulation.
    """
    if candles is None or len(candles) < TREND_EMA + 1:
        return CandidateSignal(None, None, "INSUFFICIENT_HISTORY")
    frame = candles
    if "candidate_entry_high" not in frame.columns or "candidate_ema" not in frame.columns:
        frame = add_candidate_features(frame)
    last = frame.iloc[-1]
    previous = frame.iloc[-2]
    needed = (
        "candidate_ema",
        "candidate_atr",
        "candidate_entry_high",
        "candidate_entry_low",
    )
    if any(pd.isna(last[name]) for name in needed):
        return CandidateSignal(None, None, "INDICATORS_NOT_READY")

    close = float(last["close"])
    prior_close = float(previous["close"])
    ema = float(last["candidate_ema"])
    atr = float(last["candidate_atr"])
    if atr <= 0 or not np.isfinite(atr):
        return CandidateSignal(None, None, "INVALID_ATR")

    high_channel = float(last["candidate_entry_high"])
    low_channel = float(last["candidate_entry_low"])
    previous_high_channel = float(previous.get("candidate_entry_high", np.nan))
    previous_low_channel = float(previous.get("candidate_entry_low", np.nan))

    crossed_up = prior_close <= previous_high_channel and close > high_channel
    crossed_down = prior_close >= previous_low_channel and close < low_channel
    if crossed_up and close > ema:
        return CandidateSignal("BUY", close - STOP_ATR_MULTIPLIER * atr, "DONCHIAN20_UP_BREAK")
    if crossed_down and close < ema:
        return CandidateSignal("SELL", close + STOP_ATR_MULTIPLIER * atr, "DONCHIAN20_DOWN_BREAK")
    return CandidateSignal(None, None, "NO_QUALIFIED_BREAKOUT")


def get_channel_exit(candles: pd.DataFrame, side: str) -> bool:
    """Whether the last completed close breached the opposite 10-candle band."""
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    frame = candles
    if "candidate_exit_high" not in frame.columns or "candidate_exit_low" not in frame.columns:
        frame = add_candidate_features(frame)
    if len(frame) < 2:
        return False
    last = frame.iloc[-1]
    threshold = last["candidate_exit_low"] if side == "BUY" else last["candidate_exit_high"]
    if pd.isna(threshold):
        return False
    close = float(last["close"])
    return close < float(threshold) if side == "BUY" else close > float(threshold)
