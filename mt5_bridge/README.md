# Stratex MT5 Demo Bridge

This bridge is a **separate runtime component inside the Stratex repository**, not a separate strategy bot or repository. It runs on the same Windows machine/VPS as the IC Markets MT5 terminal because the official MetaTrader5 Python package communicates with a local terminal.

## Safety model

- Demo account only by default. The bridge refuses accounts whose server name does not contain `demo`, and checks the account is not marked as real.
- The HTTP server binds to `127.0.0.1` by default. Do not expose it directly to the public internet. For remote Stratex Core access, use a private VPN (for example, Tailscale) and bind only to the private interface.
- Every request requires `Authorization: Bearer <token>`.
- Live trading is intentionally unsupported by this bridge.
- Keep MT5 terminal and bridge logs private; never commit credentials or tokens.

## Runtime

Use a Windows VPS with the IC Markets demo account logged in to the MT5 desktop terminal. Install Python 3.11, then install the optional dependency `MetaTrader5` on that Windows host. Configure a long random token, the expected demo server and a private bind address. Run `python -m mt5_bridge.server`.

Stratex Core connects using:
- `MT5_BRIDGE_ENABLED=true`
- `MT5_REQUIRE_DEMO=true`
- `MT5_BRIDGE_URL=http://<private-vpn-ip>:8765`
- `MT5_BRIDGE_TOKEN=<same-long-random-token>`

The bridge must be reachable only over the private VPN/firewall. Do not forward port 8765 from the public internet.

## Current scope

The API contract is deliberately small: health/account, positions, ticker, orderbook, candles, fee metadata, order placement and pending-order cancellation. Symbol mapping and broker-specific volume/stop constraints must be validated against IC Markets demo before enabling any automated order flow. The bridge is not a guarantee of profitable trading.
