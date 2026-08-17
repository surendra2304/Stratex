# GitHub Copilot & AI Agent Instructions

When generating code, analyzing bugs, or answering questions for this repository, AI coding assistants (like GitHub Copilot) **MUST** adhere strictly to the following rules:

## 1. Safety & Security First
- **Testnet Only**: This system is hardcoded to operate on the Binance Spot Testnet. You must **NEVER** write code, suggest changes, or provide instructions that override this safety mechanism to enable live, real-money trading.
- **Secret Management**: You must **NEVER** suggest committing `.env` files, logging API keys, or exposing credentials in plaintext anywhere.

## 2. Risk Control Integrity
- **Do Not Bypass Gates**: If the system is not placing trades, do **NOT** suggest bypassing the `ProfitabilityGate` or `RiskGate`. If a strategy's expected edge does not overcome the taker fees, it should rightly be rejected. Do not weaken risk controls merely to artificially generate trade volume.
- **Do Not Fabricate Data**: Never fabricate, mock, or hardcode market data in the core engine simply to force tests to pass.

## 3. Production Verification
- **Local Tests Are Insufficient**: Do not claim that production health is perfect based solely on passing local `pytest` suites. Real environmental constraints (network latency, Render deployment specifics) exist.
- **Check Live Endpoints**: Always advise developers to check the live Render endpoints (e.g., `/api/status`, `/api/scanner`, `/health`) and Render logs when debugging production issues.

## 4. Testing Protocols
- **Run Tests Twice**: When significantly refactoring the codebase, you must advise the developer to run `pytest tests/ -q` **twice**. The first run tests cold-start behavior; the second run ensures that persisted state (JSON ledgers, open positions) was saved safely and can be successfully re-hydrated without duplication errors.

## 5. Architectural Truth
- Treat `AGENTS.md`, `docs/LIVE_SYSTEM.md`, and `docs/AI_DEBUGGING.md` as the canonical sources of truth for the system's architecture, trading pipeline, and deployment strategy. Refer to them when orienting yourself in the repository.
