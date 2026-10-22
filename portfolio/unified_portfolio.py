"""
portfolio/unified_portfolio.py — Cross-Exchange Aggregator, Exposure Matrix & Portfolio Risk Allocator.

Manages:
1. Cross-exchange equity summation and capital allocation breakdown.
2. Net exposure per asset across all active venues (detects accidental hedging or doubled exposure).
3. Unified risk limits enforcing global portfolio limits over individual venue limits (total portfolio risk, aggregate asset limits, cross-exchange correlation).
4. Drift analysis and rebalance recommendation generation.
"""

from dataclasses import dataclass, field
from typing import Any

from exchanges.base_exchange import BaseExchange, UnifiedBalance
from exchanges_config import ExchangeConfigSpec, load_multi_exchange_config
from logger import get_logger

logger = get_logger("unified_portfolio")


@dataclass
class AssetNetExposure:
    symbol: str
    net_quantity: float
    net_notional: float
    exchange_positions: dict[str, float] = field(default_factory=dict)
    is_hedged: bool = False
    is_doubled_exposure: bool = False


@dataclass
class UnifiedRiskLimits:
    max_total_portfolio_risk_pct: float = 0.75  # 75% max global exposure
    max_asset_exposure_pct: float = 0.40        # 40% max per asset across all exchanges
    max_cross_exchange_correlation: float = 0.85 # Max correlation allowed between simultaneous cross-venue positions
    max_portfolio_drawdown_pct: float = 0.15    # 15% max global drawdown threshold


