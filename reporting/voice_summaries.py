"""
reporting/voice_summaries.py — Voice-Ready Natural Language Summary Generator.

Generates conversational, spoken-language text for audio synthesis:
- Numbers are rounded and formatted naturally for speech (e.g. "one point two percent").
- No jargon, clear cadence, and calibrated urgency.
"""



def generate_daily_voice_summary(
    net_pnl_pct: float,
    best_strategy: str,
    trades_count: int,
    risk_headroom_pct: float
) -> str:
    """Produces a conversational spoken summary of daily trading performance."""
    pnl_text = f"gained {abs(net_pnl_pct):.1f} percent" if net_pnl_pct >= 0 else f"lost {abs(net_pnl_pct):.1f} percent"
    clean_strat = best_strategy.replace("strategy_", "").title()

    summary = (
        f"Today you {pnl_text} across {trades_count} trades. "
        f"{clean_strat} was your strongest performing strategy. "
        f"Your risk budget for tomorrow is {risk_headroom_pct:.0f} percent available."
    )
    return summary


def generate_trade_voice_snippet(event_type: str, symbol: str, side: str, price: float, pnl_pct: float | None = None) -> str:
    """Generates short spoken updates for real-time trade fills and exits."""
    clean_symbol = symbol.split("/")[0] if "/" in symbol else symbol
    price_str = f"{price:,.0f}"

    if event_type == "OPEN":
        return f"Position opened: {clean_symbol} {side.lower()} at {price_str}."
    elif event_type == "CLOSE":
        if pnl_pct is not None:
            pnl_word = "profit" if pnl_pct >= 0 else "loss"
            return f"Closed {clean_symbol} {side.lower()} with a {abs(pnl_pct):.1f} percent {pnl_word}."
        return f"Closed {clean_symbol} {side.lower()} position at {price_str}."
    return f"Trade update on {clean_symbol}."


def generate_alert_voice_snippet(severity: str, title: str, recommendation: str | None = None) -> str:
    """Produces speech text for urgent system and risk alerts."""
    urgency_prefix = {
        "CRITICAL": "Critical Alert! Immediate attention needed: ",
        "HIGH": "High priority warning: ",
        "MEDIUM": "Notice: ",
        "LOW": "Information update: "
    }.get(severity.upper(), "Alert: ")

    msg = f"{urgency_prefix}{title}."
    if recommendation:
        msg += f" Recommended action: {recommendation}."
    return msg
