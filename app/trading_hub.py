"""
Stratex — Unified Trading Hub

High-level coordinator uniting all 11 trading engines:
1. CCXT (Multi-exchange market data & order routing)
2. Backtrader (Event-driven strategy simulation)
3. Vectorbt (Vectorized portfolio & signal analysis)
4. Freqtrade (Hyperopt parameter optimization & minimal ROI)
5. Hummingbot (High-frequency pure market making & inventory skew)
6. Lumibot (AI agent multi-asset backtesting)
7. Polymarket (Prediction market CLOB & probability estimation)
8. TradingAgents (Multi-agent financial debate & consensus)
9. AutoHedge (Swarm consensus & Kelly sizing)
10. Vibe-Trading (Alpha factors & GTJA 191 library)
11. FinceptTerminal (Technical indicators & order flow analytics)
"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field

try:
    from app.agents.real_ccxt import CCXTManager, ExchangeConfig, ExchangeBase
    from app.agents.real_backtrader import BacktraderCerebro
    from app.agents.real_vectorbt import SignalGenerator, PortfolioSimulation, VectorbtMetrics
    from app.agents.real_freqtrade import FreqtradeConfig, FreqtradeBacktest, FreqtradeStrategy
    from app.agents.real_hummingbot import MarketMakingStrategy, CrossExchangeArbitrage
    from app.agents.real_lumibot import LumibotManager, LumibotBacktest
    from app.agents.real_polymarket import PolymarketAPI, PredictionMarketAnalyzer
    from app.agents.real_trading_agents import TradingAgentsManager, BullBearDebate
    from app.agents.real_autohedge import SwarmConsensus, KellyPositionSizer
    from app.agents.real_vibe_trading import GTJAFactorLibrary, FactorPortfolio
    from app.agents.real_fincept_terminal import TechnicalIndicators, MarketRegimeDetector
except ImportError:
    from Stratex.app.agents.real_ccxt import CCXTManager, ExchangeConfig, ExchangeBase
    from Stratex.app.agents.real_backtrader import BacktraderCerebro
    from Stratex.app.agents.real_vectorbt import SignalGenerator, PortfolioSimulation, VectorbtMetrics
    from Stratex.app.agents.real_freqtrade import FreqtradeConfig, FreqtradeBacktest, FreqtradeStrategy
    from Stratex.app.agents.real_hummingbot import MarketMakingStrategy, CrossExchangeArbitrage
    from Stratex.app.agents.real_lumibot import LumibotManager, LumibotBacktest
    from Stratex.app.agents.real_polymarket import PolymarketAPI, PredictionMarketAnalyzer
    from Stratex.app.agents.real_trading_agents import TradingAgentsManager, BullBearDebate
    from Stratex.app.agents.real_autohedge import SwarmConsensus, KellyPositionSizer
    from Stratex.app.agents.real_vibe_trading import GTJAFactorLibrary, FactorPortfolio
    from Stratex.app.agents.real_fincept_terminal import TechnicalIndicators, MarketRegimeDetector


@dataclass
class TradingHub:
    """Unified Trading Intelligence Hub across all 11 engines."""
    ccxt_manager: CCXTManager = field(default_factory=CCXTManager)
    lumibot_manager: LumibotManager = field(default_factory=LumibotManager)
    polymarket_api: PolymarketAPI = field(default_factory=PolymarketAPI)
    trading_agents_mgr: TradingAgentsManager = field(default_factory=TradingAgentsManager)

    def __post_init__(self):
        # Configure primary exchange connectors
        for ex in ["binance", "coinbase", "kraken"]:
            self.ccxt_manager.add_exchange(ex, ExchangeConfig(name=ex))

    def get_market_quote(self, symbol: str = "BTC/USDT", exchange: str = "binance") -> dict[str, Any]:
        """Fetch unified ticker across real CCXT or simulated feeds."""
        ex = self.ccxt_manager.get_exchange(exchange)
        if ex is None:
            ex = self.ccxt_manager.add_exchange(exchange, ExchangeConfig(name=exchange))
        return ex.fetch_ticker(symbol)

    def scan_arbitrage(self, symbol: str = "BTC/USDT", min_spread_pct: float = 0.2) -> list[dict[str, Any]]:
        """Scan spatial arbitrage across CCXT exchange feeds."""
        return self.ccxt_manager.find_arbitrage_opportunities(symbol, min_spread_pct)

    def run_backtest_suite(self, ohlcv: dict[str, list[float]], fast: int = 10, slow: int = 30) -> dict[str, Any]:
        """Execute dual backtesting (Backtrader event-driven + Vectorbt vectorized)."""
        # Backtrader run
        bt_res = getattr(BacktraderCerebro, "run_real_cerebro", BacktraderCerebro.run)(ohlcv, fast=fast, slow=slow)
        
        # Vectorbt run
        closes = ohlcv.get("close", [])
        vbt_res = getattr(PortfolioSimulation, "simulate_real_vectorbt", None)
        if vbt_res is not None:
            vbt_out = vbt_res(closes, fast_window=fast, slow_window=slow)
        else:
            signals = SignalGenerator.sma_signal(closes, fast, slow)
            vbt_out = PortfolioSimulation.simulate(closes, signals)

        return {
            "backtrader": bt_res,
            "vectorbt": vbt_out,
            "benchmark_passed": bool(bt_res.get("sharpe", 0) > 0 or vbt_out.get("sharpe_ratio", 0) > 0),
        }

    def optimize_freqtrade_params(self, ohlcv: dict[str, list[float]]) -> dict[str, Any]:
        """Run Freqtrade hyperopt parameter search."""
        if hasattr(FreqtradeBacktest, "run_strategy_hyperopt"):
            return FreqtradeBacktest.run_strategy_hyperopt(ohlcv)
        return FreqtradeBacktest.run(FreqtradeConfig(), FreqtradeStrategy(), ohlcv)

    def compute_market_making_quotes(self, mid_price: float, spread: float = 0.001, inventory: float = 0.0) -> dict[str, Any]:
        """Compute Hummingbot Avellaneda-Stoikov / inventory-skewed quotes."""
        adj_spread = MarketMakingStrategy.adjust_spread(mid_price, inventory, base_spread=spread)
        bid, ask = MarketMakingStrategy.calculate_bid_ask(mid_price, adj_spread)
        return {
            "mid_price": mid_price,
            "inventory": inventory,
            "adjusted_spread": adj_spread,
            "bid": round(bid, 4),
            "ask": round(ask, 4),
        }

    def debate_trade_decision(self, symbol: str, price: float, news_headlines: list[str]) -> dict[str, Any]:
        """Run multi-agent financial debate (Bull vs Bear vs Risk Manager)."""
        debate_engine = BullBearDebate()
        news = [{"title": h, "sentiment": "positive"} for h in news_headlines]
        res = debate_engine.debate([price * 0.99, price, price * 1.01], news, rounds=3)
        
        winner = res.get("winner", "bull")
        action = "BUY" if winner == "bull" else "SELL"
        conviction = 0.8 if res.get("bull_score", 0) != res.get("bear_score", 0) else 0.5
        
        # Kelly sizing from AutoHedge
        kelly_fraction = KellyPositionSizer().kelly_fraction(
            win_prob=0.6 if action == "BUY" else 0.4,
            win_ratio=1.8,
        )
        
        return {
            "symbol": symbol,
            "price": price,
            "action": action,
            "conviction": conviction,
            "kelly_fraction": round(kelly_fraction, 4),
            "rounds": res.get("rounds", 3),
        }


# Module singleton
hub = TradingHub()
