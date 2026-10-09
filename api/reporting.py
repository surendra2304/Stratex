"""
api/reporting.py — Reporting & Intelligent Alerts API Blueprint.

Endpoints:
- GET /api/v1/reports/daily/latest : Latest daily performance report.
- GET /api/v1/reports/daily/<date> : Daily report for a specific ISO date.
- GET /api/v1/reports/weekly/latest : Latest weekly executive report.
- GET /api/v1/reports/monthly/latest : Latest monthly audit report.
- GET /api/v1/reports/alerts : Recent intelligent alerts log.
"""

import datetime
import re

from flask import Blueprint, Response, jsonify

from alerting.intelligent_alerts import IntelligentAlertEngine
from api.auth import require_permission
from api.data_shapes import format_api_response
from api.validation import RequestValidationError, query_choice, query_int
from reporting.daily_report import DailyReportGenerator
from reporting.periodic_reports import PeriodicReportGenerator

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

reporting_bp = Blueprint("reporting_api", __name__, url_prefix="/api/v1/reports")

daily_gen = DailyReportGenerator()
periodic_gen = PeriodicReportGenerator()
alert_engine = IntelligentAlertEngine()


@reporting_bp.route("/daily/latest", methods=["GET"])
@require_permission("read")
def get_latest_daily_report():
    fmt = query_choice("format", "json", ("json", "markdown", "html"))
    report = daily_gen.generate_daily_report()

    if fmt == "markdown":
        return Response(daily_gen._render_markdown(report), mimetype="text/markdown")
    elif fmt == "html":
        return Response(daily_gen._render_html(report), mimetype="text/html")
    return jsonify(format_api_response(report))


@reporting_bp.route("/daily/<date_str>", methods=["GET"])
@require_permission("read")
def get_specific_daily_report(date_str: str):
    fmt = query_choice("format", "json", ("json", "markdown", "html"))
    # The date becomes part of three file names the generator writes; only a
    # real calendar date may reach it (no arbitrary names, no markup).
    if not _DATE_RE.fullmatch(date_str):
        raise RequestValidationError("date_str", "must be a date in YYYY-MM-DD format")
    try:
        datetime.date.fromisoformat(date_str)
    except ValueError:
        raise RequestValidationError("date_str", "is not a valid calendar date") from None
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
    limit = query_int("limit", 20, min=1, max=500)
    alerts = alert_engine.get_recent_alerts(limit=limit)
    return jsonify(format_api_response(alerts))
