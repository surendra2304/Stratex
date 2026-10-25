from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request

try:
    from app.agents.real_autohedge import (
        SwarmAgent,
        SwarmConsensus,
        KellyPositionSizer,
        RiskManager,
        PortfolioRebalancer,
        BacktestEngine,
        DrawdownController,
        VolatilityTargeter,
        AgentCoordinator,
    )
    from app.agents.real_vibe_trading import (
        MomentumFactor,
        ReversalFactor,
        VolatilityFactor,
        ValueFactor,
        LiquidityFactor,
        GTJAFactorLibrary,
        FactorZScore,
        FactorCorrelation,
        FactorIC,
        FactorRotation,
        FactorPortfolio,
        RiskDecomposition,
        BacktestMetrics,
    )
    from app.agents.real_fincept_terminal import (
        CandlestickPattern,
        TechnicalIndicators,
        OrderFlowAnalysis,
        CrossAssetAnalysis,
        EconomicCalendar,
        FinancialStatementAnalysis,
        PortfolioAnalytics,
        MarketRegimeDetector,
    )
except ImportError:
    from Stratex.app.agents.real_autohedge import (
        SwarmAgent,
        SwarmConsensus,
        KellyPositionSizer,
        RiskManager,
        PortfolioRebalancer,
        BacktestEngine,
        DrawdownController,
        VolatilityTargeter,
        AgentCoordinator,
    )
    from Stratex.app.agents.real_vibe_trading import (
        MomentumFactor,
        ReversalFactor,
        VolatilityFactor,
        ValueFactor,
        LiquidityFactor,
        GTJAFactorLibrary,
        FactorZScore,
        FactorCorrelation,
        FactorIC,
        FactorRotation,
        FactorPortfolio,
        RiskDecomposition,
        BacktestMetrics,
    )
    from Stratex.app.agents.real_fincept_terminal import (
        CandlestickPattern,
        TechnicalIndicators,
        OrderFlowAnalysis,
        CrossAssetAnalysis,
        EconomicCalendar,
        FinancialStatementAnalysis,
        PortfolioAnalytics,
        MarketRegimeDetector,
    )

logger = logging.getLogger("stratex.real_trading_routes")
real_trading_bp = Blueprint("real_trading", __name__, url_prefix="/api/v1/trading/engines")


# ── AUTOHEDGE ENDPOINTS ──────────────────────────────────────────────────────

@real_trading_bp.route("/autohedge/consensus", methods=["POST"])
def autohedge_consensus():
    """Evaluate swarm agent consensus and voting on market signals."""
    data = request.get_json() or {}
    agent_defs = data.get("agents", [])
    raw_signals = data.get("signals", [0.5, 0.6, -0.2])

    consensus = SwarmConsensus()
    if agent_defs:
        for ad in agent_defs:
            agent = SwarmAgent(
                name=ad.get("name", "agent"),
                risk_tolerance=ad.get("risk_tolerance", 0.5),
                conviction=ad.get("conviction", 0.7),
                specialty=ad.get("specialty", "balanced"),
                weight=ad.get("weight", 1.0),
                recent_accuracy=ad.get("recent_accuracy", 0.6),
            )
            consensus.add_agent(agent)
    else:
        consensus.add_agent(SwarmAgent(name="trend_follower", conviction=0.85, weight=1.2))
        consensus.add_agent(SwarmAgent(name="mean_reversion", conviction=0.75, weight=1.0))
        consensus.add_agent(SwarmAgent(name="volatility_breakout", conviction=0.65, weight=0.8))

    # Normalize signals to list[float]
    if isinstance(raw_signals, dict):
        signals = [float(v) for v in raw_signals.values()]
    else:
        signals = [float(s) for s in raw_signals]

    consensus_res = consensus.consensus_signal(signals)
    weighted_vote_val = consensus.weighted_vote(signals)

    return jsonify({
        "status": "success",
        "engine": "AutoHedge",
        "consensus_signal": consensus_res,
        "weighted_vote": weighted_vote_val,
        "agent_count": len(consensus.agents),
    })


