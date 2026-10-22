"""stratex_nautilus_adapter/event_bus.py

Lightweight, microsecond-latency in-memory Publish/Subscribe Event Bus inspired by NautilusTrader:
- Topic-based subscription with prefix wildcard routing (e.g., 'data.*', 'orders.*')
- Nanosecond timestamped event envelopes
- Dispatch telemetry tracking (published count, delivery count, error count)
"""

from __future__ import annotations

import time
import fnmatch
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Event:
    """Standardized event envelope with nanosecond precision timestamp."""
    topic: str
    payload: Any
    ts_event: int  # Nanoseconds since Unix epoch


class NautilusEventBus:
    """In-process high-throughput publish/subscribe event bus."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[Event], None]]] = {}
        self._published_count: int = 0
        self._delivered_count: int = 0
        self._error_count: int = 0

    def subscribe(self, topic_pattern: str, handler: Callable[[Event], None]) -> None:
        """Subscribes a callable handler to a topic pattern (supports '*' wildcard)."""
        if topic_pattern not in self._handlers:
            self._handlers[topic_pattern] = []
        if handler not in self._handlers[topic_pattern]:
            self._handlers[topic_pattern].append(handler)

    def unsubscribe(self, topic_pattern: str, handler: Callable[[Event], None]) -> bool:
        """Unsubscribes a callable handler from a topic pattern."""
        if topic_pattern in self._handlers and handler in self._handlers[topic_pattern]:
            self._handlers[topic_pattern].remove(handler)
            if not self._handlers[topic_pattern]:
                del self._handlers[topic_pattern]
            return True
        return False

    def publish(self, topic: str, payload: Any) -> Event:
        """Publishes an event to all handlers matching the topic."""
        event = Event(
            topic=topic,
            payload=payload,
            ts_event=time.time_ns(),
        )
        self._published_count += 1

        # Match exact and wildcard topic patterns
        for pattern, handlers in list(self._handlers.items()):
            if pattern == topic or fnmatch.fnmatch(topic, pattern):
                for handler in handlers:
                    try:
                        handler(event)
                        self._delivered_count += 1
                    except Exception:
                        self._error_count += 1

        return event

    def get_stats(self) -> dict[str, Any]:
        """Returns bus telemetry metrics."""
        return {
            "subscribed_patterns": list(self._handlers.keys()),
            "total_handlers": sum(len(h) for h in self._handlers.values()),
            "published_count": self._published_count,
            "delivered_count": self._delivered_count,
            "error_count": self._error_count,
        }

    def clear(self) -> None:
        """Resets all subscriptions and counters."""
        self._handlers.clear()
        self._published_count = 0
        self._delivered_count = 0
        self._error_count = 0


# Default global event bus singleton
default_event_bus = NautilusEventBus()
