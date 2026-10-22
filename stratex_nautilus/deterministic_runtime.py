from collections import deque
from typing import Callable
from .event_model import MarketEvent, RuntimeState

class DeterministicRuntime:
    """Deterministic event dispatcher for paper/research parity testing."""
    def __init__(self):
        self._queue = deque()
        self.state = RuntimeState()

    def submit(self, event: MarketEvent):
        self._queue.append(event)

    def run(self, handler: Callable[[MarketEvent], None], max_events: int | None = None):
        self.state.running = True
        processed = 0
        try:
            while self._queue and (max_events is None or processed < max_events):
                event = self._queue.popleft()
                if self.state.last_event_ns is not None and event.ts_ns < self.state.last_event_ns:
                    raise ValueError("non-monotonic event timestamp")
                handler(event)
                self.state.last_event_ns = event.ts_ns
                self.state.processed_events += 1
                processed += 1
        except Exception as exc:
            self.state.fault = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.state.running = False
        return processed
