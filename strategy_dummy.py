from collections import namedtuple

SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"]
)

def get_signal(df):
    if df is None or len(df) < 2:
        return SignalResult(None, None, None, "RULE_BASED", 0.5, 1.0)
    
    close = df['close'].iloc[-1]
    
    # Intentionally bad TP/SL to ensure the ProfitabilityGate REJECTS it.
    # This proves the bot is evaluating without risking money.
    return SignalResult("BUY", close * 0.99, close * 1.0001, "RULE_BASED", 0.5, 0.1)
