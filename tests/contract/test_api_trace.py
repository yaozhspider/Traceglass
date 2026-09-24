from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from traceglass.api.app import create_app
from traceglass.infrastructure.trace_store import JsonlTraceStore


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(store=JsonlTraceStore(tmp_path / "trace.jsonl")))


def test_create_run_append_event_and_replay_after_cursor(tmp_path: Path) -> None:
    api = client(tmp_path)
    created = api.post(
        "/runs",
        json={"task_id": "task-1", "run_id": "run-1", "workspace_id": "ws-1"},
    )
    assert created.status_code == 201
    assert created.json()["run"]["id"] == "run-1"

    started = api.post("/runs/run-1/start")
    assert started.status_code == 200
    first = api.post(
        "/runs/run-1/events",
        json={
            "event_id": "evt-1",
            "type": "assistant.message",
            "title": "开始分析",
            "content": "先建立样本画像。",
        },
    )
    assert first.status_code == 201
    assert first.json()["event"]["sequence"] == 3

    second = api.post(
        "/runs/run-1/events",
        json={"event_id": "evt-2", "type": "thinking.update", "content": "检查导入点"},
    )
    assert second.status_code == 201

    page = api.get("/runs/run-1/trace?limit=1")
    assert page.status_code == 200
    assert page.json()["events"][0]["type"] == "run.created"
    assert page.json()["next_cursor"] == "1"
    resumed = api.get("/runs/run-1/trace", params={"after": page.json()["next_cursor"]})
    assert [event["event_id"] for event in resumed.json()["events"]][1:] == ["evt-1", "evt-2"]


def test_subagent_is_visible_and_detail_is_retrievable(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/runs", json={"task_id": "task-1", "run_id": "run-1"})
    created = api.post(
        "/runs/run-1/subruns",
        json={
            "subrun_id": "subrun-1",
            "role": "static-reverse",
            "prompt": "定位持久化入口",
            "capabilities": ["disassemble"],
        },
    )
    assert created.status_code == 201
    assert created.json()["subrun"]["id"] == "subrun-1"
    detail = api.get("/subruns/subrun-1")
    assert detail.status_code == 200
    assert detail.json()["subrun"]["role"] == "static-reverse"


def test_duplicate_event_is_idempotent_and_invalid_parent_is_404(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/runs", json={"task_id": "task-1", "run_id": "run-1"})
    body = {"event_id": "evt-1", "type": "assistant.message", "content": "same"}
    first = api.post("/runs/run-1/events", json=body)
    repeated = api.post("/runs/run-1/events", json=body)
    assert first.status_code == repeated.status_code == 201
    assert repeated.json()["event"] == first.json()["event"]
    missing = api.post(
        "/runs/missing/events",
        json={"type": "assistant.message", "content": "no parent"},
    )
    assert missing.status_code == 404
