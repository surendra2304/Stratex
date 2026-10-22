"""
monitoring/metrics.py — Comprehensive Prometheus Metrics Collection & Exporter.

Metrics tracked:
- trading_bot_equity (gauge)
- trading_bot_pnl_realized_total (counter)
- trading_bot_pnl_unrealized (gauge)
- trading_bot_positions_open (gauge)
- trading_bot_trades_total (counter by strategy, outcome)
- trading_bot_risk_daily_loss_pct (gauge)
- trading_bot_drawdown_pct (gauge)
- trading_bot_advisory_recommendations_total (counter by verdict)
- trading_bot_strategy_allocation (gauge by strategy)
- trading_bot_exchange_latency_ms (histogram by exchange)
- trading_bot_ai_universe_latency_ms (histogram)
- trading_bot_circuit_breaker_state (gauge by type)
"""



class PrometheusMetricsRegistry:
    """Lightweight in-memory Prometheus metric collector and text serializer."""

    def __init__(self):
        # Gauges
        self.equity: float = 10000.0
        self.pnl_unrealized: float = 0.0
        self.positions_open: int = 0
        self.risk_daily_loss_pct: float = 0.0
        self.drawdown_pct: float = 0.0
        self.strategy_allocations: dict[str, float] = {}
        self.circuit_breaker_states: dict[str, int] = {}  # 0=nominal, 1=tripped

        # Counters
        self.pnl_realized_total: float = 0.0
        self.trades_total: dict[str, int] = {}  # key: (strategy, outcome)
        self.advisory_recommendations_total: dict[str, int] = {}  # key: verdict
        self.intelx_market_research_total: int = 0
        self.market_context_enriched_consultations_total: int = 0
        self.futuris_context_included_consultations_total: int = 0
        self.forecast_accuracy_pct: float = 100.0

        # Histograms / Latency recordings
        self.exchange_latencies: dict[str, list[float]] = {}
        self.ai_universe_latencies: list[float] = []

    def set_equity(self, value: float) -> None:
        self.equity = float(value)

    def set_pnl_unrealized(self, value: float) -> None:
        self.pnl_unrealized = float(value)

    def add_pnl_realized(self, value: float) -> None:
        self.pnl_realized_total += float(value)

    def set_positions_open(self, count: int) -> None:
        self.positions_open = int(count)

    def record_trade(self, strategy: str, outcome: str) -> None:
        key = f'{strategy}:{outcome}'
        self.trades_total[key] = self.trades_total.get(key, 0) + 1

    def set_risk_metrics(self, daily_loss_pct: float, drawdown_pct: float) -> None:
        self.risk_daily_loss_pct = float(daily_loss_pct)
        self.drawdown_pct = float(drawdown_pct)

    def record_advisory_recommendation(self, verdict: str) -> None:
        self.advisory_recommendations_total[verdict] = self.advisory_recommendations_total.get(verdict, 0) + 1

    def set_strategy_allocation(self, strategy: str, weight: float) -> None:
        self.strategy_allocations[strategy] = float(weight)

    def record_exchange_latency(self, exchange: str, latency_ms: float) -> None:
        self.exchange_latencies.setdefault(exchange, []).append(float(latency_ms))
        if len(self.exchange_latencies[exchange]) > 100:
            self.exchange_latencies[exchange].pop(0)

    def record_ai_universe_latency(self, latency_ms: float) -> None:
        self.ai_universe_latencies.append(float(latency_ms))
        if len(self.ai_universe_latencies) > 100:
            self.ai_universe_latencies.pop(0)

    def set_circuit_breaker_state(self, breaker_type: str, is_tripped: bool) -> None:
        self.circuit_breaker_states[breaker_type] = 1 if is_tripped else 0

    def generate_prometheus_text(self) -> str:
        """Renders standard Prometheus exposition text format."""
        lines = []

        # Gauges
        lines.append("# HELP trading_bot_equity Total portfolio equity in USD")
        lines.append("# TYPE trading_bot_equity gauge")
        lines.append(f"trading_bot_equity {self.equity:.2f}")

        lines.append("# HELP trading_bot_pnl_realized_total Cumulative realized PnL in USD")
        lines.append("# TYPE trading_bot_pnl_realized_total counter")
        lines.append(f"trading_bot_pnl_realized_total {self.pnl_realized_total:.2f}")

        lines.append("# HELP trading_bot_pnl_unrealized Current unrealized open position PnL in USD")
        lines.append("# TYPE trading_bot_pnl_unrealized gauge")
        lines.append(f"trading_bot_pnl_unrealized {self.pnl_unrealized:.2f}")

        lines.append("# HELP trading_bot_positions_open Number of currently active open positions")
        lines.append("# TYPE trading_bot_positions_open gauge")
        lines.append(f"trading_bot_positions_open {self.positions_open}")

        lines.append("# HELP trading_bot_trades_total Total trade executions by strategy and outcome")
        lines.append("# TYPE trading_bot_trades_total counter")
        if self.trades_total:
            for k, count in self.trades_total.items():
                strat, outcome = k.split(":", 1)
                lines.append(f'trading_bot_trades_total{{strategy="{strat}",outcome="{outcome}"}} {count}')
        else:
            lines.append('trading_bot_trades_total{strategy="system",outcome="WIN"} 0')

        lines.append("# HELP trading_bot_risk_daily_loss_pct Current rolling daily loss as percentage of capital")
        lines.append("# TYPE trading_bot_risk_daily_loss_pct gauge")
        lines.append(f"trading_bot_risk_daily_loss_pct {self.risk_daily_loss_pct:.4f}")

        lines.append("# HELP trading_bot_drawdown_pct Current peak-to-trough drawdown percentage")
        lines.append("# TYPE trading_bot_drawdown_pct gauge")
        lines.append(f"trading_bot_drawdown_pct {self.drawdown_pct:.4f}")

        lines.append("# HELP trading_bot_advisory_recommendations_total Total AI recommendations processed by verdict")
        lines.append("# TYPE trading_bot_advisory_recommendations_total counter")
        if self.advisory_recommendations_total:
            for verdict, count in self.advisory_recommendations_total.items():
                lines.append(f'trading_bot_advisory_recommendations_total{{verdict="{verdict}"}} {count}')
        else:
            lines.append('trading_bot_advisory_recommendations_total{verdict="APPROVED"} 0')

        lines.append("# HELP intelx_market_research_total Total market research queries submitted to IntelX")
        lines.append("# TYPE intelx_market_research_total counter")
        lines.append(f"intelx_market_research_total {self.intelx_market_research_total}")

        lines.append("# HELP market_context_enriched_consultations_total Total AI consultations enriched with IntelX market context")
        lines.append("# TYPE market_context_enriched_consultations_total counter")
        lines.append(f"market_context_enriched_consultations_total {self.market_context_enriched_consultations_total}")

        lines.append("# HELP futuris_context_included_consultations_total Total AI consultations enriched with Futuris forecast context")
        lines.append("# TYPE futuris_context_included_consultations_total counter")
        lines.append(f"futuris_context_included_consultations_total {self.futuris_context_included_consultations_total}")

        lines.append("# HELP forecast_accuracy_pct Historical accuracy percentage of Futuris volatility forecasts")
        lines.append("# TYPE forecast_accuracy_pct gauge")
        lines.append(f"forecast_accuracy_pct {self.forecast_accuracy_pct:.2f}")

        lines.append("# HELP trading_bot_strategy_allocation Portfolio weight allocation per strategy")
        lines.append("# TYPE trading_bot_strategy_allocation gauge")
        for strat, weight in self.strategy_allocations.items():
            lines.append(f'trading_bot_strategy_allocation{{strategy="{strat}"}} {weight:.4f}')

        lines.append("# HELP trading_bot_exchange_latency_ms Exchange request latency in milliseconds")
        lines.append("# TYPE trading_bot_exchange_latency_ms histogram")
        for ex, lats in self.exchange_latencies.items():
            if lats:
                count = len(lats)
                total_sum = sum(lats)
                lines.append(f'trading_bot_exchange_latency_ms_sum{{exchange="{ex}"}} {total_sum:.2f}')
                lines.append(f'trading_bot_exchange_latency_ms_count{{exchange="{ex}"}} {count}')
            else:
                lines.append(f'trading_bot_exchange_latency_ms_sum{{exchange="{ex}"}} 0.0')
                lines.append(f'trading_bot_exchange_latency_ms_count{{exchange="{ex}"}} 0')

        lines.append("# HELP trading_bot_circuit_breaker_state Circuit breaker status (0=Nominal, 1=Tripped)")
        lines.append("# TYPE trading_bot_circuit_breaker_state gauge")
        for cb, state in self.circuit_breaker_states.items():
            lines.append(f'trading_bot_circuit_breaker_state{{type="{cb}"}} {state}')

        return "\n".join(lines) + "\n"


# Singleton instance
_GLOBAL_PROMETHEUS_REGISTRY = PrometheusMetricsRegistry()

def get_metrics_registry() -> PrometheusMetricsRegistry:
    return _GLOBAL_PROMETHEUS_REGISTRY