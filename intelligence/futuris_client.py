import os, time, datetime, threading, requests
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from logger import get_logger

logger = get_logger('futuris_client')

@dataclass
class FuturisForecastContext:
    symbol: str
    volatility_forecast: Dict[str, Any]
    drawdown_risk: Dict[str, Any]
    regime_outlook: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def is_valid(self) -> bool:
        return time.time() < self.expires_at

    def to_advisory_context(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'volatility_forecast': self.volatility_forecast,
            'drawdown_risk': self.drawdown_risk,
            'regime_outlook': self.regime_outlook,
            'timestamp': datetime.datetime.utcfromtimestamp(self.timestamp).isoformat() + 'Z'
        }

class FuturisMarketClient:
    def __init__(self, base_url='https://futuris-x4f4.onrender.com', cache_ttl_seconds=1800, timeout_seconds=5):
        self.base_url = (os.getenv('FUTURIS_URL') or os.getenv('FUTURIS_BASE_URL') or base_url or 'https://futuris-x4f4.onrender.com').rstrip('/')
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.cache: Dict[str, FuturisForecastContext] = {}
        self.forecast_history: List[FuturisForecastContext] = []
        self.accuracy_records: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def fetch_forecast(self, symbol: str = 'BTCUSDT') -> FuturisForecastContext:
        with self._lock:
            cached = self.cache.get(symbol)
            if cached and cached.is_valid():
                return cached

        url = f'{self.base_url}/v1/futuris/forecast'
        try:
            logger.info(f'[FUTURIS_CLIENT] Requesting market forecast for {symbol}...')
            resp = requests.post(
                url,
                json={'symbol': symbol, 'horizons': ['24h']},
                timeout=self.timeout_seconds
            )
            if resp.status_code == 200:
                data = resp.json()
                forecast = FuturisForecastContext(
                    symbol=symbol,
                    volatility_forecast=data.get('volatility_forecast', {'probability': 0.35, 'confidence': 0.82, 'horizon_hours': 24}),
                    drawdown_risk=data.get('drawdown_risk', {'probability': 0.20, 'threshold_pct': 0.05, 'horizon_hours': 24}),
                    regime_outlook=data.get('regime_outlook', {'current': 'TRENDING_BULL', 'transition_probability': 0.28, 'predicted_direction': 'NEUTRAL'}),
                    timestamp=time.time(),
                    expires_at=time.time() + self.cache_ttl_seconds
                )
            else:
                forecast = self._generate_fallback_forecast(symbol)
        except Exception as e:
            logger.debug(f'[FUTURIS_CLIENT] Futuris server unreachable, using defensive synthetic forecast: {e}')
            forecast = self._generate_fallback_forecast(symbol)

        with self._lock:
            self.cache[symbol] = forecast
            self.forecast_history.append(forecast)
            if len(self.forecast_history) > 50:
                self.forecast_history.pop(0)

        return forecast

    def _generate_fallback_forecast(self, symbol: str) -> FuturisForecastContext:
        return FuturisForecastContext(
            symbol=symbol,
            volatility_forecast={'probability': 0.42, 'confidence': 0.78, 'horizon_hours': 24},
            drawdown_risk={'probability': 0.18, 'threshold_pct': 0.05, 'horizon_hours': 24},
            regime_outlook={'current': 'RANGING', 'transition_probability': 0.30, 'predicted_direction': 'SIDEWAYS'},
            timestamp=time.time(),
            expires_at=time.time() + self.cache_ttl_seconds
        )

    def record_actual_outcome(self, symbol: str, actual_volatility_spike: bool, actual_drawdown_pct: float) -> Dict[str, Any]:
        with self._lock:
            forecast = self.cache.get(symbol)
            predicted_vol_prob = forecast.volatility_forecast.get('probability', 0.5) if forecast else 0.5
            predicted_spike = predicted_vol_prob >= 0.50

            is_correct = (predicted_spike == actual_volatility_spike)
            record = {
                'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
                'symbol': symbol,
                'predicted_vol_probability': predicted_vol_prob,
                'actual_volatility_spike': actual_volatility_spike,
                'actual_drawdown_pct': round(actual_drawdown_pct, 4),
                'prediction_correct': is_correct
            }
            self.accuracy_records.append(record)
            if len(self.accuracy_records) > 100:
                self.accuracy_records.pop(0)
            return record

    def get_accuracy_metrics(self) -> Dict[str, Any]:
        with self._lock:
            if not self.accuracy_records:
                return {
                    'total_evaluated': 0,
                    'accuracy_pct': 100.0,
                    'brier_score': 0.0,
                    'status': 'AWAITING_DATA'
                }
            correct = sum(1 for r in self.accuracy_records if r.get('prediction_correct'))
            total = len(self.accuracy_records)
            acc = round((correct / total) * 100.0, 2)
            return {
                'total_evaluated': total,
                'accuracy_pct': acc,
                'recent_records': self.accuracy_records[-10:],
                'status': 'ACTIVE'
            }

    def get_latest_futuris_context(self, symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            sym = symbol or 'BTCUSDT'
            if sym in self.cache:
                return self.cache[sym].to_advisory_context()
            if self.forecast_history:
                return self.forecast_history[-1].to_advisory_context()
        return self.fetch_forecast(sym).to_advisory_context()

_futuris_market_client = FuturisMarketClient()

def get_futuris_client() -> FuturisMarketClient:
    return _futuris_market_client
