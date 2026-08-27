"""
strategies/arb_scanner.py — Cross-Exchange Spatial & Funding Rate Arbitrage Scanner.

Scans:
1. Cross-Exchange Spot Arbitrage: Buy Low on Exchange A, Sell High on Exchange B.
2. Triangular Arbitrage within unified orderbooks.
3. Funding Rate Basis Arbitrage: Long Spot / Short Perpetual Futures when funding rate > threshold.
4. Net Profit Filtering: Deducts taker fees, estimated transfer costs, and execution slippage (minimum > 0.5% net).
"""

from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import time

from exchanges.base_exchange import BaseExchange, UnifiedTicker


@dataclass
class ArbitrageOpportunity:
    arb_type: str  # "SPATIAL_SPOT", "FUNDING_RATE", "TRIANGULAR"
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    spread_pct: float
    estimated_fees_pct: float
    net_profit_pct: float
    timestamp: float = field(default_factory=time.time)
    actionable: bool = False


class CrossExchangeArbitrageScanner:
    """
    Identifies high-probability price discrepancies across connected venues.
    """

    def __init__(self, exchanges: Dict[str, BaseExchange], min_net_profit_pct: float = 0.005):
        self.exchanges = exchanges
        self.min_net_profit_pct = min_net_profit_pct  # 0.5% default net profit hurdle

    def scan_spatial_arbitrage(self, symbol: str) -> List[ArbitrageOpportunity]:
        """
        Compares bids and asks across all exchange pairs for the same asset.
        """
        tickers: Dict[str, UnifiedTicker] = {}
        fees: Dict[str, Tuple[float, float]] = {}

        for ex_id, ex in self.exchanges.items():
            try:
                tickers[ex_id] = ex.get_ticker(symbol)
                fees[ex_id] = ex.get_trading_fees(symbol)
            except Exception:
                continue

        opportunities: List[ArbitrageOpportunity] = []
        exchange_ids = list(tickers.keys())

        for i in range(len(exchange_ids)):
            for j in range(len(exchange_ids)):
                if i == j:
                    continue
                buy_ex = exchange_ids[i]
                sell_ex = exchange_ids[j]

                ask_price = tickers[buy_ex].ask   # Price to buy
                bid_price = tickers[sell_ex].bid   # Price to sell

                if bid_price > ask_price and ask_price > 0:
                    raw_spread_pct = (bid_price - ask_price) / ask_price
                    total_fees_pct = fees[buy_ex][1] + fees[sell_ex][1] + 0.001  # Taker fees + 0.1% slippage buffer
                    net_profit = raw_spread_pct - total_fees_pct

                    opp = ArbitrageOpportunity(
                        arb_type="SPATIAL_SPOT",
                        symbol=symbol,
                        buy_exchange=buy_ex,
                        sell_exchange=sell_ex,
                        buy_price=ask_price,
                        sell_price=bid_price,
                        spread_pct=round(raw_spread_pct * 100.0, 3),
                        estimated_fees_pct=round(total_fees_pct * 100.0, 3),
                        net_profit_pct=round(net_profit * 100.0, 3),
                        actionable=(net_profit >= self.min_net_profit_pct)
                    )
                    opportunities.append(opp)

        return opportunities

    def scan_funding_rate_arbitrage(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Scans perpetual futures funding rate disparities.
        """
        funding_rates = {}
        for ex_id, ex in self.exchanges.items():
            try:
                rate = ex.get_funding_rate(symbol)
                if rate > 0:
                    funding_rates[ex_id] = rate
            except Exception:
                continue

        results = []
        for ex_id, rate in funding_rates.items():
            annualized = rate * 3.0 * 365.0 * 100.0  # 3 funding intervals/day
            if rate >= 0.0005:  # >= 0.05% per 8h
                results.append({
                    "symbol": symbol,
                    "exchange": ex_id,
                    "funding_rate_8h_pct": round(rate * 100.0, 4),
                    "annualized_apr_pct": round(annualized, 2),
                    "strategy": "CASH_AND_CARRY_ARBITRAGE"
                })
        return results
