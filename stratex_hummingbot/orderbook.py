from dataclasses import dataclass

@dataclass(frozen=True)
class OrderBookSnapshot:
    symbol: str
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    ts_ms: int

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        bb = self.best_bid
        ba = self.best_ask
        return (bb + ba) / 2.0 if (bb > 0 and ba > 0) else 0.0

    @property
    def spread(self) -> float:
        bb = self.best_bid
        ba = self.best_ask
        return max(0.0, ba - bb) if (bb > 0 and ba > 0) else 0.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        return (self.spread / mid) * 10_000 if mid > 0 else 0.0

    def is_stale(self, max_age_ms: int = 5000, current_ts_ms: int | None = None) -> bool:
        """Returns True if snapshot age exceeds max_age_ms."""
        import time
        now_ms = current_ts_ms if current_ts_ms is not None else int(time.time() * 1000)
        return (now_ms - self.ts_ms) > max_age_ms


class OrderBookImbalance:
    @staticmethod
    def top_n(snapshot: OrderBookSnapshot, n: int = 10) -> float:
        bid_qty = sum(q for _, q in snapshot.bids[:n])
        ask_qty = sum(q for _, q in snapshot.asks[:n])
        total = bid_qty + ask_qty
        return 0.0 if total <= 0 else (bid_qty - ask_qty) / total

    @staticmethod
    def depth_summary(snapshot: OrderBookSnapshot, n: int = 10) -> dict:
        bid_qty = sum(q for _, q in snapshot.bids[:n])
        ask_qty = sum(q for _, q in snapshot.asks[:n])
        return {
            "symbol": snapshot.symbol,
            "best_bid": snapshot.best_bid,
            "best_ask": snapshot.best_ask,
            "mid_price": snapshot.mid_price,
            "spread": snapshot.spread,
            "spread_bps": snapshot.spread_bps,
            "bid_depth_n": bid_qty,
            "ask_depth_n": ask_qty,
            "imbalance_top_n": OrderBookImbalance.top_n(snapshot, n=n),
            "is_stale": snapshot.is_stale(),
        }