@real_trading_bp.route("/autohedge/position-size", methods=["POST"])
def autohedge_position_size():
    """Calculate optimal position sizing via Kelly Criterion and Drawdown Controller."""
    data = request.get_json() or {}
    win_prob = float(data.get("win_prob", 0.55))
    win_ratio = float(data.get("win_ratio", 1.8))
    fraction = float(data.get("fraction", 0.5))
    bankroll = float(data.get("bankroll", 100000.0))
    current_value = float(data.get("current_value", bankroll))

    sizer = KellyPositionSizer(bankroll=bankroll)
    k_fraction = sizer.kelly_fraction(win_prob, win_ratio)
    pos_size = sizer.position_size(win_prob, win_ratio, fraction=fraction)
    frac_kelly = sizer.fractional_kelly(k_fraction, fraction=fraction)

    controller = DrawdownController(peak_value=bankroll, soft_limit=0.1, hard_limit=0.2)
    multiplier = controller.position_multiplier(current_value)

    adjusted_size = pos_size * multiplier

    return jsonify({
        "status": "success",
        "engine": "AutoHedge",
        "kelly_fraction": k_fraction,
        "raw_position_size": pos_size,
        "fractional_kelly": frac_kelly,
        "drawdown_multiplier": multiplier,
        "recommended_position_size": adjusted_size,
    })


@real_trading_bp.route("/autohedge/rebalance", methods=["POST"])
def autohedge_rebalance():
    """Calculate portfolio drift and rebalance orders."""
    data = request.get_json() or {}
    positions = {k: float(v) for k, v in data.get("positions", {}).items()}
    prices = {k: float(v) for k, v in data.get("prices", {}).items()}
    target_weights = {k: float(v) for k, v in data.get("target_weights", {}).items()}
    cash = float(data.get("cash", 10000.0))

    rebalancer = PortfolioRebalancer(target_weights=target_weights, cash=cash, positions=positions)
    total_val = cash + sum(positions.get(a, 0.0) * prices.get(a, 0.0) for a in set(positions) | set(prices))

    drifts = {asset: rebalancer.drift(asset, total_val) for asset in target_weights}
    needs_reb = rebalancer.needs_rebalance(total_val, threshold=data.get("threshold", 0.05))
    rebalance_orders = rebalancer.rebalance(prices)

    return jsonify({
        "status": "success",
        "engine": "AutoHedge",
        "total_value": total_val,
        "drift": drifts,
        "needs_rebalance": needs_reb,
        "orders": rebalance_orders,
    })


@real_trading_bp.route("/autohedge/backtest", methods=["POST"])
def autohedge_backtest():
    """Run AutoHedge backtest engine and compute performance metrics."""
    data = request.get_json() or {}
    initial_capital = float(data.get("initial_capital", 100000.0))
    trades = data.get("trades", [])
    prices = {k: float(v) for k, v in data.get("prices", {}).items()}
    returns = [float(r) for r in data.get("returns", [0.01, 0.015, -0.005, 0.02, 0.01])]

    engine = BacktestEngine(initial_capital=initial_capital)
    for trade in trades:
        action = trade.get("action", "buy")
        asset = trade.get("asset", "BTC")
        price = float(trade.get("price", 50000.0))
        shares = float(trade.get("shares", 1.0))
        if action == "buy":
            engine.buy(asset, price, shares)
        elif action == "sell":
            engine.sell(asset, price, shares)

    total_val = engine.total_value(prices)
    sharpe = engine.sharpe_ratio(returns)
    sortino = engine.sortino_ratio(returns)

    return jsonify({
        "status": "success",
        "engine": "AutoHedge",
        "initial_capital": initial_capital,
        "final_value": total_val,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "cash_balance": engine.cash,
        "positions": engine.positions,
    })


# ── VIBE-TRADING ENDPOINTS ───────────────────────────────────────────────────

