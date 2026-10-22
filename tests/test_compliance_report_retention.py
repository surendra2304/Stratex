"""Tests for safe and explicit compliance-report retention cleanup."""

import datetime
import os

import pytest

from autonomy.compliance_reporting import ComplianceReporter


UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _write_report_group(directory, report_date, *, mtime, extensions=("json", "md", "html")):
    paths = []
    for extension in extensions:
        path = directory / f"compliance_daily_{report_date}.{extension}"
        path.write_text(f"test artifact {report_date} {extension}\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))
        paths.append(path)
    return paths


def test_find_expired_daily_reports_lists_only_old_whitelisted_regular_files(tmp_path):
    reporter = ComplianceReporter(str(tmp_path), retention_days=90)
    old_timestamp = (NOW - datetime.timedelta(days=91)).timestamp()
    fresh_timestamp = (NOW - datetime.timedelta(days=10)).timestamp()
    old_group = _write_report_group(tmp_path, "2026-01-01", mtime=old_timestamp)
    fresh_group = _write_report_group(tmp_path, "2026-10-01", mtime=fresh_timestamp)

    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")
    os.utime(unrelated, (old_timestamp, old_timestamp))
    invalid_name = tmp_path / "compliance_daily_not-a-date.json"
    invalid_name.write_text("keep me too", encoding="utf-8")
    os.utime(invalid_name, (old_timestamp, old_timestamp))
    external_dir = tmp_path / "compliance_daily_2025-01-01.json"
    external_dir.mkdir()

    expired = reporter.find_expired_daily_reports(now=NOW)

    assert expired == sorted(path.name for path in old_group)
    assert all(path.exists() for path in old_group + fresh_group)
    assert unrelated.exists()
    assert invalid_name.exists()
    assert external_dir.is_dir()


def test_purge_expired_daily_reports_defaults_to_dry_run(tmp_path):
    reporter = ComplianceReporter(str(tmp_path), retention_days=90)
    cutoff_old = (NOW - datetime.timedelta(days=91)).timestamp()
    old_group = _write_report_group(tmp_path, "2025-12-01", mtime=cutoff_old)

    proposed = reporter.purge_expired_daily_reports(now=NOW)

    assert proposed == sorted(path.name for path in old_group)
    assert all(path.exists() for path in old_group)


def test_purge_expired_daily_reports_deletes_only_expired_daily_artifacts_when_explicitly_enabled(tmp_path):
    reporter = ComplianceReporter(str(tmp_path), retention_days=90)
    cutoff_old = (NOW - datetime.timedelta(days=91)).timestamp()
    old_group = _write_report_group(tmp_path, "2025-12-01", mtime=cutoff_old)
    fresh_group = _write_report_group(
        tmp_path,
        "2026-10-01",
        mtime=(NOW - datetime.timedelta(days=30)).timestamp(),
    )
    unrelated = tmp_path / "quarterly_audit.json"
    unrelated.write_text("retain", encoding="utf-8")
    os.utime(unrelated, (cutoff_old, cutoff_old))

    deleted = reporter.purge_expired_daily_reports(now=NOW, dry_run=False)

    assert deleted == sorted(path.name for path in old_group)
    assert all(not path.exists() for path in old_group)
    assert all(path.exists() for path in fresh_group)
    assert unrelated.exists()


def test_a_fresh_artifact_keeps_the_entire_report_date_group(tmp_path):
    reporter = ComplianceReporter(str(tmp_path), retention_days=90)
    old_timestamp = (NOW - datetime.timedelta(days=120)).timestamp()
    fresh_timestamp = (NOW - datetime.timedelta(days=2)).timestamp()
    group = _write_report_group(tmp_path, "2025-11-01", mtime=old_timestamp)
    os.utime(group[1], (fresh_timestamp, fresh_timestamp))

    assert reporter.find_expired_daily_reports(now=NOW) == []
    assert reporter.purge_expired_daily_reports(now=NOW, dry_run=False) == []
    assert all(path.exists() for path in group)


@pytest.mark.parametrize("bad_now", [datetime.datetime(2026, 10, 21), "2026-10-21"])
def test_retention_methods_reject_naive_or_non_datetime_now(tmp_path, bad_now):
    reporter = ComplianceReporter(str(tmp_path), retention_days=90)

    with pytest.raises(ValueError, match="timezone-aware datetime"):
        reporter.find_expired_daily_reports(now=bad_now)

    with pytest.raises(ValueError, match="timezone-aware datetime"):
        reporter.purge_expired_daily_reports(now=bad_now, dry_run=True)


@pytest.mark.parametrize("bad_dry_run", [0, 1, None, "false"])
def test_purge_requires_an_explicit_boolean_for_deletion_mode(tmp_path, bad_dry_run):
    reporter = ComplianceReporter(str(tmp_path), retention_days=90)

    with pytest.raises(ValueError, match="dry_run must be a boolean"):
        reporter.purge_expired_daily_reports(now=NOW, dry_run=bad_dry_run)


def test_retention_days_rejects_boolean_and_non_positive_values(tmp_path):
    for invalid in (True, 0, -1, 1.5):
        with pytest.raises(ValueError, match="retention_days must be a positive integer"):
            ComplianceReporter(str(tmp_path), retention_days=invalid)
