# ==============================================================================
# PAPER ENGINE CONFIGURATION
# ==============================================================================

# Risk Limits
MAX_POSITION_SIZE = 5000.0        # Max notional value per position
MAX_PORTFOLIO_EXPOSURE = 20000.0  # Max total absolute notional exposure 
MAX_SIMULTANEOUS_POSITIONS = 5    # Max number of open positions at once
MAX_DAILY_LOSS = 500.0            # Max allowable dollar loss per day
MAX_STRATEGY_LOSS = 1000.0        # Max allowable dollar loss per strategy
MAX_DRAWDOWN_PCT = 0.20           # Max 20% drawdown before blocking trades

# Execution Assumptions
# "LOW", "BASE", "HIGH"
LATENCY_MODEL = "BASE"            
COST_MODEL = "BASE"

# Limit Order Model
# "OPTIMISTIC" = Fills exactly on touch
# "CONSERVATIVE" = Requires penetration of the limit price
LIMIT_FILL_MODEL = "CONSERVATIVE"

# Heartbeat & Reconciler
STALE_POSITION_TIMEOUT_SEC = 300  # 5 minutes
HEARTBEAT_INTERVAL_SEC = 60       # 1 minute

# Default starting state
STARTING_PAPER_CAPITAL = 10000.0
