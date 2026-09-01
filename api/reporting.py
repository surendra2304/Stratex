"""
api/reporting.py — Reporting & Intelligent Alerts API Blueprint.

Endpoints:
- GET /api/v1/reports/daily/latest : Latest daily performance report.
- GET /api/v1/reports/daily/<date> : Daily report for a specific ISO date.
- GET /api/v1/reports/weekly/latest : Latest weekly executive report.
- GET /api/v1/reports/monthly/latest : Latest monthly audit report.
- GET /api/v1/reports/alerts : Recent intelligent alerts log.
"""

from flask import Blueprint, Response, jsonify, request

from alerting.intelligent_alerts import IntelligentAlertEngine
from api.auth import require_permission
from api.data_shapes import format_api_response
from reporting.daily_report import DailyReportGenerator
from reporting.periodic_reports import PeriodicReportGenerator

reporting_bp = Blueprint("reporting_api", __name__, url_prefix="/api/v1/reports")

daily_gen = DailyReportGenerator()
periodic_gen = PeriodicReportGenerator()
alert_engine = IntelligentAlertEngine()


@reporting_bp.route("/daily/latest", methods=["GET"])
@require_permission("read")
def get_latest_daily_report():
    fmt = request.args.get("format", "json").lower()
    report = daily_gen.generate_daily_report()

    if fmt == "markdown":
        return Response(daily_gen._render_markdown(report), mimetype="text/markdown")
    elif fmt == "html":
        return Response(daily_gen._render_html(report), mimetype="text/html")
    return jsonify(format_api_response(report))


@reporting_bp.route("/daily/<date_str>", methods=["GET"])
@require_permission("read")
def get_specific_daily_report(date_str: str):
    fmt = request.args.get("format", "json").lower()
    report = daily_gen.generate_daily_report(date_str=date_str)

    if fmt == "markdown":
        return Response(daily_gen._render_markdown(report), mimetype="text/markdown")
    elif fmt == "html":
        return Response(daily_gen._render_html(report), mimetype="text/html")
    return jsonify(format_api_response(report))


@reporting_bp.route("/weekly/latest", methods=["GET"])
@require_permission("read")
def get_latest_weekly_report():
    report = periodic_gen.generate_weekly_report()
    return jsonify(format_api_response(report))


@reporting_bp.route("/monthly/latest", methods=["GET"])
@require_permission("read")
def get_latest_monthly_report():
    report = periodic_gen.generate_monthly_report()
    return jsonify(format_api_response(report))


@reporting_bp.route("/alerts", methods=["GET"])
@require_permission("read")
def get_alerts():
    limit = int(request.args.get("limit", 20))
    alerts = alert_engine.get_recent_alerts(limit=limit)
    return jsonify(format_api_response(alerts))
