from __future__ import annotations

from pathlib import Path

import pytest

from traceglass.application.trace_service import TraceService
from traceglass.domain.errors import InvariantViolation
from traceglass.domain.models import RunStatus, SubRunStatus, TraceEventType
from traceglass.infrastructure.trace_store import JsonlTraceStore


@pytest.fixture
def service(tmp_path: Path) -> TraceService:
    return TraceService(JsonlTraceStore(tmp_path / "trace.jsonl"))


def test_service_registers_run_subrun_and_replayable_events(service: TraceService) -> None:
    run = service.create_run(task_id="task-1", run_id="run-1")
    assert run.status is RunStatus.CREATED

    service.start_run(run.id)
    event = service.emit(
        run.id,
        event_type=TraceEventType.ASSISTANT_MESSAGE,
        title="开始静态分析",
        content="先建立样本画像，再定位入口。",
    )
    subrun = service.create_subrun(
        parent_run_id=run.id,
        subrun_id="subrun-1",
        role="static-reverse",
        prompt="定位持久化相关函数",
        capabilities=["disassemble"],
    )
    service.start_subrun(subrun.id)
    service.emit(
        run.id,
        event_type=TraceEventType.SUBAGENT_MESSAGE,
        title="子 Agent 已返回证据",
        content="发现服务创建调用。",
        agent_run_id=subrun.id,
        parent_event_id=event.event_id,
        evidence_ids=["E-1"],
    )

    page = service.replay(run.id)
    assert [item.type for item in page.events] == [
        TraceEventType.RUN_CREATED,
        TraceEventType.RUN_STATUS_CHANGED,
        TraceEventType.ASSISTANT_MESSAGE,
        TraceEventType.SUBAGENT_CREATED,
        TraceEventType.SUBAGENT_STARTED,
        TraceEventType.SUBAGENT_MESSAGE,
    ]
    assert page.events[-1].agent_run_id == subrun.id
    assert page.events[-1].evidence_ids == ["E-1"]


def test_service_rejects_second_level_subagent_and_unknown_parent(service: TraceService) -> None:
    service.create_run(task_id="task-1", run_id="run-1")
    subrun = service.create_subrun(
        parent_run_id="run-1",
        subrun_id="subrun-1",
        role="verifier",
        prompt="复核结论",
    )
    with pytest.raises(InvariantViolation):
        service.create_subrun(
            parent_run_id=subrun.id,
            subrun_id="subrun-2",
            role="experiment",
            prompt="二级代理不允许",
        )
    with pytest.raises(KeyError):
        service.create_subrun(
            parent_run_id="missing",
            subrun_id="subrun-3",
            role="experiment",
            prompt="父 Run 不存在",
        )


def test_tool_lifecycle_emits_started_and_completed(service: TraceService) -> None:
    service.create_run(task_id="task-1", run_id="run-1")
    service.start_run("run-1")
    service.tool_started(
        "run-1", tool_name="strings", arguments={"path": "sample.bin"}, call_id="call-1"
    )
    service.tool_completed(
        "run-1",
        call_id="call-1",
        result={"matches": 3},
        duration_ms=17,
    )
    events = service.replay("run-1").events
    assert [event.type for event in events[-2:]] == [
        TraceEventType.TOOL_STARTED,
        TraceEventType.TOOL_COMPLETED,
    ]
    assert events[-1].tool is not None
    assert events[-1].tool.result == {"matches": 3}


def test_completion_updates_run_state(service: TraceService) -> None:
    service.create_run(task_id="task-1", run_id="run-1")
    service.start_run("run-1")
    run = service.complete_run("run-1")
    assert run.status is RunStatus.COMPLETED
    assert service.get_run("run-1").status is RunStatus.COMPLETED
