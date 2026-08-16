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
  - **Reward / Risk Ratio**: `1.50` (3.0 ATR / 2.0 ATR)
- **Economic & Mathematical Formulation**:
  - **OOS Win Rate Prior ($P_{\text{win}}$)**: `49.40%` ($0.4940$)
  - **Taker Round-Trip Friction ($F$)**: `31.0 bps` ($0.003100$)
  - **Normalized ATR Ratio**: $a = \frac{\text{ATR}(14)}{P_0}$
  - **Expected Gross Return**: $(0.4940 \times 3.0 \times a) - (0.5060 \times 2.0 \times a) = \mathbf{+0.4700 \times a}$
  - **Expected Net Return**: $\mathbf{(+0.4700 \times a) - 0.003100}$
  - **Exemplar Net Edge (at 1.5% ATR)**: `+39.5 bps`
  - **Break-Even ATR Ratio**: $a \ge \frac{0.003600}{0.4700} = \mathbf{0.766\% \text{ (76.6 bps)}}$
  - **Minimum Required Edge**: `5.0 bps` ($0.000500$)
  - **Gate Decision**: `ACCEPTED` for all 4h candle signals with $a \ge 0.766\%$

---

## 2. Inactive / Disabled Strategies

| Strategy | Timeframe | Execution Model | Status | Rationale for Inactivation |
|---|---|---|---|---|
| **`aggressor`** | `1m` | `RULE_BASED` | `DISABLED` | 1m order book volume delta scalp targets (10–16 bps) are mathematically incapable of beating 31 bps taker friction (Empirical OOS: -31.99 bps net return). |
| **`scalper`** | `1m` / `15m` | `RULE_BASED` | `DISABLED` | 1m mean-reversion with Bollinger Bands produces negative net expectancy under realistic exchange friction. |
| **`supertrend`** | `15m` / `1h` | `RULE_BASED` | `DISABLED` | Uses an unvalidated 50% TP heuristic that violates exchange price filters and fails multi-asset OOS validation. |
| **`swing`** | `1d` | `RULE_BASED` | `DISABLED` | Unvalidated win-rate prior; pending long-term multi-asset backtest audit. |
| **`ml`** | `15m` | `PROBABILISTIC` | `DISABLED` | Requires pre-trained XGBoost model with calibrated barrier probability >= 43.0% to beat 31 bps round-trip friction. |

---

## 3. Economic Cost Engine Assumptions

| Friction Component | Basis Points (bps) | Percentage (%) |
|---|---|---|
| Entry Commission | 10.0 bps | 0.10% |
| Exit Commission | 10.0 bps | 0.10% |
| Entry Slippage | 5.0 bps | 0.05% |
| Exit Slippage | 5.0 bps | 0.05% |
| Half-Spread | 1.0 bps | 0.01% |
| **Total Round-Trip Friction** | **31.0 bps** | **0.31%** |
