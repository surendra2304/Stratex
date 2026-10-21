"""Tests for bounded advisory-scheduler retry behavior."""

import threading
from unittest.mock import MagicMock

import pytest

from advisory_scheduler import AdvisoryScheduler
from ai_universe_client import AIUniverseClient


def _scheduler(**kwargs):
    options = {"interval_hours": 4.0}
    options.update(kwargs)
    return AdvisoryScheduler(
        client=MagicMock(spec=AIUniverseClient),
        shadow_mode=True,
        **options,
    )


def test_failed_consultations_use_capped_exponential_backoff():
    scheduler = _scheduler(
        retry_backoff_base_seconds=5.0,
        retry_backoff_max_seconds=20.0,
    )

    assert scheduler._schedule_retry(now_monotonic=100.0) == 5.0
    assert scheduler._retry_delay_remaining(now_monotonic=100.0) == 5.0
    assert scheduler._retry_delay_remaining(now_monotonic=104.0) == 1.0
    assert scheduler._retry_delay_remaining(now_monotonic=105.0) == 0.0

    assert scheduler._schedule_retry(now_monotonic=105.0) == 10.0
    assert scheduler._schedule_retry(now_monotonic=115.0) == 20.0
    assert scheduler._schedule_retry(now_monotonic=135.0) == 20.0
    assert scheduler._consecutive_failures == 4


def test_successful_cycle_resets_failure_backoff():
    scheduler = _scheduler(
        retry_backoff_base_seconds=2.0,
        retry_backoff_max_seconds=8.0,
    )
    scheduler._schedule_retry(now_monotonic=10.0)
    scheduler._schedule_retry(now_monotonic=12.0)
    assert scheduler._consecutive_failures == 2

    scheduler._clear_retry_backoff()

    assert scheduler._consecutive_failures == 0
    assert scheduler._next_attempt_monotonic == 0.0
    assert scheduler._retry_delay_remaining(now_monotonic=0.0) == 0.0
    assert scheduler._schedule_retry(now_monotonic=20.0) == 2.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"retry_backoff_base_seconds": 0.0},
        {"retry_backoff_base_seconds": -1.0},
        {"retry_backoff_base_seconds": float("nan")},
        {"retry_backoff_base_seconds": float("inf")},
        {"retry_backoff_base_seconds": True},
        {"retry_backoff_max_seconds": 0.0},
        {"retry_backoff_max_seconds": float("nan")},
        {"retry_backoff_max_seconds": True},
        {"retry_backoff_base_seconds": 30.0, "retry_backoff_max_seconds": 10.0},
        {"interval_hours": 0.0},
        {"interval_hours": float("nan")},
        {"interval_hours": float("inf")},
    ],
)
def test_scheduler_rejects_invalid_interval_and_backoff_settings(kwargs):
    with pytest.raises(ValueError):
        _scheduler(**kwargs)


def test_worker_respects_retry_backoff_before_repeating_failed_startup_consultation(monkeypatch):
    scheduler = _scheduler(
        retry_backoff_base_seconds=0.05,
        retry_backoff_max_seconds=0.05,
    )
    calls = []
    second_attempt_finished = threading.Event()

    def failed_consultation(reason="SCHEDULED"):
        calls.append(reason)
        if len(calls) == 2:
            second_attempt_finished.set()
            scheduler._stop_event.set()
        return None

    scheduler.run_consultation_cycle = failed_consultation
    scheduler._worker_loop(check_interval_sec=0.005)

    assert second_attempt_finished.is_set()
    assert len(calls) == 2
    assert calls == ["STARTUP_SCHEDULED", "STARTUP_SCHEDULED"]
    assert scheduler._consecutive_failures == 2
