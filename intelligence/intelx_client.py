import datetime
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from logger import get_logger

logger = get_logger('intelx_client')

@dataclass
class MarketResearchReport:
    symbol: str
    trigger_reason: str
    query: str
    findings: dict[str, Any]
    summary: str
    sentiment_drivers: list[str]
    regulatory_changes: list[str]
    macro_events: list[str]
    timestamp: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def is_valid(self) -> bool:
        return time.time() < self.expires_at

    def to_market_context(self) -> dict[str, Any]:
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
        self.api_key = os.getenv('INTELX_API_KEY', 'intelx_api')
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.cache: dict[str, MarketResearchReport] = {}
        self.research_history: list[MarketResearchReport] = []
        self._lock = threading.Lock()
        self.total_queries_submitted = 0

    def _get_headers(self) -> dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
            headers['X-API-Key'] = self.api_key
        return headers

    def should_trigger_research(self, symbol: str, volatility_z_score: float = 0.0, advisory_confidence: float = 1.0, current_drawdown_pct: float = 0.0) -> tuple[bool, str]:
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
        self.total_queries_submitted += 1

        try:
            from monitoring.metrics import get_metrics_registry
            get_metrics_registry().intelx_market_research_total += 1
        except Exception:
            pass

        report = None
        try:
            logger.info(f'[INTELX_CLIENT] Submitting market research query for {symbol} (trigger: {trigger_reason})')
            # 1. Query Knowledge endpoint
            know_url = f'{self.base_url}/api/v1/knowledge/query'
            resp = requests.post(know_url, json={'q': f'{symbol} market sentiment regulatory catalysts'}, headers=self._get_headers(), timeout=self.timeout_seconds)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get('results', [])
                summary_text = f"IntelX intelligence: {len(results)} active claims tracked for {symbol}."
                drivers = ['High volume institutional positioning']
                if results:
                    drivers = [r.get('text', '')[:100] for r in results[:3] if r.get('text')]
                report = MarketResearchReport(
                    symbol=symbol,
                    trigger_reason=trigger_reason,
                    query=query,
                    findings={'status': 'INTELX_LIVE', 'count': len(results), 'results': results},
                    summary=summary_text,
                    sentiment_drivers=drivers,
                    regulatory_changes=['Standard regulatory baseline'],
                    macro_events=['FOMC interest rate expectations'],
                    timestamp=time.time(),
                    expires_at=time.time() + self.cache_ttl_seconds
                )
            else:
                # Fallback to general intelligence feed
                feed_url = f'{self.base_url}/api/v1/friday-universe/intelligence?agent=all&limit=20'
                f_resp = requests.get(feed_url, headers=self._get_headers(), timeout=self.timeout_seconds)
                if f_resp.status_code == 200:
                    f_data = f_resp.json()
                    items = f_data.get('items', [])
                    drivers = [item.get('title', '') for item in items[:3]]
                    report = MarketResearchReport(
                        symbol=symbol,
                        trigger_reason=trigger_reason,
                        query=query,
                        findings={'status': 'INTELX_FEED', 'items_count': len(items)},
                        summary=f"IntelX global intelligence feed: {len(items)} events tracked.",
                        sentiment_drivers=drivers if drivers else ['Market liquidity flows'],
                        regulatory_changes=['No imminent regulatory enforcement reported'],
                        macro_events=['Correlated crypto market flows'],
                        timestamp=time.time(),
                        expires_at=time.time() + self.cache_ttl_seconds
                    )
                else:
                    report = self._generate_fallback_report(symbol, trigger_reason, query)
        except Exception as e:
            logger.debug(f'[INTELX_CLIENT] Failed to connect to IntelX, generating defensive fallback: {e}')
            report = self._generate_fallback_report(symbol, trigger_reason, query)

        if report is None:
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

    def evaluate_market_sentiment(self, symbol: str, side: str) -> tuple[bool, float, str, dict[str, Any]]:
        """
        Evaluates IntelX intelligence for live trade decisions.
        Returns:
            is_allowed (bool): True if signal is safe to trade, False if hard vetoed
            score_multiplier (float): Ranking score multiplier (e.g. 1.2x boost, 0.8x penalty)
            reason (str): Canonical verdict reason
            details (dict): Context summary for live scanner and opportunity log
        """
        report = self.query_market_research(symbol, trigger_reason='TRADE_DECISION_EVAL')
        details = {
            'symbol': symbol,
            'side': side,
            'summary': report.summary,
            'sentiment_drivers': report.sentiment_drivers[:2],
            'status': report.findings.get('status', 'NOMINAL')
        }

        combined_text = (report.summary + " " + " ".join(report.sentiment_drivers) + " " + " ".join(report.regulatory_changes)).lower()
        
        # Hard Veto Triggers
        adverse_keywords = ['sec enforcement', 'subpoena', 'regulatory crackdown', 'delisting', 'exploit', 'insolvency', 'hack', 'freeze']
        for bad_word in adverse_keywords:
            if bad_word in combined_text:
                if side == 'BUY':
                    return False, 0.0, 'INTELX_ADVERSE_REGULATORY_RISK', details

        # Confluence Boost Triggers
        bullish_keywords = ['etf inflow', 'institutional accumulation', 'partnership', 'reserve asset', 'rate cut', 'bullish']
        bearish_keywords = ['whale selling', 'exchange deposit surge', 'bearish distribution', 'liquidation cascade']

        if side == 'BUY':
            if any(k in combined_text for k in bullish_keywords):
                return True, 1.25, 'INTELX_CONFLUENCE_BULLISH_BOOST', details
            return True, 1.0, 'INTELX_SENTIMENT_NOMINAL', details
        elif side == 'SELL':
            if any(k in combined_text for k in bearish_keywords):
                return True, 1.20, 'INTELX_CONFLUENCE_BEARISH_BOOST', details
            return True, 1.0, 'INTELX_SENTIMENT_NOMINAL', details

        return True, 1.0, 'INTELX_SENTIMENT_NOMINAL', details

    def get_latest_market_context(self, symbol: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if symbol and symbol in self.cache:
                return self.cache[symbol].to_market_context()
            if self.research_history:
                return self.research_history[-1].to_market_context()
        return None

_intelx_market_client = IntelXMarketClient()

def get_intelx_client() -> IntelXMarketClient:
    return _intelx_market_client
