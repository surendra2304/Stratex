# Phase 12.1 Security and Integrity Report

## Execution Architecture

The execution architecture has been entirely centralized through the `ExecutionPolicy` class inside `execution.py`. Previously, complicated mode-specific boolean checks were scattered throughout the `place_market_order` function and the exchange client was instantiated conditionally with `testnet=True` for *both* LIVE and TESTNET modes, presenting a significant security risk.

Now:
- **PAPER**: No `binance.client.Client` is ever instantiated. Exchange connections are structurally avoided.
- **TESTNET**: Client is specifically instantiated with `testnet=True`.
- **LIVE**: Client is instantiated purely for production endpoints.

## Permission Matrix

The `ExecutionPolicy.can_place_order()` logic adheres to a strictly defined, unit-tested truth table:

| Mode | Safe Mode | Research | Testnet Enabled | Live Enabled | Result |
| PAPER | true | no | any | any | BLOCK |
| PAPER | false | no | any | any | BLOCK |
| PAPER | any | yes | any | any | BLOCK |
| TESTNET | false | no | false | any | BLOCK |
| TESTNET | false | no | true | any | ALLOW TESTNET PATH |
| TESTNET | any | yes | true | any | BLOCK |
| LIVE | any | yes | any | true | BLOCK |
| LIVE | true | no | any | true | BLOCK |
| LIVE | false | no | any | false | BLOCK |
| LIVE | false | no | any | true | ALLOW LIVE PATH |

## State Corruption Behavior

`active_trades.json` loading now includes a mandatory schema validation ensuring required parameters (`strategy`, `symbol`, `side`, `quantity`, `entry_price`, `oco_id`, `tp_price`, `sl_price`) are well-formed and logical (no negative prices). If the schema check fails or JSON loading raises an exception, the system surfaces a `StateCorruptionError`. This actively blocks `ExecutionPolicy.can_place_order()` to prevent new entries and bubbles up to the dashboard as a "STATE CORRUPTED" health warning rather than silently behaving as if there are zero trades.

In addition, an atomic backup is saved as `active_trades.json.bak` inside the `backup/` directory to prevent state destruction during IO failures.

## Runtime File Policy

To prevent the user's generated simulation states or internal runtime files from being accidentally versioned or merged into GitHub, the `.gitignore` was expanded to automatically ignore `paper_trade_ledger.jsonl`, `bot.log`, and `*.json` artifacts inside the root and specific subdirectories. Previous versions that had polluted the git cache were explicitly untracked utilizing `git rm --cached`.

## Configuration Policy

- Security keys and base configurations are defined securely through the `.env.example` structure, and runtime secrets must not be stored in GitHub.
- Validation checks in `config.py` correctly identify and block contradictory modes immediately upon application startup.

## Test Results

The suite successfully passed **53/53** rigorous assertions, including newly defined tests designed strictly around safety isolation boundaries (`tests/test_safety_gates.py` and `tests/test_paper_safety.py`).

## Remaining Limitations

- Current monitoring scripts expect `PAPER` mode logs to be manually wiped if resetting the paper equity completely.
- In `LIVE` mode, the `ExecutionPolicy` assumes accurate synchronicity. However, we are yet to build live reconciliation loops that match local state against Binance exchange state directly in order to capture external order execution delays. This should be prioritized immediately before Phase 13 or as part of Phase 14 if LIVE is intended.
