"""
portfolio/unified_portfolio.py — Cross-Exchange Aggregator, Exposure Matrix & Portfolio Risk Allocator.

Manages:
1. Cross-exchange equity summation and capital allocation breakdown.
2. Net exposure per asset across all active venues (detects accidental hedging or doubled exposure).
3. Unified risk limits enforcing global portfolio limits over individual venue limits.
4. Drift analysis and rebalance recommendation generation.
"""

from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import datetime

from exchanges.base_exchange import BaseExchange, UnifiedPosition, UnifiedBalance
from exchanges_config import ExchangeConfigSpec, load_multi_exchange_config


@dataclass
class AssetNetExposure:
    symbol: str
    net_quantity: float
    net_notional: float
    exchange_positions: Dict[str, float] = field(default_factory=dict)
    is_hedged: bool = False


class UnifiedPortfolioManager:
    """
    Supervises multi-exchange capital, exposure, and global risk limits.
    """

    def __init__(self, exchanges: Dict[str, BaseExchange]):
        self.exchanges = exchanges
        self.config_specs = load_multi_exchange_config()

    def get_unified_equity(self) -> Dict[str, Any]:
        """Calculates total combined portfolio equity across all connected venues."""
        total_equity = 0.0
        total_free_cash = 0.0
        exchange_balances = {}

        for ex_id, ex in self.exchanges.items():
            try:
                bals = ex.get_balance()
                # Aggregate USD and USDT values
                usdt_val = bals.get("USDT", UnifiedBalance("USDT", 0, 0, 0)).total
                usd_val = bals.get("USD", UnifiedBalance("USD", 0, 0, 0)).total
                ex_total = usdt_val + usd_val
                ex_free = bals.get("USDT", UnifiedBalance("USDT", 0, 0, 0)).free + bals.get("USD", UnifiedBalance("USD", 0, 0, 0)).free
                total_equity += ex_total
                total_free_cash += ex_free
                exchange_balances[ex_id] = {
                    "total_equity": round(ex_total, 2),
                    "free_cash": round(ex_free, 2)
                }
            except Exception:
                exchange_balances[ex_id] = {"total_equity": 0.0, "free_cash": 0.0}

        return {
            "total_portfolio_equity": round(total_equity, 2),
            "total_free_cash": round(total_free_cash, 2),
            "exchange_breakdown": exchange_balances
        }

    def get_cross_exchange_positions(self) -> Dict[str, Any]:
        """Gathers all open positions across all exchanges and computes net asset exposure."""
        all_positions: List[Dict[str, Any]] = []
        asset_exposures: Dict[str, AssetNetExposure] = {}

        for ex_id, ex in self.exchanges.items():
            try:
                positions = ex.get_positions()
                for pos in positions:
                    pos_dict = {
                        "exchange": ex_id,
                        "symbol": pos.symbol,
                        "side": pos.side,
                        "quantity": pos.quantity,
                        "entry_price": pos.entry_price,
                        "mark_price": pos.mark_price,
                        "unrealized_pnl": pos.unrealized_pnl,
                        "notional": round(pos.quantity * pos.mark_price, 2)
                    }
                    all_positions.append(pos_dict)

                    if pos.symbol not in asset_exposures:
                        asset_exposures[pos.symbol] = AssetNetExposure(
                            symbol=pos.symbol,
                            net_quantity=0.0,
                            net_notional=0.0,
                            exchange_positions={}
                        )

                    qty_signed = pos.quantity if pos.side == "LONG" else -pos.quantity
                    asset_exposures[pos.symbol].net_quantity += qty_signed
                    asset_exposures[pos.symbol].net_notional += qty_signed * pos.mark_price
                    asset_exposures[pos.symbol].exchange_positions[ex_id] = qty_signed
            except Exception:
                pass

        # Check for unintended hedging (e.g. Long on Binance, Short on Bybit)
        for sym, exp in asset_exposures.items():
            ex_qtys = list(exp.exchange_positions.values())
            if any(q > 0 for q in ex_qtys) and any(q < 0 for q in ex_qtys):
                exp.is_hedged = True

        return {
            "total_positions_count": len(all_positions),
            "positions_list": all_positions,
            "net_asset_exposures": {
                sym: {
                    "net_quantity": round(exp.net_quantity, 4),
                    "net_notional": round(exp.net_notional, 2),
                    "is_hedged": exp.is_hedged,
                    "exchanges": exp.exchange_positions
                }
                for sym, exp in asset_exposures.items()
            }
        }

    def check_allocation_drift(self, threshold_pct: float = 0.08) -> Tuple[bool, Dict[str, float]]:
        """
        Computes deviation of current exchange balances from target capital allocation percentages.
        Returns: (needs_rebalance, drift_deltas)
        """
        eq_info = self.get_unified_equity()
        total_eq = eq_info["total_portfolio_equity"]
        if total_eq <= 0:
            return False, {}

        drifts = {}
        needs_rebalance = False

        for ex_id, info in eq_info["exchange_breakdown"].items():
            curr_pct = info["total_equity"] / total_eq
            target_pct = self.config_specs.get(ex_id, ExchangeConfigSpec(ex_id)).capital_allocation_pct
            drift = round(abs(curr_pct - target_pct), 4)
            drifts[ex_id] = drift
            if drift >= threshold_pct:
                needs_rebalance = True

        return needs_rebalance, drifts
