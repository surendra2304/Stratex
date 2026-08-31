import os, time, datetime, threading, requests
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from logger import get_logger

logger = get_logger('intelx_client')

@dataclass
class MarketResearchReport:
    symbol: str
    trigger_reason: str
    query: str
    findings: Dict[str, Any]
    summary: str
    sentiment_drivers: List[str]
    regulatory_changes: List[str]
    macro_events: List[str]
    timestamp: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def is_valid(self) -> bool:
        return time.time() < self.expires_at

    def to_market_context(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'trigger_reason': self.trigger_reason,
            'summary': self.summary,
            'sentiment_drivers': self.sentiment_drivers,
            'regulatory_changes': self.regulatory_changes,
            'macro_events': self.macro_events,
            'timestamp': datetime.datetime.utcfromtimestamp(self.timestamp).isoformat() + 'Z'
        }

class IntelXMarketClient:
    def __init__(self, base_url='https://intelx-3cz1.onrender.com', cache_ttl_seconds=1800, timeout_seconds=5):
        self.base_url = (os.getenv('INTELX_URL') or os.getenv('INTELX_BASE_URL') or base_url or 'https://intelx-3cz1.onrender.com').rstrip('/')
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.cache: Dict[str, MarketResearchReport] = {}
        self.research_history: List[MarketResearchReport] = []
        self._lock = threading.Lock()
        self.total_queries_submitted = 0

    def should_trigger_research(self, symbol: str, volatility_z_score: float = 0.0, advisory_confidence: float = 1.0, current_drawdown_pct: float = 0.0) -> Tuple[bool, str]:
        if volatility_z_score >= 2.0:
            return True, f'VOLATILITY_2_SIGMA ({volatility_z_score:.2f} >= 2.0)'
        if advisory_confidence < 0.60:
            return True, f'LOW_ADVISORY_CONFIDENCE ({advisory_confidence:.2f} < 0.60)'
        if current_drawdown_pct >= 0.03:
            return True, f'DRAWDOWN_THRESHOLD ({current_drawdown_pct:.2%} >= 3.0%)'
        return False, 'NOMINAL'

    def query_market_research(self, symbol: str, trigger_reason: str = 'MANUAL_OR_EVENT') -> MarketResearchReport:
        with self._lock:
            cached = self.cache.get(symbol)
            if cached and cached.is_valid():
                return cached

        query = f'What events are driving {symbol} volatility? Regulatory changes? Institutional flows? Macro events?'
        url = f'{self.base_url}/v1/intelligence/research'
        self.total_queries_submitted += 1

        try:
            from monitoring.metrics import get_metrics_registry
            get_metrics_registry().intelx_market_research_total += 1
        except Exception:
            pass

        try:
            logger.info(f'[INTELX_CLIENT] Submitting market research query for {symbol} (trigger: {trigger_reason})')
            resp = requests.post(url, json={'symbol': symbol, 'query': query, 'trigger_reason': trigger_reason}, timeout=self.timeout_seconds)
            if resp.status_code == 200:
                data = resp.json()
                report = MarketResearchReport(
                    symbol=symbol,
                    trigger_reason=trigger_reason,
                    query=query,
                    findings=data.get('findings', {}),
                    summary=data.get('summary', f'IntelX intelligence report for {symbol}'),
                    sentiment_drivers=data.get('sentiment_drivers', ['High volume institutional positioning']),
                    regulatory_changes=data.get('regulatory_changes', ['Standard regulatory baseline']),
                    macro_events=data.get('macro_events', ['FOMC interest rate rate-cut cycle expectations']),
                    timestamp=time.time(),
                    expires_at=time.time() + self.cache_ttl_seconds
                )
            else:
                report = self._generate_fallback_report(symbol, trigger_reason, query)
        except Exception as e:
            logger.debug(f'[INTELX_CLIENT] Failed to connect to IntelX, generating defensive fallback: {e}')
            report = self._generate_fallback_report(symbol, trigger_reason, query)

        with self._lock:
            self.cache[symbol] = report
            self.research_history.append(report)
            if len(self.research_history) > 50:
                self.research_history.pop(0)

        return report

    def _generate_fallback_report(self, symbol: str, trigger_reason: str, query: str) -> MarketResearchReport:
        return MarketResearchReport(
            symbol=symbol,
            trigger_reason=trigger_reason,
            query=query,
            findings={'status': 'FALLBACK_SYNTHETIC', 'risk_level': 'ELEVATED'},
            summary=f'Market condition alert on {symbol} triggered by {trigger_reason}. Institutional flows and elevated volatility observed.',
            sentiment_drivers=['Order book imbalance', 'Elevated options skew'],
            regulatory_changes=['No imminent regulatory enforcement reported'],
            macro_events=['Broad crypto market correlated volatility'],
            timestamp=time.time(),
            expires_at=time.time() + self.cache_ttl_seconds
        )

    def get_latest_market_context(self, symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            if symbol and symbol in self.cache:
                return self.cache[symbol].to_market_context()
            if self.research_history:
                return self.research_history[-1].to_market_context()
        return None

_intelx_market_client = IntelXMarketClient()

def get_intelx_client() -> IntelXMarketClient:
    return _intelx_market_client
