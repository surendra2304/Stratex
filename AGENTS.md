# AI Agent Canonical Instructions

Welcome, AI Agent. If you have been granted access to this repository, you **MUST** read and follow these instructions completely before modifying any code. This file serves as the definitive guide to interacting with this algorithmic trading repository.

## 1. What this project is
This is a robust, multi-strategy quantitative algorithmic trading and statistical validation platform. It strictly enforces risk controls, runs on Binance Testnet, and monitors performance via a Flask-based real-time dashboard.

## 2. Where the trading engine is
- **Main Entry Point**: `bot.py`
- **Supervisor**: `supervise_services.py` (Manages background daemon processes in production)
- **Orchestrator**: `testnet_engine/service.py` (Coordinates the testnet trading lifecycle)
- **Scanner**: `testnet_engine/market_scanner.py` (Evaluates all symbols across strategies)
- **Execution & Safety**: `execution.py` (Handles Binance order routing and safety blocks)
- **Market Data**: `data.py` (Historical & live WebSocket data loading)
- **Feature Engineering**: `features.py` (Technical analysis and ML features)
- **Strategies**: Implementations found in `strategy_*.py` files in the root directory.

## 3. Where the dashboard is
- **Backend API & Web Server**: `dashboard.py` (Flask)
- **Frontend Assets**: `static/index.html`, `static/style.css`, `static/app.js`

## 4. Where the live deployment is
- **Base URL**: https://algorithmic-trading-bot-fra.onrender.com
- **Region**: Frankfurt, Germany (chosen for low latency to Binance APIs)
- **Platform**: Render
- **Infrastructure**: Docker + Supervisor (see `Dockerfile`)

## 5. How to verify production health
- General Health: https://algorithmic-trading-bot-fra.onrender.com/health
- Engine Internal Health: https://algorithmic-trading-bot-fra.onrender.com/api/engine-health
- Real-time Status: https://algorithmic-trading-bot-fra.onrender.com/api/status
- Market Scanner State: https://algorithmic-trading-bot-fra.onrender.com/api/scanner
- Trades Ledger: https://algorithmic-trading-bot-fra.onrender.com/api/trades

## 6. How to run tests
```bash
pytest tests/ -q
```
**RULE:** You must run tests TWICE after any significant changes to ensure state persistence idempotency and stability.

## 7. How to deploy
Deployment is handled automatically via Render's GitHub integration upon pushing to the `master` branch. Ensure code passes all tests locally before committing.

## 8. What must NEVER be changed (CRITICAL SAFETY RULES)
- **TESTNET ONLY**: You must NEVER enable live trading or remove the testnet safeguards.
- **NO CREDENTIALS**: You must NEVER commit API keys, expose secrets, or print secrets to logs.
- **NO FABRICATION**: You must NEVER fabricate market data or trade data to force tests to pass.
- **NO RISK WEAKENING**: You must NEVER weaken risk controls, profitability gates, or safety checks merely to generate more trades.
- **NO FALSE CONFIDENCE**: You must NEVER claim production health is perfect based solely on local tests; you must verify against live endpoints.

---

### Further Documentation
- **Production Architecture**: See `docs/LIVE_SYSTEM.md`
- **Debugging Guide**: See `docs/AI_DEBUGGING.md`
- **GitHub Copilot Instructions**: See `.github/copilot-instructions.md`
