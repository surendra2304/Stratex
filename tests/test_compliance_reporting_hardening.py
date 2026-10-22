"""Regression tests for compliance-report integrity and truthful measurements."""

import json

import pytest

from autonomy.compliance_reporting import ComplianceReporter


def test_daily_dossier_signature_covers_the_final_payload_and_detects_tampering(tmp_path):
    reporter = ComplianceReporter(reports_dir=str(tmp_path))
    dossier = reporter.generate_daily_compliance_dossier(
        trades_count=2,
        daily_pnl=12.5,
        max_drawdown_reached=3.0,
        decisions_count=4,
    )

    assert dossier["regulatory_invariants"]["cryptographic_signatures_verified"] is True
    assert reporter.verify_signed_payload(dossier) is True

    saved_path = tmp_path / f"compliance_daily_{dossier['timestamp'][:10]}.json"
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert reporter.verify_signed_payload(saved) is True

    tampered = json.loads(json.dumps(dossier))
    tampered["metrics"]["net_pnl_dollars"] = 99_999.0
    assert reporter.verify_signed_payload(tampered) is False

    malformed_signature = json.loads(json.dumps(dossier))
    malformed_signature["signature"] = "é"
    assert reporter.verify_signed_payload(malformed_signature) is False


def test_compliance_voice_summary_does_not_invent_a_pnl_percentage_and_html_escapes_text(tmp_path):
    reporter = ComplianceReporter(reports_dir=str(tmp_path))
    dossier = reporter.generate_daily_compliance_dossier(
        trades_count=2,
        daily_pnl=1234.56,
        max_drawdown_reached=2.0,
        decisions_count=3,
        best_strategy="<script>alert(1)</script>",
    )

    assert "net profit of $1,234.56" in dossier["voice_summary"]
    assert "gained 2469.1 percent" not in dossier["voice_summary"]

    html_path = tmp_path / f"compliance_daily_{dossier['timestamp'][:10]}.html"
    rendered = html_path.read_text(encoding="utf-8")
    assert "<script>" not in rendered.lower()
    assert "&lt;script&gt;" in rendered.lower()


def test_compliance_report_rejects_non_finite_and_invalid_metrics(tmp_path):
    reporter = ComplianceReporter(reports_dir=str(tmp_path))

    with pytest.raises(ValueError, match="daily_pnl must be a finite number"):
        reporter.generate_daily_compliance_dossier(
            trades_count=1,
            daily_pnl=float("nan"),
            max_drawdown_reached=1.0,
            decisions_count=1,
        )

    with pytest.raises(ValueError, match="max_drawdown_reached must be >= 0.0"):
        reporter.generate_daily_compliance_dossier(
            trades_count=1,
            daily_pnl=1.0,
            max_drawdown_reached=-1.0,
            decisions_count=1,
        )

    with pytest.raises(ValueError, match="trades_count must be a non-negative integer"):
        reporter.generate_daily_compliance_dossier(
            trades_count=True,
            daily_pnl=1.0,
            max_drawdown_reached=1.0,
            decisions_count=1,
        )


def test_quarterly_package_signature_is_verifiable(tmp_path):
    reporter = ComplianceReporter(reports_dir=str(tmp_path))
    package = reporter.generate_quarterly_audit_package()
    assert package["status"] == "GENERATED_SIGNED"
    assert reporter.verify_signed_payload(package) is True
