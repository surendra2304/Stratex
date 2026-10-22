"""NautilusTrader-inspired event-sourced reconciliation boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    event_id: str
    sequence: int
    kind: str
    payload: dict


class EventLog:
    def __init__(self):
        self._events: list[ExecutionEvent] = []
        self._seen: set[str] = set()

    def append(self, event: ExecutionEvent) -> bool:
        if event.event_id in self._seen:
            return False
        if self._events and event.sequence <= self._events[-1].sequence:
            raise ValueError("event sequence must be strictly increasing")
        self._events.append(event)
        self._seen.add(event.event_id)
        return True

    def replay(self) -> list[ExecutionEvent]:
        return list(self._events)
