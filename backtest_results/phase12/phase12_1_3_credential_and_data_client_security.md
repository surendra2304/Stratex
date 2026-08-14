# Phase 12.1.3: Credential Security and Read-Only Client Cleanup

## Credential Audit Result: CREDENTIALS_FOUND = 0 (after cleanup)

> [!CAUTION]
> **Actual Binance Testnet API credentials were found hardcoded in the repository since the initial commit (bd10680).**
> They appeared in: `test_connection.py` (lines 3–4) and `config.py` (lines 8–9).
> These keys have been removed from source AND from the entire git history.
> **ACTION REQUIRED: Revoke and rotate these Binance Testnet API keys immediately via the Binance Testnet portal. Do NOT reuse them.**

---

## What Was Fixed

### 1. Credential Removal from Source
- `test_connection.py`: Rewritten from scratch — hardcoded API_KEY/SECRET_KEY lines removed, now uses `AccountClient` and `MarketDataClient` with no credentials embedded.
- `config.py`: Lines 8–9 replaced. `API_KEY` and `SECRET_KEY` now load exclusively from `os.getenv("API_KEY", "")` and `os.getenv("SECRET_KEY", "")`. No credential defaults. Validation no longer raises on empty keys in PAPER mode.

### 2. Git History Rewrite
Used `git-filter-repo --replace-text` to scrub both credential strings from every commit in the repository's history. Verified with:
```
git log --all -S "<API_KEY_VALUE>" --oneline  → (empty — no commits found)
```
Force-pushed the rewritten history to GitHub (`0361697...a6099e8`).

### 3. MarketDataClient Architecture (data_client.py)
- **No trading credentials**: Constructor now uses `Client("", "", testnet=True)` — unauthenticated connection to Binance public endpoints (ticker, klines, funding rates do not require auth).
- **No `API_KEY` or `SECRET_KEY` imports**.
- **No `_client` exposure**: internal attribute renamed to `__client` (name-mangled), no `get_client()` escape hatch.
- **Explicit method whitelist only**: Approved methods defined explicitly, `__getattr__` blocks everything else by name.
- **Data source label**: `BINANCE_TESTNET_READ_ONLY` (non-PAPER) or `DATA_UNAVAILABLE` (PAPER).

### 4. AccountClient Architecture (account_client.py) — NEW
- New isolated class for read-only account diagnostics (`get_account`, `get_balances`, `get_open_orders`).
- Explicitly blocks: `create_order`, `cancel_order`, `withdraw`, `transfer`.
- Completely separate from both `MarketDataClient` and `ExecutionClient`.

### 5. ExecutionClient Architecture (execution.py)
- Unchanged — continues to be gated exclusively by `ExecutionPolicy`.
- `PAPER` / `RESEARCH` → `None`.
- `TESTNET` → requires `TESTNET_ENABLED=true`.
- `LIVE` → requires `LIVE_TRADING_ENABLED=true`.

### 6. Diagnostic Scripts Fixed
- `test_connection.py`: Uses `AccountClient` for balance check, `MarketDataClient` for price check. No credentials.
- `status_check.py`: Uses `AccountClient` + `MarketDataClient`. Removed `API_KEY/SECRET_KEY` imports.

### 7. Research Module Cleanup
Removed unused `API_KEY, SECRET_KEY` imports from:
- `data.py`
- `research_phase7/data_loader.py`
- `research_phase9/funding_research.py`
- `backtester.py`

### 8. Credential Regression Tests (tests/test_credentials.py) — NEW
6 tests added:
- `test_no_hardcoded_credentials_in_source` — scans all `.py` files for literal API_KEY/SECRET_KEY assignments
- `test_env_example_contains_only_placeholders` — verifies `.env.example` only contains `YOUR_*` values
- `test_market_data_client_no_credentials` — inspects `MarketDataClient.__init__` source for credential references
- `test_market_data_client_no_execution_methods` — blocks `create_order`, `cancel_order`, `withdraw`, `get_account`
- `test_account_client_no_execution_methods` — blocks `create_order`, `cancel_order`, `withdraw`, `transfer`
- `test_no_credentials_in_test_files` — scans all test files for hardcoded credentials

## Final Test Results
```
66 passed, 3 warnings in 5.94s
```
**CREDENTIALS_FOUND = 0**

## Client Architecture Summary
```
Research / Data scripts
    └─► MarketDataClient (data_client.py)
          - No credentials
          - Public Binance endpoints only
          - Explicit method whitelist
          - PAPER → DATA_UNAVAILABLE

Account Diagnostics
    └─► AccountClient (account_client.py)
          - Credentials via .env only
          - get_account / get_balances / get_open_orders
          - No order execution

Execution (trading only)
    └─► get_exchange_client() (execution.py)
          - Gated by ExecutionPolicy
          - PAPER → None
          - RESEARCH → None
          - TESTNET → requires TESTNET_ENABLED=true
          - LIVE → requires LIVE_TRADING_ENABLED=true
```

## Remaining Action Required (User Must Complete)
> [!CAUTION]
> You must revoke the Binance Testnet API credentials that were exposed:
> 1. Go to https://testnet.binance.vision
> 2. Delete the API key that was exposed (the one starting with `fNW4...`)
> 3. Generate new Testnet credentials
> 4. Place them in your local `.env` file ONLY — never in source code
> 5. Verify the old keys are fully deactivated