@real_trading_bp.route("/vibe/alphas", methods=["POST"])
def vibe_alphas():
    """Compute alpha factors and GTJA 191 library factor values."""
    data = request.get_json() or {}
    prices = [float(p) for p in data.get("prices", [100.0 + i * 0.5 for i in range(30)])]
    volumes = [float(v) for v in data.get("volumes", [1000.0 + (i % 5) * 100 for i in range(len(prices))])]
    open_prices = [float(p) for p in data.get("open", [p * 0.99 for p in prices])]
    high_prices = [float(p) for p in data.get("high", [p * 1.02 for p in prices])]
    low_prices = [float(p) for p in data.get("low", [p * 0.98 for p in prices])]

    mom = MomentumFactor(window=min(10, len(prices) - 1)).compute(prices, volumes)
    rev = ReversalFactor(window=min(5, len(prices) - 1)).compute(prices, volumes)
    vol = VolatilityFactor(window=min(10, len(prices) - 1)).compute(prices, volumes)
    val = ValueFactor(window=min(10, len(prices) - 1)).compute(prices, volumes)
    liq = LiquidityFactor(window=min(10, len(prices) - 1)).compute(prices, volumes)

    # Compute key GTJA Alpha expressions
    gtja = GTJAFactorLibrary()
    gtja_results = {
        "alpha_001": gtja.alpha_001(close=prices, open_=open_prices, high=high_prices, low=low_prices),
        "alpha_002": gtja.alpha_002(close=prices, volumes=volumes),
        "alpha_003": gtja.alpha_003(close=prices),
        "alpha_010": gtja.alpha_010(close=prices),
        "alpha_191": gtja.alpha_191(close=prices, high=high_prices, low=low_prices, volumes=volumes),
    }

    return jsonify({
        "status": "success",
        "engine": "VibeTrading",
        "core_factors": {
            "momentum": mom,
            "reversal": rev,
            "volatility": vol,
            "value": val,
            "liquidity": liq,
        },
        "gtja_alphas": gtja_results,
    })


@real_trading_bp.route("/vibe/portfolio", methods=["POST"])
def vibe_portfolio():
    """Optimize factor portfolio and compute risk decomposition."""
    data = request.get_json() or {}
    factor_weights = {k: float(v) for k, v in data.get("weights", {"momentum": 0.4, "value": 0.3, "volatility": 0.3}).items()}
    raw_values = data.get("values", {"momentum": [1.5]*10, "value": [0.8]*10, "volatility": [-0.4]*10})
    top_n = int(data.get("top_n", 2))

    # Convert scalar values to lists if needed
    factor_values: dict[str, list[float]] = {}
    for k, v in raw_values.items():
        if isinstance(v, list):
            factor_values[k] = [float(x) for x in v]
        else:
            factor_values[k] = [float(v)] * 10

    portfolio = FactorPortfolio(factor_weights=factor_weights)
    combined = portfolio.combined_factor(factor_values)
    top_selection = portfolio.top_n_selection(combined, top_n)
    bottom_selection = portfolio.bottom_n_selection(combined, top_n)

    # Risk decomposition with diagonal covariance approximation if not provided
    factor_cov = data.get("covariances")
    if not factor_cov:
        factor_cov = {f: {f2: (0.04 if f == f2 else 0.01) for f2 in factor_weights} for f in factor_weights}

    decomp = RiskDecomposition()
    total_risk = decomp.total_risk(factor_weights, factor_cov)
    marginal = {f: decomp.marginal_risk(factor_weights, factor_cov, f) for f in factor_weights}
    contrib = decomp.factor_contribution(factor_weights, factor_cov)

    return jsonify({
        "status": "success",
        "engine": "VibeTrading",
        "combined_factor": combined,
        "top_n_selection": top_selection,
        "bottom_n_selection": bottom_selection,
        "risk_decomposition": {
            "total_risk": total_risk,
            "marginal_risk": marginal,
            "factor_contribution": contrib,
        },
    })


# ── FINCEPT TERMINAL ENDPOINTS ───────────────────────────────────────────────

@real_trading_bp.route("/fincept/technical", methods=["POST"])
def fincept_technical():
    """Compute technical indicators and candlestick pattern detection."""
    data = request.get_json() or {}
    prices = [float(p) for p in data.get("prices", [100.0, 102.0, 101.5, 103.0, 105.0, 104.5, 106.0, 108.0, 107.5, 110.0])]
    highs = [float(h) for h in data.get("high", [p * 1.02 for p in prices])]
    lows = [float(l) for l in data.get("low", [p * 0.98 for p in prices])]
    volumes = [float(v) for v in data.get("volumes", [1000.0 + i * 50 for i in range(len(prices))])]
    open_prices = [prices[max(0, i - 1)] * 0.995 for i in range(len(prices))]

    tech = TechnicalIndicators()
    sma_val = tech.sma(prices, min(5, len(prices)))
    ema_val = tech.ema(prices, min(5, len(prices)))
    rsi_val = tech.rsi(prices, min(6, len(prices) - 1))
    bb = tech.bollinger_bands(prices, min(5, len(prices)), 2.0)
    atr_val = tech.atr(highs, lows, prices, min(5, len(prices)))
    vwap_val = tech.vwap(highs, lows, prices, volumes)

    # Candlestick patterns
    candle = CandlestickPattern()
    patterns = {
        "bullish_engulfing": candle.bullish_engulfing(open_prices, prices),
        "bearish_engulfing": candle.bearish_engulfing(open_prices, prices),
        "hammer": candle.hammer(lows, highs, prices),
        "doji": candle.doji(open_prices, prices),
    }

    return jsonify({
        "status": "success",
        "engine": "FinceptTerminal",
        "technical_indicators": {
            "sma": sma_val,
            "ema": ema_val,
            "rsi": rsi_val,
            "bollinger_bands": bb,
            "atr": atr_val,
            "vwap": vwap_val,
        },
        "candlestick_patterns": patterns,
    })


