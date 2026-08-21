# ALGORITHMIC TRADING BOT — PROJECT HANDOFF

> **Target Audience**: Incoming AI Engineering Agent (Claude, Codex, Gemini, GPT, etc.)  
> **Repository**: `https://github.com/surendra2304/algorithmic-trading-bot.git`  
> **Local Workspace**: `D:/MT5/python_bot`  
> **Active Branch**: `master`  
> **Production / Render Dashboard**: `https://algorithmic-trading-bot-fra.onrender.com`  
> **System State**: TESTNET / PAPER ONLY — Live Trading Permanently Locked (`LIVE_TRADING_ENABLED = False`)

---

## 1. Executive Summary & Purpose

### What This Algorithmic Trading Bot Is:
- A production-grade, asynchronous **Algorithmic Trading Research, Backtesting, Paper Trading & Binance Testnet Execution Platform**.
- A multi-strategy evaluation framework supporting technical indicator strategies, machine learning models, quantum-inspired optimization research, and Gemini AI advisory analytics.
- A real-time telemetry, risk management, and web dashboard monitoring system served via Flask with a vanilla JavaScript / Chart.js frontend.

### What This Project Is NOT:
- **NOT** a live real-money trading bot. Live execution is permanently locked and disabled in code.
- **NOT** a guaranteed profitable money-making system. The scientific verdict from out-of-sample (OOS) research indicates **insufficient evidence of statistically significant out-of-sample edge** to warrant live capital allocation.
- **NOT** dependent on external proprietary closed-source engines. Everything is self-contained in Python.

---

## 2. Next AI Instructions

When taking over this repository, follow these rules strictly:

1. **Read in order**:
   - `PROJECT_HANDOFF.md`
   - `FINAL_HANDOFF_AUDIT.md`
   - `DIARY.md`
2. **Treat repository code as the single source of truth**: Do not assume claims in previous AI markdown files are true without inspecting the code and running commands.
3. **Preserve Safety Invariants**:
   - `config.LIVE_TRADING_ENABLED` must remain `False`.
   - Never weaken risk gates, daily loss limits (`MAX_DAILY_LOSS_PCT = 0.02`), or drawdown limits (`MAX_TESTNET_DRAWDOWN_PCT = 0.05`).
   - Gemini AI and Quantum modules must remain strictly **ADVISORY ONLY** with zero execution authority.
4. **Never fabricate results**: Never invent profit factors, win rates, backtest numbers, or test results.
5. **Differentiate simulation from real execution**: Keep Paper trading, Binance Testnet, and Backtest runs distinct.
6. **Maintain test suite cleanliness**: Run `pytest tests/ -q` twice before and after major changes. Ensure 505/505 tests pass.

---

## 3. Technology Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.11+ |
| **Web Server & API** | Flask, Flask-CORS, Gunicorn (on Render) |
| **Market Data & Exchange** | `binance-connector-python`, WebSockets (`websockets`), REST API |
| **Data Processing & ML** | `pandas`, `numpy`, `scipy`, `scikit-learn`, `joblib`, `pyarrow` (Parquet) |
| **Quantum Research** | PennyLane, Qiskit (Classical CPU simulation) |
| **Generative AI** | Google Gemini API via official SDK / REST |
| **Frontend** | HTML5, CSS3, Vanilla ES6 JavaScript, Chart.js |
| **Testing** | `pytest`, `pytest-cov`, `node -c`, `htmlhint` |
| **Deployment** | Render (Docker / Python Web Service) |

---

## 4. Architecture & Trading Pipeline

```
┌────────────────────────────────────────────────────────┐
│                   MARKET DATA LAYER                    │
│   Binance WS / REST  ───►  Candle Aggregation & Normalization │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                    FEATURES ENGINE                     │
│   features.py (SMA, EMA, RSI, ATR, Bollinger, ADX)     │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                    STRATEGY ENGINES                    │
│   strategy_*.py (Scalper, Supertrend, ML, Swing, etc.)  │
└───────────────────────────┬────────────────────────────┘
                            │ Raw Signal (BUY / SELL / HOLD)
┌───────────────────────────▼────────────────────────────┐
│                  PROFITABILITY GATE                    │
│   Expected Edge > 0.0001, Spread / Fee Hurdle Checked   │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                       RISK GATE                        │
│   Max Exposure (5%), Daily Loss (2%), Cooldown (300s)  │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                   EXECUTION POLICY                     │
│   LIVE_TRADING_ENABLED == False Check (Hard Block)     │
│   Routes to: Paper Engine OR Binance Testnet           │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                 ORDER & POSITION STATE                 │
│   ENTRY_SUBMITTED ──► ENTRY_FILLED ──► PROTECTED ──► CLOSED
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│               ACCOUNTING & TELEMETRY                   │
│   Equity = Cash + Used Margin + Unrealized PnL         │
│   Ledger JSONL Logging & TelemetryManager Analytics     │
└────────────────────────────────────────────────────────┘
```

---

## 5. Active Strategies Inventory

All strategies reside in the repository root:

