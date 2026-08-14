# ==============================================================================
# DATA.PY - Market Data Module: fetches OHLCV candles from Binance Testnet
# ==============================================================================
import pandas as pd
import ta
from binance.client import Client
from config import API_KEY, SECRET_KEY, BASE_URL, SYMBOL, TIMEFRAME

client = Client(API_KEY, SECRET_KEY, testnet=True)

def get_candles(symbol=SYMBOL, interval=TIMEFRAME, limit=300):
    """Fetches the latest candles from Binance Testnet and returns a DataFrame."""
    try:
        raw = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ])
        df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        df[["open","high","low","close","volume"]] = df[["open","high","low","close","volume"]].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception as e:
        print(f"[DATA] Error fetching candles: {e}")
        return None

def add_indicators(df):
    """Adds all technical indicators needed by all strategies to the DataFrame."""
    # --- Trend ---
    df["ema_200"] = ta.trend.ema_indicator(df["close"], window=200)
    df["ema_50"]  = ta.trend.ema_indicator(df["close"], window=50)
    df["ema_20"]  = ta.trend.ema_indicator(df["close"], window=20)

    # --- Momentum ---
    df["rsi"] = ta.momentum.rsi(df["close"], window=14)
    macd = ta.trend.MACD(df["close"], window_fast=12, window_slow=26, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    # --- Volatility ---
    bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["atr"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)

    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_current_price(symbol=SYMBOL):
    """Gets the latest ticker price."""
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    except Exception as e:
        print(f"[DATA] Error getting price: {e}")
        return None
