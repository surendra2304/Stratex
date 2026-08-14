#!/usr/bin/env python3
"""
status_check.py - Read-only system status diagnostic.
Uses MarketDataClient for market data and AccountClient for account checks.
No hardcoded credentials. Reads from config (loaded from .env).
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from account_client import AccountClient
from data_client import MarketDataClient
from data import get_top_gainers, get_candles, add_indicators
from config import TOP_COINS_LIMIT
from datetime import datetime

print("=" * 50)
print("ALGORITHMIC TRADING BOT — LIVE STATUS CHECK")
print(f"Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
print("=" * 50)

status_report = []

# 1. Account balance check
try:
    acc = AccountClient()
    if acc.is_available():
        balances = acc.get_balances()
        print("\n[BALANCE] ✅ SUCCESS")
        for asset, amount in balances.items():
            print(f"  {asset}: {amount:.4f}")
        status_report.append("[API] ✅ Connection Successful")
    else:
        print("\n[BALANCE] ⚠️ PAPER mode — no live account connection")
        status_report.append("[API] ⚠️ PAPER mode")
except Exception as e:
    print(f"\n[BALANCE] ❌ FAILED: {e}")
    status_report.append("[API] ❌ Connection Failed")

# 2. Market data check
mdc = MarketDataClient()
symbol = "BTCUSDT"
try:
    top_coins = get_top_gainers(1)
    if top_coins:
        symbol = top_coins[0]

    if mdc.is_available():
        ticker = mdc.get_symbol_ticker(symbol=symbol)
        price = float(ticker['price'])
        print(f"\n[MARKET] ✅ SUCCESS | {symbol} Price: ${price:,.2f}")
        status_report.append("[MARKET] ✅ Market Data OK")
    else:
        print(f"\n[MARKET] ⚠️ DATA_UNAVAILABLE (PAPER mode)")
        status_report.append("[MARKET] ⚠️ DATA_UNAVAILABLE")
except Exception as e:
    print(f"\n[MARKET] ❌ FAILED: {e}")
    status_report.append("[MARKET] ❌ Market Data Failed")

# 3. Candles and indicators check
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
