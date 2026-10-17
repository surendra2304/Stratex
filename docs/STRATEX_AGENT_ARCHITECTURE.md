# Stratex Agent Architecture

## Design rule

Stratex is a single trading platform, not a collection of independent trading bots. Venue agents own broker-specific connectivity and execution semantics. Strategies emit normalized trade intents; deterministic policy/risk gates approve or reject them; order management persists and reconciles the lifecycle; portfolio and audit components observe resulting positions and fills.

No AI/LLM agent has direct order authority. Inference, Futuris and IntelX are advisory-only inputs. Sentinel/authorization policy and deterministic risk checks cannot be overridden by an LLM recommendation.

## Agent roles

| Agent | Responsibility | Order authority |
| --- | --- | --- |
| Stratex Core Orchestrator | Coordinates lifecycle and configuration | No independent authority |
| Market Data Agent | Normalizes tickers/candles/symbol metadata and freshness | None |
| Strategy Supervisor | Loads existing strategy registry; only validated strategies may propose executable signals | None |
| Risk & Authorization Agent | Deterministic account/portfolio limits, kill switch, order permission | May deny; cannot bypass policy |
| Binance Futures Venue Agent | Selects configured demo/testnet endpoints and owns Binance client credentials | Venue operations only after Core policy |
| IC Markets MT5 Venue Agent | Talks to a demo-only Windows bridge and preserves order/position tickets | Venue operations only after Core policy |
| Portfolio & Exposure Agent | Aggregates balances and exposures without treating one venue's rules as another's | None |
| Order Reconciliation Agent | Resolves intent/ack/fill/open-order/position state after timeouts/restarts | Must not blindly retry an ambiguous order |
| Inference Advisory Agent | Optional LLM/context summaries | None |
| Futuris Forecast Agent | Optional calibrated regime/forecast features | None |
| IntelX Research Agent | Optional source-backed event/news context | None |
| Audit & Monitoring Agent | Records mode, readiness, order lifecycle events, rejections and incidents | None |

The initial /api/agents endpoint returns a safe, credential-free catalog and configuration status. Configured does not mean the remote connection has passed a health check, and it does not mean the venue is authorized to execute orders.

## Binance endpoint modes

- BINANCE_DEMO_TRADING=true: uses new Binance Demo Trading credentials from BINANCE_API_KEY and BINANCE_API_SECRET, pins Spot API access to https://demo-api.binance.com/api and USDⓈ-M Futures REST access to https://demo-fapi.binance.com/fapi.
- BINANCE_DEMO_TRADING=false: preserves legacy Testnet behavior using API_KEY / SECRET_KEY and legacy testnet URLs.
- The two key sets are not interchangeable. When current Demo Trading mode is selected, the agent fails closed if new credentials are missing; it never falls back to legacy variables.
- LIVE_TRADING_ENABLED must remain false. The venue agent rejects live mode and rejects unrecognized endpoint URLs.

