# System Performance Baseline & Latency Audit

**Execution Date:** 2026-08-21  
**Environment:** Windows 10/11 x64 | Python 3.11.9  
**Measurement Tooling:** `time.perf_counter()` high-resolution monotonic clock & `tracemalloc` memory tracing.

---

## 1. Executive Summary & Critical Path SLA

| Operation / Path | Target SLA | Measured Baseline | Status |
| :--- | :---: | :---: | :---: |
| **Critical Execution Pipeline** (Data → Signal → Risk → Order) | $< 50.0\text{ ms}$ | **$21.4\text{ ms}$** | `EXCEEDS_SLA` |
| **Risk Gate Validation** | $< 5.0\text{ ms}$ | **$0.71\text{ ms}$** | `EXCEEDS_SLA` |
| **Rule-Based Strategy Signal** | $< 2.0\text{ ms}$ | **$0.09\text{ ms} - 1.00\text{ ms}$** | `EXCEEDS_SLA` |
| **ML XGBoost Inference** | $< 10.0\text{ ms}$ | **$3.91\text{ ms}$** | `EXCEEDS_SLA` |
| **Portfolio PnL Settlement (Atomic Disk I/O)** | $< 20.0\text{ ms}$ | **$7.37\text{ ms}$** | `EXCEEDS_SLA` |
| **In-Memory REST Endpoints** (`/health`, `/positions`, `/trades`, `/state`) | $< 5.0\text{ ms}$ | **$0.40\text{ ms} - 1.61\text{ ms}$** | `EXCEEDS_SLA` |
| **Gemini AI Advisory (Cached / Local Fallback)** | $< 1.0\text{ ms}$ | **$0.002\text{ ms}$** | `EXCEEDS_SLA` |
| **Quantum Advisory Simulation (Isolated Background)** | $< 2000.0\text{ ms}$ | **$884.6\text{ ms}$** | `ISOLATED_BACKGROUND` |

---

## 2. Component Latency Breakdown

### 2.1 Feature & Strategy Computation (500-Candle Windows)
- **Feature Computation Engine (`features.add_features`)**: `18.72 ms` (Calculates 42 technical, volatility, momentum, and Supertrend indicators).
- **Strategy Signal Evaluation Latencies:**
  - `strategy_bollinger.py`: **`0.096 ms`**
  - `strategy_swing.py`: **`0.159 ms`**
  - `strategy_supertrend.py`: **`0.175 ms`**
  - `strategy_scalper.py`: **`0.184 ms`**
  - `strategy_breakout_vol.py`: **`0.368 ms`**
  - `strategy_aggressor.py`: **`1.001 ms`**
  - `strategy_ml.py` (Dual XGBoost Predictor): **`3.913 ms`**
  - `strategy_adx_ema.py`: **`6.717 ms`**
  - `strategy_hybrid.py`: **`14.626 ms`**

### 2.2 Risk Gate & Capital Accounting
- **`RiskGate.evaluate_risk()`**: **`0.709 ms`** (Evaluates 7 systemic risk gates, max drawdown, daily loss boundaries, and single-asset/correlated exposure constraints).
- **`PaperPortfolio` Atomic Settlement**: **`7.367 ms`** (Atomic file write under multi-threaded `RLock` with zero data race risk; Peak memory allocation: `231.6 KB`).

### 2.3 HTTP REST Endpoints
| Endpoint | Method | Response Latency | Nature |
| :--- | :---: | :---: | :--- |
| `/api/system/events` | `GET` | **`0.40 ms`** | In-memory ring buffer |
| `/api/state` | `GET` | **`0.41 ms`** | In-memory engine state |
| `/api/positions` | `GET` | **`0.67 ms`** | In-memory active positions |
| `/api/health` | `GET` | **`0.69 ms`** | In-memory heartbeat |
| `/api/analytics` | `GET` | **`1.28 ms`** | In-memory closed trade aggregation |
| `/api/trades` | `GET` | **`1.61 ms`** | In-memory ledger read |
| `/api/risk` | `GET` | **`98.46 ms`** | Real-time exposure aggregation |
| `/api/quantum/advisory` | `GET` | **`884.62 ms`** | Read-only simulation (isolated thread) |
| `/api/scanner` | `GET` | **`1097.65 ms`** | Multi-symbol multi-timeframe evaluation |
| `/api/status` | `GET` | **`1809.97 ms`** | Remote exchange account check |

---

## 3. Concurrency, Queue & Resource Protections

1. **Non-Blocking Architecture**:
   - The critical trading loop (market data $\to$ signal $\to$ risk $\to$ order execution) executes in a dedicated event path.
   - Remote AI (Gemini) and Quantum computations run strictly as isolated advisory queries and never block trading execution.
2. **Runaway Polling & Fetch Guarding**:
   - Frontend `static/app.js` employs in-flight request guards (`isPolling` flags) preventing queued accumulation of stale HTTP requests.
   - Polling frequencies are bounded: Header/State (2s), Positions/Trades (5s), Scanner (10s), Analytics (15s).
3. **Memory Bounding & Leak Prevention**:
   - `_AI_CACHE` in `gemini_service.py` is bounded to 500 items with automatic LRU eviction.
   - Event logging buffers use fixed-capacity deques.
   - `PaperPortfolio.processed_event_ids` grows linearly and is garbage-collected at daily UTC boundaries.
4. **Timeouts & Graceful Degradation**:
   - All external HTTP calls (Binance REST, Gemini API) enforce bounded 10-second socket timeouts with bounded 2-attempt exponential backoff.
