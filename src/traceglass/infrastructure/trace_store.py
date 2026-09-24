"""Durable, backend-neutral storage for execution trace events.

The execution engine only depends on :class:`TraceStore`.  The default
implementation is an append-only JSONL file because it is easy to inspect and
back up.  :class:`SQLiteTraceStore` implements the same contract for larger
workspaces or deployments that need indexed queries.

The store assigns ``sequence`` values per run.  A cursor is the last sequence
returned for that run, represented as a decimal string (for example ``"12"``).
Reads with ``after="12"`` therefore resume at sequence 13.  Event IDs are
idempotency keys: replaying the same event returns the original record, while
reusing an ID for different content is rejected.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import enum
import json
import math
import sqlite3
import threading
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class TraceStoreConflictError(ValueError):
    """Raised when an event ID is reused for a different event."""


# A more descriptive alias that callers may prefer.
DuplicateTraceEventError = TraceStoreConflictError


@dataclass(frozen=True)
class StoredTraceEvent(Mapping[str, Any]):
    """A JSON-safe trace event returned by a store.

    It behaves like a read-only mapping and also exposes fields as attributes,
    making it convenient for both API serializers and application services.
    Unknown fields from future event schema versions are retained in ``data``.
    """

    data: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", _json_safe(dict(self.data)))

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __getattr__(self, name: str) -> Any:
        # Dataclass fields (``data``) are handled by normal attribute lookup.
        try:
            return _attribute_view(self.data[name])
        except KeyError as exc:  # pragma: no cover - normal Python semantics
            raise AttributeError(name) from exc

    def to_dict(self) -> dict[str, Any]:
        """Return a detached mutable representation suitable for JSON output."""

        return json.loads(json.dumps(self.data, ensure_ascii=False, allow_nan=False))

    model_dump = to_dict


@dataclass(frozen=True)
class _AttributeMapping(Mapping[str, Any]):
    """Read-only nested mapping that supports ``event.tool.result`` access."""

    data: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        return _attribute_view(self.data[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.data) == dict(other)
        return NotImplemented

    def __getattr__(self, name: str) -> Any:
        try:
            return _attribute_view(self.data[name])
        except KeyError as exc:  # pragma: no cover - normal Python semantics
            raise AttributeError(name) from exc


def _attribute_view(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _AttributeMapping(value)
    if isinstance(value, list):
        return [_attribute_view(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_attribute_view(item) for item in value)
    return value


@dataclass(frozen=True)
class TracePage(Sequence[StoredTraceEvent]):
    """A cursor page returned from :meth:`TraceStore.read`."""

    events: tuple[StoredTraceEvent, ...]
    next_cursor: str | None
    has_more: bool

    # ``items`` is a useful spelling for API clients that do not use the word
    # event, while ``events`` remains the canonical field.
    @property
    def items(self) -> tuple[StoredTraceEvent, ...]:
        return self.events

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, index: int | slice) -> StoredTraceEvent | tuple[StoredTraceEvent, ...]:
        return self.events[index]

    def __iter__(self) -> Iterator[StoredTraceEvent]:
        return iter(self.events)


class TraceStore(Protocol):
    """Backend contract consumed by the execution layer."""

    def append(self, event: Any) -> StoredTraceEvent:
        ...

    def get(self, event_id: str) -> StoredTraceEvent | None:
        ...

    def read(
        self,
        run_id: str,
        *,
        after: str | int | None = None,
        limit: int | None = None,
    ) -> TracePage:
        ...

    def find_by_agent_run(self, agent_run_id: str) -> tuple[StoredTraceEvent, ...]:
        """Return events emitted by a child agent across parent runs."""
        ...


def _json_safe(value: Any) -> Any:
    """Recursively convert common Python values into strict JSON values.

    Trace payloads frequently include timestamps, paths, enum values, and
    small byte snippets.  Converting those values at the storage boundary
    avoids backend-specific encoders and makes replay deterministic.
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # JSON's NaN/Infinity values are not portable between clients.
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return _json_safe(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe(dataclasses.asdict(value))
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except TypeError:
            return _json_safe(value.model_dump())
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        # Sets have no stable iteration order; sort by repr so retries produce
        # the same serialized bytes and idempotency comparisons remain valid.
        return [_json_safe(item) for item in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    # A trace should remain durable even when an extension puts a custom value
    # into metadata.  Its repr is safer and more deterministic than pickle.
    return str(value)


def _event_mapping(event: Any) -> dict[str, Any]:
    if isinstance(event, StoredTraceEvent):
        raw = event.to_dict()
    elif isinstance(event, Mapping):
        raw = dict(event)
    elif dataclasses.is_dataclass(event) and not isinstance(event, type):
        raw = dataclasses.asdict(event)
    elif hasattr(event, "model_dump") and callable(event.model_dump):
        try:
            raw = event.model_dump(mode="json")
        except TypeError:
            raw = event.model_dump()
    else:
        raise TypeError("trace event must be a mapping, dataclass, or model_dump object")

    result = _json_safe(raw)
    if not isinstance(result, dict):  # defensive; all supported inputs are maps
        raise TypeError("trace event did not serialize to an object")
    event_id = result.get("event_id")
    run_id = result.get("run_id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("trace event requires a non-empty event_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("trace event requires a non-empty run_id")
    if not isinstance(result.get("type"), str) or not result["type"].strip():
        raise ValueError("trace event requires a non-empty type")
    if "timestamp" not in result:
        result["timestamp"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if "payload" not in result or result["payload"] is None:
        result["payload"] = {}
    if not isinstance(result["payload"], dict):
        raise ValueError("trace event payload must be an object")
    return result


def _event_has_timestamp(event: Any) -> bool:
    """Whether the caller supplied a timestamp instead of store defaulting it."""

    if isinstance(event, Mapping):
        return "timestamp" in event
    if isinstance(event, StoredTraceEvent):
        return "timestamp" in event.data
    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        return "timestamp" in dataclasses.asdict(event)
    if hasattr(event, "model_dump") and callable(event.model_dump):
        try:
            return "timestamp" in event.model_dump(mode="json")
        except TypeError:
            return "timestamp" in event.model_dump()
    return True


def _sequence_from_cursor(after: str | int | None, *, run_id: str) -> int:
    if after is None:
        return 0
    if isinstance(after, bool):
        raise ValueError("cursor must be a non-negative sequence")
    text = str(after).strip()
    # Accept a qualified cursor as a convenience for workspace streams while
    # retaining a compact sequence-only cursor in API responses.
    if ":" in text:
        cursor_run, text = text.rsplit(":", 1)
        if cursor_run != run_id:
            raise ValueError("cursor belongs to another run")
    try:
        sequence = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("cursor must be a non-negative sequence") from exc
    if sequence < 0:
        raise ValueError("cursor must be a non-negative sequence")
    return sequence


def _without_sequence(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("sequence", None)
    return result


def _same_event(
    previous: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    ignore_generated_timestamp: bool = False,
) -> bool:
    previous_value = _without_sequence(previous)
    candidate_value = _without_sequence(candidate)
    if ignore_generated_timestamp:
        previous_value.pop("timestamp", None)
        candidate_value.pop("timestamp", None)
    return previous_value == candidate_value


def _record(mapping: Mapping[str, Any], sequence: int) -> StoredTraceEvent:
    data = dict(mapping)
    data["sequence"] = sequence
    return StoredTraceEvent(data)


class _PathLockMixin:
    _locks: MutableMapping[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    @classmethod
    def _lock_for(cls, path: str) -> threading.RLock:
        with cls._locks_guard:
            lock = cls._locks.get(path)
            if lock is None:
                lock = threading.RLock()
                cls._locks[path] = lock
            return lock


class JsonlTraceStore(_PathLockMixin):
    """Append-only JSONL trace store.

    The file is one complete event object per line.  Every append flushes and
    fsyncs while holding a per-path lock; readers can therefore replay a file
    while another thread is writing it without observing a partial line.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = self._lock_for(str(self.path.resolve()))
        self._events: dict[str, StoredTraceEvent] = {}
        self._load()

    def _load(self) -> None:
        self._events = {}
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid trace JSON at line {line_number}") from exc
                if not isinstance(value, dict):
                    raise ValueError(f"trace line {line_number} is not an object")
                stored = StoredTraceEvent(value)
                event_id = stored.data.get("event_id")
                if not isinstance(event_id, str):
                    raise ValueError(f"trace line {line_number} has no event_id")
                self._events[event_id] = stored

    def _refresh(self) -> None:
        # Full reload keeps separate store instances convergent and is adequate
        # for the append-only event stream.  SQLite handles larger histories.
        self._load()

    def append(self, event: Any) -> StoredTraceEvent:
        candidate = _event_mapping(event)
        timestamp_provided = _event_has_timestamp(event)
        event_id = candidate["event_id"]
        with self._lock:
            self._refresh()
            previous = self._events.get(event_id)
            if previous is not None:
                if not _same_event(
                    previous.data,
                    candidate,
                    ignore_generated_timestamp=not timestamp_provided,
                ):
                    raise TraceStoreConflictError(f"event_id already contains different data: {event_id}")
                return previous
            run_id = candidate["run_id"]
            sequence = max(
                (
                    int(item.data["sequence"])
                    for item in self._events.values()
                    if item.data.get("run_id") == run_id
                ),
                default=0,
            ) + 1
            stored = _record(candidate, sequence)
            line = json.dumps(stored.data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                # ``fsync`` is intentionally best effort on filesystems that do
                # not expose it (for example some in-memory test mounts).
                try:
                    import os

                    os.fsync(handle.fileno())
                except OSError:
                    pass
            self._events[event_id] = stored
            return stored

    def get(self, event_id: str) -> StoredTraceEvent | None:
        with self._lock:
            self._refresh()
            return self._events.get(event_id)

    def read(
        self,
        run_id: str,
        *,
        after: str | int | None = None,
        limit: int | None = None,
    ) -> TracePage:
        cursor = _sequence_from_cursor(after, run_id=run_id)
        if limit is not None and limit <= 0:
            raise ValueError("limit must be a positive integer")
        with self._lock:
            self._refresh()
            events = sorted(
                (
                    item
                    for item in self._events.values()
                    if item.data.get("run_id") == run_id
                    and int(item.data.get("sequence", 0)) > cursor
                ),
                key=lambda item: int(item.data["sequence"]),
            )
            has_more = limit is not None and len(events) > limit
            if limit is not None:
                events = events[:limit]
            next_cursor = str(events[-1].sequence) if events else None
            return TracePage(tuple(events), next_cursor, has_more)

    def find_by_agent_run(self, agent_run_id: str) -> tuple[StoredTraceEvent, ...]:
        with self._lock:
            self._refresh()
            events = [
                item for item in self._events.values() if item.data.get("agent_run_id") == agent_run_id
            ]
            return tuple(sorted(events, key=lambda item: (item.run_id, item.sequence)))


class SQLiteTraceStore(_PathLockMixin):
    """SQLite implementation of the :class:`TraceStore` contract."""

    def __init__(self, path: str | Path):
        self.path = ":memory:" if str(path) == ":memory:" else str(Path(path).expanduser())
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = self._lock_for(str(Path(self.path).resolve()))
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    UNIQUE (run_id, sequence)
                )
                """
            )
            self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def append(self, event: Any) -> StoredTraceEvent:
        candidate = _event_mapping(event)
        timestamp_provided = _event_has_timestamp(event)
        event_id = candidate["event_id"]
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT event_json FROM trace_events WHERE event_id = ?", (event_id,)
                ).fetchone()
                if row is not None:
                    previous = StoredTraceEvent(json.loads(row["event_json"]))
                    if not _same_event(
                        previous.data,
                        candidate,
                        ignore_generated_timestamp=not timestamp_provided,
                    ):
                        raise TraceStoreConflictError(
                            f"event_id already contains different data: {event_id}"
                        )
                    self._connection.rollback()
                    return previous
                run_id = candidate["run_id"]
                row = self._connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                    "FROM trace_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                sequence = int(row["next_sequence"])
                stored = _record(candidate, sequence)
                event_json = json.dumps(stored.data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                self._connection.execute(
                    "INSERT INTO trace_events(event_id, run_id, sequence, event_json) VALUES (?, ?, ?, ?)",
                    (event_id, run_id, sequence, event_json),
                )
                self._connection.commit()
                return stored
            except Exception:
                self._connection.rollback()
                raise

    def get(self, event_id: str) -> StoredTraceEvent | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT event_json FROM trace_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return StoredTraceEvent(json.loads(row["event_json"])) if row is not None else None

    def read(
        self,
        run_id: str,
        *,
        after: str | int | None = None,
        limit: int | None = None,
    ) -> TracePage:
        cursor = _sequence_from_cursor(after, run_id=run_id)
        if limit is not None and limit <= 0:
            raise ValueError("limit must be a positive integer")
        with self._lock:
            sql = (
                "SELECT event_json FROM trace_events "
                "WHERE run_id = ? AND sequence > ? ORDER BY sequence ASC"
            )
            params: list[Any] = [run_id, cursor]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit + 1)
            rows = self._connection.execute(sql, params).fetchall()
            has_more = limit is not None and len(rows) > limit
            if limit is not None:
                rows = rows[:limit]
            events = tuple(StoredTraceEvent(json.loads(row["event_json"])) for row in rows)
            next_cursor = str(events[-1].sequence) if events else None
            return TracePage(events, next_cursor, has_more)

    def find_by_agent_run(self, agent_run_id: str) -> tuple[StoredTraceEvent, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_json FROM trace_events "
                "WHERE json_extract(event_json, '$.agent_run_id') = ? "
                "ORDER BY run_id, sequence",
                (agent_run_id,),
            ).fetchall()
            return tuple(StoredTraceEvent(json.loads(row["event_json"])) for row in rows)


__all__ = [
    "DuplicateTraceEventError",
    "JsonlTraceStore",
    "SQLiteTraceStore",
    "StoredTraceEvent",
    "TracePage",
    "TraceStore",
    "TraceStoreConflictError",
]
