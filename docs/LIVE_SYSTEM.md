# Live System Architecture

This document describes the production deployment and the core trading pipeline of the algorithmic trading bot.

## Trading Pipeline Flow

The bot operates on a strict event-driven pipeline, primarily triggering on completed timeframes.

1. **Market Data**: Ingested via Binance REST API and WebSocket streams.
2. **Candle Close**: Engine evaluates the market exactly on candle close events to prevent intra-candle repainting.
3. **Scanner**: `market_scanner.py` iterates over the discovered symbol universe.
4. **Strategy**: The designated strategy (e.g., ML Predictor, ADX+EMA) evaluates the technical features.
5. **Signal**: A raw signal (LONG/SHORT) with a calculated confidence and expected edge is generated.
6. **Profitability Gate**: Ensures the expected net return overcomes microstructural friction (taker fees + bid/ask spread).
7. **Risk Gate**: Enforces account-level limits (max open positions, daily loss limit, max portfolio exposure).
8. **Execution**: The order is routed to the Binance Testnet via `execution.py`.
9. **Fill**: Exchange acknowledges and fills the order.
10. **Position**: Position is tracked. Real-time Stop Loss (SL) and Take Profit (TP) lifecycle management begins.
11. **PnL**: Realized and Unrealized Profit and Loss are calculated upon exit.
12. **Persistence**: Trade results and portfolio states are appended to atomic JSON ledgers.
13. **Dashboard**: Telemetry is served to the frontend UI via `dashboard.py`.

## Production Environment

- **Hosting Platform**: Render
- **Region**: Frankfurt, Germany. 
  *Note: Frankfurt is deliberately chosen to minimize network latency to Binance's European API endpoints.*
- **Containerization**: Docker (see `Dockerfile`).
- **Process Management**: `supervise_services.py` uses Python's `subprocess` to act as a lightweight supervisor. It runs both `dashboard.py` (the Flask web server) and `bot.py` (the trading engine) concurrently within the same Docker container.
- **Exchange**: Binance Spot Testnet.

## Live Endpoints

- **Frontend UI**: https://algorithmic-trading-bot-fra.onrender.com
- **Web Health**: https://algorithmic-trading-bot-fra.onrender.com/health
- **Engine Diagnostic**: https://algorithmic-trading-bot-fra.onrender.com/api/engine-health
- **System Status**: https://algorithmic-trading-bot-fra.onrender.com/api/status
- **Scanner Stats**: https://algorithmic-trading-bot-fra.onrender.com/api/scanner
- **Trade Ledger**: https://algorithmic-trading-bot-fra.onrender.com/api/trades
