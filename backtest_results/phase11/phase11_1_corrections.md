# Phase 11.1: Paper Engine Corrections

## Objective
The goal of Phase 11.1 was to harden the Paper Trading Engine to be robust, perfectly isolated from the exchange, and statistically accurate. 

## Key Fixes & Enhancements

1. **Strict Double-Count Prevention**
   - The authoritative metric is now strictly `EQUITY = cash + unrealized PnL`.
   - Realized PnL simply updates `cash`. Dashboard and portfolio views explicitly avoid summing realized PnL twice.
   - PnL metrics like Win Rate and Profit Factor are now calculated exclusively on fully closed trades parsed from the durable `paper_trade_ledger.jsonl`.

2. **Durable Trade Ledger & Equity Snapshotting**
   - Introduced `paper_trade_ledger.jsonl`. Closed positions emit an immutable dictionary containing precise fields (gross, net, fees, funding).
   - Introduced `paper_equity_curve.jsonl` which allows independent tracking of Max Drawdown, eliminating arbitrary session resets.

3. **Data Health Strict Validation**
   - `MarketDataFeed` explicitly prevents backward/duplicate timestamps, nan/inf prices, and inverted spreads.
   - Separate `received_timestamp` and `market_timestamp` tracking perfectly segregates internal staleness from market delay.

4. **Multi-Leg Simulator Robustness**
   - Funding and Pairs simulators explicitly account for Leg A vs Leg B partial fills, gracefully entering `UNHEDGED` status rather than blindly assuming 100% matched execution. 
   - Notional sizing handles spread discrepancy directly.

5. **Heartbeat State Monitoring**
   - `HeartbeatState` (`heartbeat.json`) persistently logs process and data freshness. The dashboard now properly reports `STALE` or `OFFLINE` instead of defaulting to OK.

6. **Research Execution Guard**
   - `execution.py` blocks the `place_market_order` API if imported by any file with `research` or `backtest` in the path, preventing accidental live orders during iteration.

## Acceptance Test Results
- Ran 27-step Acceptance Scenario.
- `pytest tests/test_phase11_1_acceptance.py` passed with 0 failures.
- Zero `binance.client` side effects verified.

## Conclusion
The Forward-Validation module is fully corrected and hardened. The system is ready to operate safely.
