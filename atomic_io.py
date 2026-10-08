"""atomic_io.py — crash- and concurrency-safe persistence helpers.

The codebase historically wrote state as ``path + ".tmp"`` followed by
``os.replace``. With a shared temporary name, two concurrent writers (threaded
Flask handlers, a supervisor and its child, a census burst) race: the first
``os.replace`` moves the shared temp file away and the second fails with
``FileNotFoundError`` — or, worse, one writer's half-written bytes are renamed
over the state file. Without ``fsync`` a power loss right after the rename can
also leave a zero-length file behind.

``atomic_write_text`` / ``atomic_write_json`` fix both problems:

* a unique temporary file is created in the destination directory
  (``tempfile.mkstemp``), so concurrent writers never share it and the final
  ``os.replace`` stays on one filesystem (atomic on POSIX and Windows);
* the payload is flushed and ``fsync``-ed before the rename;
* the temporary file is removed if anything fails, and the error propagates
  (callers decide whether a failed write is fatal — it is never silent here).

Last-writer-wins semantics are unchanged; readers always observe either the
complete old file or the complete new file.

Read-modify-write cycles additionally need mutual exclusion: two writers that
each load, mutate and atomically replace the same file still lose one update.
``locked_path`` serializes such cycles across threads (per-path re-entrant
lock) and processes (advisory ``flock`` on a ``<file>.lock`` sidecar).

Corrupt state must never be silently replaced. Treating an unreadable store
as empty and then saving over it destroys whatever was still recoverable (and
for ledgers, the audit trail). ``load_json_state`` therefore *quarantines* an
unreadable file — renames it to ``<file>.corrupt-<UTC timestamp>`` so the
evidence survives — before handing the caller its default.

JSONL helpers: ``append_jsonl`` writes each record with a single ``write`` call
under the path lock so concurrent appenders never interleave partial lines;
``read_jsonl`` tolerates a truncated final line (crash mid-append) and corrupt
lines, reporting how many were skipped instead of raising or hiding it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:  # POSIX advisory locks
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None  # type: ignore[assignment]

try:  # Windows byte-range locks
    import msvcrt as _msvcrt
except ImportError:  # POSIX
    _msvcrt = None  # type: ignore[assignment]

logger = logging.getLogger("atomic_io")

#: Upper bound for state files this module will parse (protects against a
#: runaway writer or a hostile file exhausting memory on load).
DEFAULT_MAX_STATE_BYTES = 64 * 1024 * 1024


class StateFileError(RuntimeError):
    """A persisted state file is unreadable or structurally invalid."""


# ── atomic replacement ──────────────────────────────────────────────────────

def atomic_write_text(path: str | os.PathLike[str], text: str, *, encoding: str = "utf-8", fsync: bool = True) -> None:
    """Atomically replace ``path`` with ``text``."""
    target = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(target)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
    if fsync:
        _fsync_directory(directory)


def atomic_write_bytes(path: str | os.PathLike[str], payload: bytes, *, fsync: bool = True) -> None:
    """Atomically replace ``path`` with raw ``payload`` bytes."""
    target = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(target)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
    if fsync:
        _fsync_directory(directory)


def atomic_write_json(
    path: str | os.PathLike[str],
    data: Any,
    *,
    indent: int | None = 2,
    fsync: bool = True,
    **dumps_kwargs: Any,
) -> None:
    """Serialize ``data`` first (so a serialization error never touches disk), then replace atomically."""
    payload = json.dumps(data, indent=indent, **dumps_kwargs)
    atomic_write_text(path, payload, fsync=fsync)


def _fsync_directory(directory: str) -> None:
    """Persist the rename itself (POSIX); best effort where unsupported."""
    if os.name != "posix":  # pragma: no cover - Windows has no directory fds
        return
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


# ── locking ─────────────────────────────────────────────────────────────────

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_HELD = threading.local()


def _thread_lock_for(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


def lock_file_path(path: str | os.PathLike[str]) -> str:
    """Sidecar lock file used by :func:`locked_path` for ``path``."""
    return os.fspath(path) + ".lock"


@contextmanager
def locked_path(path: str | os.PathLike[str]) -> Iterator[None]:
    """Serialize a read-modify-write of ``path`` across threads and processes.

    Re-entrant within one thread (nested use for the same path does not
    deadlock). Fails closed (raises :class:`StateFileError`) when the platform
    offers no inter-process lock primitive rather than silently racing.
    """
    key = os.path.abspath(os.fspath(path))
    thread_lock = _thread_lock_for(key)
    with thread_lock:
        held: dict[str, int] = getattr(_HELD, "counts", None) or {}
        _HELD.counts = held
        if held.get(key, 0) > 0:  # re-entrant: the OS lock is already ours
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return

        lock_path = lock_file_path(key)
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        with open(lock_path, "a+b") as lock_handle:
            if _fcntl is not None:
                _fcntl.flock(lock_handle.fileno(), _fcntl.LOCK_EX)
            elif _msvcrt is not None:  # pragma: no cover - Windows
                lock_handle.seek(0)
                if not lock_handle.read(1):
                    lock_handle.write(b"0")
                    lock_handle.flush()
                lock_handle.seek(0)
                _msvcrt.locking(lock_handle.fileno(), _msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - exotic runtime
                raise StateFileError(f"No inter-process file-lock backend available for {key}")
            held[key] = 1
            try:
                yield
            finally:
                held[key] = 0
                if _fcntl is not None:
                    _fcntl.flock(lock_handle.fileno(), _fcntl.LOCK_UN)
                elif _msvcrt is not None:  # pragma: no cover - Windows
                    lock_handle.seek(0)
                    _msvcrt.locking(lock_handle.fileno(), _msvcrt.LK_UNLCK, 1)


# ── corrupt-state handling ──────────────────────────────────────────────────

def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def quarantine_file(path: str | os.PathLike[str], reason: str) -> str | None:
    """Move an unreadable state file aside so it is preserved, not overwritten.

    Returns the quarantine path, or ``None`` if the file vanished or could not
    be moved (the failure is logged; the caller still must not trust it).
    """
    source = os.fspath(path)
    destination = f"{source}.corrupt-{_utc_stamp()}"
    try:
        os.replace(source, destination)
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.error("Could not quarantine corrupt state file %s (%s): %s", source, reason, exc)
        return None
    logger.error("Quarantined corrupt state file %s -> %s (%s)", source, destination, reason)
    return destination


@dataclass
class LoadResult:
    """Outcome of :func:`load_json_state`."""

    data: Any
    status: str  # "ok" | "missing" | "corrupt"
    quarantined_to: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _reject_non_finite(token: str) -> float:
    raise ValueError(f"non-finite JSON number {token!r}")


def parse_json_strict(text: str) -> Any:
    """``json.loads`` that rejects ``NaN``/``Infinity`` tokens (not valid JSON)."""
    return json.loads(text, parse_constant=_reject_non_finite)


def load_json_state(
    path: str | os.PathLike[str],
    *,
    expected_type: type | tuple[type, ...] = dict,
    default_factory: Callable[[], Any] = dict,
    quarantine: bool = True,
    max_bytes: int = DEFAULT_MAX_STATE_BYTES,
    allow_non_finite: bool = False,
) -> LoadResult:
    """Load a JSON state file without ever masking corruption.

    * missing file → ``default_factory()`` with status ``"missing"``;
    * unreadable / invalid JSON / wrong top-level type / oversized → the file
      is quarantined (unless ``quarantine=False``), the error is logged and the
      default is returned with status ``"corrupt"``;
    * otherwise the parsed value with status ``"ok"``.
    """
    source = os.fspath(path)
    try:
        size = os.path.getsize(source)
    except FileNotFoundError:
        return LoadResult(default_factory(), "missing")
    except OSError as exc:
        return LoadResult(default_factory(), "corrupt", error=f"stat failed: {exc}")

    error: str | None = None
    data: Any = None
    if size > max_bytes:
        error = f"file is {size} bytes, above the {max_bytes}-byte safety limit"
    else:
        try:
            with open(source, encoding="utf-8") as handle:
                text = handle.read()
        except FileNotFoundError:
            return LoadResult(default_factory(), "missing")
        except (OSError, UnicodeDecodeError) as exc:
            error = f"unreadable: {exc}"
        else:
            if not text.strip():
                error = "file is empty (truncated write?)"
            else:
                try:
                    data = json.loads(text) if allow_non_finite else parse_json_strict(text)
                except (ValueError, RecursionError) as exc:
                    error = f"invalid JSON: {exc}"
                else:
                    if not isinstance(data, expected_type):
                        error = f"top-level JSON is {type(data).__name__}, expected {_type_names(expected_type)}"

    if error is None:
        return LoadResult(data, "ok")

    logger.error("State file %s is corrupt: %s", source, error)
    moved = quarantine_file(source, error) if quarantine else None
    return LoadResult(default_factory(), "corrupt", quarantined_to=moved, error=error)


def _type_names(expected: type | tuple[type, ...]) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


# ── JSON Lines ──────────────────────────────────────────────────────────────

def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def encode_jsonl_record(record: Any) -> str:
    """Serialize one JSONL record (single line, non-finite floats rejected)."""
    line = json.dumps(record, default=_json_default, allow_nan=False, separators=(",", ":"))
    if "\n" in line:  # json.dumps never emits raw newlines, but be explicit
        raise ValueError("JSONL record serialized across multiple lines")
    return line + "\n"


def append_jsonl(path: str | os.PathLike[str], record: Any, *, fsync: bool = False) -> None:
    """Append one record as a complete line.

    The record is serialized *before* the file is touched, written with one
    ``write`` call under :func:`locked_path`, and — if the existing file ends
    in a torn partial line from an earlier crash — a newline is inserted first
    so the new record is never glued onto the corrupt fragment.
    """
    line = encode_jsonl_record(record)
    target = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
    with locked_path(target):
        needs_separator = False
        try:
            with open(target, "rb") as existing:
                existing.seek(0, os.SEEK_END)
                if existing.tell() > 0:
                    existing.seek(-1, os.SEEK_END)
                    needs_separator = existing.read(1) != b"\n"
        except FileNotFoundError:
            pass
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(("\n" if needs_separator else "") + line)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())


@dataclass
class JsonlReadResult:
    """Records parsed by :func:`read_jsonl` plus what had to be skipped."""

    records: list[Any] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    truncated_tail: bool = False

    @property
    def clean(self) -> bool:
        return self.skipped == 0


def read_jsonl(
    path: str | os.PathLike[str],
    *,
    expected_type: type | tuple[type, ...] = dict,
    max_errors_reported: int = 20,
) -> JsonlReadResult:
    """Read a JSONL file, skipping (and counting) unparseable lines.

    A missing file yields an empty, clean result. Lines that are blank are
    ignored; lines that fail to parse, contain non-finite numbers, or are not
    of ``expected_type`` are skipped and reported. A final line without a
    trailing newline that fails to parse is flagged as ``truncated_tail``
    (the signature of a crash mid-append).
    """
    result = JsonlReadResult()
    source = os.fspath(path)
    try:
        with open(source, encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except FileNotFoundError:
        return result
    lines = content.split("\n")
    ends_with_newline = content.endswith("\n")
    for index, raw in enumerate(lines):
        if not raw.strip():
            continue
        try:
            record = parse_json_strict(raw)
        except (ValueError, RecursionError) as exc:
            result.skipped += 1
            is_last = index == len(lines) - 1 and not ends_with_newline
            if is_last:
                result.truncated_tail = True
            if len(result.errors) < max_errors_reported:
                result.errors.append(f"line {index + 1}: {exc}")
            continue
        if not isinstance(record, expected_type):
            result.skipped += 1
            if len(result.errors) < max_errors_reported:
                result.errors.append(f"line {index + 1}: expected {_type_names(expected_type)}, got {type(record).__name__}")
            continue
        result.records.append(record)
    if result.skipped:
        logger.warning("Skipped %d unreadable line(s) in %s: %s", result.skipped, source, result.errors[:3])
    return result


# ── numeric hygiene for persisted values ────────────────────────────────────

def finite_or_none(value: Any) -> float | None:
    """Return ``float(value)`` if it is a real finite number, else ``None``.

    Booleans are rejected (``True`` is not a price), as are strings that parse
    to NaN/inf.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
