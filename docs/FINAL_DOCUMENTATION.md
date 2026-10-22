# Final Comprehensive Documentation & Compliance Reference

## 1. System Architecture Summary

The quantitative trading bot is an institutional-grade algorithmic trading platform featuring:
- **Dual Execution Tracks**: Paper engine with microsecond ledger persistence and Binance Testnet/Futures live execution.
- **Quantitative Strategy Suite**: 6 algorithmic strategies (Aggressive Scalper, Supertrend, ADX-EMA Trend, Swing Momentum, ML Classifier, MTF Momentum).
- **AI-Universe Advisory Subsystem**: Multi-agent reasoning intelligence offering bounded parameter modulations via double-key safety authorization.
- **Dynamic Risk & Sizing Engine**: Fixed fractional, Volatility sizing, Half-Kelly, Risk Parity, and real-time VaR/CVaR calculations.
- **Portfolio Optimization**: Markowitz Mean-Variance Sharpe maximization and Black-Litterman model.
- **Algorithmic Execution**: TWAP slicing, Iceberg orders, and Implementation Shortfall attribution.
- **Enterprise Monitoring**: Prometheus metrics exporter, multi-tier alert dispatcher, and cryptographic HMAC audit verification.

---

## 2. API & Endpoint Reference

| Endpoint | Method | Purpose | Auth Required |
| :--- | :--- | :--- | :--- |
| `/api/health` | GET | Overall system health rollup | No |
| `/api/health/trading` | GET | Real-time equity, drawdown, open positions | No |
| `/api/health/advisory`| GET | AI-Universe consultation latency & overrides | No |
| `/api/health/system`  | GET | Host CPU, Memory, and Disk utilization | No |
| `/api/metrics`        | GET | Prometheus metrics scraper | No |
| `/api/alerts`         | GET/POST | Query or acknowledge production alerts | No / POST |
| `/api/panic`          | POST | Emergency kill switch & order cancellation | Yes (`X-BOT-API-KEY`) |
| `/api/testnet/advisory/toggle` | POST | Toggle between SHADOW and APPLY modes | Yes (`X-BOT-API-KEY`) |

---

## 3. Regulatory & Risk Disclosures

- **Simulation & Testing**: The platform operates in simulated paper and testnet sandbox environments. Live capital order placement is strictly blocked by `ExecutionPolicy` unless explicitly authenticated and configured.
- **Model Risk**: Quantitative models and AI advisory outputs are probabilistic and cannot guarantee against extreme black swan events or sudden liquidity evaporation. Hard limits (15% Max Drawdown, 5% Max Daily Loss) are permanently locked to constrain maximum downside.
- **Audit Trails**: All trades, configuration mutations, and advisory verdicts are cryptographically signed and stored in append-only JSONL ledgers.
