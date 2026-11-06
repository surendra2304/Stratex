#!/usr/bin/env python3
"""
verify_binance_key.py — Validate a Binance Futures API key end-to-end.

Usage:
    python verify_binance_key.py <API_KEY> <API_SECRET> [demo|testnet|live]

Checks, in order:
  1. Server time + clock offset
  2. Signed request: /fapi/v2/balance
  3. Signed request: /fapi/v2/positionRisk
  4. Public data: klines for BTCUSDT
  5. Signed order validation: /fapi/v1/order/test (does NOT place an order)

Nothing is traded by this script.
"""
import os
import sys
import json

sys.path.insert(0, "/opt/friday_universe/Stratex")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: verify_binance_key.py <API_KEY> <API_SECRET> [demo|testnet|live]")
        return 2

    api_key = sys.argv[1].strip()
    api_secret = sys.argv[2].strip()
    env = (sys.argv[3] if len(sys.argv) > 3 else "demo").strip().lower()

    os.environ["BINANCE_API_KEY"] = api_key
    os.environ["BINANCE_API_SECRET"] = api_secret
    os.environ["BINANCE_ENV"] = env

    from exchanges.binance_futures_adapter import BinanceFuturesAdapter

    print(f"== Environment: {env} ==")
    adapter = BinanceFuturesAdapter(
        api_key=api_key,
        secret_key=api_secret,
        testnet=(env != "live"),
    )
    print(f"base_url       : {adapter.base_url}")
    print(f"time offset    : {adapter._time_offset} ms")

    ok = True

    # 1. Balance (signed)
    balances = adapter.get_balance()
    if balances:
        print("\n-- Balance (signed /fapi/v2/balance) --")
        for asset, bal in balances.items():
            print(f"  {asset:8s} total={bal.total:<14.4f} free={bal.free:<14.4f}")
    else:
        print("\n!! Balance FAILED (bad key/secret, or no permission)")
        ok = False

    # 2. Positions (signed)
    positions = adapter.get_positions()
    if ok:
        print(f"\n-- Open positions: {len(positions)} --")
        for p in positions:
            print(f"  {p.symbol} {p.side} qty={p.quantity} entry={p.entry_price} pnl={p.unrealized_pnl}")

    # 3. Public candles
    candles = adapter.get_historical_data("BTCUSDT", "1h", 5)
    print(f"\n-- Candles BTCUSDT 1h: {len(candles)} --")
    if candles:
        print(f"  last close: {candles[-1]['close']}")

    # 4. Order validation (signed, no execution)
    if ok:
        payload = {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": "0.002",
        }
        res = adapter._post("/fapi/v1/order/test", signed=True, params=payload)
        print(f"\n-- /fapi/v1/order/test: {'PASS' if res is not None else 'FAIL'} --")

    print("\nRESULT:", "KEY OK" if ok else "KEY INVALID")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
