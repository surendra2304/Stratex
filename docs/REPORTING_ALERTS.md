# Autonomous Reporting & Intelligent Alerting Guide

## Overview

The Autonomous Reporting & Intelligent Alerting system converts raw telemetry into structured, executive-ready intelligence and voice summaries for **FRIDAY** and dashboard UIs.

---

## 1. Reporting Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Daily Performance Reporter (00:05 UTC)               │
│                        (reporting/daily_report.py)                     │
├────────────────────────────────────────────────────────────────────────┤
│ • Executive Narrative + FRIDAY Voice Synthesis Summary                 │
│ • PnL & Win Rate Tables per Quantitative Strategy                      │
│ • Max Drawdown & Risk Budget Remaining Metrics                         │
│ • AI Advisory Empirical Attribution (+Alpha per decision type)        │
│ • Auto-persisted in JSON, Markdown, and HTML formats                   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
    ┌───────────────────────────────┴───────────────────────────────┐
    ▼                                                               ▼
┌────────────────────────────────────────┐      ┌────────────────────────────────────────┐
│  Periodic Reviews (reporting/periodic) │      │  Intelligent Alert Engine & Anomalies  │
├────────────────────────────────────────┤      ├────────────────────────────────────────┤
│ • Weekly Reviews (Monday): Consistency │      │ • RAW / CONTEXTUAL / INSIGHTFUL levels │
│ • Monthly Audits (1st): Tier readiness │      │ • Deduplication (15m window)           │
│ • AI Advisory Cumulative Alpha         │      │ • 2-Sigma Underperformance & 3σ Vol    │
└────────────────────────────────────────┘      └────────────────────────────────────────┘
```

---

## 2. Alert Severity & Intelligent Routing

| Severity | Urgency | Context & Mitigation | Primary Routing Destination |
| :--- | :--- | :--- | :--- |
| **CRITICAL** | Act Now | Risk limit hit, exchange disconnection with open trades | Webhook + FRIDAY speech + Dashboard |
| **HIGH** | Act Today | Limit approaching (e.g. Drawdown 8%), strategy $2\sigma$ drop | Webhook + FRIDAY + Dashboard |
| **MEDIUM** | Review Soon | Performance drift, regime transition | Dashboard + Daily Report |
| **LOW** | Informational | Generation completed, report archived | Daily Report digest only |

---

## 3. FRIDAY Voice Summaries

Every report and alert produces a sanitized, conversational natural language string for speech synthesis:
- *Daily Summary Example*: `"Today you gained 1.2 percent across 18 trades. Supertrend was your strongest performing strategy. Your risk budget for tomorrow is 93 percent available."`
- *Trade Open Example*: `"Position opened: BTC long at 60,200."`

---

## 4. API Endpoints

- `GET /api/v1/reports/daily/latest` (supports `?format=json|markdown|html`)
- `GET /api/v1/reports/daily/{date}`
- `GET /api/v1/reports/weekly/latest`
- `GET /api/v1/reports/monthly/latest`
- `GET /api/v1/reports/alerts`
