# Anti Gravity Trading Bot Framework

A professional-grade, multi-strategy algorithmic trading bot built in Python, connected to the **Binance Testnet** for simulated trading with zero financial risk.

## Features

- **4 Live Trading Strategies** running simultaneously
- **Scalping Bot** — RSI + Bollinger Band mean reversion for high-frequency trading
- **Swing Trading Bot** — MACD + 200 EMA trend-following for larger moves
- **Machine Learning Bot** — Random Forest AI model that predicts price direction
- **Multi-Strategy Engine** — Runs all three strategies at once and takes the first valid signal
- **Automatic Trade Logging** — Every trade is saved to `trade_log.csv` for performance analysis
- **Live Market Data** — Fetches real-time OHLCV candles from Binance
- **Dynamic Indicators** — RSI, MACD, Bollinger Bands, ATR, EMA (all computed from live data)

## Project Structure

```
python_bot/
├── bot.py                  # Main entry point - run this to start the bot
├── config_template.py      # Copy to config.py and add your API keys
├── data.py                 # Market data fetching + indicator calculation
├── execution.py            # Order placement engine (Binance API)
├── logger.py               # Trade logging to CSV
├── strategy_scalper.py     # High-frequency scalping strategy
├── strategy_swing.py       # Swing / trend-following strategy
├── strategy_ml.py          # Machine Learning (Random Forest) strategy
├── status_check.py         # Live health check script
└── requirements.txt        # Python dependencies
```

## Setup & Installation

### 1. Prerequisites
- Python 3.11+
- A free [Binance Testnet](https://testnet.binance.vision) account

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure API Keys
```bash
# Copy the template
cp config_template.py config.py

# Edit config.py and add your Binance Testnet API keys
```

### 4. Run the Bot
```bash
python bot.py
```

## Switching Strategies

Open `config.py` and change the `ACTIVE_STRATEGY` value:

```python
ACTIVE_STRATEGY = "scalper"   # High-frequency RSI + BB scalping
ACTIVE_STRATEGY = "swing"     # MACD + EMA trend following
ACTIVE_STRATEGY = "ml"        # AI-powered Random Forest predictions
ACTIVE_STRATEGY = "multi"     # Runs all three simultaneously (default)
```

## Tech Stack

| Technology | Purpose |
|---|---|
| Python 3.11 | Core language |
| python-binance | Binance API integration |
| pandas | Data manipulation |
| ta (Technical Analysis) | Indicator calculations |
| scikit-learn | Machine Learning model |
| numpy | Numerical computing |

## Security Notice

> **Never commit your `config.py` file to GitHub.** It is listed in `.gitignore` for your protection. Always use `config_template.py` as the reference.

## Disclaimer

This project is built for educational purposes and uses the **Binance Testnet** (no real money). Always backtest thoroughly before using any strategy with real funds.

---
Built with Python by Surendra | [Binance Testnet](https://testnet.binance.vision)
