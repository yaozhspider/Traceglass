"""Contract tests for durable trace stores.

The tests intentionally exercise the store through its small backend-neutral
interface.  A JSONL implementation is the default, while the SQLite backend
proves that the execution layer is not coupled to one storage format.
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
from pathlib import Path

import pytest

from traceglass.infrastructure.trace_store import (
    JsonlTraceStore,
    SQLiteTraceStore,
    TraceStoreConflictError,
)


def event(event_id: str, run_id: str = "run-1", **extra: object) -> dict[str, object]:
    value: dict[str, object] = {
        "event_id": event_id,
        "run_id": run_id,
        "type": "assistant.message",
        # Keep the fixture deterministic so a repeated idempotency-key
        # submission represents the same event payload.
        "timestamp": datetime(2026, 9, 24, tzinfo=timezone.utc),
        "content": f"message {event_id}",
        "payload": {"event_id": event_id, "nested": [1, 2, {"ok": True}]},
    }
    value.update(extra)
    return value


@pytest.fixture(params=["jsonl", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "jsonl":
        return JsonlTraceStore(tmp_path / "trace.jsonl")
    return SQLiteTraceStore(tmp_path / "trace.sqlite3")


def test_append_assigns_monotonic_sequence_and_returns_structured_record(store):
    first = store.append(event("evt-1"))
    second = store.append(event("evt-2"))
    other_run = store.append(event("evt-3", run_id="run-2"))

    assert first.event_id == "evt-1"
    assert first.run_id == "run-1"
    assert first.sequence == 1
    assert second.sequence == 2
    assert other_run.sequence == 1
    assert first.type == "assistant.message"
    assert first.payload["nested"][2]["ok"] is True


def test_read_supports_run_filter_and_cursor(store):
    for index in range(1, 5):
        store.append(event(f"evt-{index}"))
    store.append(event("other-1", run_id="run-2"))

    page = store.read("run-1", limit=2)
    assert [item.event_id for item in page.events] == ["evt-1", "evt-2"]
    assert page.next_cursor == "2"
    assert page.has_more is True

    resumed = store.read("run-1", after=page.next_cursor)
    assert [item.event_id for item in resumed.events] == ["evt-3", "evt-4"]
    assert resumed.next_cursor == "4"
    assert resumed.has_more is False


def test_event_detail_survives_reopen_and_payload_is_json_safe(tmp_path: Path):
    path = tmp_path / "trace.jsonl"
    original = JsonlTraceStore(path)
    stored = original.append(
        event(
            "evt-safe",
            payload={
                "when": datetime(2026, 9, 24, 12, 30, tzinfo=timezone.utc),
                "path": Path("/tmp/sample.bin"),
                "bytes": b"abc",
            },
        )
    )

    reopened = JsonlTraceStore(path)
    detail = reopened.get("evt-safe")
    assert detail == stored
    assert detail.payload["when"] == "2026-09-24T12:30:00+00:00"
    assert detail.payload["path"] == "/tmp/sample.bin"
    assert detail.payload["bytes"] == "YWJj"


def test_duplicate_event_id_is_idempotent_for_same_payload_but_conflicts_for_new_payload(store):
    stored = store.append(event("evt-1"))
    repeated = store.append(event("evt-1"))
    assert repeated == stored

    with pytest.raises(TraceStoreConflictError):
        store.append(event("evt-1", content="a different event"))


def test_parent_event_and_agent_links_are_retained(store):
    stored = store.append(
        event(
            "evt-child",
            parent_event_id="evt-parent",
            agent_run_id="subrun-1",
            agent_type="static-reverse",
            evidence_ids=["E-1"],
            artifact_ids=["A-1"],
        )
    )

    assert stored.parent_event_id == "evt-parent"
    assert stored.agent_run_id == "subrun-1"
    assert stored.agent_type == "static-reverse"
    assert stored.evidence_ids == ["E-1"]
    assert stored.artifact_ids == ["A-1"]


def test_child_agent_events_can_be_found_across_parent_runs(store):
    store.append(event("evt-child", agent_run_id="subrun-1"))
    store.append(event("evt-child-2", agent_run_id="subrun-1"))
    found = store.find_by_agent_run("subrun-1")
    assert [item.event_id for item in found] == ["evt-child", "evt-child-2"]


def test_concurrent_appends_preserve_unique_sequences(tmp_path: Path):
    store = JsonlTraceStore(tmp_path / "trace.jsonl")

    def append(index: int):
        return store.append(event(f"evt-{index}"))

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        records = list(executor.map(append, range(32)))

    assert sorted(record.sequence for record in records) == list(range(1, 33))
    assert len({record.event_id for record in records}) == 32


def test_invalid_cursor_and_missing_event_are_reported(store):
    store.append(event("evt-1"))
    assert store.get("missing") is None
    with pytest.raises(ValueError, match="cursor"):
        store.read("run-1", after="not-a-sequence")
