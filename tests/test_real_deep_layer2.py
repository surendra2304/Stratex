"""Tests for Nautilus Trader Engine implementation."""

from __future__ import annotations

import sys
from pathlib import Path

# Add Stratex to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agents.real_nautilus_trader import (
    NautilusStrategy,
    NautilusBacktestEngine,
    NautilusOrderBook,
    NautilusDataFeed,
    Account,
    Position,
    Order,
    OrderType,
    OrderSide,
    OrderStatus,
    Bar,
    Tick,
)


def test_tick():
    """Test Tick creation and properties."""
    tick = Tick(
        symbol="BTCUSDT",
        timestamp=1000.0,
        bid=50000.0,
        ask=50010.0,
        last=50005.0,
        volume=1.5,
    )

    assert tick.symbol == "BTCUSDT"
    assert tick.mid_price == 50005.0
    assert tick.spread == 10.0

    result = tick.to_dict()
    assert "BTCUSDT" in result["symbol"]
    assert "mid_price" in result


def test_bar():
    """Test Bar creation and properties."""
    bar = Bar(
        symbol="BTCUSDT",
        frequency="1m",
        timestamp=1000.0,
        open=50000.0,
        high=50100.0,
        low=49900.0,
        close=50050.0,
        volume=100.0,
        trades=50,
    )

    assert bar.symbol == "BTCUSDT"
    assert bar.is_bullish
    assert not bar.is_bearish
    assert bar.body == 50.0
    assert bar.range == 200.0

    result = bar.to_dict()
    assert "BTCUSDT" in result["symbol"]
    assert "typical_price" in result


def test_order():
    """Test Order creation and properties."""
    order = Order(
        order_id="ord_1",
        symbol="BTCUSDT",
        order_type=OrderType.MARKET,
        side=OrderSide.BUY,
        quantity=1.0,
        price=50000.0,
        status=OrderStatus.PENDING,
    )

    assert order.order_id == "ord_1"
    assert order.remaining == 1.0
    assert not order.is_complete

    order.status = OrderStatus.FILLED
    order.filled_quantity = 1.0
    assert order.is_complete

    result = order.to_dict()
    assert "ord_1" in result["order_id"]


def test_position():
    """Test Position tracking."""
    pos = Position(symbol="BTCUSDT", quantity=1.0, avg_entry_price=50000.0)

    assert pos.is_long
    assert not pos.is_short
    assert not pos.is_flat
    assert pos.unrealized_pnl(51000.0) == 1000.0

    trade = Order(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        quantity=1.0,
        price=51000.0,
    )
    pos.update_on_fill(trade)
    assert pos.is_flat


def test_account():
    """Test Account buy/sell operations."""
    account = Account(cash=100_000.0)

    trade = account.buy("BTCUSDT", 1.0, 50000.0, commission=50.0)
    assert trade.symbol == "BTCUSDT"
    assert trade.side == OrderSide.BUY
    assert account.positions["BTCUSDT"].quantity == 1.0
    assert account.cash == 100_000 - 50050

    trade = account.sell("BTCUSDT", 1.0, 51000.0, commission=50.0)
    assert trade.symbol == "BTCUSDT"
    assert trade.side == OrderSide.SELL
    assert "BTCUSDT" not in account.positions
    assert account.realized_pnl > 0


def test_strategy_lifecycle():
    """Test NautilusStrategy lifecycle."""
    strategy = NautilusStrategy(name="TestStrategy")
    strategy.on_start()

    tick = Tick(symbol="BTCUSDT", last=50000.0)
    strategy.on_tick(tick)

    bar = Bar(symbol="BTCUSDT", close=50050.0)
    strategy.on_bar(bar)

    order = strategy.buy("BTCUSDT", 1.0)
    assert order.side == OrderSide.BUY

    strategy.on_stop()

    assert len(strategy.ticks) == 1
    assert len(strategy.bars) == 1
    assert len(strategy.orders) == 1


def test_backtest_engine():
    """Test NautilusBacktestEngine."""
    strategy = NautilusStrategy(name="TestStrategy")
    account = Account(cash=100_000.0)
    engine = NautilusBacktestEngine(strategy, account)

    # Create sample bars
    bars = []
    for i in range(10):
        bar = Bar(
            symbol="BTCUSDT",
            frequency="1m",
            timestamp=1000 + i * 60,
            open=50000 + i * 100,
            high=50100 + i * 100,
            low=49900 + i * 100,
            close=50050 + i * 100,
            volume=100.0,
            trades=50,
        )
        bars.append(bar)

    engine.add_bars(bars)
    strategy.buy("BTCUSDT", 0.5, 50000.0)

    results = engine.run()

    assert "initial_equity" in results
    assert "final_equity" in results
    assert "total_return_pct" in results
    assert "sharpe_ratio" in results
    assert "max_drawdown_pct" in results
    assert "total_trades" in results


def test_order_book():
    """Test NautilusOrderBook."""
    book = NautilusOrderBook(symbol="BTCUSDT")

    book.update_bids([(50000.0, 1.5, 10), (49990.0, 2.0, 5)])
    book.update_asks([(50010.0, 1.0, 8), (50020.0, 1.5, 6)])

    assert book.best_bid == 50000.0
    assert book.best_ask == 50010.0
    assert book.spread == 10.0
    assert book.mid_price == 50005.0
    assert book.bid_depth(2) == 3.5
    assert book.ask_depth(2) == 2.5
    assert 0 < book.imbalance < 1

    result = book.to_dict()
    assert "BTCUSDT" in result["symbol"]
    assert "imbalance" in result


def test_data_feed():
    """Test NautilusDataFeed."""
    from app.agents.real_nautilus_trader import NautilusDataFeed

    config = NautilusDataFeed.FeedConfig(
        symbol="BTCUSDT",
        base_price=50000.0,
        volatility=0.01,
    )
    feed = NautilusDataFeed(config)

    # Generate some ticks
    for _ in range(10):
        tick = feed.generate_tick()
        assert tick.symbol == "BTCUSDT"
        assert tick.bid > 0
        assert tick.ask > 0

    # Generate a bar
    bar = feed.generate_bar("1m")
    assert bar.symbol == "BTCUSDT"
    assert bar.open > 0
    assert bar.close > 0

    # Check history
    history = feed.get_history("1m", 5)
    assert len(history) <= 5


def test_position_short():
    """Test short position handling."""
    pos = Position(symbol="BTCUSDT", quantity=-1.0, avg_entry_price=50000.0)

    assert pos.is_short
    assert not pos.is_long
    assert pos.unrealized_pnl(49000.0) == 1000.0  # profit when price drops


def test_account_insufficient_cash():
    """Test account raises error on insufficient cash."""
    account = Account(cash=1000.0)

    try:
        account.buy("BTCUSDT", 1.0, 50000.0)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Insufficient cash" in str(e)


def test_account_insufficient_position():
    """Test account raises error on insufficient position."""
    account = Account(cash=100_000.0)

    try:
        account.sell("BTCUSDT", 1.0, 50000.0)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Insufficient position" in str(e)