1. **`strategy_scalper.py` (`scalper`)**: Fast momentum scalping on 1m/3m/5m using EMA crossovers and RSI bounds. Tight SL/TP (1.5:1 RR).
2. **`strategy_supertrend.py` (`supertrend`)**: Trend-following strategy using ATR bands. Timeframes: 5m, 15m, 30m, 1h.
3. **`strategy_adx_ema.py` (`adx_ema`)**: Directional movement index with ADX threshold (> 25) combined with EMA slope. Timeframes: 15m, 30m, 1h, 4h.
4. **`strategy_aggressor.py` (`aggressor`)**: High-frequency order flow and volume delta breakout strategy on 1m/3m/5m.
5. **`strategy_swing.py` (`swing`)**: Multi-day / multi-hour support-resistance and swing highs/lows on 1h/2h/4h.
6. **`strategy_bollinger.py` (`bollinger`)**: Mean reversion strategy utilizing 20-period 2-std bands with RSI overbought/oversold confirmation.
7. **`strategy_breakout_vol.py` (`breakout_vol`)**: Volume-confirmed resistance breakout detection.
8. **`strategy_hybrid.py` (`hybrid`)**: Multi-indicator ensemble combining Supertrend direction with Scalper entry timing.
9. **`strategy_ml.py` (`ml`)**: Machine learning classifier predicting forward return barriers. *(Status: Evaluated in OOS; in-sample artifacts removed; see Section 7).*

---

## 6. Accounting & Execution Invariant

The core accounting identity enforced across `testnet_engine/service.py`, `paper_engine/portfolio.py`, and `dashboard.py`:

$$\mathbf{Total\ Managed\ Equity} = \mathbf{USDT\ Total\ Cash} + \mathbf{Active\ Used\ Margin} + \mathbf{Unrealized\ PnL}$$

- **Position Representation**: Canonical side is standardized (`BUY` / `SELL` and direction `LONG` / `SHORT`).
- **Fee Accounting**: $0.1\%$ standard spot fee ($0.001$) deducted upon entry and exit in all paper and backtest models.
- **Slippage**: $0.05\%$ ($0.0005$) model applied.

---

## 7. Machine Learning, Quantum & AI Status

### Machine Learning (`strategy_ml.py`):
- **OOS Verdict**: Strict walk-forward testing revealed that earlier reported in-sample gains (e.g. +$419.19 on LINKUSDT) were overfitting artifacts. The current OOS evaluation confirms no significant alpha over baseline without lookahead bias.

### Quantum Subsystem (`quantum/`):
- **Implementation**: PennyLane / Qiskit VQC, Hybrid Classifier, and QUBO Portfolio Optimizer running in **Classical CPU Simulation**.
- **Physical QPU Usage**: 0.0 seconds consumed (no physical quantum hardware).
- **Benchmarking**: 5-fold walk-forward validation + 10,000 paired bootstrap resamplings on BTCUSDT 1m.
- **Scientific Verdict**: **B — NO QUANTUM ADVANTAGE DETECTED** ($p > 0.63$). Remains strictly an advisory research prototype.

### Gemini AI (`gemini_service.py`):
- **Status**: Live integration via `gemini-flash-latest` / `gemini-3.7-flash` with graceful backoff and key failover.
- **Authority**: Strictly **ADVISORY ONLY**. Gemini provides qualitative signal reviews and post-trade journaling. It has zero capability to alter trading orders, risk parameters, or execution state.

---

## 8. Directory & File Map

```
D:/MT5/python_bot/
├── dashboard.py                  # Main Flask Web Application & API endpoints (55+ routes)
├── config.py                     # Central configuration & safety gates
├── features.py                   # Technical indicator calculation engine
├── backtest_engine.py            # Event-driven backtesting engine with fee & slippage modeling
├── metrics.py                    # Financial metrics (Sharpe, Sortino, Calmar, MaxDD, WinRate)
├── gemini_service.py             # Google Gemini AI integration (Advisory only)
├── quantum_endpoint.py           # Blueprint for /api/quantum/advisory
├── static/
│   ├── index.html                # Single-page terminal UI (10 views)
│   ├── app.js                    # Frontend state manager, polling, and Chart.js renderer
│   └── style.css                 # Dark institutional styling
├── paper_engine/                 # Standalone paper trading simulation engine
├── testnet_engine/               # Binance Testnet async execution service & TelemetryManager
├── quantum/                      # Quantum research package (VQC, circuits, validation)
├── research/                     # ExperimentRunner, OOS validation, and 240-config manifest
├── tests/                        # 505 unit, integration, chaos, and security tests
├── data_cache/                   # Parquet candle data caches
└── DIARY.md                      # Complete daily engineering history
```

---

## 9. API Security & Input Hardening

All numeric query parameters across Flask endpoints are hardened using `safe_int_param` and `safe_float_param` in `dashboard.py`:
- Protects against malformed parameters (`?limit=abc`, `?limit=1.5`, `?limit=NaN`, `?limit=-1`, `?limit=999999999999999999`).
- Automatically clamps values to allowed safe ranges (e.g. $[1, 1000]$).
- Prevents unhandled `ValueError` HTTP 500 crashes.

---

## 10. Local Setup & Verification

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run unit & integration test suite (505 tests)
pytest tests/ -q

# 3. Verify static frontend assets
node -c static/app.js
npx -y htmlhint static/index.html

# 4. Start local development dashboard
python dashboard.py
# Access at http://localhost:5000
```

---

## 11. Known Limitations

1. **Live Trading**: Permanently locked (`LIVE_TRADING_ENABLED = False`). Do not attempt live exchange execution.
2. **Scientific Edge**: Current strategies exhibit mean-reverting and trending characteristics without statistically proven excess alpha after full transaction costs across long out-of-sample horizons.
3. **Browser Automation**: Full Headless Chrome / Playwright end-to-end browser runtime tests are not in the automated CI pipeline; static linting (`node -c`, `htmlhint`) and manual UAT are used.
4. **24-Hour Soak**: Multi-hour battery soak runners exist (`battery_soak_runner.py`), but an uninterrupted continuous 24-hour live soak test has not been executed in this environment.
5. **Quantum**: Fully simulated on CPU; no physical QPU hardware acceleration.
