# Phase 12.1.2: Data/Execution Separation Report

## 1. Problem Identification
In Phase 12.1.1, we bottlenecked all API communication through `get_exchange_client()`, which dynamically resolved the environment status against `ExecutionPolicy`. However, `RESEARCH_MODE=1` strictly disables execution capabilities. By forcing research scripts to also route through `get_exchange_client()`, we accidentally cut off their ability to obtain historical backtest data. Research requires pure read-only access without exposing execution pathways.

## 2. Architecture & Design
We introduced a strictly isolated abstraction: `MarketDataClient` inside `data_client.py`.
- **ExecutionClient (`get_exchange_client`)**: Remains exclusively inside `execution.py`. Deals only with API Keys tied to trading functionality and is aggressively blocked by `ExecutionPolicy`.
- **MarketDataClient**: A wrapper class solely utilized by analytical routines (`data.py`, `data_loader.py`, `phase10_runner.py`).

## 3. MarketDataClient Implementation
`MarketDataClient` encapsulates the raw python-binance client instance but actively traps standard `getattr` invocations.
- Allowed Methods: `get_historical_klines()`, `get_ticker()`, `get_klines()`, `futures_funding_rate()`.
- Prohibited Methods: `create_order()`, `cancel_order()`, `withdraw()`, etc. Any attempt immediately raises an `AttributeError` explicitly detailing the isolation restriction.

## 4. PAPER Mode Safety
When `TRADING_MODE=PAPER`, `MarketDataClient` explicitly defaults to a `DATA_UNAVAILABLE` internal state and avoids connecting to the Binance network. Legacy fake fallback data vectors (e.g., arbitrarily returning `["BTCUSDT", "ETHUSDT", "SOLUSDT"]` when disconnected) were stripped out in favor of clean error handling and empty data frames, preventing any confusion regarding the origin of signal data.

## 5. RESEARCH Mode Operations
In `RESEARCH` or `TESTNET`/`LIVE` setups, the adapter initializes an anonymous/read-only Testnet connection. We inject `df.attrs['data_source'] = "BINANCE_READ_ONLY"` directly into resulting data structures to ensure trace reproducibility, removing the ambiguity of whether data originated from local caches, synthetics, or live fetching. 

## 6. Testing & Validation
We created `tests/test_data_client.py`:
- Validated that `MarketDataClient` successfully rejects injection of `create_order` and network routing capabilities.
- Proved `DATA_UNAVAILABLE` triggers cleanly during strictly locked modes.
- Added comprehensive unit proofs across `test_safety_gates.py`, successfully maintaining 0 constructor initializations for `ExecutionClient` paths while proving data clients remain decoupled.
- The entire Pytest suite passed perfectly (60 out of 60).

## 7. Remaining Limitations
Scripts currently executing without local caches explicitly default to `DATA_UNAVAILABLE` under Paper configurations. When operating fully disconnected tests on live models, strategies that blindly ask `MarketDataClient` for ticks will deliberately halt. We maintain this as a safety paradigm to distinguish between cached test vectors and unvalidated local environments.
