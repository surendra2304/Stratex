"""
tests/test_reporting_alerts.py — Tests for Autonomous Reporting & Intelligent Alerting.

Verifies:
1. Voice Summaries generation (spoken phrasing, rounding).
2. Advisory Impact attribution calculations.
3. Daily, weekly, and monthly report generators (JSON, Markdown, HTML persistence).
4. IntelligentAlertEngine context-aware routing, severity filtering, and deduplication.
5. AnomalyDetectionEngine 2-sigma performance drop and 3-sigma volatility anomaly detection.
6. Reporting API Endpoints (/api/v1/reports/daily/latest, /weekly/latest, /alerts).
"""

import os
import json
import tempfile
import pytest

from dashboard import app
from reporting.voice_summaries import generate_daily_voice_summary, generate_trade_voice_snippet
from reporting.advisory_impact import AdvisoryImpactAnalyzer
from reporting.daily_report import DailyReportGenerator
from reporting.periodic_reports import PeriodicReportGenerator
from alerting.intelligent_alerts import IntelligentAlertEngine
from alerting.anomaly_detection import AnomalyDetectionEngine


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_voice_summaries_generation():
    voice = generate_daily_voice_summary(net_pnl_pct=1.2, best_strategy="strategy_supertrend", trades_count=15, risk_headroom_pct=12.5)
    assert "gained 1.2 percent" in voice
    assert "Supertrend was your strongest" in voice

    trade_snip = generate_trade_voice_snippet("OPEN", "BTC/USDT", "LONG", 60200.0)
    assert "Position opened: BTC long at 60,200" in trade_snip


def test_advisory_impact_attribution():
    analyzer = AdvisoryImpactAnalyzer()
    attr = analyzer.evaluate_decision_impact("DEC_001", "stop_loss_pct", "supertrend", pre_trades_pnl=1.0, post_trades_pnl=1.4)
    assert attr.estimated_alpha_pct == 0.4
    assert attr.status == "POSITIVE_CONTRIBUTION"


def test_daily_and_periodic_reports():
    with tempfile.TemporaryDirectory() as tmpdir:
        daily_gen = DailyReportGenerator(reports_dir=f"{tmpdir}/daily")
        rep = daily_gen.generate_daily_report(date_str="2026-08-27")
        assert rep["report_date"] == "2026-08-27"
        assert os.path.exists(f"{tmpdir}/daily/report_2026-08-27.json")
        assert os.path.exists(f"{tmpdir}/daily/report_2026-08-27.md")
        assert os.path.exists(f"{tmpdir}/daily/report_2026-08-27.html")

        periodic_gen = PeriodicReportGenerator(base_reports_dir=tmpdir)
        w_rep = periodic_gen.generate_weekly_report(week_str="2026-W35")
        assert w_rep["report_type"] == "WEEKLY_PERFORMANCE_REVIEW"

        m_rep = periodic_gen.generate_monthly_report(month_str="2026-08")
        assert m_rep["report_type"] == "MONTHLY_EXECUTIVE_AUDIT"


def test_intelligent_alerting_and_deduplication():
    engine = IntelligentAlertEngine(dedup_window_seconds=300, max_alerts_per_hour=5)

    # 1. Emit Alert
    a1 = engine.emit_alert(
        severity="HIGH",
        category="RISK",
        title="Drawdown Spike",
        message="Drawdown reached 7.5%",
        context="High volatility market regime",
        recommendation="Throttle scalper sizing"
    )
    assert a1 is not None
    assert a1.severity == "HIGH"

    # 2. Duplicate Alert within window -> Suppressed
    a2 = engine.emit_alert(
        severity="HIGH",
        category="RISK",
        title="Drawdown Spike",
        message="Drawdown reached 7.5%"
    )
    assert a2 is None  # Suppressed duplicate


def test_statistical_anomaly_detection():
    alert_engine = IntelligentAlertEngine()
    detector = AnomalyDetectionEngine(alert_engine=alert_engine)

    # Performance anomaly (< 2 sigma)
    hist_win_rates = [60.0, 62.0, 58.0, 65.0, 61.0, 59.0, 63.0, 60.0, 64.0, 61.0]
    res = detector.check_strategy_performance_anomaly("strategy_scalper", current_win_rate=45.0, historical_win_rates=hist_win_rates)
    assert res is not None
    assert res["severity"] == "HIGH"

    # Volatility anomaly (> 3 sigma)
    hist_atrs = [1.0, 1.1, 0.9, 1.0, 1.2, 0.95, 1.05, 1.0, 1.1, 0.9, 1.0, 1.15, 1.0, 0.95, 1.05]
    res_vol = detector.check_market_volatility_anomaly("BTC/USDT", current_atr=3.5, historical_atrs=hist_atrs)
    assert res_vol is not None
    assert res_vol["severity"] == "CRITICAL"


def test_reporting_api_endpoints(client):
    headers = {"X-API-Key": "read_key_default_secret_123"}

    # Latest daily report
    res = client.get("/api/v1/reports/daily/latest", headers=headers)
    assert res.status_code == 200
    assert "voice_summary" in res.get_json()["data"]

    # Markdown format
    res_md = client.get("/api/v1/reports/daily/latest?format=markdown", headers=headers)
    assert res_md.status_code == 200
    assert "Daily Performance Report" in res_md.get_data(as_text=True)

    # Weekly report
    res_w = client.get("/api/v1/reports/weekly/latest", headers=headers)
    assert res_w.status_code == 200

    # Alerts history
    res_alt = client.get("/api/v1/reports/alerts", headers=headers)
    assert res_alt.status_code == 200
