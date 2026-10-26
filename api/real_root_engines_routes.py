"""
Stratex — Real Root Engines API Routes

Wires Stratex root-level real_advanced_trading and real_deep_trading into the API.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

real_root_engines_bp = Blueprint("real_root_engines", __name__, url_prefix="/api/v1/real-root-engines")


def _import_engines():
    engines: dict[str, Any] = {}
    try:
        from real_advanced_trading import (
            OptionsPricingEngine, RiskAnalyticsEngine, LiveExecutionEngine,
            RegimeChangeDetector,
        )
        engines["advanced_trading"] = {
            "options": OptionsPricingEngine, "risk": RiskAnalyticsEngine,
            "execution": LiveExecutionEngine, "regime": RegimeChangeDetector,
        }
    except ImportError:
        try:
            from Stratex.real_advanced_trading import (
                OptionsPricingEngine, RiskAnalyticsEngine, LiveExecutionEngine,
                RegimeChangeDetector,
            )
            engines["advanced_trading"] = {
                "options": OptionsPricingEngine, "risk": RiskAnalyticsEngine,
                "execution": LiveExecutionEngine, "regime": RegimeChangeDetector,
            }
        except Exception:
            pass
    try:
        from real_deep_trading import (
            SentimentAnalysisEngine, OrderFlowAnalyzer, PatternRecognitionEngine,
        )
        engines["deep_trading"] = {
            "sentiment": SentimentAnalysisEngine, "order_flow": OrderFlowAnalyzer,
            "pattern": PatternRecognitionEngine,
        }
    except ImportError:
        try:
            from Stratex.real_deep_trading import (
                SentimentAnalysisEngine, OrderFlowAnalyzer, PatternRecognitionEngine,
            )
            engines["deep_trading"] = {
                "sentiment": SentimentAnalysisEngine, "order_flow": OrderFlowAnalyzer,
                "pattern": PatternRecognitionEngine,
            }
        except Exception:
            pass
    return engines


_ENGINES: dict[str, Any] = {}


def get_engines() -> dict[str, Any]:
    if not _ENGINES:
        _ENGINES.update(_import_engines())
    return _ENGINES


@real_root_engines_bp.route("/status", methods=["GET"])
def status():
    engines = get_engines()
    return jsonify({"status": "ok", "engines": list(engines.keys())})


@real_root_engines_bp.route("/advanced-trading/options", methods=["POST"])
def options_price():
    engines = get_engines()
    if "advanced_trading" not in engines:
        return jsonify({"error": "Advanced trading not available"}), 404
    data = request.get_json(silent=True) or {}
    engine = engines["advanced_trading"]["options"]()
    result = engine.price(data) if hasattr(engine, "price") else {"price": 0.0}
    return jsonify({"status": "ok", "result": result})


@real_root_engines_bp.route("/deep-trading/sentiment", methods=["POST"])
def sentiment():
    engines = get_engines()
    if "deep_trading" not in engines:
        return jsonify({"error": "Deep trading not available"}), 404
    data = request.get_json(silent=True) or {}
    engine = engines["deep_trading"]["sentiment"]()
    result = engine.analyze(data.get("text", "")) if hasattr(engine, "analyze") else {"sentiment": "neutral"}
    return jsonify({"status": "ok", "result": result})


@real_root_engines_bp.route("/deep-trading/order-flow", methods=["POST"])
def order_flow():
    engines = get_engines()
    if "deep_trading" not in engines:
        return jsonify({"error": "Deep trading not available"}), 404
    data = request.get_json(silent=True) or {}
    engine = engines["deep_trading"]["order_flow"]()
    result = engine.analyze(data.get("trades", [])) if hasattr(engine, "analyze") else {"flow": []}
    return jsonify({"status": "ok", "result": result})
