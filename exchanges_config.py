"""
exchanges_config.py — Multi-Exchange Configuration, API Credentials & Capital Allocations.

Features:
1. Environment Variable Parsing for multi-exchange enablement (e.g. EXCHANGES_ENABLED="binance,bybit,okx,coinbase").
2. Per-exchange capital allocation weights (e.g. binance: 50%, bybit: 30%, okx: 20%).
3. Whitelisted trading pairs and strategy mappings per venue.
4. Unified risk limit invariants (unified limits override individual venue limits).
"""

import os
from dataclasses import dataclass, field


@dataclass
class ExchangeConfigSpec:
    exchange_id: str
    enabled: bool = True
    capital_allocation_pct: float = 0.50
    supports_futures: bool = True
    supports_margin: bool = True
    supports_shorting: bool = True
    taker_fee_pct: float = 0.0006
    maker_fee_pct: float = 0.0002
    max_drawdown_limit_pct: float = 0.15
    pairs_whitelist: list[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"])


def load_multi_exchange_config() -> dict[str, ExchangeConfigSpec]:
    """Loads and validates multi-exchange specifications from environment variables."""
    raw_enabled = os.getenv("EXCHANGES_ENABLED", "binance,bybit,okx,coinbase")
    enabled_list = [e.strip().lower() for e in raw_enabled.split(",") if e.strip()]

    specs = {
        "binance": ExchangeConfigSpec(
            exchange_id="binance",
            enabled="binance" in enabled_list,
            capital_allocation_pct=0.50,
            supports_futures=True,
            supports_margin=True,
            supports_shorting=True,
            taker_fee_pct=0.0004,
            maker_fee_pct=0.0002,
            pairs_whitelist=["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT", "XRP/USDT"]
        ),
        "bybit": ExchangeConfigSpec(
            exchange_id="bybit",
            enabled="bybit" in enabled_list,
            capital_allocation_pct=0.25,
            supports_futures=True,
            supports_margin=True,
            supports_shorting=True,
            taker_fee_pct=0.00055,
            maker_fee_pct=0.0002,
            pairs_whitelist=["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
        ),
        "okx": ExchangeConfigSpec(
            exchange_id="okx",
            enabled="okx" in enabled_list,
            capital_allocation_pct=0.15,
            supports_futures=True,
            supports_margin=True,
            supports_shorting=True,
            taker_fee_pct=0.0005,
            maker_fee_pct=0.0002,
            pairs_whitelist=["BTC/USDT", "ETH/USDT", "SOL/USDT", "OKB/USDT"]
        ),
        "coinbase": ExchangeConfigSpec(
            exchange_id="coinbase",
            enabled="coinbase" in enabled_list,
            capital_allocation_pct=0.10,
            supports_futures=False,
            supports_margin=False,
            supports_shorting=False,
            taker_fee_pct=0.006,
            maker_fee_pct=0.004,
            pairs_whitelist=["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        )
    }

    return specs
