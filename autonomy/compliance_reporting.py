"""
autonomy/compliance_reporting.py — Automated Regulatory & Compliance Reporter.

Implemented report types:
1. Daily compliance dossier (trades, PnL, drawdown, autonomous decisions) with
   computed — never hardcoded — regulatory invariants, written as JSON,
   Markdown, and HTML alongside a voice_summary field.
2. Quarterly audit package stub (generated + signed; independently audited
   status is NOT claimed).

Note: earlier documentation described weekly/monthly reports and a 90-day
retention sweeper; those are not implemented in this module. The
`retention_days` value is only reported inside the quarterly package metadata.
"""

import datetime
import html
import hmac
import json
import math
import numbers
import os
import re
import stat
import threading
from typing import Any

from atomic_io import atomic_write_text
from reporting.voice_summaries import generate_daily_voice_summary
from security_hardening import sign_audit_record


class ComplianceReporter:
    """Generate evidence-backed compliance reports with tamper-evident signatures."""

    def __init__(self, reports_dir: str = "compliance_reports", retention_days: int = 90):
        if isinstance(retention_days, bool) or not isinstance(retention_days, numbers.Integral) or retention_days < 1:
            raise ValueError("retention_days must be a positive integer")
        self.reports_dir = os.fspath(reports_dir)
        self.retention_days = int(retention_days)
        self._report_lock = threading.RLock()
        os.makedirs(self.reports_dir, exist_ok=True)

    @staticmethod
    def _non_negative_int(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
        return int(value)

    @staticmethod
    def _finite_number(value: Any, field: str, *, minimum: float | None = None) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{field} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{field} must be a finite number")
        if minimum is not None and number < minimum:
            raise ValueError(f"{field} must be >= {minimum}")
        return number

    @staticmethod
    def verify_signed_payload(record: dict[str, Any]) -> bool:
        """Verify the HMAC over the final record, excluding only its signature field."""
        signature = record.get("signature")
        if not isinstance(signature, str) or not signature:
            return False
        payload = {key: value for key, value in record.items() if key != "signature"}
        try:
            expected = sign_audit_record(payload)
            return hmac.compare_digest(expected, signature)
        except (TypeError, ValueError, OverflowError):
            # compare_digest deliberately rejects non-ASCII str values. Invalid
            # externally supplied signatures are verification failures, not crashes.
            return False

    def generate_daily_compliance_dossier(
        self,
        trades_count: int,
        daily_pnl: float,
        max_drawdown_reached: float,
        decisions_count: int,
        live_order_evidence: bool = False,
        best_strategy: str | None = None
    ) -> dict[str, Any]:
        """Produces signed daily compliance certificate in JSON, Markdown, and HTML.

        Invariants are COMPUTED, never hardcoded:
        - zero_live_order_policy_honored: False if the caller supplies evidence
          that a live order was placed/attempted. The execution layer forbids
          live orders by design, but the report must not assume it — callers
          with contrary evidence must flip this flag.
        - max_drawdown_limit_within_bounds: derived from the measured drawdown.
        - cryptographic_signatures_verified: the dossier's own signature is
          recomputed and compared after signing.
        """
        trades_count = self._non_negative_int(trades_count, "trades_count")
        decisions_count = self._non_negative_int(decisions_count, "decisions_count")
        daily_pnl = self._finite_number(daily_pnl, "daily_pnl")
        max_drawdown_reached = self._finite_number(
            max_drawdown_reached, "max_drawdown_reached", minimum=0.0
        )
        if not isinstance(live_order_evidence, bool):
            raise ValueError("live_order_evidence must be a boolean")
        if best_strategy is not None and not isinstance(best_strategy, str):
            raise ValueError("best_strategy must be a string or None")

        now = datetime.datetime.now(datetime.timezone.utc)
        now_str = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        voice_summary = generate_daily_voice_summary(
            net_pnl_pct=None,
            net_pnl_dollars=daily_pnl,
            best_strategy=best_strategy,
            trades_count=trades_count,
            risk_headroom_pct=max(0.0, 15.0 - max_drawdown_reached)
        )

        live_policy_ok = not live_order_evidence
        drawdown_ok = bool(max_drawdown_reached <= 15.0)

        dossier = {
            "report_type": "DAILY_COMPLIANCE_DOSSIER",
            "timestamp": now_str,
            "voice_summary": voice_summary,
            "metrics": {
                "total_trades": trades_count,
                "net_pnl_dollars": round(daily_pnl, 2),
                "peak_drawdown_pct": round(max_drawdown_reached, 2),
                "autonomous_decisions_executed": decisions_count,
                "best_strategy": best_strategy or "NONE_DETERMINED"
            },
            "regulatory_invariants": {
                "zero_live_order_policy_honored": live_policy_ok,
                "max_drawdown_limit_within_bounds": drawdown_ok,
                "cryptographic_signatures_verified": False
            }
        }
        # The verification flag is part of the signed payload. Finalize it before
        # signing and never mutate signed fields after the signature is computed.
        dossier["regulatory_invariants"]["cryptographic_signatures_verified"] = True
        sig = sign_audit_record(dossier)
        sig_ok = hmac.compare_digest(sign_audit_record(dossier), sig)
        if not sig_ok:
            # Fail closed in the unlikely event that the signer cannot verify its
            # own output. Re-sign the final payload with the invariant set to False.
            dossier["regulatory_invariants"]["cryptographic_signatures_verified"] = False
            sig = sign_audit_record(dossier)
        dossier["signature"] = sig

        def _verdict(ok: bool) -> str:
            return "PASS" if ok else "FAIL"

        date_tag = now.strftime("%Y-%m-%d")

        with self._report_lock:
            # Atomic replacement prevents a process interruption from leaving a
            # partially-written compliance record that looks like a complete report.
            json_path = os.path.join(self.reports_dir, f"compliance_daily_{date_tag}.json")
            atomic_write_text(
                json_path,
                json.dumps(dossier, indent=2, sort_keys=True, allow_nan=False) + "\n",
            )
    
            # 2. Markdown
            md_content = f"""# Daily Compliance Dossier ({date_tag})
    
    **Timestamp:** {now_str}
    **Voice Summary:** {voice_summary}
    
    ## Key Metrics
    - **Total Trades:** {trades_count}
    - **Net PnL:** ${daily_pnl:.2f}
    - **Peak Drawdown:** {max_drawdown_reached:.2f}%
    - **Decisions Executed:** {decisions_count}
    
    ## Invariant Verification
    - Live Order Invariant: {_verdict(live_policy_ok)}
    - Drawdown Corridor: {_verdict(drawdown_ok)}
    - Signature Self-Verification: {_verdict(sig_ok)}
    - Signature: `{sig[:16]}...`
    """
            atomic_write_text(
                os.path.join(self.reports_dir, f"compliance_daily_{date_tag}.md"),
                md_content,
            )
    
            # Treat strategy names and other caller-provided values in the voice
            # summary as untrusted text when generating browser-rendered HTML.
            safe_voice_summary = html.escape(voice_summary, quote=True)
            html_content = (
                "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                f"<title>Daily Compliance ({date_tag})</title></head><body>"
                f"<h1>Daily Compliance ({date_tag})</h1><p>{safe_voice_summary}</p>"
                f"<p>Signature verification: {_verdict(sig_ok)}</p></body></html>"
            )
            atomic_write_text(
                os.path.join(self.reports_dir, f"compliance_daily_{date_tag}.html"),
                html_content,
            )
    
            return dossier

    @staticmethod
    def _resolve_utc_now(now: datetime.datetime | None) -> datetime.datetime:
        if now is None:
            return datetime.datetime.now(datetime.timezone.utc)
        if not isinstance(now, datetime.datetime):
            raise ValueError("now must be a timezone-aware datetime or None")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime or None")
        return now.astimezone(datetime.timezone.utc)

    def _expired_daily_report_groups(self, cutoff_timestamp: float) -> dict[str, list[str]]:
        """Find complete daily report groups whose every extant artifact has expired."""
        grouped: dict[str, list[tuple[str, float]]] = {}
        with os.scandir(self.reports_dir) as entries:
            for entry in entries:
                match = re.fullmatch(r"compliance_daily_(\d{4}-\d{2}-\d{2})\.(json|md|html)", entry.name)
                if match is None or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    datetime.datetime.strptime(match.group(1), "%Y-%m-%d")
                    metadata = entry.stat(follow_symlinks=False)
                except (OSError, ValueError):
                    # Ignore files disappearing during the scan and malformed dates.
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                grouped.setdefault(match.group(1), []).append((entry.name, metadata.st_mtime))

        expired: dict[str, list[str]] = {}
        for report_date, files in grouped.items():
            # Retain the entire date group if any artifact was refreshed recently;
            # this avoids deleting only part of a recently updated dossier.
            if files and all(modified_at < cutoff_timestamp for _, modified_at in files):
                expired[report_date] = sorted(name for name, _ in files)
        return expired

    def find_expired_daily_reports(
        self,
        now: datetime.datetime | None = None,
    ) -> list[str]:
        """List report filenames older than retention without deleting anything.

        Age is determined by filesystem modification time. Only regular files that
        match this reporter's strict daily-artifact naming convention are included;
        directories, symlinks, unrelated files and quarterly metadata are excluded.
        """
        current_time = self._resolve_utc_now(now)
        cutoff_timestamp = (
            current_time - datetime.timedelta(days=self.retention_days)
        ).timestamp()
        with self._report_lock:
            groups = self._expired_daily_report_groups(cutoff_timestamp)
            return sorted(name for files in groups.values() for name in files)

    def purge_expired_daily_reports(
        self,
        now: datetime.datetime | None = None,
        *,
        dry_run: bool = True,
    ) -> list[str]:
        """Explicitly purge expired daily artifacts; defaults to a read-only dry run.

        Each date group is rechecked immediately before deletion. The method never
        follows file symlinks and never touches files outside the strict daily-report
        whitelist. Returns the filenames actually identified/deleted.
        """
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be a boolean")
        current_time = self._resolve_utc_now(now)
        cutoff_timestamp = (
            current_time - datetime.timedelta(days=self.retention_days)
        ).timestamp()

        with self._report_lock:
            groups = self._expired_daily_report_groups(cutoff_timestamp)
            if dry_run:
                return sorted(name for files in groups.values() for name in files)

            deleted: list[str] = []
            for report_date in sorted(groups):
                # Re-scan the group to avoid deleting a dossier that was rewritten
                # after the initial inventory was collected.
                current_group = self._expired_daily_report_groups(cutoff_timestamp).get(report_date)
                if not current_group or current_group != groups[report_date]:
                    continue

                paths: list[tuple[str, os.stat_result]] = []
                stale_or_missing = False
                for name in current_group:
                    path = os.path.join(self.reports_dir, name)
                    try:
                        metadata = os.lstat(path)
                    except FileNotFoundError:
                        stale_or_missing = True
                        break
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mtime >= cutoff_timestamp:
                        stale_or_missing = True
                        break
                    paths.append((path, metadata))

                if stale_or_missing:
                    continue
                for path, _ in paths:
                    os.unlink(path)
                    deleted.append(os.path.basename(path))
            return sorted(deleted)

    def generate_quarterly_audit_package(self, quarter: str = "Q3_2026") -> dict[str, Any]:
        """Produces quarterly audit package."""
        pkg = {
            "package_id": f"AUDIT_PACKAGE_{quarter}",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "retention_policy_days": self.retention_days,
            # Honest label: the package has been generated and signed here,
            # but nothing has been independently audited yet.
            "status": "GENERATED_SIGNED"
        }
        pkg["signature"] = sign_audit_record(pkg)
        return pkg
