# Production Deployment, Security & Monitoring Guide

## Overview

This document outlines the production architecture, security invariants, deployment procedure, monitoring mechanisms, and emergency protocols for the algorithmic trading bot framework.

---

## 1. Production Architecture & Safety Boundaries

```
                 ┌──────────────────────────────────────────────┐
                 │       Production Deployment Pipeline         │
                 │            (deploy_production.py)            │
                 └──────────────────────┬───────────────────────┘
                                        │
                        ┌───────────────▼───────────────┐
                        │   Security & Env Audit        │
                        │   All Tests Passing (100%)    │
                        │   Signed Audit Log Generated  │
                        └───────────────┬───────────────┘
                                        │
                 ┌──────────────────────▼───────────────────────┐
                 │      Unified Production Runtime Daemons      │
                 └──────────────┬───────────────────────┬───────┘
                                │                       │
            ┌───────────────────┴───┐               ┌───┴───────────────────┐
            ▼                       ▼               ▼                       ▼
┌───────────────────────────────┐       ┌───────────────────────────────┐
│ Live Engine (bot.py / service)│       │ Flask Web Dashboard / API     │
├───────────────────────────────┤       ├───────────────────────────────┤
│ • RiskGate / ProfitGate locked│       │ • /api/health (Overall)       │
│ • Max Drawdown <= 15% ceiling │       │ • /api/health/trading         │
│ • Daily Loss <= 5% hard stop  │       │ • /api/health/advisory        │
│ • Token-Bucket Rate Limiting  │       │ • /api/health/system          │
│ • Anomaly Spike Detection     │       │ • /api/metrics (Prometheus)   │
└───────────────────────────────┘       └───────────────────────────────┘
```

### Safety Constraints Enforced (`config_production.py`)

| Constraint | Limit | Behavior on Breach |
| :--- | :--- | :--- |
| **Max Drawdown Limit** | 15.0% | Trips circuit breaker, halts live parameter mutation, reverts overrides |
| **Daily Loss Limit** | 5.0% | `RiskGate` halts new order placement for the remainder of the UTC day |
| **Warning Drawdown** | 10.0% | Emits WARNING alert to `production_alerts.jsonl` |
| **Warning Daily Loss** | 3.0% | Emits WARNING alert to `production_alerts.jsonl` |
| **Max Position Size** | 1.5x current size | Scale proposals exceeding 1.5x are rejected |
| **Max Parameter Delta**| ±20.0% | Parameter modulations exceeding ±20% are rejected |
| **Leverage Invariant** | Non-increasing | Leverage increase attempts are strictly blocked |
| **Forbidden Parameters** | Core risk variables | Changes to `max_daily_loss`, `max_drawdown`, `api_key`, etc. are rejected |

---

## 2. Deployment Procedure

Run the automated production deployment pipeline:

```bash
python deploy_production.py
```

The script automatically executes 5 validation steps:
1. **Security & Env Audit**: Verifies `DEBUG=False`, safety gate locks, and masks sensitive credentials.
2. **AI-Universe Health Check**: Verifies AI connection (falls back to validated defaults if offline).
3. **Automated Test Suite**: Executes unit and safety test suites (must pass 100%).
4. **State Storage Initialization**: Creates atomic production namespaces and log directories.
5. **Signed Audit Log Generation**: Cryptographically signs deployment metadata (`deployment_audit_log.json`) using HMAC SHA-256.

---

## 3. Real-Time Monitoring & Alerting

### Health & Prometheus Endpoints
- `GET /api/health`: Comprehensive system health rollup (`HEALTHY`, `WARNING`, `CRITICAL`).
- `GET /api/health/trading`: Equity, drawdown, open positions, realized PnL.
- `GET /api/health/advisory`: AI-Universe latency, acceptance rate, active parameter overrides.
- `GET /api/health/system`: Host CPU utilization %, Memory %, and Disk % free.
- `GET /api/metrics`: Prometheus plain-text scrape endpoint for Grafana.
- `GET /api/alerts`: Active system and trading alerts.
- `POST /api/alerts`: Acknowledge an alert (`{"alert_id": "ALT_..."}`).

### Alerting Thresholds

| Condition | Level | Action Taken |
| :--- | :--- | :--- |
| Account Drawdown $\ge 10.0\%$ | `WARNING` | Logged to alerts ledger & dashboard |
| Account Drawdown $\ge 15.0\%$ | `CRITICAL` | Circuit breaker tripped, parameter rollback |
| Daily Loss $\ge 3.0\%$ | `WARNING` | Risk warning emitted |
| Daily Loss $\ge 5.0\%$ | `CRITICAL` | `RiskGate` halts daily execution |
| Host CPU / Memory $\ge 80.0\%$ | `WARNING` | Resource warning emitted |
| Host Disk $\ge 85.0\%$ | `WARNING` | Disk space warning emitted |
| AI-Universe Unreachable $\ge 5$m | `WARNING` | Engine defaults to last validated parameters |

---

## 4. Security Hardening (`security_hardening.py`)

1. **IP Rate Limiting**: In-memory token bucket enforcing 100 requests / hour per IP.
2. **Deep Input Sanitization**: Strips HTML tags, script injection patterns, and control characters from inputs.
3. **HMAC SHA-256 Audit Trail**: Signs all audit records with chained hashing to verify ledger integrity.
4. **Trading Anomaly Detector**: Detects rapid order frequency spikes (> 10 orders / 60s) or excessive order notional ($> \$50,000$).

---

## 5. Emergency Procedures

### 1. Emergency Kill-Switch (Immediate Halt)
To immediately stop all trading activity and cancel open orders:
```bash
curl -X POST http://localhost:5000/api/panic -H "X-BOT-API-KEY: YOUR_BOT_API_KEY"
```

### 2. Manual Overlay Reset (Revert to Defaults)
If AI advisory recommendations need to be rolled back immediately:
```python
from advisory_params import get_advisory_overlay
get_advisory_overlay().reset_to_defaults(reason="MANUAL_OPERATOR_RESET")
```

### 3. Service Restart
```bash
# Graceful stop:
kill $(cat bot.pid)
# Start daemon:
python bot.py
```
