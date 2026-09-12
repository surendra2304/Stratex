"""tests/test_stratex_backtrader_complete.py

Comprehensive test suite verifying Backtrader capabilities in Stratex:
1. Domain models, OrderStatus, and TradeRecord calculations
2. Simulated Broker order fills, commissions, and slippage
3. Decoupled Position Sizers (FixedSize, PercentSizer, VolatilitySizer, KellySizer)
4. Quantitative Analyzers (SharpeRatio, SortinoRatio, DrawDown, TradeAnalyzer, SQN, CalmarRatio)
5. Strategy lifecycle hooks (init, next, buy, sell, close, notify_order, notify_trade)
6. Cerebro end-to-end event-driven backtesting execution loop
7. Permanent security invariant: LIVE_TRADING_ENABLED = False
8. Flask REST API endpoints (/api/v1/backtrader/*)
"""

import pytest
import pandas as pd
import numpy as np

from stratex_backtrader_adapter import (
    bt,
    Cerebro,
    Strategy,
    SMACrossStrategy,
    RSIStrategy,
    BacktraderBroker,
    CommissionScheme,
    FixedSize,
    PercentSizer,
    VolatilitySizer,
    KellySizer,
    SharpeRatio,
    SortinoRatio,
    DrawDown,
    TradeAnalyzer,
    SQN,
    CalmarRatio,
    OrderSide,
    OrderType,
    OrderStatus,
    BacktestOrder,
    TradeRecord,
    BacktestResult,
)
from dashboard import app


@pytest.fixture
def flask_client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def sample_ohlcv_data():
    """Generates synthetic trend and reversal data for strategy testing."""
    np.random.seed(42)
    bars = 100
    base_price = 100.0
    prices = [base_price]
    for _ in range(bars - 1):
        prices.append(prices[-1] + np.random.normal(0.2, 1.5))

    df = pd.DataFrame({
        "open": prices,
        "high": [p + 1.0 for p in prices],
        "low": [p - 1.0 for p in prices],
        "close": [p + 0.1 for p in prices],
        "volume": [100.0 for _ in prices],
    })
    return df


# ------------------------------------------------------------------------------
# 1. Models & TradeRecord Tests
# ------------------------------------------------------------------------------
def test_backtrader_models_and_enums():
    order = BacktestOrder(
        order_id="ord_1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        size=2.0,
        price=50000.0,
    )
    assert order.status == OrderStatus.SUBMITTED
    assert order.status.is_closed is False

    trade = TradeRecord(
        trade_id="trd_1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        size=1.0,
        entry_price=50000.0,
        exit_price=52000.0,
        entry_idx=5,
        exit_idx=10,
        pnl=1980.0,
        pnl_pct=3.96,
        commission=20.0,
        duration_bars=5,
    )
    assert trade.is_win is True
    assert trade.is_loss is False


