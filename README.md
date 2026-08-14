# Algorithmic Trading Bot Framework

A professional, multi-strategy automated trading system built in Python, connected to the **Binance Testnet** for live simulated trading with zero financial risk.

## Features

- **4 Live Trading Strategies** running simultaneously
- **Scalping Bot** — RSI + Bollinger Band mean reversion for high-frequency trading
- **Swing Trading Bot** — MACD + 200 EMA trend-following for capturing larger moves
- **Machine Learning Bot** — Random Forest model trained on live market data to predict price direction
- **Multi-Strategy Engine** — Runs all three strategies concurrently and executes the first valid signal
- **Automatic Trade Logging** — Every trade is recorded to `trade_log.csv` for performance review
- **Live Market Data** — Real-time OHLCV candle data streamed directly from Binance
- **Dynamic Indicators** — RSI, MACD, Bollinger Bands, ATR, EMA (computed fresh on every tick)

## Project Structure

```
python_bot/
├── bot.py                  # Main entry point — run this to start the bot
├── config_template.py      # Copy to config.py and fill in your API keys
├── data.py                 # Market data fetching + technical indicator engine
├── execution.py            # Order placement and account management
├── logger.py               # Trade logging to CSV
├── strategy_scalper.py     # High-frequency scalping strategy
├── strategy_swing.py       # Swing / trend-following strategy
├── strategy_ml.py          # Machine Learning (Random Forest) strategy
├── status_check.py         # Live health check and market snapshot
└── requirements.txt        # Python dependencies
```

## Setup & Installation

### Prerequisites
- Python 3.11+
- A free [Binance Testnet](https://testnet.binance.vision) account

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure API Keys
```bash
# Copy the template
cp config_template.py config.py

# Edit config.py and add your Binance Testnet API keys
```

### 3. Run the Bot
```bash
python bot.py
```

### 4. Check Live Status
```bash
python status_check.py
```

## Switching Strategies

Open `config.py` and change the `ACTIVE_STRATEGY` value:

```python
ACTIVE_STRATEGY = "scalper"   # High-frequency RSI + Bollinger Band scalping
ACTIVE_STRATEGY = "swing"     # MACD + 200 EMA trend following
ACTIVE_STRATEGY = "ml"        # AI-powered Random Forest price prediction
ACTIVE_STRATEGY = "multi"     # Runs all three simultaneously (default)
```

## Tech Stack

| Technology | Purpose |
|---|---|
| Python 3.11 | Core language |
| python-binance | Binance API integration |
| pandas | Data manipulation and analysis |
| ta | Technical indicator calculations |
| scikit-learn | Machine Learning model |
| numpy | Numerical computing |

## Security

> **Never commit your `config.py` to GitHub.** It is protected by `.gitignore`. Always use `config_template.py` as the public reference.

## Disclaimer

This project uses the **Binance Testnet** (simulated environment — no real funds involved). Always backtest thoroughly before deploying any strategy to a live account.

---
Built by [Surendra](https://github.com/surendra2304)
