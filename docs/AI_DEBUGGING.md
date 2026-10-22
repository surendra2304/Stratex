# AI Agent Debugging Guide

When diagnosing issues in the production trading system, future AI coding agents **MUST** inspect the live endpoints and Render deployment logs. Relying solely on local testing can mask environmental or network-specific production failures.

## 1. Discrepancies Between UI and Backend

- **Symptom**: The dashboard shows zero, missing, or stale data, but the backend claims to be running.
- **Diagnostic Action**: 
  - Directly inspect the API payloads (e.g., query `/api/status`, `/api/scanner`, `/api/trades`). If the JSON payload contains the correct data, the issue is on the frontend.
  - Inspect JavaScript console errors and network requests.
  - Verify DOM ID mappings in `static/app.js` match `static/index.html`.
  - Check for UI rendering logic being trapped in loading/error states.

## 2. Signal Generation Failures (Zero Signals)

- **Symptom**: The dashboard indicates `0 Total Signals`.
- **Trace Path**: `Market Data` → `Candle Close Event` → `Strategy Evaluation` → `Signal Generation`.
- **Diagnostic Action**:
  - Verify that WebSocket candle-close events are actively firing.
  - Verify if the REST polling fallback is functioning when WebSockets disconnect.
  - Check strategy configuration limits (e.g., minimum volatility thresholds preventing signals).

## 3. Execution Failures (Signals Exist, Orders are Zero)

- **Symptom**: Signals are being generated, but the system shows `0 Filled Orders`.
- **Trace Path**: `Profitability Gate` → `Risk Gate` → `Execution Routing`.
- **Diagnostic Action**:
  - Check `/api/scanner` to see if signals are failing the `PROFITABILITY_REJECTED` gate (i.e., the expected edge is too small to cover taker fees and slippage).
  - Check if signals are failing the `RISK_REJECTED` gate (e.g., daily loss limit hit, max concurrent positions reached).
  - Do **NOT** simply disable the risk or profitability gates to force trades to execute. Fix the underlying signal edge.

## 4. Total Engine Halts

- **Symptom**: The engine stops evaluating new market data completely.
- **Diagnostic Action**:
  - Check `/api/engine-health` to view the timestamp of the last evaluation loop.
  - Inspect the Render logs for unhandled exceptions causing thread death.
  - Check for deadlocks in threading primitives, async callbacks, or stale WebSocket connections that failed to reconnect.
