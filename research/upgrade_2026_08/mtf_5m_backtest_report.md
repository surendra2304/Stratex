# Multi-Timeframe (1h/5m) ADX+EMA Strategy Backtest Report

## 1. Executive Summary & Verdict
- **Benchmark Period**: 2024-01-01 to 2026-08-23 (~32 months out-of-sample data)
- **Assets Tested**: BTCUSDT, ETHUSDT, SOLUSDT
- **Friction Model**: 15 bps total round-trip friction (8 bps taker fees + 7 bps slippage)
- **Leverage**: 5x Isolated Margin
- **Scientific Verdict**: **FAILED (NET PF < 1.20 - REQUIRES PARAMETER TUNING)**

> **Portfolio Net Profit Factor**: **0.42** (Gross PF: 0.79)  
> **Overall Win Rate**: **28.5%** (5796 total trades)  
> **Average Hold Time**: **43.7 minutes**  
> **Max Drawdown**: **100.00%**

---

## 2. Asset-by-Asset Performance Breakdown

| Symbol | Trades | Win Rate | Gross PF | Net PF (15 bps) | Avg Hold (mins) | Net PnL ($10k base) | Max DD (%) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **BTCUSDT** | `2135` | `24.7%` | `0.60` | **`0.24`** | `41.8` | `+$-9999.96` | `100.00%` |
| **ETHUSDT** | `1900` | `28.1%` | `0.83` | **`0.39`** | `42.9` | `+$-9996.15` | `99.96%` |
| **SOLUSDT** | `1761` | `33.6%` | `0.87` | **`0.55`** | `46.7` | `+$-9854.96` | `98.55%` |

---

## 3. Leverage & Friction Mathematics

- **Base Asset Friction**: 15 bps (0.15%) round-trip.
- **Leverage Drag on Margin**: At 5x leverage, 15 bps fee drag consumes **0.75% of allocated margin** per round-trip trade.
- **Risk/Reward Geometry**: 1.5x 5m ATR Stop vs 3.0x 5m ATR Target provides a structural **1:2.0 Risk/Reward ratio**, which allows the strategy to maintain positive net expectancy even with sub-45% win rates.

---

## 4. Key Recommendations
- If Net PF >= 1.20: The strategy proves resilient to 15 bps taker friction on 5m candles due to 1h macro trend alignment. Safe for phased paper/testnet soak.
- If Net PF < 1.20: Parameter tuning required (e.g. increase SL to 2.0x ATR / TP to 4.0x ATR, or use Limit Maker entries to reduce friction from 15 bps to 6 bps).