# ------------------------------------------------------------------------------
# 2. Broker Order Execution, Slippage & Commissions Tests
# ------------------------------------------------------------------------------
def test_backtrader_broker_execution():
    scheme = CommissionScheme(commission_pct=0.001, fixed_fee=1.0, slippage_bps=10.0)
    broker = BacktraderBroker(cash=10000.0, commission_scheme=scheme)

    # Submit BUY order
    order = BacktestOrder(
        order_id="ord_test",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        size=1.0,
    )
    broker.submit_order(order)

    # Process bar at open=100.0
    bar_data = {"BTCUSDT": {"open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0}}
    closed_trades = broker.process_bar(bar_idx=0, market_data=bar_data)

    assert len(closed_trades) == 0  # Opened position, not closed
    assert order.status == OrderStatus.COMPLETED
    # 10 bps slippage on BUY: 100 * 1.001 = 100.10
    assert order.executed_price == pytest.approx(100.10)
    assert broker.get_position("BTCUSDT") == 1.0

    # Submit SELL order to close
    sell_order = BacktestOrder(
        order_id="ord_sell",
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        size=1.0,
    )
    broker.submit_order(sell_order)

    # Process bar at open=120.0
    bar_data_close = {"BTCUSDT": {"open": 120.0, "high": 122.0, "low": 119.0, "close": 121.0}}
    closed_trades = broker.process_bar(bar_idx=1, market_data=bar_data_close)

    assert len(closed_trades) == 1
    trade = closed_trades[0]
    assert trade.pnl > 18.0  # Profitable round-trip
    assert broker.get_position("BTCUSDT") == 0.0


# ------------------------------------------------------------------------------
# 3. Position Sizers Tests
# ------------------------------------------------------------------------------
def test_backtrader_sizers():
    broker = BacktraderBroker(cash=10000.0)

    # 1. FixedSize
    fixed = FixedSize(size=5.0)
    assert fixed.get_size(broker, "BTCUSDT", 100.0) == 5.0

    # 2. PercentSizer (10% of $10,000 = $1,000 -> 10 units at $100)
    pct_sizer = PercentSizer(percent=10.0)
    assert pct_sizer.get_size(broker, "BTCUSDT", 100.0) == 10.0

    # 3. VolatilitySizer (1% risk = $100 risk, ATR=2.0 * mult 2.0 = $4 stop distance -> 25 units)
    vol_sizer = VolatilitySizer(risk_pct=1.0, atr_multiplier=2.0)
    assert vol_sizer.get_size(broker, "BTCUSDT", 100.0, atr=2.0) == 25.0

    # 4. KellySizer (win_rate=0.6, payoff=2.0 -> K = 0.6 - 0.4/2 = 0.4. Half-kelly = 0.2 = 20% -> 20 units)
    kelly = KellySizer(win_rate=0.6, payoff_ratio=2.0, fraction=0.5, max_pct=30.0)
    assert kelly.get_size(broker, "BTCUSDT", 100.0) == 20.0


# ------------------------------------------------------------------------------
# 4. Quantitative Analyzers Tests
# ------------------------------------------------------------------------------
def test_backtrader_analyzers():
    # 1. Sharpe & Sortino
    sharpe = SharpeRatio(riskfree_rate=0.0)
    sortino = SortinoRatio(riskfree_rate=0.0)
    equities = [10000.0, 10200.0, 10100.0, 10400.0, 10600.0]
    for idx, eq in enumerate(equities):
        sharpe.notify_bar(idx, eq)
        sortino.notify_bar(idx, eq)

    res_sharpe = sharpe.get_analysis()
    res_sortino = sortino.get_analysis()
    assert res_sharpe["sharperatio"] > 0.0
    assert res_sortino["sortinoratio"] > 0.0

    # 2. DrawDown
    dd = DrawDown()
    dd_curve = [10000.0, 12000.0, 9600.0, 11000.0]  # Peak 12k, trough 9.6k = 20% DD
    for idx, eq in enumerate(dd_curve):
        dd.notify_bar(idx, eq)
    res_dd = dd.get_analysis()
    assert res_dd["max"]["drawdown"] == 20.0
    assert res_dd["peak_equity"] == 12000.0

    # 3. TradeAnalyzer & SQN
    trade_analyzer = TradeAnalyzer()
    sqn = SQN()
    trades = [
        TradeRecord("t1", "BTCUSDT", OrderSide.BUY, 1.0, 100, 110, 0, 1, 10.0, 10.0, 0.0, 1),
        TradeRecord("t2", "BTCUSDT", OrderSide.BUY, 1.0, 110, 105, 1, 2, -5.0, -4.5, 0.0, 1),
        TradeRecord("t3", "BTCUSDT", OrderSide.BUY, 1.0, 105, 120, 2, 3, 15.0, 14.2, 0.0, 1),
    ]
    for t in trades:
        trade_analyzer.notify_trade(t)
        sqn.notify_trade(t)

    res_trades = trade_analyzer.get_analysis()
    res_sqn = sqn.get_analysis()

    assert res_trades["total"]["total"] == 3
    assert res_trades["total"]["won"] == 2
    assert res_trades["total"]["lost"] == 1
    assert res_trades["profit_factor"] == 5.0  # (10 + 15) / 5 = 5.0
    assert res_sqn["sqn"] > 0.0


# ------------------------------------------------------------------------------
# 5. Strategy Lifecycle Hooks Tests
# ------------------------------------------------------------------------------
def test_backtrader_strategy_lifecycle():
    class TestLifecycleStrategy(Strategy):
        def init(self):
            self.started = False
            self.orders_seen = 0
            self.trades_seen = 0

        def start(self):
            self.started = True

        def next(self):
            if self._current_idx == 0:
                self.buy(size=1.0)
            elif self._current_idx == 2:
                self.close()

        def notify_order(self, order):
            self.orders_seen += 1

        def notify_trade(self, trade):
            self.trades_seen += 1

    df = pd.DataFrame({
        "open": [100.0, 105.0, 110.0, 115.0],
        "high": [102.0, 107.0, 112.0, 117.0],
        "low": [98.0, 103.0, 108.0, 113.0],
        "close": [101.0, 106.0, 111.0, 116.0],
    })

    cerebro = Cerebro()
    cerebro.add_data(df, name="TEST")
    cerebro.add_strategy(TestLifecycleStrategy)
    res = cerebro.run()

    assert len(res.trades) == 1
    assert res.total_pnl > 0.0


# ------------------------------------------------------------------------------
# 6. Cerebro End-to-End Orchestration Tests
# ------------------------------------------------------------------------------
def test_backtrader_cerebro_execution(sample_ohlcv_data):
    cerebro = Cerebro()
    cerebro.set_cash(20000.0)
    cerebro.set_commission(commission_pct=0.0005)
    cerebro.add_data(sample_ohlcv_data, name="BTCUSDT")
    cerebro.add_strategy(SMACrossStrategy, fast_period=5, slow_period=15)
    cerebro.set_sizer(PercentSizer, percent=15.0)

    res = cerebro.run()
    assert isinstance(res, BacktestResult)
    assert res.strategy_name == "SMACrossStrategy"
    assert len(res.equity_curve) == len(sample_ohlcv_data)
    assert "sharpe" in res.analyzers
    assert "drawdown" in res.analyzers
    assert "trades" in res.analyzers


# ------------------------------------------------------------------------------
# 7. Permanent Security Invariant Tests
# ------------------------------------------------------------------------------
def test_backtrader_security_invariant():
    assert bt.LIVE_TRADING_ENABLED is False
    dummy_order = BacktestOrder("live_ord", "BTCUSDT", OrderSide.BUY, OrderType.MARKET, 1.0)
    with pytest.raises(PermissionError) as exc_info:
        bt.route_live_order(dummy_order)
    assert "LIVE_TRADING_ENABLED = False is permanent" in str(exc_info.value)


# ------------------------------------------------------------------------------
# 8. Flask REST API Routes Tests
# ------------------------------------------------------------------------------
def test_flask_backtrader_routes(flask_client, sample_ohlcv_data):
    # GET /status
    res = flask_client.get("/api/v1/backtrader/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
    assert "FixedSize" in data["data"]["sizers"]

    # GET /sizers
    res = flask_client.get("/api/v1/backtrader/sizers")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["data"]) >= 4

    # GET /analyzers
    res = flask_client.get("/api/v1/backtrader/analyzers")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["data"]) >= 6

    # POST /analyze
    trades_payload = {
        "trades": [
            {"symbol": "BTCUSDT", "pnl": 100.0},
            {"symbol": "BTCUSDT", "pnl": -40.0},
            {"symbol": "BTCUSDT", "pnl": 60.0},
        ]
    }
    res = flask_client.post("/api/v1/backtrader/analyze", json=trades_payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
    assert data["data"]["trade_analysis"]["total"]["total"] == 3

    # POST /run
    candles = sample_ohlcv_data.to_dict(orient="records")
    run_payload = {
        "candles": candles,
        "strategy": "SMACross",
        "sizer": "PercentSizer",
        "initial_cash": 10000.0,
        "params": {"fast_period": 5, "slow_period": 15},
    }
    res = flask_client.post("/api/v1/backtrader/run", json=run_payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
    assert data["data"]["strategy"] == "SMACrossStrategy"
    assert "analyzers" in data["data"]
