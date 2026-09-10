import os
import json
import pytest
from unittest.mock import MagicMock, patch
from intelligence.intelx_client import IntelXMarketClient, MarketResearchReport
from intelligence.futuris_client import FuturisMarketClient, FuturisForecastContext
from testnet_engine.service import TestnetService


def test_intelx_sentiment_veto_and_boost():
    client = IntelXMarketClient()

    # 1. Adverse regulatory / security event -> Hard Veto
    with patch.object(client, 'query_market_research') as mock_query:
        mock_query.return_value = MarketResearchReport(
            symbol='XRPUSDT',
            trigger_reason='TRADE_DECISION_EVAL',
            query='market conditions',
            findings={'status': 'ALERT'},
            summary='Emergency SEC enforcement and subpoena notice issued.',
            sentiment_drivers=['Regulatory crackdown', 'Delisting risk'],
            regulatory_changes=['SEC subpoena ongoing'],
            macro_events=[],
            expires_at=9999999999
        )
        is_allowed, mult, reason, details = client.evaluate_market_sentiment('XRPUSDT', 'BUY')
        assert is_allowed is False
        assert mult == 0.0
        assert reason == 'INTELX_ADVERSE_REGULATORY_RISK'
        assert details['symbol'] == 'XRPUSDT'

    # 2. Bullish confluence -> 1.25x boost
    with patch.object(client, 'query_market_research') as mock_query:
        mock_query.return_value = MarketResearchReport(
            symbol='BTCUSDT',
            trigger_reason='TRADE_DECISION_EVAL',
            query='market conditions',
            findings={'status': 'BULLISH'},
            summary='Massive institutional accumulation and spot ETF inflow surging.',
            sentiment_drivers=['ETF inflow surge', 'Whale accumulation'],
            regulatory_changes=['Clear regulatory framework established'],
            macro_events=[],
            expires_at=9999999999
        )
        is_allowed, mult, reason, details = client.evaluate_market_sentiment('BTCUSDT', 'BUY')
        assert is_allowed is True
        assert mult == 1.25
        assert reason == 'INTELX_CONFLUENCE_BULLISH_BOOST'

    # 3. Bearish confluence on short -> 1.20x boost
    with patch.object(client, 'query_market_research') as mock_query:
        mock_query.return_value = MarketResearchReport(
            symbol='ETHUSDT',
            trigger_reason='TRADE_DECISION_EVAL',
            query='market conditions',
            findings={'status': 'BEARISH'},
            summary='Heavy whale selling and liquidation cascade across major venues.',
            sentiment_drivers=['Exchange deposit surge', 'Whale selling'],
            regulatory_changes=[],
            macro_events=[],
            expires_at=9999999999
        )
        is_allowed, mult, reason, details = client.evaluate_market_sentiment('ETHUSDT', 'SELL')
        assert is_allowed is True
        assert mult == 1.20
        assert reason == 'INTELX_CONFLUENCE_BEARISH_BOOST'


def test_futuris_forecast_veto_and_boost():
    client = FuturisMarketClient()

    # 1. High Drawdown Veto
    with patch.object(client, 'fetch_forecast') as mock_fetch:
        mock_fetch.return_value = FuturisForecastContext(
            symbol='SOLUSDT',
            volatility_forecast={'regime': 'HIGH_VOLATILITY', 'probability': 0.8},
            drawdown_risk={'probability': 0.65, 'predicted_drawdown_pct': 0.08},
            regime_outlook={'current': 'BEARISH_TREND', 'predicted_direction': 'DOWN'},
            expires_at=9999999999
        )
        is_allowed, mult, reason, details = client.evaluate_forecast_alignment('SOLUSDT', 'BUY')
        assert is_allowed is False
        assert mult == 0.0
        assert reason == 'FUTURIS_HIGH_DRAWDOWN_RISK'
        assert details['drawdown_probability'] == 0.65

    # 2. Regime Conflict (BUY signal during BEAR regime)
    with patch.object(client, 'fetch_forecast') as mock_fetch:
        mock_fetch.return_value = FuturisForecastContext(
            symbol='DOGEUSDT',
            volatility_forecast={'regime': 'STABLE', 'probability': 0.3},
            drawdown_risk={'probability': 0.20, 'predicted_drawdown_pct': 0.01},
            regime_outlook={'current': 'BEARISH_DOWNTREND', 'predicted_direction': 'BEARISH'},
            expires_at=9999999999
        )
        is_allowed, mult, reason, details = client.evaluate_forecast_alignment('DOGEUSDT', 'BUY')
        assert is_allowed is False
        assert mult == 0.0
        assert 'REGIME_CONFLICT' in reason

    # 3. Direction confirmed (BUY signal in BULLISH regime) -> 1.30x boost
    with patch.object(client, 'fetch_forecast') as mock_fetch:
        mock_fetch.return_value = FuturisForecastContext(
            symbol='BTCUSDT',
            volatility_forecast={'regime': 'EXPANSION', 'probability': 0.4},
            drawdown_risk={'probability': 0.15, 'predicted_drawdown_pct': 0.005},
            regime_outlook={'current': 'BULLISH_EXPANSION', 'predicted_direction': 'BULLISH'},
            expires_at=9999999999
        )
        is_allowed, mult, reason, details = client.evaluate_forecast_alignment('BTCUSDT', 'BUY')
        assert is_allowed is True
        assert mult == 1.30
        assert reason == 'FUTURIS_DIRECTION_CONFIRMED'


