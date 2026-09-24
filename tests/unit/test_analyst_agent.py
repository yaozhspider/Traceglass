from __future__ import annotations

from pathlib import Path

from traceglass.agent.analyst import AnalystAgent
from traceglass.agent.executor import ToolRegistry
from traceglass.application.trace_service import TraceService
from traceglass.domain.models import TraceEventType
from traceglass.infrastructure.trace_store import JsonlTraceStore


def test_analyst_agent_exposes_plan_and_execution_trace(tmp_path: Path) -> None:
    service = TraceService(JsonlTraceStore(tmp_path / "trace.jsonl"))
    service.create_run(task_id="task-1", run_id="run-1")
    tools = ToolRegistry()
    for name in ("prepare", "static-reverse", "dynamic-evidence", "verifier", "reporter"):
        tools.register(name, lambda context, tool=name: {"tool": tool, "ok": True})

    result = AnalystAgent(service, tools).analyze(
        run_id="run-1",
        objective="分析样本的持久化机制",
        sample_ids=["sample-1"],
    )

    assert result.plan.objective == "分析样本的持久化机制"
    assert len(result.execution.completed_step_ids) == 5
    events = service.replay("run-1").events
    assert events[0].type is TraceEventType.RUN_CREATED
    assert any(event.type is TraceEventType.ASSISTANT_MESSAGE for event in events)
    assert events[-1].type is TraceEventType.RUN_STATUS_CHANGED
