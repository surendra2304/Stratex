"""
strategy_factory_winners.py — Top 5 Strategy Factory Winners (Futures)

Generated and selected by research/strategy_factory/mass_backtester.py across 204 variations
under realistic 8 bps Maker/Taker Futures friction.

WINNERS INCLUDED:
  1. factory_winner_1 (factory_macd_bb_confluence_5m_182, 5m): Net PF 1.481, Win Rate 43.1%, SL 1.5x ATR, TP 3.0x ATR
  2. factory_winner_2 (factory_macd_bb_confluence_5m_181, 5m): Net PF 1.449, Win Rate 41.3%, SL 1.0x ATR, TP 2.0x ATR
  3. factory_winner_3 (factory_macd_bb_confluence_5m_183, 5m): Net PF 1.433, Win Rate 33.4%, SL 0.5x ATR, TP 1.5x ATR
  4. factory_winner_4 (factory_macd_bb_confluence_5m_184, 5m): Net PF 1.390, Win Rate 41.0%, SL 2.0x ATR, TP 4.0x ATR
  5. factory_winner_5 (factory_macd_bb_confluence_15m_187, 15m): Net PF 1.361, Win Rate 30.5%, SL 0.5x ATR, TP 1.5x ATR
"""

from collections import namedtuple
import pandas as pd


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior


_STRATEGY_TYPE = "RULE_BASED"


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(period) with Wilder's EWM smoothing."""
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low']  - df['close'].shift(1))
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Computes MACD(12, 26, 9), Bollinger Bands(20, 2), and ATR(14)."""
    df = df.copy()
    macd_line, sig_line = compute_macd(df['close'], 12, 26, 9)
    df['macd'] = macd_line
    df['macd_signal'] = sig_line
    
    df['bb_mid'] = df['close'].rolling(window=20).mean()
    df['bb_std'] = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
    
    df['atr'] = compute_atr(df, 14)
    return df


def _evaluate_macd_bb_confluence(df: pd.DataFrame, sl_atr: float, tp_atr: float, rr_ratio: float, win_rate_prior: float) -> SignalResult:
    _NO_SIGNAL = SignalResult(None, None, None, _STRATEGY_TYPE, win_rate_prior, rr_ratio)

    if df is None or len(df) < 30:
        return _NO_SIGNAL

    if 'macd' not in df.columns or 'bb_mid' not in df.columns or 'atr' not in df.columns:
        df = add_features(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    if pd.isna(last['macd']) or pd.isna(last['macd_signal']) or pd.isna(last['bb_mid']) or pd.isna(last['atr']):
        return _NO_SIGNAL

    atr_val = last['atr']
    if atr_val <= 0:
        return _NO_SIGNAL

    close_p = last['close']
    
    # Long: MACD crosses above signal line AND price is below/at middle Bollinger Band (buying dip in trend)
    cross_above = (last['macd'] > last['macd_signal']) and (prev['macd'] <= prev['macd_signal'])
    long_confluence = cross_above and (close_p < last['bb_mid'])

    # Short: MACD crosses below signal line AND price is above/at middle Bollinger Band (selling rally in trend)
    cross_below = (last['macd'] < last['macd_signal']) and (prev['macd'] >= prev['macd_signal'])
    short_confluence = cross_below and (close_p > last['bb_mid'])

    if long_confluence:
        sl = close_p - (sl_atr * atr_val)
        tp = close_p + (tp_atr * atr_val)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, win_rate_prior, rr_ratio)

    if short_confluence:
        sl = close_p + (sl_atr * atr_val)
        tp = close_p - (tp_atr * atr_val)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, win_rate_prior, rr_ratio)

    return _NO_SIGNAL


# Default module-level get_signal evaluates Winner #1
def get_signal(df: pd.DataFrame, **kwargs) -> SignalResult:
    return get_signal_winner_1(df, **kwargs)


def get_signal_winner_1(df: pd.DataFrame, **kwargs) -> SignalResult:
    """Winner #1: 5m MACD+BB Confluence (SL 1.5x ATR, TP 3.0x ATR, RR 2.0, Net PF 1.481)"""
    return _evaluate_macd_bb_confluence(df, sl_atr=1.5, tp_atr=3.0, rr_ratio=2.0, win_rate_prior=0.431)


def get_signal_winner_2(df: pd.DataFrame, **kwargs) -> SignalResult:
    """Winner #2: 5m MACD+BB Confluence (SL 1.0x ATR, TP 2.0x ATR, RR 2.0, Net PF 1.449)"""
    return _evaluate_macd_bb_confluence(df, sl_atr=1.0, tp_atr=2.0, rr_ratio=2.0, win_rate_prior=0.413)


def get_signal_winner_3(df: pd.DataFrame, **kwargs) -> SignalResult:
    """Winner #3: 5m MACD+BB Confluence (SL 0.5x ATR, TP 1.5x ATR, RR 3.0, Net PF 1.433)"""
    return _evaluate_macd_bb_confluence(df, sl_atr=0.5, tp_atr=1.5, rr_ratio=3.0, win_rate_prior=0.334)


def get_signal_winner_4(df: pd.DataFrame, **kwargs) -> SignalResult:
    """Winner #4: 5m MACD+BB Confluence (SL 2.0x ATR, TP 4.0x ATR, RR 2.0, Net PF 1.390)"""
    return _evaluate_macd_bb_confluence(df, sl_atr=2.0, tp_atr=4.0, rr_ratio=2.0, win_rate_prior=0.410)


def get_signal_winner_5(df: pd.DataFrame, **kwargs) -> SignalResult:
    """Winner #5: 15m MACD+BB Confluence (SL 0.5x ATR, TP 1.5x ATR, RR 3.0, Net PF 1.361)"""
    return _evaluate_macd_bb_confluence(df, sl_atr=0.5, tp_atr=1.5, rr_ratio=3.0, win_rate_prior=0.305)
