"""
api/export.py — Historical Data Export Blueprint (CSV & JSON).

Endpoints:
- GET /api/v1/export/trades : Exports closed trade records.
- GET /api/v1/export/equity : Exports equity curve data points.
- GET /api/v1/export/advisory-log : Exports AI advisory decisions log.
- GET /api/v1/export/risk-events : Exports risk triggers and events.
"""

import csv
import io
import json
import os

from flask import Blueprint, Response, jsonify

from api.auth import require_permission
from api.validation import query_choice, query_int
from atomic_io import read_jsonl

export_bp = Blueprint("export_api", __name__, url_prefix="/api/v1/export")


# Records per response: exports are paged (offset/limit) instead of loading
# and returning an arbitrarily large ledger in one body.
DEFAULT_EXPORT_LIMIT = 10_000
MAX_EXPORT_LIMIT = 100_000
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Neutralize spreadsheet formula injection in exported text cells."""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True)
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def _export_jsonl_file(filepath: str, format_type: str, root_field: str = "data"):
    """Reads a JSONL file and returns one page of it as CSV or JSON.

    Unreadable lines (e.g. a torn tail left by a crash) are skipped and
    counted instead of failing the whole export with a 500.
    """
    offset = query_int("offset", 0, min=0, max=10**9)
    limit = query_int("limit", DEFAULT_EXPORT_LIMIT, min=1, max=MAX_EXPORT_LIMIT)
    if not os.path.exists(filepath):
        return jsonify({"status": "OK", "count": 0, "total": 0, root_field: []})

    try:
        result = read_jsonl(filepath, expected_type=dict)
    except OSError:
        return jsonify({"status": "ERROR", "error": "EXPORT_SOURCE_UNREADABLE"}), 503
    total = len(result.records)
    records = result.records[offset:offset + limit]
    page = {
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(records) < total,
        "skipped_lines": result.skipped,
    }

    if format_type == "csv" and records:
        fieldnames = list(dict.fromkeys(key for record in records for key in record))
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows({key: _csv_safe(value) for key, value in record.items()} for record in records)
        headers = {
            "Content-Disposition": f"attachment;filename={os.path.basename(filepath)}.csv",
            "X-Export-Total": str(total),
            "X-Export-Truncated": "true" if page["truncated"] else "false",
        }
        return Response(output.getvalue(), mimetype="text/csv", headers=headers)

    return jsonify({"status": "OK", "count": len(records), **page, root_field: records})


def _format_arg() -> str:
    return query_choice("format", "json", ("json", "csv"))


@export_bp.route("/trades", methods=["GET"])
@require_permission("read")
def export_trades():
    fmt = _format_arg()
    return _export_jsonl_file("paper_trade_ledger.jsonl", fmt, "trades")


@export_bp.route("/equity", methods=["GET"])
@require_permission("read")
def export_equity():
    fmt = _format_arg()
    return _export_jsonl_file("live_equity_curve.jsonl", fmt, "equity_points")


@export_bp.route("/advisory-log", methods=["GET"])
@require_permission("read")
def export_advisory():
    fmt = _format_arg()
    return _export_jsonl_file("advisory_log.jsonl", fmt, "advisory_entries")


@export_bp.route("/risk-events", methods=["GET"])
@require_permission("read")
def export_risk():
    fmt = _format_arg()
    return _export_jsonl_file("production_alerts.jsonl", fmt, "risk_events")
