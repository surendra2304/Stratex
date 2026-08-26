# Multi-Timeframe (1h/15m) ADX+EMA Strategy Backtest Report

## 1. Executive Summary & Verdict
- **Benchmark Period**: 2024-01-01 to 2026-08-23 (~32 months out-of-sample data)
- **Assets Tested**: BTCUSDT, ETHUSDT, SOLUSDT
- **Timeframes**: 1h HTF Trend Filter / 15m LTF Sniper Entry
- **Friction Model**: 8 bps total round-trip friction (LIMIT_MAKER entry + taker stop/target exit)
- **Leverage**: 5x Isolated Margin
- **Scientific Verdict**: **PASSED (VIABLE FOR DEPLOYMENT)**

> **Portfolio Net Profit Factor**: **1.26** (Gross PF: 1.40)  
> **Overall Win Rate**: **51.6%** (122 total trades)  
> **Average Hold Time**: **542.0 minutes**  
> **Max Drawdown**: **2.85%**

---

## 2. Asset-by-Asset Performance Breakdown

| Symbol | Trades | Win Rate | Gross PF | Net PF (8 bps) | Avg Hold (mins) | Net PnL ($10k base) | Max DD (%) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **BTCUSDT** | `43` | `48.8%` | `1.25` | **`1.11`** | `571.7` | `+$128.73` | `2.10%` |
| **ETHUSDT** | `43` | `51.2%` | `1.37` | **`1.24`** | `417.6` | `+$270.92` | `2.85%` |
| **SOLUSDT** | `36` | `55.6%` | `1.64` | **`1.52`** | `655.0` | `+$444.93` | `2.04%` |

---

## 3. Leverage & Friction Mathematics

- **Base Asset Friction**: 8 bps (0.08%) round-trip with LIMIT_MAKER entry model.
- **Leverage Drag on Margin**: At 5x leverage, 8 bps fee drag consumes **0.40% of allocated margin** per round-trip trade (down from 0.75% in 15 bps taker model).
- **Risk/Reward Geometry**: 1.5x 15m ATR Stop vs 3.0x 15m ATR Target provides a structural **1:2.0 Risk/Reward ratio**.

---

## 4. Key Recommendations
- If Net PF >= 1.20: The strategy proves resilient to realistic friction on 15m candles with 1h macro trend alignment. Safe for gradual testnet soak.
- If Net PF < 1.20: Strategy requires higher timeframes or additional volume/momentum filters.