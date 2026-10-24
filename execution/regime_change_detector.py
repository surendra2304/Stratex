"""
Stratex/execution/regime_change_detector.py — Stratex Regime Change Detector.

Market regime detection, structural break identification, volatility clustering,
and regime transition forecasting.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class RegimePeriod:
    """A market regime period."""
    regime_id: str
    regime_type: str = "bull"  # bull, bear, sideways, volatile, crash
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    confidence: float = 0.0
    characteristics: dict = field(default_factory=dict)


@dataclass
class StructuralBreak:
    """A detected structural break."""
    break_id: str
    symbol: str
    break_time: float = field(default_factory=time.time)
    break_type: str = "volatility"  # volatility, trend, correlation, volume
    severity: str = "medium"
    description: str = ""


@dataclass
class RegimeForecast:
    """A regime transition forecast."""
    forecast_id: str
    symbol: str
    current_regime: str
    predicted_regime: str
    transition_probability: float = 0.0
    expected_time_to_transition: float = 0.0
    factors: list[str] = field(default_factory=list)


class RegimeChangeDetector:
    """Engine for market regime detection and transition forecasting."""

    def __init__(self) -> None:
        self._regimes: dict[str, RegimePeriod] = {}
        self._breaks: list[StructuralBreak] = []
        self._forecasts: list[RegimeForecast] = []

    def record_regime(self, symbol: str, regime_type: str, confidence: float = 0.5,
                      characteristics: dict | None = None) -> str:
        """Record a market regime."""
        regime_id = f"reg_{uuid.uuid4().hex[:12]}"
        regime = RegimePeriod(
            regime_id=regime_id, regime_type=regime_type,
            confidence=confidence, characteristics=characteristics or {},
        )
        self._regimes[symbol] = regime
        return regime_id

    def detect_break(self, symbol: str, break_type: str = "volatility",
                      severity: str = "medium", description: str = "") -> StructuralBreak:
        """Detect a structural break."""
        break_id = f"brk_{uuid.uuid4().hex[:12]}"
        brk = StructuralBreak(
            break_id=break_id, symbol=symbol, break_type=break_type,
            severity=severity, description=description,
        )
        self._breaks.append(brk)
        return brk

    def forecast_transition(self, symbol: str, current_regime: str,
                            predicted_regime: str, transition_probability: float = 0.3,
                            expected_time: float = 30.0,
                            factors: list[str] | None = None) -> RegimeForecast:
        """Forecast a regime transition."""
        forecast_id = f"fc_{uuid.uuid4().hex[:12]}"
        forecast = RegimeForecast(
            forecast_id=forecast_id, symbol=symbol,
            current_regime=current_regime, predicted_regime=predicted_regime,
            transition_probability=transition_probability,
            expected_time_to_transition=expected_time,
            factors=factors or [],
        )
        self._forecasts.append(forecast)
        return forecast

    def get_current_regime(self, symbol: str) -> dict[str, Any]:
        """Get current regime for a symbol."""
        regime = self._regimes.get(symbol)
        if not regime:
            raise HTTPException(status_code=404, detail="No regime recorded")
        return {
            "symbol": symbol,
            "regime_type": regime.regime_type,
            "confidence": regime.confidence,
            "characteristics": regime.characteristics,
        }

    def get_breaks(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Get detected breaks."""
        breaks = self._breaks
        if symbol:
            breaks = [b for b in breaks if b.symbol == symbol]
        return [
            {"symbol": b.symbol, "break_type": b.break_type,
             "severity": b.severity, "description": b.description[:80]}
            for b in breaks
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_regimes": len(self._regimes),
            "total_breaks": len(self._breaks),
            "total_forecasts": len(self._forecasts),
            "engine_status": "operational",
        }


engine = RegimeChangeDetector()


@router.post("/trading/regime/detect")
async def record_regime(symbol: str, regime_type: str, confidence: float = 0.5,
                        characteristics: dict | None = None):
    """Record a market regime."""
    regime_id = engine.record_regime(symbol, regime_type, confidence, characteristics)
    return {"symbol": symbol, "regime_type": regime_type, "confidence": confidence}


@router.post("/trading/regime/breaks")
async def detect_break(symbol: str, break_type: str = "volatility",
                       severity: str = "medium", description: str = ""):
    """Detect a structural break."""
    brk = engine.detect_break(symbol, break_type, severity, description)
    return {"break_id": brk.break_id, "symbol": symbol, "severity": severity}


@router.post("/trading/regime/forecast")
async def forecast_transition(symbol: str, current_regime: str, predicted_regime: str,
                              transition_probability: float = 0.3, expected_time: float = 30.0,
                              factors: list[str] | None = None):
    """Forecast a regime transition."""
    forecast = engine.forecast_transition(symbol, current_regime, predicted_regime,
                                          transition_probability, expected_time, factors)
    return {
        "symbol": symbol,
        "predicted_regime": forecast.predicted_regime,
        "probability": forecast.transition_probability,
    }


@router.get("/trading/regime/current/{symbol}")
async def get_current_regime(symbol: str):
    """Get current regime."""
    return engine.get_current_regime(symbol)


@router.get("/trading/regime/breaks")
async def get_breaks(symbol: str | None = None):
    """Get detected breaks."""
    return {"breaks": engine.get_breaks(symbol)}


@router.get("/trading/regime/report")
async def regime_report():
    """Get regime detector report."""
    return engine.get_report()
