# PRODUCTION STRATEGY REGISTRY

This document is the authoritative registry of all algorithmic trading strategies evaluated for Binance Testnet execution.

---

## 1. Validated Production Strategies

### Strategy 1: ADX + EMA Trend Following (`adx_ema`)
- **Status**: `VALIDATED`
- **Execution Mode**: `RULE_BASED`
- **Timeframe**: `4h`
- **Target Assets**: `BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, `XRPUSDT`, `LINKUSDT`
- **Indicators**:
  - `EMA(20)` Fast Trend
  - `EMA(50)` Slow Trend
  - `EMA(200)` Regime Direction Filter
  - `ADX(14)` Trend Strength Filter (Threshold: 25)
  - `ATR(14)` Volatility-Based Position & Barrier Sizing
- **Entry Logic**:
  - **BUY**: `EMA(20)` crosses above `EMA(50)` AND `Close > EMA(200)` AND `ADX(14) > 25`
  - **SELL**: `EMA(20)` crosses below `EMA(50)` AND `Close < EMA(200)` AND `ADX(14) > 25`
- **Exit & Protection Logic**:
  - **BUY Stop-Loss**: `Close - (2.0 × ATR)`
  - **BUY Take-Profit**: `Close + (3.0 × ATR)`
  - **SELL Stop-Loss**: `Close + (2.0 × ATR)`
  - **SELL Take-Profit**: `Close - (3.0 × ATR)`
  - **Reward / Risk Ratio**: `1.5` (3.0 ATR / 2.0 ATR)
- **Economic & Mathematical Validation**:
  - **OOS Win Rate Prior**: `49.4%` (Multi-asset holdout benchmark)
  - **Taker Round-Trip Friction**: `31.0 bps` (0.31% = 0.10% entry fee + 0.10% exit fee + 0.05% entry slippage + 0.05% exit slippage + 0.01% spread)
  - **Expected Gross Return**: `(0.494 × 3.0×ATR) - (0.506 × 2.0×ATR) = +0.47 × ATR` (~70.5 bps on 4h)
  - **Expected Net Return**: `+70.5 bps - 31.0 bps = +39.5 bps`
  - **Minimum Required Edge**: `5.0 bps` (0.05%)
  - **Gate Decision**: `ACCEPTED` (+39.5 bps >= 5.0 bps)

---

## 2. Inactive / Unvalidated Strategies

| Strategy | Timeframe | Execution Model | Status | Rationale for Inactivation |
|---|---|---|---|---|
| **`aggressor`** | `1m` | `RULE_BASED` | `DISABLED` | 1m order book volume delta scalp targets (10–16 bps) are mathematically incapable of beating 31 bps taker friction (Empirical OOS: -31.99 bps net return). |
| **`scalper`** | `1m` / `15m` | `RULE_BASED` | `DISABLED` | 1m mean-reversion with Bollinger Bands produces negative net expectancy under realistic exchange friction. |
| **`supertrend`** | `15m` / `1h` | `RULE_BASED` | `DISABLED` | Uses an unvalidated 50% TP heuristic that violates exchange price filters and fails multi-asset OOS validation. |
| **`swing`** | `1d` | `RULE_BASED` | `DISABLED` | Unvalidated win-rate prior; pending long-term multi-asset backtest audit. |
| **`ml`** | `15m` | `PROBABILISTIC` | `DISABLED` | Requires pre-trained XGBoost model with calibrated barrier probability >= 43.0% to beat 31 bps round-trip friction. |

---

## 3. Economic Cost Engine Assumptions

All strategy profitabilities are evaluated using decoupled, transparent Binance Spot Taker friction:

| Friction Component | Basis Points (bps) | Percentage (%) |
|---|---|---|
| Entry Commission | 10.0 bps | 0.10% |
| Exit Commission | 10.0 bps | 0.10% |
| Entry Slippage | 5.0 bps | 0.05% |
| Exit Slippage | 5.0 bps | 0.05% |
| Half-Spread | 1.0 bps | 0.01% |
| **Total Round-Trip Friction** | **31.0 bps** | **0.31%** |
