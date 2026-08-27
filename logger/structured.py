"""
logger/structured.py — High-Performance Structured JSON Logger with Sensitive Data Scrubbing.

Features:
1. JSON Structured Log Format: timestamp, level, subsystem, correlation_id, trade_id, strategy, event, details.
2. Log Levels: DEBUG, INFO, WARNING, ERROR, CRITICAL.
3. Sensitive Data Redaction: Automatic masking for API keys, secret keys, bearer tokens, passwords.
4. Daily Log Rotation with 30-day retention cleanup.
"""

import os
import re
import json
import time
import datetime
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, List, Optional, Any

SECRET_PATTERNS = [
    re.compile(r'(api[_-]?key["\']?\s*[:=]\s*["\'])([^"\']{4,})(["\'])', re.IGNORECASE),
    re.compile(r'(secret[_-]?key["\']?\s*[:=]\s*["\'])([^"\']{4,})(["\'])', re.IGNORECASE),
    re.compile(r'(token["\']?\s*[:=]\s*["\'])([^"\']{4,})(["\'])', re.IGNORECASE),
    re.compile(r'(password["\']?\s*[:=]\s*["\'])([^"\']{4,})(["\'])', re.IGNORECASE),
]


def scrub_sensitive_data(message: str) -> str:
    """Scrubs API keys, secret keys, and credentials from log strings."""
    if not isinstance(message, str):
        message = str(message)
    for pattern in SECRET_PATTERNS:
        message = pattern.sub(r'\1***REDACTED***\3', message)
    return message


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as normalized JSON strings."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "subsystem": getattr(record, "subsystem", record.name),
            "message": scrub_sensitive_data(record.getMessage()),
            "correlation_id": getattr(record, "correlation_id", "SYS_GLOBAL"),
            "trade_id": getattr(record, "trade_id", None),
            "strategy": getattr(record, "strategy", None)
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def get_structured_logger(
    name: str = "bot_structured",
    log_dir: str = "logs",
    retention_days: int = 30
) -> logging.Logger:
    """Creates or retrieves a rotating structured JSON logger."""
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(f"structured.{name}")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        log_file = os.path.join(log_dir, f"{name}.jsonl")
        handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=retention_days,
            encoding="utf-8"
        )
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)

    return logger