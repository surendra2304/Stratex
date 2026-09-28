import pandas as pd

from paper_forward_runner import filter_closed_candles, is_new_completed_bar
from paper_engine.signal_logger import SignalLogger


def test_filter_closed_candles_excludes_forming_and_invalid_rows():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([1_000, 2_000, 3_000], unit="ms"),
            "close_time": [999, 2_000, "not-a-timestamp"],
            "close": [100.0, 101.0, 102.0],
        }
    )

    result = filter_closed_candles(frame, now_ms=2_000)

    # A bar ending exactly at the cutoff has not passed its close time yet.
    assert result["close"].tolist() == [100.0]
    assert result["timestamp"].tolist() == [pd.Timestamp(1_000, unit="ms")]


def test_filter_closed_candles_does_not_guess_when_close_time_is_missing():
    frame = pd.DataFrame({"timestamp": [pd.Timestamp(1_000, unit="ms")], "close": [100.0]})

    assert filter_closed_candles(frame, now_ms=2_000).empty


def test_paper_runner_accepts_each_completed_bar_once_in_order():
    first = pd.Timestamp("2026-09-28T10:00:00Z")
    later = pd.Timestamp("2026-09-28T11:00:00Z")

    assert is_new_completed_bar(first, None)
    assert not is_new_completed_bar(first, first)
    assert not is_new_completed_bar(first - pd.Timedelta(hours=1), first)
    assert is_new_completed_bar(later, first)


def test_stable_bar_signal_id_is_deduplicated_after_logger_restart(tmp_path):
    log_path = tmp_path / "signals.jsonl"
    first_logger = SignalLogger(str(log_path))
    first_logger.log_signal({"signal_id": "bar-id-1", "decision": "NO_SIGNAL"})

    restarted_logger = SignalLogger(str(log_path))

    assert restarted_logger.has_signal_id("bar-id-1")
    assert not restarted_logger.has_signal_id("bar-id-2")
