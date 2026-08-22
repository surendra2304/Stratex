import pandas as pd

from data_client import MarketDataClient
from logger import get_logger

logger = get_logger("data")


def get_top_gainers(limit=5):
    """Fetches the top gaining USDT pairs from Binance Testnet in the last 24h."""
    client = MarketDataClient()
    if not client.is_available():
        logger.warning("[DATA] Exchange client disabled. DATA_UNAVAILABLE.")
        return []

    try:
        tickers = client.get_ticker()
        usdt_pairs = [t for t in tickers if t['symbol'].endswith('USDT')]
        sorted_pairs = sorted(usdt_pairs, key=lambda x: float(x['priceChangePercent']), reverse=True)
        top_symbols = [t['symbol'] for t in sorted_pairs[:limit]]
        logger.info(f"[DATA] Hot Coins Detected: {', '.join(top_symbols)}")
        return top_symbols
    except Exception as e:
        logger.error(f"[DATA] Error fetching top gainers: {e}")
        return []  # DATA_UNAVAILABLE

def get_candles(symbol, interval="15m", limit=300):
    """Fetches the latest candles from Binance Testnet and returns a DataFrame."""
    client = MarketDataClient()
    if not client.is_available():
        logger.warning(f"[DATA] Exchange client disabled. Cannot fetch live candles for {symbol}. DATA_UNAVAILABLE.")
        return pd.DataFrame()

    try:
        # Binance TESTNET caps klines at ~101 per request; paginate backwards
        # until the requested depth is reached or history is exhausted.
        raw = []
        end_id = None
        for _ in range(8):
            params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
            if end_id is not None:
                params["endTime"] = end_id
            page = client.get_klines(**params)
            if not page:
                break
            raw = page + raw if end_id is not None else page
            if len(page) < 2 or len(raw) >= limit:
                break
            end_id = page[0][0] - 1
        if not raw or len(raw) == 0:
            logger.warning(f"[DATA] Empty candle data returned for {symbol}.")
            return pd.DataFrame()

        df = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ])
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        df = df[["timestamp", "open", "high", "low", "close", "volume", "taker_buy_base", "close_time"]].copy()
        
        # Safe numeric conversion
        numeric_cols = ["open", "high", "low", "close", "volume", "taker_buy_base"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
        df.dropna(subset=numeric_cols, inplace=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        
        # Calculate Volume Delta (Buy Volume - Sell Volume)
        df["buy_vol"] = df["taker_buy_base"]
        df["sell_vol"] = df["volume"] - df["buy_vol"]
        df["vol_delta"] = df["buy_vol"] - df["sell_vol"]
        
        return df
    except Exception as e:
        logger.error(f"[DATA] Error fetching candles for {symbol}: {e}")
        return pd.DataFrame()

def add_indicators(df):
    """Adds all technical indicators needed by all strategies to the DataFrame."""
    if df is None or df.empty or len(df) < 20:
        return df
        
    try:
        from features import add_features
        df = add_features(df)
        
        # Keep old column names for backward compatibility with strategies
        df['rsi'] = df['rsi_14']
        df['atr'] = df['atr_14']
        df['bb_mid'] = df['bb_middle']

        df.dropna(inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df
    except Exception as e:
        logger.error(f"[DATA] Error adding indicators: {e}")
        return pd.DataFrame()

def get_current_price(symbol):
    """Gets the latest ticker price."""
    client = MarketDataClient()
    if not client.is_available():
        return None
        
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    except Exception as e:
        logger.error(f"[DATA] Error getting price: {e}")
        return None
