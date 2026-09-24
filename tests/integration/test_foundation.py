from __future__ import annotations

from pathlib import Path

from traceglass.application.trace_service import TraceService
from traceglass.domain.models import AgentRole, RunStatus, SubRunStatus, TraceEventType
from traceglass.infrastructure.trace_store import JsonlTraceStore


def test_trace_survives_process_boundary_and_cursor_resume(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "trace.jsonl"
    first = TraceService(JsonlTraceStore(path))
    first.create_run(task_id="task-1", run_id="run-1")
    first.emit(
        "run-1",
        event_type=TraceEventType.ASSISTANT_MESSAGE,
        event_id="evt-1",
        content="主 Agent 已建立分析计划。",
    )
    first.emit(
        "run-1",
        event_type=TraceEventType.THINKING_UPDATE,
        event_id="evt-2",
        content="下一步核查字符串与导入表。",
    )
    second = TraceService(JsonlTraceStore(path))
    second.create_run(task_id="task-1", run_id="run-1")
    page = second.replay("run-1", after="2")
    assert [event.event_id for event in page.events] == ["evt-2"]
    assert page.next_cursor == "3"

    first.start_run("run-1")
    first.complete_run("run-1")
    restarted = TraceService(JsonlTraceStore(path))
    restarted.create_run(task_id="task-1", run_id="run-1")
    assert restarted.get_run("run-1").status is RunStatus.COMPLETED


def test_child_agent_can_be_opened_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "trace.jsonl"
    first = TraceService(JsonlTraceStore(path))
    first.create_run(task_id="task-1", run_id="run-1")
    child = first.create_subrun(
        parent_run_id="run-1",
        subrun_id="subrun-1",
        role=AgentRole.STATIC_REVERSE,
        prompt="定位服务创建入口",
        capabilities=["disassemble"],
    )
    first.start_subrun(child.id)
    first.complete_subrun(child.id)

    restarted = TraceService(JsonlTraceStore(path))
    hydrated = restarted.get_subrun("subrun-1")
    assert hydrated.parent_run_id == "run-1"
    assert hydrated.status is SubRunStatus.COMPLETED
