# Algorithmic Trading Bot

A professional, multi-strategy automated trading system built in Python. Connects to the **Binance Testnet** for live paper trading with zero financial risk — a complete framework for building, testing, and deploying quantitative trading strategies.

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![Binance](https://img.shields.io/badge/Exchange-Binance-yellow?logo=binance)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Overview

This project implements a complete algorithmic trading pipeline — from live market data ingestion and technical indicator computation, to signal generation, automated order execution, and trade logging. Four distinct strategies are included, ranging from classical technical analysis to machine learning.

---

## Strategies

| Strategy | Logic | Best For |
|---|---|---|
| **Scalper** | RSI + Bollinger Band mean reversion | High-frequency short bursts |
| **Swing Trader** | MACD crossover filtered by 200 EMA | Medium-term trend following |
| **ML Predictor** | Random Forest trained on live candle data | AI-driven directional bias |
| **Multi-Strategy** | Runs all three simultaneously | Maximum signal coverage |

---

## Project Structure

```
.
├── bot.py                  # Main entry point — start here
├── config_template.py      # Copy to config.py and add your API keys
├── data.py                 # Live candle fetching + indicator engine
├── execution.py            # Order placement and account management
├── logger.py               # Structured trade logging to CSV
├── strategy_scalper.py     # High-frequency scalping strategy
├── strategy_swing.py       # Trend-following swing strategy
├── strategy_ml.py          # Machine learning price direction model
├── status_check.py         # Live market snapshot and health check
├── requirements.txt        # Dependencies
└── .gitignore              # Keeps secrets out of version control
```

---

## Getting Started

### Prerequisites
- Python 3.11 or higher
- A free [Binance Testnet](https://testnet.binance.vision) account

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/surendra2304/python-trading-bot.git
cd python-trading-bot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up your API keys
cp config_template.py config.py
# Edit config.py and paste your Binance Testnet keys
```

### Running the Bot

```bash
# Start the bot
python bot.py

# Check live market status and indicators
python status_check.py
```

---

## Configuration

Open `config.py` to switch strategies or adjust risk parameters:

```python
# Choose your active strategy
ACTIVE_STRATEGY = "scalper"   # High-frequency RSI + Bollinger Band scalping
ACTIVE_STRATEGY = "swing"     # MACD + 200 EMA trend following
ACTIVE_STRATEGY = "ml"        # Random Forest ML prediction
ACTIVE_STRATEGY = "multi"     # All three running simultaneously (default)

# Risk parameters
TRADE_QTY = 0.001             # Trade size in BTC
MAX_OPEN_TRADES = 1           # Max concurrent open positions
```

---

## Tech Stack

| Library | Purpose |
|---|---|
| `python-binance` | Binance REST API and WebSocket integration |
| `pandas` | Time-series data manipulation |
| `ta` | Technical indicator library (RSI, MACD, BB, ATR) |
| `scikit-learn` | Random Forest machine learning model |
| `numpy` | Numerical computing |

---

## Security

> **Important:** Never commit `config.py` to version control. It is listed in `.gitignore` by default. Use `config_template.py` as the shareable reference — it contains no real credentials.

---

## Disclaimer

This project is built for **educational and research purposes** using the Binance Testnet (simulated environment — no real funds involved). Past performance of any strategy does not guarantee future results. Always conduct thorough backtesting before deploying to a live account.

---

*Built by [Surendra](https://github.com/surendra2304)*
