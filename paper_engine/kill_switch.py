import os
import json
import time
from logger import get_logger

logger = get_logger("kill_switch")

def trigger_kill_switch(reason: str, portfolio=None):
    """
    Emergency halt mechanism.
    If portfolio is provided, attempts to flatten all positions.
    """
    logger.critical(f"KILL SWITCH TRIGGERED: {reason}")
    
    # 1. Write lock file to block future executions
    try:
        with open("KILL_SWITCH_ACTIVE.lock", "w") as f:
            json.dump({"timestamp": time.time(), "reason": reason}, f)
    except Exception as e:
        logger.error(f"Failed to write kill switch lock file: {e}")
        
    # 2. Flatten positions if possible
    if portfolio:
        try:
            for pos_id, pos in list(portfolio.positions.items()):
                if pos['status'] == "OPEN":
                    # We exit at 0 cost just to flatten state in paper. 
                    # In live, this would issue aggressive market orders.
                    portfolio.close_position(pos_id, pos['entry_price'], exit_fee=0.0)
            logger.info("Kill switch flattened all paper positions.")
        except Exception as e:
            logger.error(f"Kill switch failed to flatten portfolio: {e}")
            
    # 3. Halt process
    logger.critical("SYSTEM HALTED.")
    import sys
    sys.exit(1)

def is_kill_switch_active() -> bool:
    return os.path.exists("KILL_SWITCH_ACTIVE.lock")
