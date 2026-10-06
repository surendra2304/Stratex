"""Execution package init.

``execution.py`` (module) and ``execution/`` (package) coexist for historical
reasons. The package wins the import, so the module's source is exec'd into
these globals to keep ``import execution`` behaving like the flat module.

That indirection must never fail quietly. The previous ``except Exception: pass``
meant that ANY error while loading ``execution.py`` — including a transient
failure in one of its ~10 transitive imports — produced an EMPTY package with no
diagnostic: ``execution.ExecutionPolicy`` and ``execution.place_market_order``
simply stopped existing, and any caller using ``getattr(execution, name, default)``
silently took its default branch. ``ExecutionPolicy`` is the last gate before an
order leaves this process, so its absence has to be loud.

The load lives in ``_load_execution_module()`` so a test can prove each failure
mode raises instead of passing silently.
"""

import os

_curr_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_curr_dir)
_exec_file = os.path.join(_root_dir, "execution.py")

#: Names that MUST be present after loading, or the package is not usable.
_REQUIRED_API = ("ExecutionPolicy",)


def _load_execution_module(exec_file: str = _exec_file, target: dict | None = None) -> dict:
    """Exec ``execution.py`` into ``target`` (default: this package's globals).

    Returns the namespace it defined. Raises ``ImportError`` — never returns a
    partial namespace — when the source is missing, unreadable, fails to execute,
    or does not define the required execution API.

    Exec'ing into the real package globals (rather than a scratch dict that is
    copied afterwards) keeps the original semantics: functions defined here
    resolve their module-level lookups against ``execution`` itself, so a caller
    monkeypatching ``execution.SOMETHING`` is seen by them.
    """
    namespace = globals() if target is None else target

    if not os.path.exists(exec_file):
        raise ImportError(
            f"execution package cannot initialize: '{exec_file}' is missing. This "
            "package re-exports that module; without it ExecutionPolicy is undefined."
        )

    try:
        with open(exec_file, "r", encoding="utf-8") as f:
            code = f.read()
    except OSError as err:
        raise ImportError(
            f"execution package failed to read '{exec_file}': "
            f"{type(err).__name__}: {err}. Refusing to expose an empty 'execution' "
            "module, because ExecutionPolicy (the last gate before an order) would "
            "otherwise disappear silently."
        ) from err

    try:
        exec(compile(code, exec_file, 'exec'), namespace)
    except Exception as err:  # noqa: BLE001 - re-raised below with full context
        raise ImportError(
            f"execution package failed to load '{exec_file}': "
            f"{type(err).__name__}: {err}. Refusing to expose an empty 'execution' "
            "module, because ExecutionPolicy (the last gate before an order) would "
            "otherwise disappear silently."
        ) from err

    missing = [name for name in _REQUIRED_API if name not in namespace]
    if missing:
        raise ImportError(
            f"execution package loaded '{exec_file}' but it does not define "
            f"{', '.join(missing)}. The execution safety gate is missing."
        )

    return namespace


_load_execution_module()
