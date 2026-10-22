from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Schedule:
    interval_seconds: float
    jitter_seconds: float = 0.0


class CooperativeScheduler:
    """Background scheduler with explicit stop, exception isolation and overlap prevention."""

    def __init__(self):
        self._tasks: list[tuple[Schedule, Callable[[], None], str]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running: set[str] = set()
        self._lock = threading.RLock()

    def add(self, name: str, schedule: Schedule, callback: Callable[[], None]) -> None:
        if schedule.interval_seconds <= 0:
            raise ValueError("interval must be positive")
        with self._lock:
            self._tasks.append((schedule, callback, name))

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="stratex-scheduler", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        t = self._thread
        if t:
            t.join(timeout=timeout)

    def _run(self) -> None:
        next_run = {name: time.monotonic() for _, _, name in self._tasks}
        while not self._stop.wait(0.25):
            now = time.monotonic()
            for schedule, callback, name in list(self._tasks):
                if now < next_run.get(name, now):
                    continue
                with self._lock:
                    if name in self._running:
                        next_run[name] = now + schedule.interval_seconds
                        continue
                    self._running.add(name)
                try:
                    callback()
                except Exception:  # noqa: BLE001, S110
                    pass

                finally:
                    with self._lock:
                        self._running.discard(name)
                next_run[name] = now + schedule.interval_seconds
