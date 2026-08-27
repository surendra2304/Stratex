# System Integration API Reference (v1) — Consumer-Agnostic

## Overview

The System Integration API provides clean, secure, consumer-agnostic REST endpoints for external dashboards, monitoring platforms, and supervisor services to inspect trading telemetry, query risk headroom, stream data exports, and issue control commands.

**Architecture Rules:**
- The trading bot is a pure standalone service that knows only **AI-Universe** (its advisory source).
- The trading bot has **no knowledge of any external consumer systems or supervisors**.
- Outbound webhooks are optional and configured strictly via environment variables.

---

## 1. Authentication & Security

All requests require authentication via the `X-API-Key` or `Authorization: Bearer <KEY>` header.

### Key Roles:
| Role | Environment Variable | Permissions |
| :--- | :--- | :--- |
| **READ** | `TRADING_BOT_API_KEY_READ` | Status, Positions, Trades, Strategies, Risk, Exports, Health |
| **CONTROL** | `TRADING_BOT_API_KEY_CONTROL` | Read + Pause, Resume, Strategy Toggle, Limits, Emergency Panic Stop |

### Rate Limits:
- **Read Endpoints**: 60 requests / minute per IP.
- **Control Endpoints**: 10 requests / minute per IP.

---

## 2. Public Status API (`/api/v1/`)

### `GET /api/v1/status`
Returns complete rollup of bot mode, real-time equity, daily PnL, win rate, active strategies, advisory status, and drawdown headroom.

**Response Example:**
```json
{
  "status": "OK",
  "timestamp": "2026-08-27T18:30:00.000Z",
  "data": {
    "mode": "TESTNET",
    "trading_active": true,
    "equity": 5035.98,
    "unrealized_pnl": 15.20,
    "realized_pnl": 420.50,
    "daily_pnl": 45.50,
    "daily_pnl_display": {"color": "green", "trend": "up"},
    "win_rate": 62.5,
    "profit_factor": 1.68,
    "max_drawdown_pct": 2.1,
    "open_positions_count": 2,
    "strategies_active": [
      "strategy_scalper", "strategy_supertrend", "strategy_adx_ema", "strategy_swing"
    ],
    "advisory_status": {
      "shadow_mode": true,
      "active_overrides_count": 0,
      "last_decision": "DEC_1786825200000",
      "last_verdict": "APPROVED"
    },
    "risk_status": {
      "daily_loss_pct": 0.8,
      "drawdown_pct": 2.1,
      "max_drawdown_limit_pct": 15.0,
      "drawdown_headroom_pct": 12.9,
      "risk_state": "NOMINAL"
    }
  }
}
```

### Additional Read Endpoints:
- `GET /api/v1/positions` — Active open positions with mark price and unrealized PnL.
- `GET /api/v1/trades?page=1&limit=20` — Paginated closed trade history.
- `GET /api/v1/strategies` — Per-strategy win rates, trade counts, and net PnL.
- `GET /api/v1/advisory` — AI Advisory state and recent consultation history.
- `GET /api/v1/risk` — Live VaR/CVaR, daily loss limit proximity, and drawdown headroom.
- `GET /api/v1/history/equity` — Historical equity curve data points.

---

## 3. Control API (`/api/v1/control/`)

Requires `CONTROL` API key.

### `POST /api/v1/control/pause`
Pauses opening new trades (existing open positions remain actively managed).

### `POST /api/v1/control/resume`
Resumes new trade entry evaluation.

### `POST /api/v1/control/panic`
**Emergency Stop**: Flattens all open positions and locks execution.
- **Safety Requirement**: Requires explicit confirmation payload: `{"confirm": true}`.
- Every invocation is cryptographically signed and logged to `control_audit.jsonl`.

### `POST /api/v1/control/strategy/{name}/toggle`
Enables or disables an individual strategy:
```json
{
  "enabled": false
}
```

---

## 4. Data Export API (`/api/v1/export/`)

Supports both JSON and CSV streaming for large analytical exports:
- `GET /api/v1/export/trades?format=csv`
- `GET /api/v1/export/equity?format=json`
- `GET /api/v1/export/advisory-log?format=json`
- `GET /api/v1/export/risk-events?format=csv`

---

## 5. Webhooks & Health

### Webhooks (`WEBHOOK_URLS` environment variable)
Dispatches signed JSON payloads with HMAC signatures in `X-Bot-Signature`:
- `trade.opened`, `trade.closed`
- `risk.threshold_warning`, `risk.limit_hit`
- `advisory.recommendation`, `advisory.applied`

### Health Probes:
- `GET /api/v1/health` — Ultra-fast liveness probe (HTTP 200).
- `GET /api/v1/health/detailed` — Resource diagnostics (CPU, Memory, Storage, Trading, Advisory).
- `GET /api/v1/health/integrations` — External connectivity to AI-Universe and Exchange APIs.