@real_trading_bp.route("/fincept/orderflow", methods=["POST"])
def fincept_orderflow():
    """Analyze order book microstructure, microprice, and flow imbalance."""
    data = request.get_json() or {}
    bids = data.get("bids", [[100.0, 10.0], [99.5, 25.0], [99.0, 40.0]])
    asks = data.get("asks", [[100.5, 12.0], [101.0, 30.0], [101.5, 50.0]])
    trades = data.get("trades", [
        {"price": 100.2, "volume": 5.0, "side": "buy"},
        {"price": 100.1, "volume": 3.0, "side": "sell"},
        {"price": 100.3, "volume": 8.0, "side": "buy"},
    ])

    flow = OrderFlowAnalysis()
    micro = flow.microprice(bids, asks)
    imbalance = flow.bid_ask_imbalance(bids, asks)
    trade_imbalance = flow.order_flow_imbalance(trades)

    return jsonify({
        "status": "success",
        "engine": "FinceptTerminal",
        "microprice": micro,
        "bid_ask_imbalance": imbalance,
        "order_flow_imbalance": trade_imbalance,
    })


@real_trading_bp.route("/fincept/regime", methods=["POST"])
def fincept_regime():
    """Detect current market regime and transition probabilities."""
    data = request.get_json() or {}
    prices = [float(p) for p in data.get("prices", [])]
    if not prices:
        returns = [float(r) for r in data.get("returns", [0.01, 0.015, -0.005, 0.02, 0.008, -0.012, 0.025, 0.018])]
        p = 100.0
        prices = [p]
        for r in returns:
            p = p * (1.0 + r)
            prices.append(p)

    # Pad prices if fewer than 50
    if len(prices) < 50:
        step = (prices[-1] - prices[0]) / max(1, len(prices) - 1)
        while len(prices) < 50:
            prices.append(prices[-1] + step)

    window = int(data.get("window", min(len(prices), 50)))
    current_regime = MarketRegimeDetector.detect_regime(prices, window=window)

    return jsonify({
        "status": "success",
        "engine": "FinceptTerminal",
        "detected_regime": current_regime,
        "sample_size": len(prices),
        "window": window,
    })


@real_trading_bp.route("/fincept/fundamentals", methods=["POST"])
def fincept_fundamentals():
    """Analyze corporate financial statements and compute overall financial health."""
    data = request.get_json() or {}
    assets = float(data.get("assets", 500000.0))
    liabilities = float(data.get("liabilities", 200000.0))
    debt = float(data.get("debt", 150000.0))
    equity = float(data.get("equity", 300000.0))
    income = float(data.get("income", 45000.0))
    revenue = float(data.get("revenue", 250000.0))
    price = float(data.get("price", 150.0))
    eps = float(data.get("eps", 7.5))
    bvps = float(data.get("bvps", 50.0))

    fa = FinancialStatementAnalysis()
    metrics = {
        "current_ratio": fa.current_ratio(assets, liabilities),
        "debt_to_equity": fa.debt_to_equity(debt, equity),
        "return_on_equity": fa.return_on_equity(income, equity),
        "return_on_assets": fa.return_on_assets(income, assets),
        "price_to_earnings": fa.price_to_earnings(price, eps),
        "price_to_book": fa.price_to_book(price, bvps),
        "profit_margin": fa.profit_margin(income, revenue),
    }
    health_score = fa.financial_health_score(metrics)

    return jsonify({
        "status": "success",
        "engine": "FinceptTerminal",
        "metrics": metrics,
        "financial_health_score": health_score,
    })
