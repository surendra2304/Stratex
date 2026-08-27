"""
api/export.py — Historical Data Export Blueprint (CSV & JSON).

Endpoints:
- GET /api/v1/export/trades : Exports closed trade records.
- GET /api/v1/export/equity : Exports equity curve data points.
- GET /api/v1/export/advisory-log : Exports AI advisory decisions log.
- GET /api/v1/export/risk-events : Exports risk triggers and events.
"""

import os
import json
import csv
import io
from flask import Blueprint, request, Response, jsonify
from api.auth import require_permission

export_bp = Blueprint("export_api", __name__, url_prefix="/api/v1/export")


def _export_jsonl_file(filepath: str, format_type: str, root_field: str = "data"):
    """Reads JSONL file and formats as CSV or JSON stream."""
    if not os.path.exists(filepath):
        return jsonify({"status": "OK", "count": 0, root_field: []})

    records = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line.strip()))
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

    if format_type.lower() == "csv" and records:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
        return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename={os.path.basename(filepath)}.csv"})

    return jsonify({"status": "OK", "count": len(records), root_field: records})


@export_bp.route("/trades", methods=["GET"])
@require_permission("read")
def export_trades():
    fmt = request.args.get("format", "json")
    return _export_jsonl_file("paper_trade_ledger.jsonl", fmt, "trades")


@export_bp.route("/equity", methods=["GET"])
@require_permission("read")
def export_equity():
    fmt = request.args.get("format", "json")
    return _export_jsonl_file("live_equity_curve.jsonl", fmt, "equity_points")


@export_bp.route("/advisory-log", methods=["GET"])
@require_permission("read")
def export_advisory():
    fmt = request.args.get("format", "json")
    return _export_jsonl_file("advisory_log.jsonl", fmt, "advisory_entries")


@export_bp.route("/risk-events", methods=["GET"])
@require_permission("read")
def export_risk():
    fmt = request.args.get("format", "json")
    return _export_jsonl_file("production_alerts.jsonl", fmt, "risk_events")
