# quantum_endpoint.py
"""Flask blueprint exposing the quantum advisory endpoint.
This endpoint is read‑only and returns a JSON payload adhering to the
strict result contract defined in `quantum/schemas.py`. It does **not**
trigger any order execution or modify trading state.
"""

from flask import Blueprint, jsonify, request
from quantum.service import QuantumService

# Create a Blueprint – registration is performed in `dashboard.py`
quantum_bp = Blueprint('quantum', __name__)

# Instantiate a single shared service (stateless reads only)
_quantum_service = QuantumService()

@quantum_bp.route('/advisory', methods=['GET'])
def advisory():
    """Return quantum advisory results for a given symbol and timeframe.
    Query parameters:
        symbol (default "BTCUSDT")
        tf    (default "15m")
    The response follows the `QuantumResultSchema` fields and never
    includes any execution‑related keys.
    """
    symbol = request.args.get('symbol', 'BTCUSDT')
    tf = request.args.get('tf', '15m')
    result = _quantum_service.get_advisory(symbol=symbol, tf=tf)
    return jsonify(result)