Binance REST endpoint references: [USDⓈ-M Futures general information](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info) and [CCXT Binance documentation](https://github.com/ccxt/ccxt/wiki/binance).

## MT5 endpoint modes

The bridge belongs to this repository but is deployed separately beside the MetaTrader 5 terminal, normally on a Windows VPS. It must bind to loopback or a private VPN address, authenticate every request, and refuse non-demo accounts. Do not expose its HTTP port publicly.

The MT5 bridge currently provides a transport/API foundation. Before connecting it to autonomous strategy execution, complete and validate: strategy-to-MT5 order lifecycle, broker-specific symbol/contract mapping, attached stop-loss and take-profit for market orders, current-order/position reconciliation, client-side idempotency across timeouts, accurate commission/swap accounting, retry rules, and end-to-end demo tests.

## Operational stages

1. Validate Binance Demo connection and runtime endpoint selection.
2. Make the existing order lifecycle venue-aware without forking strategy logic.
3. Finish a unified OMS with client IDs, persisted intents, fill-state tracking and reconciliation.
4. Integrate MT5 orders with broker-native attached protection and close-by-ticket semantics.
5. Add per-account risk limits and portfolio aggregation without cross-venue lot-size assumptions.
6. Run paper simulation and adapter contract tests; then a demo-only soak test with restart/disconnect/partial-fill cases.
7. Integrate FRIDAY, Inference, Futuris and IntelX as advisory inputs only. Never expose direct order permissions to an LLM.
8. Keep the live-money path blocked unless a future, separately reviewed design explicitly changes that invariant.

## Secure credential handling

Never commit or chat-post API keys, API secrets, MT5 passwords, bridge tokens or OCI credentials. Set them in the private deployment environment or secret manager. For Binance Demo Trading, use a newly generated Demo Trading key with the smallest permissions needed for demo account/order operations; withdrawals/transfers should remain disabled. MT5 credentials should be entered only into the MT5 terminal on its host.

## Durable OMS foundation

The repository now includes `stratex_agents.order_intent.TradeIntent`, `stratex_agents.order_store.OrderIntentStore` and `stratex_agents.oms.StratexOrderManager`. The manager:

- requires `STRATEX_EXECUTION_MODE=DEMO` before any venue submission;
- rejects live-money mode and enforces a venue allowlist;
- applies an injected deterministic authorization/risk decision and the existing panic/kill-switch state;
- persists an intent before it crosses the venue boundary;
- reuses completed results for an identical client ID and rejects ID reuse with a different payload;
- marks ambiguous submit outcomes `UNKNOWN` and refuses blind retry until reconciliation resolves them;
- intentionally does not automatically fail over from one venue to another.

This is a durable OMS foundation with contract tests, not yet an assertion that every legacy strategy uses the manager. Wiring the existing signal/trailing/protection lifecycle through this manager is a separate integration milestone.


## Binance Futures OMS adapter foundation

`stratex_agents.venue.binance_futures_oms_adapter.BinanceFuturesOMSAdapter` implements the venue submitter contract for market-entry intents in the selected Binance Demo Trading or legacy Futures Testnet environment. It verifies the account position mode, loads the venue's symbol filters and quantity step, checks the configured contract is tradable and the order is above minimum notional, submits with the stable client order ID, and establishes both broker-native stop-loss and take-profit conditional orders using the Futures Algo Order API.

- It is deliberately limited to MARKET entries for this phase; LIMIT and STOP entries are refused until their asynchronous fill and protection lifecycle is implemented.
- It respects One-way and Hedge position mode, using positionSide where required.
- If bracket creation fails after entry, it attempts to close the filled quantity; it cancels an acknowledged stop only after the close is confirmed. If safety close is ambiguous, the stop is preserved and the shared OMS records UNKNOWN.
- The adapter doesn't decide whether a strategy signal should trade. Call it only through the shared OMS after deterministic risk and authorization checks.
- Conditional Futures orders use `futures_create_algo_order`; conditional types moved to the algo API in recent Binance API changes. See [python-binance Futures Algo Order example](https://github.com/sammchardy/python-binance/blob/master/examples/futures_algo_order_examples.py) and [Binance USDⓈ-M Futures order documentation](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order).

This does not yet migrate every legacy `bot.py` entry, trailing stop, close and ledger path through the OMS. Do not treat the adapter foundation as production approval.


## Venue-neutral market-data agent

`stratex_agents.market_data.MarketDataAgent` normalizes instrument metadata, quotes and OHLCV candles from the explicitly selected venue. It preserves exact broker-native symbols (including IC Markets suffixes such as `XAUUSD.a`) and distinguishes Binance crypto perpetuals from MT5 Forex, commodity, index, equity and other CFD classifications based on broker metadata. It does not fabricate missing prices and never falls back to another venue's market data.

The MT5 bridge's authenticated `GET /v1/symbols` endpoint returns broker-native names and contract details such as contract size, profit/margin currencies, digits, tick size/value, minimum/maximum/step volume, stop distance, swap and margin fields. Filter by query or Market Watch visibility when the terminal's full symbol catalogue is large. Market-data endpoints do not submit orders.

Dashboard read-only endpoints (API read scope required):
- `GET /api/v1/market/instruments?venue=all`
- `GET /api/v1/market/quote?venue=mt5&symbol=EURUSD`
- `GET /api/v1/market/candles?venue=mt5&symbol=EURUSD&timeframe=15m&limit=300`

The API returns normalized market values but does not imply a signal is approved or an execution venue is enabled. Current MT5 availability still depends on a configured, authenticated, demo-only Windows bridge.


## Opt-in route from the existing Futures runner to the shared OMS

The existing Futures strategy path now has an explicit, disabled-by-default integration point. `STRATEX_ORDER_MANAGER_ENABLED=false` preserves the legacy executor. When it is explicitly enabled, the Futures entry call requires the current RiskGate and risk context, creates a normalized `TradeIntent`, and sends it through the shared OMS. The OMS calls the same deterministic RiskGate again immediately before submit, checks the execution policy and kill switch, persists the order ID/status, and refuses blind retries for an ambiguous result. Successful protected fills are mirrored to the legacy active-trade store so the existing Futures monitor/trailing loops can see the protective Algo Order IDs.

This flag is **not a production readiness toggle**. Keep `STRATEX_EXECUTION_MODE=DISABLED` and `STRATEX_ORDER_MANAGER_ENABLED=false` until CI is green, Demo Trading keys are installed privately, a Binance demo integration run verifies fills + both protective Algo orders + sibling-order cancellation, and restart/reconciliation tests have passed. MT5 routing remains allowlisted separately and requires its own demo bridge soak test.


## MT5 CFD/Forex risk sizing and broker preflight

The demo bridge now exposes `POST /v1/risk/size`. It uses the connected demo account's equity/free margin and the broker's native `order_calc_profit` / `order_calc_margin` calculations to size lots, rather than applying Binance's base-quantity formula to Forex or CFDs. Before an entry, the bridge rechecks the actual requested volume against the account-currency stop-loss budget and margin budget, then runs `order_check` before reserving/submitting the order. The broker order-check result is a preflight only; it does not guarantee a subsequent fill.

Default environment limits are `MT5_MAX_RISK_PER_TRADE=0.005` (0.5% of equity, hard-capped by code at 2%) and `MT5_MAX_MARGIN_FRACTION=0.20` (20% of equity and never more than half of current free margin; hard-capped by code at 35%). These limits apply per new entry. This does not yet replace the shared portfolio-wide daily-loss/drawdown controller, and no LLM response is allowed to increase them.

Official API references: [MetaTrader 5 `order_calc_profit`](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcprofit_py), [`order_calc_margin`](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcmargin_py), and [`order_check`](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py).


## Opt-in reconciliation worker

`scripts/reconciliation_worker.py` is supervised as a separate, **read-only** child process when both `STRATEX_ORDER_MANAGER_ENABLED=true` and `STRATEX_RECONCILIATION_ENABLED=true`, with `TRADING_MODE=FUTURES`, `STRATEX_EXECUTION_MODE=DEMO`, live-money trading disabled, and a valid venue allowlist. It reuses `STRATEX_ORDER_DB`, polls on `STRATEX_RECONCILIATION_INTERVAL_SECONDS` (default 30 seconds, clamped to 5–300), and records a state only when a venue explicitly confirms it. Confirmed open and partially-filled orders remain queued. Missing order lookups and ambiguous protection state stay unresolved; no automated cross-venue retry is attempted.

The worker is not enabled by default. Leave both flags false until Binance Demo Trading and the MT5 Demo bridge are privately configured and the end-to-end order/protection/restart tests have passed.


## Portfolio-wide risk gate and persisted equity state

The existing strategy-to-OMS path can opt into a second deterministic risk gate with `STRATEX_PORTFOLIO_RISK_ENABLED=true`. Immediately before authorization it refreshes a read-only portfolio snapshot, requires broker-reported account equity for every enabled venue, persists a UTC-day starting equity and high-water mark in `STRATEX_PORTFOLIO_RISK_DB`, and checks daily equity loss, drawdown, open-position count, gross exposure and snapshot freshness. The gate is composed with—not substituted for—the existing Binance risk gate and MT5 broker-native lot/margin evaluator.

The gate fails closed if any venue is degraded, account equity is unavailable, a position has no verified notional, a currency conversion is missing, the candidate's notional is not in the reporting currency, or the snapshot is stale. Binance Futures reports account equity in USDT; when the reporting currency is USD, configure an explicit, timestamped rate in `STRATEX_FX_RATES_JSON`, e.g. `{"USDT":{"rate":1.0,"timestamp":<current Unix epoch seconds>}}`. The rate must be within `STRATEX_FX_MAX_AGE_SECONDS` (default 300 seconds). A value of 1.0 is only an operator-supplied demo assumption, not a live FX quote. MT5 candidate notional must be computed using broker contract metadata and passed in the reporting currency; the system deliberately does not treat a lot as a crypto quantity.

The state store establishes its daily baseline from the first healthy, verified account-equity snapshot for that UTC day. It does not automatically adjust the baseline for deposits/withdrawals or account changes. Use dedicated demo accounts and reset the baseline deliberately if account equity is changed outside trading. The feature remains disabled by default and is not a certification of production/live readiness.


The shared `build_venue_aware_authorizer` factory can compose the existing Binance RiskGate, MT5 broker-aware evaluator, portfolio gate and a per-intent fresh-context provider into one callback for the shared OMS. It fails closed when the context provider raises or a venue-specific risk evaluator is missing.


## Durable OMS audit trail

The SQLite order store now appends an `INTENT_RESERVED` event, every state transition, and repeated valid duplicate requests to `order_audit_events` in the same transaction as the corresponding ledger change. SQLite triggers reject direct updates/deletes of audit rows. Event details use an allowlist of intent/result fields and omit arbitrary intent metadata and credential-like response fields. The store also enforces monotonic lifecycle transitions, so a partial fill cannot regress to an accepted/new state. Back up the OMS and portfolio-risk SQLite files together on a persistent volume.