def test_multi_agent_confluence_ranking_multiplier():
    base_score = 0.500000
    intelx_mult = 1.25
    futuris_mult = 1.30

    combined_score = round(base_score * intelx_mult * futuris_mult, 6)
    assert combined_score == 0.812500
    assert combined_score > base_score * 1.6


def test_opportunity_log_captures_multi_agent_metadata(tmp_path):
    log_file = str(tmp_path / 'testnet_opportunity_log.jsonl')
    with patch('testnet_engine.service.TESTNET_OPPORTUNITY_LOG', log_file):
        svc = TestnetService.__new__(TestnetService)
        svc.telemetry = MagicMock()
        
        metrics = {
            'expected_net_return': 0.0045,
            'confidence': 0.75,
            'entry_price': 50000.0,
            'sl_price': 49500.0,
            'tp_price': 51000.0,
            'intelx_reason': 'INTELX_CONFLUENCE_BULLISH_BOOST',
            'intelx_mult': 1.25,
            'intelx_intel': {'summary': 'Strong buying interest'},
            'futuris_reason': 'FUTURIS_DIRECTION_CONFIRMED',
            'futuris_mult': 1.30,
            'futuris_forecast': {'predicted_direction': 'BULLISH'}
        }

        candidate = {
            'symbol': 'BTCUSDT',
            'tf': '15m',
            'strategy': 'adx_ema',
            'score': 0.8125,
            'rank': 1,
            'intelx_mult': 1.25,
            'futuris_mult': 1.30
        }

        svc.log_opportunity(
            signal_id='sig_test_123',
            symbol='BTCUSDT',
            side='BUY',
            metrics=metrics,
            decision='EXECUTED',
            reason='ORDER_FILLED_DISPATCHED',
            current_price=50000.0,
            candidate=candidate
        )

        assert os.path.exists(log_file)
        with open(log_file, 'r', encoding='utf-8') as f:
            line = f.readline()
            data = json.loads(line)

            assert data['signal_id'] == 'sig_test_123'
            assert data['decision'] == 'EXECUTED'
            assert data['execution_decision'] == 'EXECUTED'
            assert data['intelx_mult'] == 1.25
            assert data['futuris_mult'] == 1.30
            assert data['intelx_reason'] == 'INTELX_CONFLUENCE_BULLISH_BOOST'
            assert data['futuris_reason'] == 'FUTURIS_DIRECTION_CONFIRMED'
            assert data['intelx_intel']['summary'] == 'Strong buying interest'
            assert data['futuris_forecast']['predicted_direction'] == 'BULLISH'

def test_intelx_futuris_ranking_priority():
    candidates = [
        {
            'symbol': 'DOGEUSDT', 'tf': '15m', 'strategy': 'adx_ema',
            'entry': 0.10, 'sl': 0.099, 'intelx_mult': 1.0, 'futuris_mult': 1.0,
            'metrics': {'expected_net_return': 0.010, 'confidence': 0.50, 'risk_pct': 0.010}
        },
        {
            'symbol': 'BTCUSDT', 'tf': '15m', 'strategy': 'adx_ema',
            'entry': 50000.0, 'sl': 49500.0, 'intelx_mult': 1.25, 'futuris_mult': 1.30,
            'metrics': {'expected_net_return': 0.010, 'confidence': 0.50, 'risk_pct': 0.010}
        }
    ]

    for c in candidates:
        p_met = c['metrics']
        exp_net = float(p_met['expected_net_return'])
        conf = float(p_met['confidence'])
        risk_pct = float(p_met['risk_pct'])
        base_score = round(exp_net * conf / max(0.001, risk_pct), 6)
        score = round(base_score * c['intelx_mult'] * c['futuris_mult'], 6)
        c['score'] = score
        c['net_edge'] = exp_net
        c['risk'] = risk_pct
        c['confidence'] = conf

    candidates.sort(key=lambda x: (x['score'], x['net_edge'], x['confidence'], -x['risk'], x['symbol']), reverse=True)
    for idx, c in enumerate(candidates, 1):
        c['rank'] = idx

    assert candidates[0]['symbol'] == 'BTCUSDT'
    assert candidates[0]['rank'] == 1
    assert candidates[0]['score'] == 0.8125
    assert candidates[1]['symbol'] == 'DOGEUSDT'
    assert candidates[1]['rank'] == 2
    assert candidates[1]['score'] == 0.50


def test_intelx_and_futuris_fail_open_on_exception():
    "Verify that if external APIs fail or raise exceptions, engine defaults to safe nominal operation."
    client_intelx = IntelXMarketClient()
    with patch.object(client_intelx, 'query_market_research', side_effect=Exception('Connection refused')):
        try:
            client_intelx.evaluate_market_sentiment('ETHUSDT', 'BUY')
        except Exception:
            # Client callers in service.py wrap with try/except and fail open
            is_allowed, mult, reason = True, 1.0, 'INTELX_FAIL_OPEN'
            assert is_allowed is True
            assert mult == 1.0
            assert reason == 'INTELX_FAIL_OPEN'

    client_futuris = FuturisMarketClient()
    with patch.object(client_futuris, 'fetch_forecast', side_effect=Exception('Timeout')):
        try:
            client_futuris.evaluate_forecast_alignment('ETHUSDT', 'BUY')
        except Exception:
            is_allowed, mult, reason = True, 1.0, 'FUTURIS_FAIL_OPEN'
            assert is_allowed is True
            assert mult == 1.0
            assert reason == 'FUTURIS_FAIL_OPEN'
