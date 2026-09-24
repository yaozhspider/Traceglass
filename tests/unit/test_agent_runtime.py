from __future__ import annotations

from pathlib import Path

import pytest

from traceglass.agent.executor import PlanExecutor, ToolRegistry
from traceglass.agent.planner import AnalysisPlan, Planner
from traceglass.agent.subagent_manager import EvidenceBundle, SubagentManager
from traceglass.application.trace_service import TraceService
from traceglass.domain.errors import InvariantViolation
from traceglass.domain.models import AgentRole, TraceEventType
from traceglass.infrastructure.trace_store import JsonlTraceStore


@pytest.fixture
def service(tmp_path: Path) -> TraceService:
    return TraceService(JsonlTraceStore(tmp_path / "trace.jsonl"))


def test_planner_returns_stable_plan_execute_steps() -> None:
    plan = Planner().build("分析样本的持久化与网络行为", sample_ids=["sha256:abc"])
    assert isinstance(plan, AnalysisPlan)
    assert [step.role for step in plan.steps] == [
        AgentRole.PREPARE,
        AgentRole.STATIC_REVERSE,
        AgentRole.DYNAMIC_EVIDENCE,
        AgentRole.VERIFIER,
        AgentRole.REPORTER,
    ]
    assert plan.steps[1].depends_on == [plan.steps[0].id]
    assert plan.sample_ids == ["sha256:abc"]


def test_plan_executor_emits_user_visible_react_trace(service: TraceService) -> None:
    service.create_run(task_id="task-1", run_id="run-1")
    service.start_run("run-1")
    plan = Planner().build("提取样本画像")
    registry = ToolRegistry()
    registry.register("prepare", lambda context: {"files": 3})
    executor = PlanExecutor(service, registry, max_turns_per_step=2)
    result = executor.execute("run-1", plan, roles={AgentRole.PREPARE})

    assert result.completed_step_ids == [plan.steps[0].id]
    events = service.replay("run-1").events
    types = [event.type for event in events]
    assert TraceEventType.THINKING_UPDATE in types
    assert TraceEventType.TODO_CREATED in types
    assert TraceEventType.TOOL_STARTED in types
    assert TraceEventType.TOOL_COMPLETED in types
    assert any(event.tool and event.tool.result == {"files": 3} for event in events)


def test_subagent_manager_returns_structured_bundle_and_blocks_nesting(service: TraceService) -> None:
    service.create_run(task_id="task-1", run_id="run-1")
    manager = SubagentManager(service)

    def handler(prompt: str, capabilities: list[str]) -> EvidenceBundle:
        return EvidenceBundle(
            summary=f"完成：{prompt}",
            evidence=[{"id": "E-1", "statement": "发现服务创建调用"}],
            artifacts=[],
        )

    manager.register_handler(AgentRole.STATIC_REVERSE, handler)
    bundle = manager.run(
        parent_run_id="run-1",
        subrun_id="subrun-1",
        role=AgentRole.STATIC_REVERSE,
        prompt="定位持久化入口",
        capabilities=["disassemble"],
    )
    assert bundle.summary.startswith("完成")
    assert bundle.evidence[0]["id"] == "E-1"
    with pytest.raises(InvariantViolation):
        manager.run(
            parent_run_id="subrun-1",
            subrun_id="subrun-2",
            role=AgentRole.VERIFIER,
            prompt="二级代理",
        )
