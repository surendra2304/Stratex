import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from execution import get_exchange_client
from config import API_KEY, SECRET_KEY, TOP_COINS_LIMIT
from data import get_top_gainers, get_candles, add_indicators
from datetime import datetime

print("=" * 50)
print("ALGORITHMIC TRADING BOT — LIVE STATUS CHECK")
print("=" * 50)

status_report = []

try:
    client = get_exchange_client()
    # Check balance
    account = client.get_account()
    balances = {b['asset']: float(b['free']) for b in account['balances'] if float(b['free']) > 0}
    print("\n[BALANCE] ✅ SUCCESS")
    for asset, amount in balances.items():
        print(f"  {asset}: {amount:.4f}")
    status_report.append("[API] ✅ Connection Successful")
except Exception as e:
    print(f"\n[BALANCE] ❌ FAILED: {e}")
    status_report.append("[API] ❌ Connection Failed")
    sys.exit(1)

# Check live price for a dynamic top symbol
try:
    top_coins = get_top_gainers(1)
    if not top_coins:
        top_coins = ["BTCUSDT"]
    symbol = top_coins[0]
    
    ticker = client.get_symbol_ticker(symbol=symbol)
    price = float(ticker['price'])
    print(f"\n[MARKET] ✅ SUCCESS | {symbol} Price: ${price:,.2f}")
except Exception as e:
    print(f"\n[MARKET] ❌ FAILED: {e}")
    symbol = "BTCUSDT"

# Fetch candles and compute indicators
try:
    print(f"\n[DATA] Fetching candles for {symbol}...")
    df = get_candles(symbol, limit=300)
    
    if df.empty:
        raise ValueError("DataFrame is empty")
        
    df = add_indicators(df)
    
    last = df.iloc[-1]
    print(f"\n[INDICATORS] ✅ SUCCESS (Latest Candle)")
    print(f"  RSI     : {last['rsi']:.2f}")
    print(f"  EMA 200 : {last['ema_200']:.2f}")
    print(f"  MACD    : {last['macd']:.4f}")
    print(f"  BB Upper: {last['bb_upper']:.2f}")
    print(f"  BB Lower: {last['bb_lower']:.2f}")
    print(f"  ATR     : {last['atr']:.2f}")
    
    status_report.append("[DATA] ✅ Data & Indicators Pipeline Functional")
except Exception as e:
    print(f"\n[INDICATORS] ❌ FAILED: {e}")
    status_report.append("[DATA] ❌ Data Pipeline Failed")

print("\n[STATUS REPORT SUMMARY]")
for r in status_report:
    print(f"  {r}")
print("=" * 50)
