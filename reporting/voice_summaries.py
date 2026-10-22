"""Truthful, voice-ready summaries of explicitly supplied trading evidence."""


def generate_daily_voice_summary(
    net_pnl_pct: float | None,
    best_strategy: str | None,
    trades_count: int | None,
    risk_headroom_pct: float | None,
    net_pnl_dollars: float | None = None,
) -> str:
    """Create spoken text without implying unavailable measurements are zero."""
    trade_suffix = f" across {trades_count} closed trades" if trades_count is not None else ""
    if trades_count == 0 and net_pnl_pct is None and net_pnl_dollars in (None, 0, 0.0):
        # A zero-filled aggregator with no closed trades is absence of evidence,
        # not a measured flat or profitable session.
        performance_line = "Performance results are unavailable because no closed trades were measured. "
    elif net_pnl_pct is not None:
        pnl_text = (
            f"gained {abs(net_pnl_pct):.1f} percent"
            if net_pnl_pct >= 0
            else f"lost {abs(net_pnl_pct):.1f} percent"
        )
        performance_line = f"Observed equity value {pnl_text}{trade_suffix}. "
    elif net_pnl_dollars is not None:
        pnl_text = (
            f"a net profit of ${abs(net_pnl_dollars):,.2f}"
            if net_pnl_dollars >= 0
            else f"a net loss of ${abs(net_pnl_dollars):,.2f}"
        )
        performance_line = f"Measured realized PnL was {pnl_text}{trade_suffix}. "
    else:
        performance_line = (
            "Performance results are unavailable because no complete, period-matched "
            "measurement was available. "
        )

    if best_strategy:
        clean_strat = best_strategy.replace("strategy_", "").title()
        strat_line = f"{clean_strat} was your strongest measured strategy by net PnL. "
    else:
        strat_line = "No single strategy could be confirmed from measured results. "

    if risk_headroom_pct is None:
        risk_line = "Risk budget headroom is unavailable from the current evidence."
    else:
        risk_line = f"Measured risk budget headroom is {risk_headroom_pct:.1f} percent."

    return f"{performance_line}{strat_line}{risk_line}"


def generate_trade_voice_snippet(
    event_type: str,
    symbol: str,
    side: str,
    price: float,
    pnl_pct: float | None = None,
) -> str:
    """Generate a concise spoken update for a real trade fill or exit."""
    clean_symbol = symbol.split("/")[0] if "/" in symbol else symbol
    price_str = f"{price:,.0f}"

    if event_type == "OPEN":
        return f"Position opened: {clean_symbol} {side.lower()} at {price_str}."
    if event_type == "CLOSE":
        if pnl_pct is not None:
            pnl_word = "profit" if pnl_pct >= 0 else "loss"
            return f"Closed {clean_symbol} {side.lower()} with a {abs(pnl_pct):.1f} percent {pnl_word}."
        return f"Closed {clean_symbol} {side.lower()} position at {price_str}."
    return f"Trade update on {clean_symbol}."


def generate_alert_voice_snippet(
    severity: str,
    title: str,
    recommendation: str | None = None,
) -> str:
    """Produce spoken text for urgent system and risk alerts."""
    urgency_prefix = {
        "CRITICAL": "Critical Alert! Immediate attention needed: ",
        "HIGH": "High priority warning: ",
        "MEDIUM": "Notice: ",
        "LOW": "Information update: ",
    }.get(severity.upper(), "Alert: ")

    message = f"{urgency_prefix}{title}."
    if recommendation:
        message += f" Recommended action: {recommendation}."
    return message
