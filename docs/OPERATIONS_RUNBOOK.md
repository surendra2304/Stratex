# Production Operations Runbook

## Overview
This runbook defines daily, weekly, monthly, and emergency operational protocols for operators and automated supervisors managing the quantitative trading bot.

---

## 1. Daily Operations Checklist

### Pre-Market / Daily Roll (00:00 UTC)
1. **Heartbeat & Process Verification**: Verify `bot.pid` exists and process is active via `GET /api/health/system`.
2. **Reconciliation Audit**: Check `forward_reconciliation.jsonl` to ensure exchange reported balance matches local ledger balance within 0.1% tolerance.
3. **Daily Risk Reset**: Confirm daily loss counter reset to \$0.00 for the new UTC day.

### Continuous Market Monitoring
- Monitor active alert streams via `GET /api/alerts`.
- Inspect Prometheus metrics endpoint (`GET /api/metrics`) for CPU/Memory spikes ($> 80\%$) or WebSocket reconnect events.
- Audit AI-Universe consultation latency (target: $< 2000$ ms).

### End-of-Day Review (23:55 UTC)
1. Review closed trade ledger PnL (`GET /api/testnet/advisory/log`).
2. Verify atomic state backups created in `state_backups/`.

---

## 2. Weekly & Monthly Maintenance

### Weekly Review
- Compute strategy Sharpe ratios and PnL attribution (`analytics/performance_attribution.py`).
- Check weight drift across active strategies and evaluate rebalancing triggers.
- Run test suite regression check: `pytest tests/ -q`.

### Monthly Review
- Full walk-forward analysis on historical data (`backtest/advanced_backtester.py`).
- Perform cryptographic HMAC audit chain verification on `production_audit_log.jsonl`.
- Review and rotate API keys.

---

## 3. Emergency Procedures

### Protocol 1: Critical Drawdown Trip ($\ge 15.0\%$)
- **Trigger**: Account drawdown reaches 15.0%.
- **Automated Response**: Circuit breaker trips, all parameter overrides are rolled back to baseline defaults (`overlay.reset_to_defaults()`), and new orders are rejected.
- **Operator Action**: Inspect `production_alerts.jsonl` and analyze market regime breakdown before resetting.

### Protocol 2: Emergency Panic Kill-Switch
To immediately cancel all pending orders and stop the bot daemon:
```bash
curl -X POST http://localhost:5000/api/panic -H "X-BOT-API-KEY: YOUR_BOT_API_KEY"
```

### Protocol 3: Network Disconnect / Stale Feed
If WebSocket stream fails for $> 60$ seconds, the `ReliabilityHardener` automatically activates exponential backoff reconnection. If disconnected $> 5$ minutes, trading engine enters graceful degradation and rejects new entries.
