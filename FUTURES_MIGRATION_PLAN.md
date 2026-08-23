# Futures Testnet Migration Plan

## 1. Executive Summary & Objective

In our extensive out-of-sample (OOS) research across 2021–2026 Binance historical data (`research/upgrade_2026_08/`), trend-following strategies on technical timeframes demonstrated that **short positions generate substantial risk-adjusted alpha (OOS Profit Factor 3.14 on short crossovers vs long-only spot variants)** during crypto bear and distribution regimes.

Because **Binance Spot Testnet is fundamentally long-only (`LONG_ONLY = True`)**, all bearish golden cross breakdowns (`EMA20 < EMA50` with `Close < EMA200`) are blocked by exchange architectural constraints.

This document outlines the complete architectural, execution, risk, and data model migration required to transition the trading engine from **Binance Spot Testnet** to **Binance USDⓈ-M Futures Testnet** (`fapi.binance.com` / `testnet.binancefuture.com`) to unlock bidirectional execution.

---

## 2. Key Differences: Spot vs. USDⓈ-M Futures

| Feature | Binance Spot Testnet | Binance USDⓈ-M Futures Testnet |
| :--- | :--- | :--- |
| **Execution Direction** | Long-Only (`BUY` to open, `SELL` to close) | Bidirectional (`BUY` to open Long, `SELL` to open Short) |
| **Short Selling** | Not supported (requires margin borrow) | Native (contracts settled in USDT) |
| **API Endpoints (REST)** | `https://testnet.binance.vision/api/v3` | `https://testnet.binancefuture.com/fapi/v1` |
| **WebSocket Stream** | `wss://ws-api.testnet.binance.vision/ws-api/v3` | `wss://stream.binancefuture.com/ws` |
| **Python SDK Methods** | `client.create_order`, `client.create_oco_order` | `client.futures_create_order` |
| **TP/SL Mechanism** | Spot OCO (`POST /api/v3/orderList/oco`) | Conditional Algorithmic Orders (`STOP_MARKET`, `TAKE_PROFIT_MARKET` with `closePosition=True` or `reduceOnly=True`) |
| **Position Mode** | One-way asset balances (base asset qty) | One-Way Mode or Hedge Mode (`dualSidePosition`) |
| **Margin / Leverage** | 1x Notional Equity | Configurable (1x–125x; default locked to **1x / 2x** for safety) |
| **Funding Rates** | None | Periodic 8-hour funding fee settlement (`fapi/v1/fundingRate`) |
| **Balance Accounting** | Free USDT + Base Asset Value | Wallet Balance + Margin Balance + Unrealized PnL |

---

## 3. Required File Modifications & Component Changes

### 3.1 `config.py` & `config_strategy.py`
- **`LONG_ONLY`**: Change from `True` to `False` (enable bidirectional signal dispatch).
- **`BASE_URL` & `WS_URL`**: Update to USDⓈ-M Futures Testnet endpoints:
  - `FUTURES_BASE_URL = "https://testnet.binancefuture.com"`
  - `FUTURES_WS_URL = "wss://stream.binancefuture.com/ws"`
- **Futures Risk Parameters**:
  - `DEFAULT_LEVERAGE = 1` (strict 1x non-leveraged or maximum 2x conservative cap).
  - `MARGIN_TYPE = "ISOLATED"` (strict isolated margin per position; never cross-margin to prevent systemic liquidation).
  - `POSITION_MODE = "ONE_WAY"` (simplified single directional state per symbol).

### 3.2 `data_client.py` & `testnet_engine/market_scanner.py`
- **REST Endpoint Proxy**:
  - Update `get_klines` to call `client.futures_klines`.
  - Update `get_symbol_ticker` to call `client.futures_symbol_ticker`.
  - Update `get_exchange_info` to call `client.futures_exchange_info`.
- **WebSocket Manager**:
  - Update `ThreadedWebsocketManager` initialization or use `BinanceSocketManager` with futures endpoints for continuous multi-symbol candle delivery.

### 3.3 `testnet_engine/protection.py`
- **Replace Spot OCO with Futures Conditional Orders**:
  - Spot OCO (`client.create_oco_order` with `aboveType`/`belowType`) does not exist in `fapi`.
  - In USDⓈ-M Futures, TP and SL are placed as dual conditional orders:
    ```python
    # Stop Loss Order (e.g. for LONG position)
    client.futures_create_order(
        symbol=symbol,
        side="SELL",
        type="STOP_MARKET",
        stopPrice=round_price(sl_price),
        closePosition=True,
    )
    # Take Profit Order
    client.futures_create_order(
        symbol=symbol,
        side="SELL",
        type="TAKE_PROFIT_MARKET",
        stopPrice=round_price(tp_price),
        closePosition=True,
    )
    ```
  - For `SHORT` positions, `side="BUY"` with `closePosition=True`.

### 3.4 `execution.py` & `testnet_engine/service.py`
- **Order Placement**:
  - `place_market_order`: Call `client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=qty)`.
  - Ensure `side` properly accepts `BUY` (for Long entry or Short exit) and `SELL` (for Short entry or Long exit).
- **Position Tracking**:
  - Track `positionSide` ("BOTH" in one-way mode, or "LONG"/"SHORT" in hedge mode).
  - Query active open positions using `client.futures_position_information()`.
  - Distinguish entry vs. exit order fills and calculate realized PnL from `futures_account_trades`.

### 3.5 Accounting & Risk Gate (`testnet_engine/risk_gate.py`, `paper_engine/portfolio.py`)
- **Unrealized PnL Calculation**:
  - For Long: $\text{UPnL} = (\text{Mark Price} - \text{Entry Price}) \times \text{Quantity}$
  - For Short: $\text{UPnL} = (\text{Entry Price} - \text{Mark Price}) \times \text{Quantity}$
- **Invariant Guarantee**:
  $$\text{Total Equity} = \text{Wallet Balance (USDT)} + \text{Unrealized PnL}$$
- **Funding Fee Deductions**:
  - Account for 8-hour funding payments in realized PnL ledger.

---

## 4. Risk Governance & Safety Protocols

1. **Leverage Ceiling**: Enforce a strict programmatic ceiling of **1x to 2x leverage**. Liquidations are unacceptable in systematic trend-following.
2. **Isolated Margin**: Hard-code `MARGIN_TYPE = "ISOLATED"` on every symbol initialization via `client.futures_change_margin_type(symbol=symbol, marginType="ISOLATED")`.
3. **Execution Policy Invariant**: `LIVE_TRADING_ENABLED = False` remains permanently non-negotiable. Only `testnet.binancefuture.com` credentials and environments are authorized.
4. **Funding Rate Filter**: Reject trade signals if the 8-hour funding rate exceeds $\pm 0.05\%$ against the direction of the trade (cost-of-carry gate).

---

## 5. Migration Roadmap & Checklist

- [ ] **Phase 1 (Harness & Data)**: Add futures market data client and download historical USDⓈ-M futures klines.
- [ ] **Phase 2 (Protection & Execution)**: Implement `futures_create_order` with `STOP_MARKET` / `TAKE_PROFIT_MARKET` conditional bracket orders in `protection.py`.
- [ ] **Phase 3 (State & Reconciler)**: Update `testnet_engine/service.py` position reconstruction for `futures_position_information` and negative-quantity short tracking.
- [ ] **Phase 4 (Risk & Telemetry)**: Update `TelemetryManager` and `dashboard.py` to display Long/Short badges and leverage metrics.
- [ ] **Phase 5 (Staging Testnet Soak)**: Execute 48-hour continuous bidirectional paper/testnet validation on Binance Futures Testnet.
