"""panic_state.py — single source of truth for the manual panic (kill-switch) flag.

Before this module three writers and three readers disagreed on the schema of
``panic_state.json``:

* ``POST /api/panic`` wrote ``{"active": true}``;
* the FRIDAY supervision panic wrote ``{"panic_active": true}``;
* ``execution._check_panic_and_kill_switch`` (the last gate before every order)
  only read ``panic_active`` — so ``/api/panic`` never blocked it — while the
  testnet engine's ``panic_active()`` only read ``active`` — so a FRIDAY panic
  never tripped the engine's own gate;
* every reader treated an unreadable/corrupt flag file as "no panic".

Now every writer emits both keys and every reader goes through
``read_panic_state``: a flag in either key means panic, and a flag file that
exists but cannot be parsed fails CLOSED (panic assumed) and is logged loudly.
A missing file means no panic.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any

from atomic_io import atomic_write_json
from logger import get_logger

logger = get_logger("panic_state")

DEFAULT_PANIC_STATE_FILE = "panic_state.json"


def panic_file() -> str:
    return os.getenv("PANIC_STATE_FILE", DEFAULT_PANIC_STATE_FILE)


def write_panic_state(active: bool, actor: str, reason: str = "", path: str | None = None) -> dict[str, Any]:
    """Atomically persist the panic flag in the unified schema and return it."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    flag = active is True
    state: dict[str, Any] = {
        "active": flag,
        "panic_active": flag,
        "updated_at": now,
        "actor": str(actor)[:200],
        "reason": str(reason)[:500],
    }
    if flag:
        state["activated_at"] = now
        state["triggered_at"] = now
    else:
        state["released_at"] = now
        state["activated_at"] = None
    atomic_write_json(path or panic_file(), state)
    logger.warning(f"[PANIC_STATE] active={flag} actor={state['actor']} reason={state['reason']!r}")
    return state


def read_panic_state(path: str | None = None) -> dict[str, Any]:
    """Return ``{"active": bool, "reason": str, ...}`` — fail-closed on corruption."""
    target = path or panic_file()
    if not os.path.exists(target):
        return {"active": False, "reason": "NO_PANIC_FILE"}
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("panic state is not a JSON object")
    except Exception as exc:  # unreadable, truncated, not JSON, wrong type
        logger.error(f"[PANIC_STATE] Unreadable panic flag {target!r} ({exc}); failing CLOSED (panic assumed)")
        return {"active": True, "reason": f"CORRUPT_FILE:{type(exc).__name__}"}
    active = bool(data.get("active", False)) or bool(data.get("panic_active", False))
    return {
        "active": active,
        "reason": "OK",
        "actor": data.get("actor"),
        "updated_at": data.get("updated_at"),
    }


def is_panic_active(path: str | None = None) -> bool:
    return bool(read_panic_state(path)["active"])


DEFAULT_KILL_SWITCH_LOCK_FILE = "KILL_SWITCH_ACTIVE.lock"


def kill_switch_lock_file() -> str:
    return os.getenv("KILL_SWITCH_LOCK_FILE", DEFAULT_KILL_SWITCH_LOCK_FILE)


def is_kill_switch_locked() -> bool:
    return os.path.exists(kill_switch_lock_file())


def engage_order_block(
    actor: str,
    reason: str,
    *,
    panic: bool = True,
    pause: bool = True,
    kill_switch_lock: bool = False,
) -> dict[str, str]:
    """Engage the durable, cross-process order-blocking mechanisms.

    Returns ``{mechanism: "WRITTEN" | "FAILED"}`` so callers can report exactly
    what took effect. Every mechanism is attempted even if an earlier one fails.
    ``any(v == "WRITTEN" ...)`` means new order submission is blocked:
    * ``panic_flag``       — checked by execution._check_panic_and_kill_switch()
                             and the testnet engine's submission gate;
    * ``trading_pause``    — checked by the testnet engine before new entries;
    * ``kill_switch_lock`` — checked by execution._check_panic_and_kill_switch().
    """
    steps: dict[str, str] = {}
    if panic:
        try:
            write_panic_state(True, actor=actor, reason=reason)
            steps["panic_flag"] = "WRITTEN"
        except Exception as exc:
            logger.error(f"[PANIC_STATE] panic flag write failed: {exc}")
            steps["panic_flag"] = "FAILED"
    if pause:
        try:
            from trading_pause import set_trading_paused

            set_trading_paused(True, actor=actor)
            steps["trading_pause"] = "WRITTEN"
        except Exception as exc:
            logger.error(f"[PANIC_STATE] trading pause write failed: {exc}")
            steps["trading_pause"] = "FAILED"
    if kill_switch_lock:
        try:
            atomic_write_json(kill_switch_lock_file(), {
                "kill_switch_active": True,
                "triggered_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "actor": str(actor)[:200],
                "reason": str(reason)[:500],
            })
            steps["kill_switch_lock"] = "WRITTEN"
        except Exception as exc:
            logger.error(f"[PANIC_STATE] kill switch lock write failed: {exc}")
            steps["kill_switch_lock"] = "FAILED"
    return steps


def orders_blocked(steps: dict[str, str]) -> bool:
    return any(value == "WRITTEN" for value in steps.values())
