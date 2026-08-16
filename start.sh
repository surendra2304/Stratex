#!/bin/bash
echo "Starting Binance Testnet Trading Engine..."
python bot.py &

echo "Starting Real-time Dashboard..."
python dashboard.py
