# STRATEX — CCXT Unified Exchange & Market Data Integration

## 1. Overview & Objectives

This document details the **CCXT Exchange Integration** into the STRATEX platform.

The integration establishes a unified exchange abstraction and market-data acquisition layer without compromising Stratex's existing trading intelligence, risk management, or execution safety:
- **Unified Market Data**: Standardized OHLCV candles, real-time tickers, and order books.
- **Market Metadata & Precision**: Dynamic extraction of tick sizes, step sizes, minimum order amounts, and minimum notionals.
- **Symbol Normalization**: Bidirectional translation between Stratex format (`BTCUSDT`) and CCXT unified format (`BTC/USDT`).
- **Exchange Error Normalization**: Categorized exception mapping (`RATE_LIMIT`, `NETWORK_ERROR`, `INVALID_ORDER`, etc.).
- **Rate Limiting & Ambiguous Error Handling**: CCXT built-in rate limiter; strict prohibition against blind retries of unconfirmed orders.
- **Multi-Exchange Architecture**: Architectural readiness for Binance, Bybit, OKX, Kraken, and Coinbase.
- **Strict Execution Safety**: Permanent LIVE trading blocks, PAPER mode isolation, and authoritative Stratex `ExecutionPolicy` precedence.

---

## 2. Architecture & Request Pipeline

```
                               ┌────────────────────────────────┐
                               │       STRATEX Core Engine      │
                               │   (Strategies, Scanner, Mkt)   │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │   Market Data Abstraction      │
                               │ (MarketDataClient / CCXTAdap)  │
                               └───────┬────────────────┬───────┘
                                       │                │
                        ┌──────────────┘                └──────────────┐
                        ▼                                              ▼
         ┌─────────────────────────────┐                ┌─────────────────────────────┐
         │     CCXT Public Feed        │                │    Binance REST / WS Feed   │
         │ (OHLCV, Tickers, Precision) │                │  (Direct Low-Latency Public)│
         └──────────────┬──────────────┘                └──────────────┬──────────────┘
                        │                                              │
                        └──────────────────────┬───────────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │    Signal Generation Pipeline  │
                               │  (ADX+EMA, Supertrend, ML, ...)│
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │      Profitability Gate        │
                               │  (Expected Net Edge vs Taker)  │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │   Pre-Trade Protection Layer   │
                               │   (Cooldowns, Loss/DD Guards)  │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │           Risk Gate            │
                               │  (Position Sizing, Exposures)  │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │      Execution Policy          │
                               │   (TESTNET ONLY / LIVE BLOCKED)│
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │    Exchange Execution Layer    │
                               │ (CCXT / Binance Testnet Route) │
                               └────────────────────────────────┘
```

---

## 3. Key Components

### A. Normalized Models (`stratex_ccxt_adapter/models.py`)
- `NormalizedMarket`: Contains base, quote, active status, min/max amounts, min cost/notional, and precision steps.
- `NormalizedTicker`: Bid, ask, last, base/quote volume, and timestamp.
- `NormalizedOrder`: Normalized order representation with client order IDs, filled/remaining amounts, average fill price, and fees.

### B. Precision & Limits (`stratex_ccxt_adapter/precision.py`)
- `floor_step(value, step)`: Floor value to exact exchange step sizes (avoids LOT_SIZE rejection).
- `round_price(price, precision, step)`: Quantizes limit prices to tick size.
- `round_amount(amount, precision, step)`: Quantizes quantity to lot size.
- `validate_market_order(amount, price, market)`: Verifies min amount, max amount, and min notional.

### C. Error Categorization (`stratex_ccxt_adapter/errors.py`)
Maps heterogeneous exchange error classes into normalized Stratex error categories:
- `AUTHENTICATION_ERROR`
- `RATE_LIMIT`
- `NETWORK_ERROR`
- `INVALID_ORDER`
- `BAD_SYMBOL`
- `NOT_SUPPORTED`
- `BAD_REQUEST`
- `EXCHANGE_ERROR`

### D. Safety & Anti-Duplication Guarantees
1. **Blind Retry Prohibition**: Order creation never retries automatically on ambiguous network timeouts. If network status is uncertain, the system marks the attempt for reconciliation to prevent duplicate fills.
2. **Authoritative Safety Gates**: Strategy code cannot call `CCXTExchangeAdapter.create_order()` directly. It must pass through `authorize_fn` and `ExecutionPolicy`.
3. **Permanent LIVE Trading Block**: Any attempt to set `TRADING_MODE=LIVE` or `LIVE_TRADING_ENABLED=True` raises an immediate `PermissionError`.
