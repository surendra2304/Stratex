# ==============================================================================
# TRADING_PAUSE.PY - Cross-process "new entries paused" flag
# ==============================================================================
# The control API (api/control.py) and the execution engine run in SEPARATE
# processes, so an in-memory boolean can never communicate a pause. This module
# provides a durable, file-backed flag (atomic writes, same pattern as
# panic_state.json) that the engine consults before opening any new position.
#
# Semantics:
#   - paused=True  -> the engine must REJECT all NEW entries (open positions
#                     and their protective SL/TP orders remain managed).
#   - missing flag file -> NOT paused (nobody ever requested a pause).
#   - existing but unreadable/corrupt flag file -> PAUSED (fail-closed): the
#     file only exists because someone wrote a pause state, and a pause request
#     must never be silently lost. Logged loudly; clear it via the resume API.
#   - writes use unique temp files + fsync (atomic_io), so concurrent
#     pause/resume requests can no longer collide on a shared ".tmp" name.
# ==============================================================================
import datetime
import json
import os

from atomic_io import atomic_write_json
from logger import get_logger

logger = get_logger("trading_pause")

DEFAULT_PAUSE_STATE_FILE = "trading_pause_state.json"


def _pause_file() -> str:
    return os.getenv("TRADING_PAUSE_STATE_FILE", DEFAULT_PAUSE_STATE_FILE)


def set_trading_paused(paused: bool, actor: str = "unknown") -> dict:
    """Atomically persist the pause flag. Returns the written state record."""
    state = {
        "paused": bool(paused),
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "actor": str(actor)[:200],
    }
    path = _pause_file()
    try:
        atomic_write_json(path, state)
        logger.info(f"[TRADING_PAUSE] paused={state['paused']} actor={state['actor']}")
    except Exception as e:
        logger.error(f"[TRADING_PAUSE] Failed to persist pause state: {e}")
        raise
    return state


def get_pause_state() -> dict:
    """Read the pause state. Missing file -> not paused. Corrupt -> PAUSED
    (fail-closed) and logged as an error (never fail silently)."""
    path = _pause_file()
    if not os.path.exists(path):
        return {"paused": False, "reason": "NO_PAUSE_FILE"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("pause state is not a JSON object")
        return {
            "paused": bool(data.get("paused", False)),
            "updated_at": data.get("updated_at"),
            "actor": data.get("actor"),
            "reason": "OK",
        }
    except Exception as e:
        logger.error(f"[TRADING_PAUSE] Corrupt pause state file ({e}); failing CLOSED — new entries blocked.")
        return {"paused": True, "reason": f"CORRUPT_FILE:{type(e).__name__}"}


def is_trading_paused() -> bool:
    """True when new entries must be blocked."""
    return bool(get_pause_state().get("paused", False))
