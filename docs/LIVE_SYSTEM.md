# Live System Architecture

This document describes the production deployment and the core trading pipeline of STRATEX within the FRIDAY Universe.

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
13. **Dashboard**: Telemetry is served to the frontend UI via multi-threaded Gunicorn WSGI running `dashboard:app`.

## Production Environment

- **Hosting Platform**: Oracle Cloud Always Free (2 OCPUs, 12 GB RAM Ampere A1 ARM64 / AMD x86_64 host)
- **Container Network**: `friday_mesh` Docker bridge network with automated HTTPS via Caddy reverse proxy
- **Internal Port**: 8000
- **Host Port**: 8008
- **Process Management**: `scripts/supervise_services.py` acts as production process supervisor. It supervises both `dashboard:app` (via multi-threaded Gunicorn WSGI) and `bot.py` (singleton trading engine daemon) concurrently within the container.
- **Exchange**: Binance Spot Testnet / Futures Testnet.

## Live Endpoints

- **Public Gateway Route**: https://friday-universe.duckdns.org/stratex/
- **Web Health**: https://friday-universe.duckdns.org/stratex/health
- **Engine Diagnostic**: https://friday-universe.duckdns.org/stratex/api/engine-health
- **System Status**: https://friday-universe.duckdns.org/stratex/api/status
- **Scanner Stats**: https://friday-universe.duckdns.org/stratex/api/scanner
- **Trade Ledger**: https://friday-universe.duckdns.org/stratex/api/trades
- **SSE Stream**: https://friday-universe.duckdns.org/stratex/api/stream
- **Internal Mesh URL**: http://stratex:8000
