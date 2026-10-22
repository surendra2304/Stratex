"""
api/data_shapes.py — Dashboard-Friendly Standardized Data Shapes & Display Helpers.

Formats:
- ISO 8601 UTC timestamps
- Standard float decimal precision
- Display hints: {color: "green|red|yellow|neutral", trend: "up|down|flat"}
"""

import datetime
from typing import Any


def format_iso_timestamp(ts: float | None = None) -> str:
    """Returns ISO 8601 UTC timestamp string."""
    dt = datetime.datetime.utcfromtimestamp(ts) if ts else datetime.datetime.utcnow()
    return dt.isoformat() + "Z"


def create_display_hint(value: float, positive_is_good: bool = True) -> dict[str, str]:
    """Generates UI color and trend hints for numeric indicators."""
    if value > 0:
        return {
            "color": "green" if positive_is_good else "red",
            "trend": "up"
        }
    elif value < 0:
        return {
            "color": "red" if positive_is_good else "green",
            "trend": "down"
        }
    else:
        return {
            "color": "neutral",
            "trend": "flat"
        }


def format_api_response(
    data: Any,
    status: str = "OK",
    error: str | None = None,
    pagination: dict[str, int] | None = None
) -> dict[str, Any]:
    """Wraps endpoint responses in consistent envelope."""
    payload = {
        "status": status,
        "timestamp": format_iso_timestamp(),
        "data": data
    }
    if error:
        payload["error"] = error
    if pagination:
        payload["pagination"] = pagination
    return payload