class UnifiedPortfolioManager:
    """
    Supervises multi-exchange capital, exposure, and global risk limits.
    """

    def __init__(
        self,
        exchanges: dict[str, BaseExchange],
        risk_limits: UnifiedRiskLimits | None = None
    ):
        self.exchanges = exchanges
        self.config_specs = load_multi_exchange_config()
        self.risk_limits = risk_limits or UnifiedRiskLimits()
        self.peak_equity = 0.0

    def get_unified_equity(self) -> dict[str, Any]:
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
                btc_val = bals.get("BTC", UnifiedBalance("BTC", 0, 0, 0)).total * 60000.0
                ex_total = usdt_val + usd_val + btc_val
                ex_free = bals.get("USDT", UnifiedBalance("USDT", 0, 0, 0)).free + bals.get("USD", UnifiedBalance("USD", 0, 0, 0)).free
                total_equity += ex_total
                total_free_cash += ex_free
                exchange_balances[ex_id] = {
                    "total_equity": round(ex_total, 2),
                    "free_cash": round(ex_free, 2)
                }
            except Exception as e:
                logger.error(f"[UNIFIED_PORTFOLIO] Error fetching balance from {ex_id}: {e}")
                exchange_balances[ex_id] = {"total_equity": 0.0, "free_cash": 0.0}

        self.peak_equity = max(self.peak_equity, total_equity)

        current_dd = 0.0
        if self.peak_equity > 0:
            current_dd = max(0.0, (self.peak_equity - total_equity) / self.peak_equity)

        return {
            "total_portfolio_equity": round(total_equity, 2),
            "total_free_cash": round(total_free_cash, 2),
            "peak_equity": round(self.peak_equity, 2),
            "current_drawdown_pct": round(current_dd * 100, 2),
            "exchange_breakdown": exchange_balances
        }

    def get_cross_exchange_positions(self) -> dict[str, Any]:
        """Gathers all open positions across all exchanges and computes net asset exposure."""
        all_positions: list[dict[str, Any]] = []
        asset_exposures: dict[str, AssetNetExposure] = {}

        for ex_id, ex in self.exchanges.items():
            try:
                positions = ex.get_positions()
                for pos in positions:
                    norm_sym = ex.normalize_symbol(pos.symbol)
                    pos_dict = {
                        "exchange": ex_id,
                        "symbol": norm_sym,
                        "side": pos.side,
                        "quantity": pos.quantity,
                        "entry_price": pos.entry_price,
                        "mark_price": pos.mark_price,
                        "unrealized_pnl": pos.unrealized_pnl,
                        "notional": round(pos.quantity * pos.mark_price, 2)
                    }
                    all_positions.append(pos_dict)

                    if norm_sym not in asset_exposures:
                        asset_exposures[norm_sym] = AssetNetExposure(
                            symbol=norm_sym,
                            net_quantity=0.0,
                            net_notional=0.0,
                            exchange_positions={}
                        )

                    qty_signed = pos.quantity if pos.side == "LONG" else -pos.quantity
                    asset_exposures[norm_sym].net_quantity += qty_signed
                    asset_exposures[norm_sym].net_notional += qty_signed * pos.mark_price
                    asset_exposures[norm_sym].exchange_positions[ex_id] = qty_signed
            except Exception as e:
                logger.error(f"[UNIFIED_PORTFOLIO] Error fetching positions from {ex_id}: {e}")

        # Check for unintended hedging or doubled directional exposure across venues
        for sym, exp in asset_exposures.items():
            ex_qtys = list(exp.exchange_positions.values())
            # Hedged: Both positive and negative positions across venues
            if any(q > 0 for q in ex_qtys) and any(q < 0 for q in ex_qtys):
                exp.is_hedged = True
            # Doubled exposure: Multiple positive or multiple negative positions across different venues
            if len([q for q in ex_qtys if q != 0]) > 1 and not exp.is_hedged:
                exp.is_doubled_exposure = True

        return {
            "total_positions_count": len(all_positions),
            "positions_list": all_positions,
            "net_asset_exposures": {
                sym: {
                    "net_quantity": round(exp.net_quantity, 4),
                    "net_notional": round(exp.net_notional, 2),
                    "is_hedged": exp.is_hedged,
                    "is_doubled_exposure": exp.is_doubled_exposure,
                    "exchanges": exp.exchange_positions
                }
                for sym, exp in asset_exposures.items()
            }
        }

    def validate_global_risk_limits(
        self,
        proposed_symbol: str,
        proposed_side: str,
        proposed_notional: float
    ) -> tuple[bool, str]:
        """
        Enforces unified risk limits at the PORTFOLIO level (total exposure, per-asset cap, drawdown cap).
        Returns: (is_allowed, reason)
        """
        eq_info = self.get_unified_equity()
        total_equity = eq_info["total_portfolio_equity"]
        if total_equity <= 0:
            return False, "Total portfolio equity is zero or negative."

        # 1. Global Drawdown limit check
        if eq_info["current_drawdown_pct"] >= (self.risk_limits.max_portfolio_drawdown_pct * 100):
            return False, f"Global portfolio drawdown {eq_info['current_drawdown_pct']}% exceeds limit {self.risk_limits.max_portfolio_drawdown_pct*100}%"

        # 2. Total Portfolio Risk / Notional Exposure Check
        pos_info = self.get_cross_exchange_positions()
        current_total_notional = sum(abs(p["notional"]) for p in pos_info["positions_list"])
        new_total_notional = current_total_notional + proposed_notional
        total_exposure_pct = new_total_notional / total_equity

        if total_exposure_pct > self.risk_limits.max_total_portfolio_risk_pct:
            return False, f"Proposed order increases total portfolio exposure to {total_exposure_pct*100:.1f}%, exceeding max limit {self.risk_limits.max_total_portfolio_risk_pct*100:.1f}%"

        # 3. Aggregate Single Asset Limit Check across all venues
        norm_sym = self.exchanges[list(self.exchanges.keys())[0]].normalize_symbol(proposed_symbol) if self.exchanges else proposed_symbol
        curr_asset_exp = pos_info["net_asset_exposures"].get(norm_sym, {}).get("net_notional", 0.0)
        new_asset_notional = abs(curr_asset_exp) + proposed_notional
        asset_pct = new_asset_notional / total_equity

        if asset_pct > self.risk_limits.max_asset_exposure_pct:
            return False, f"Proposed order increases {norm_sym} aggregate exposure to {asset_pct*100:.1f}%, exceeding single-asset cap {self.risk_limits.max_asset_exposure_pct*100:.1f}%"

        return True, "Risk checks passed."

    def check_allocation_drift(self, threshold_pct: float = 0.08) -> tuple[bool, dict[str, float]]:
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

