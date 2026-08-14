import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from binance.client import Client
import pandas as pd
import ta

API_KEY = "REDACTED_API_KEY"
SECRET_KEY = "REDACTED_SECRET_KEY"

client = Client(API_KEY, SECRET_KEY, testnet=True)

print("=" * 50)
print("ANTI GRAVITY BOT - LIVE STATUS CHECK")
print("=" * 50)

# Check balance
account = client.get_account()
balances = {b['asset']: float(b['free']) for b in account['balances'] if float(b['free']) > 0}
print("\n[BALANCE]")
for asset, amount in balances.items():
    print(f"  {asset}: {amount:.4f}")

# Check live price
ticker = client.get_symbol_ticker(symbol="BTCUSDT")
price = float(ticker['price'])
print(f"\n[MARKET] BTCUSDT Price: ${price:,.2f}")

# Fetch candles and compute indicators
print("\n[DATA] Fetching 300 candles...")
raw = client.get_klines(symbol="BTCUSDT", interval="1m", limit=300)
df = pd.DataFrame(raw, columns=[
    "timestamp","open","high","low","close","volume",
    "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
])
df = df[["timestamp","open","high","low","close","volume"]].copy()
df[["open","high","low","close","volume"]] = df[["open","high","low","close","volume"]].astype(float)

# Indicators
df["rsi"]     = ta.momentum.rsi(df["close"], window=14)
df["ema_200"] = ta.trend.ema_indicator(df["close"], window=200)
macd          = ta.trend.MACD(df["close"])
df["macd"]    = macd.macd()
df["macd_sig"]= macd.macd_signal()
bb            = ta.volatility.BollingerBands(df["close"])
df["bb_upper"]= bb.bollinger_hband()
df["bb_lower"]= bb.bollinger_lband()
df["atr"]     = ta.volatility.average_true_range(df["high"], df["low"], df["close"])
df.dropna(inplace=True)

last = df.iloc[-1]
print(f"\n[INDICATORS] (Latest Candle)")
print(f"  RSI     : {last['rsi']:.2f}")
print(f"  EMA 200 : {last['ema_200']:.2f}")
print(f"  MACD    : {last['macd']:.4f}")
print(f"  BB Upper: {last['bb_upper']:.2f}")
print(f"  BB Lower: {last['bb_lower']:.2f}")
print(f"  ATR     : {last['atr']:.2f}")

# Strategy checks
above_ema = last['close'] > last['ema_200']
rsi_val = last['rsi']
print(f"\n[STRATEGY CHECK]")
print(f"  Price above 200 EMA : {above_ema}")
print(f"  RSI ({rsi_val:.1f})          : {'OVERSOLD - BUY ZONE' if rsi_val < 35 else 'OVERBOUGHT - SELL ZONE' if rsi_val > 65 else 'NEUTRAL'}")
print(f"  Price vs BB Lower   : {'BELOW - SCALPER BUY SIGNAL!' if last['close'] <= last['bb_lower'] else 'Above lower band'}")
print(f"  Price vs BB Upper   : {'ABOVE - SCALPER SELL SIGNAL!' if last['close'] >= last['bb_upper'] else 'Below upper band'}")
print("\n[STATUS] Bot framework verified and operational!")
print("=" * 50)
