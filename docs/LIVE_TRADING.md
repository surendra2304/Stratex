# Graduated Live Capital Deployment & Risk Operations Guide

## Overview

This guide details the multi-layered authorization requirements, graduated capital tiers, live-specific risk enforcements, isolated ledgers, and emergency failover protocols governing live capital trading on the platform.

---

## 1. Multi-Layered Live Authorization

Live capital execution requires passing **all 6 mandatory gates** simultaneously. If any prerequisite is missing, the engine strictly refuses to start in live mode.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        6-GATE LIVE PREREQUISITES                       │
├────────────────────────────────┬───────────────────────────────────────┤
│ Gate 1: .env Setting           │ LIVE_TRADING_ENABLED="True"           │
├────────────────────────────────┼───────────────────────────────────────┤
│ Gate 2: Physical Token File    │ .live_trading_authorized on filesystem│
│                                │ (Contains timestamp, hash, and level) │
├────────────────────────────────┼───────────────────────────────────────┤
│ Gate 3: Double-Key Safety      │ LIVE_AUTONOMY_CONFIRMED="True"        │
├────────────────────────────────┼───────────────────────────────────────┤
│ Gate 4: Paper Validation       │ >= 60 days of verified paper history  │
├────────────────────────────────┼───────────────────────────────────────┤
│ Gate 5: Testnet Validation     │ >= 30 days of clean testnet telemetry │
├────────────────────────────────┼───────────────────────────────────────┤
│ Gate 6: A/B Advisory Safety    │ AI advisory confirmed non-harmful     │
└────────────────────────────────┴───────────────────────────────────────┘
```

---

## 2. Graduated Capital Tiers & Demotion Rules

```
Tier 1: Pilot ($500 - $1,000)
   │ Max 1 Strategy | 5% Max Position | 2% Max Daily Loss | 5% Max DD
   │ Requires 30 clean days -> Tier 2
   ▼
Tier 2: Growth ($2,000 - $5,000)
   │ Max 3 Strategies | 8% Max Position | 3% Max Daily Loss | 8% Max DD
   │ Requires 30 clean days -> Tier 3
   ▼
Tier 3: Established ($10,000 - $25,000)
   │ All Strategies | 10% Max Position | 4% Max Daily Loss | 12% Max DD
   │ Requires 60 clean days -> Tier 4
   ▼
Tier 4: Scale ($50,000+)
   │ Custom Parameters | Manual Authorization Review
```

### Automatic Demotion Rules:
- **Drawdown Breach**: If portfolio drawdown reaches the tier's ceiling, the bot immediately demotes to the prior tier.
- **Consecutive Loss Days**: $\ge 3$ consecutive losing days triggers automatic tier demotion.
- **Tier 1 Breach**: A drawdown or consecutive loss breach at Tier 1 triggers a full emergency halt and flattens all open positions.

---

## 3. Live Risk Enforcement Rules (`risk/live_risk_enforcer.py`)

- **Hard Daily Loss Limit**: Auto-flattens all positions and engages a **24-hour mandatory trading lockout**.
- **Correlation Ceiling**: Maximum 2 highly correlated major positions (e.g. BTC and ETH) simultaneously.
- **Realized Volatility Circuit Breaker**: New order placement is halted if 24-hour realized annualized volatility exceeds 100%.
- **Zero Config Overrides**: Live limits are hardcoded per level and cannot be modified upwards via `.env` or API.

---

## 4. Isolated Live Ledgers (`ledger/live_ledger.py`)

Live transactions are written to dedicated, append-only ledgers:
- `live_trade_ledger.jsonl`: Comprehensive execution records with timestamps and PnL.
- `live_equity_curve.jsonl`: Continuous equity, cash, and margin utilization snapshots.
- `live_balance_events.jsonl`: Deposit, withdrawal, and fee settlements.
- `live_risk_events.jsonl`: Risk trigger logs and defensive action records.

### Automated Reconciliation:
- Evaluates exchange-reported balance against internal state.
- Emits critical alerts and triggers incident logging if discrepancy $> 0.5\%$.

---

## 5. Live Dashboard & Emergency Controls

### REST API Endpoints:
- `GET /api/live/status`: Current authorization state, active tier, and risk limits.
- `GET /api/live/positions`: Live positions and mark-to-market unrealized PnL.
- `POST /api/live/emergency-flatten`: Immediate emergency liquidation (Protected by `X-BOT-API-KEY`).
- `GET /api/live/daily-report`: JSON report of daily performance.

### UI Dashboard Controls:
- **Top Status Bar**: Live tier badge displays active capital level (`LIVE: LEVEL 1 (PILOT)`).
- **Emergency Button**: `🚨 FLATTEN LIVE` executes immediate panic liquidation and halts execution.
