"""Local contract checks: absent peer credentials/data must not become tradeable facts."""

from unittest.mock import patch

from intelligence.futuris_client import FuturisMarketClient
from intelligence.intelx_client import IntelXMarketClient


def test_futuris_unavailable_forecast_blocks_and_does_not_score():
    client = FuturisMarketClient(base_url="https://peer.invalid")
    with patch("intelligence.futuris_client.requests.post") as post:
        post.return_value.status_code = 401
        forecast = client.fetch_forecast("BTCUSDT")

    assert forecast.status.startswith("UNAVAILABLE:")
    assert forecast.volatility_forecast == {}
    allowed, multiplier, reason, _ = client.evaluate_forecast_alignment("BTCUSDT", "BUY")
    assert (allowed, multiplier, reason) == (False, 0.0, "FUTURIS_FORECAST_UNAVAILABLE")
    assert client.record_actual_outcome("BTCUSDT", True, 0.015)["status"] == "UNAVAILABLE"
    metrics = client.get_accuracy_metrics()
    assert metrics["total_evaluated"] == 0
    assert metrics["accuracy_pct"] is None


def test_intelx_unavailable_research_blocks_trade_advisory():
    client = IntelXMarketClient(base_url="https://peer.invalid")
    with (
        patch("intelligence.intelx_client.requests.post") as post,
        patch("intelligence.intelx_client.requests.get") as get,
    ):
        post.return_value.status_code = 401
        get.return_value.status_code = 401
        allowed, multiplier, reason, details = client.evaluate_market_sentiment("BTCUSDT", "BUY")

    assert (allowed, multiplier, reason) == (False, 0.0, "INTELX_RESEARCH_UNAVAILABLE")
    assert details["status"] == "UNAVAILABLE"
