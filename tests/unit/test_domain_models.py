from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from traceglass.domain.errors import InvalidStateTransition, InvariantViolation
from traceglass.domain.models import (
    AgentRole,
    Artifact,
    ArtifactKind,
    Evidence,
    EvidenceKind,
    Finding,
    FindingStatus,
    Run,
    RunStatus,
    Step,
    StepStatus,
    SubRun,
    SubRunStatus,
    Task,
    TaskStatus,
    ToolCall,
    TraceEvent,
    TraceEventType,
)


def test_task_and_run_have_stable_json_contracts() -> None:
    task = Task(
        id="task_001",
        objective="分析样本中的持久化机制",
        workspace_id="ws_001",
        workspace_path="/workspace/sample",
    )
    run = Run(id="run_001", task_id=task.id)

    assert task.status is TaskStatus.DRAFT
    assert run.status is RunStatus.CREATED
    payload = run.model_dump(mode="json")
    assert payload["id"] == "run_001"
    assert payload["task_id"] == "task_001"
    assert payload["status"] == "created"
    assert payload["created_at"].endswith("Z") or "+00:00" in payload["created_at"]


def test_run_lifecycle_rejects_invalid_transition() -> None:
    run = Run(id="run_002", task_id="task_001")
    run.transition_to(RunStatus.RUNNING)
    assert run.status is RunStatus.RUNNING
    run.transition_to(RunStatus.COMPLETED)
    assert run.status is RunStatus.COMPLETED

    with pytest.raises(InvalidStateTransition):
        run.transition_to(RunStatus.RUNNING)
    with pytest.raises(InvalidStateTransition):
        run.status = RunStatus.RUNNING


def test_step_dependencies_are_unique_and_step_lifecycle_is_bounded() -> None:
    step = Step(
        id="step_001",
        run_id="run_001",
        title="提取字符串",
        position=1,
        depends_on=["step_000"],
    )
    step.transition_to(StepStatus.RUNNING)
    step.transition_to(StepStatus.SUCCEEDED)
    assert step.status is StepStatus.SUCCEEDED

    with pytest.raises(InvalidStateTransition):
        step.transition_to(StepStatus.RUNNING)

    with pytest.raises(InvariantViolation):
        Step(
            id="step_002",
            run_id="run_001",
            title="循环依赖",
            position=2,
            depends_on=["step_002", "step_002"],
        )


def test_subrun_is_explicit_one_level_agent_execution() -> None:
    subrun = SubRun(
        id="subrun_001",
        parent_run_id="run_001",
        role=AgentRole.STATIC_REVERSE,
        prompt="定位反序列化入口",
        capabilities=["disassemble", "decompile"],
    )
    assert subrun.depth == 1
    assert subrun.status is SubRunStatus.CREATED
    subrun.transition_to(SubRunStatus.RUNNING)
    subrun.transition_to(SubRunStatus.COMPLETED)

    with pytest.raises(InvariantViolation):
        SubRun(
            id="subrun_002",
            parent_run_id="subrun_001",
            role=AgentRole.VERIFIER,
            prompt="不允许二级子 agent",
            depth=2,
        )


def test_trace_event_contains_replayable_parent_and_tool_links() -> None:
    event = TraceEvent(
        event_id="evt_001",
        run_id="run_001",
        sequence=1,
        type=TraceEventType.TOOL_COMPLETED,
        parent_event_id="evt_000",
        agent_run_id="subrun_001",
        agent_type=AgentRole.STATIC_REVERSE,
        title="反编译关键函数",
        content="发现调用 CreateServiceW",
        tool=ToolCall(
            name="decompile_function",
            arguments={"address": "0x401000"},
            result={"function": "install_service"},
            duration_ms=1840,
        ),
        evidence_ids=["E-031"],
    )
    dumped = event.model_dump(mode="json")
    assert dumped["type"] == "tool.completed"
    assert dumped["tool"]["name"] == "decompile_function"
    assert dumped["evidence_ids"] == ["E-031"]

    with pytest.raises(InvariantViolation):
        TraceEvent(
            event_id="evt_002",
            run_id="run_001",
            sequence=0,
            type=TraceEventType.ASSISTANT_MESSAGE,
        )


def test_evidence_and_artifact_are_immutable_source_backed_records() -> None:
    evidence = Evidence(
        id="E-031",
        run_id="run_001",
        kind=EvidenceKind.STATIC,
        statement="函数会创建名为 Updater 的服务",
        source_ref="sample.exe!install_service:0x401000",
        confidence=0.92,
    )
    assert evidence.model_dump(mode="json")["kind"] == "static"
    with pytest.raises(ValidationError):
        evidence.statement = "被修改的事实"

    artifact = Artifact(
        id="artifact_001",
        run_id="run_001",
        name="decompile.json",
        kind=ArtifactKind.JSON,
        content_hash="a" * 64,
        uri="traceglass://runs/run_001/artifacts/decompile.json",
        size_bytes=128,
        evidence_ids=["E-031"],
    )
    assert artifact.model_dump(mode="json")["content_hash"] == "a" * 64
    with pytest.raises(ValidationError):
        artifact.size_bytes = 256


def test_finding_validates_confidence_and_reference_sets() -> None:
    finding = Finding(
        id="finding_001",
        run_id="run_001",
        claim="样本通过 Windows 服务实现持久化",
        status=FindingStatus.OPEN,
        confidence=0.87,
        evidence_ids=["E-031"],
        contradiction_ids=["E-099"],
        unknowns=["服务启动时机尚未确认"],
        next_action="动态观察服务创建调用",
    )
    assert finding.model_dump(mode="json")["status"] == "open"

    with pytest.raises(InvariantViolation):
        Finding(
            id="finding_002",
            run_id="run_001",
            claim="重复引用",
            evidence_ids=["E-031", "E-031"],
        )
    with pytest.raises(ValidationError):
        Finding(
            id="finding_003",
            run_id="run_001",
            claim="越界置信度",
            confidence=1.1,
        )


def test_timestamps_are_timezone_aware_and_ids_are_non_empty() -> None:
    aware = datetime.now(timezone.utc)
    task = Task(
        id="task_aware",
        objective="检查时间",
        workspace_id="ws",
        created_at=aware,
        updated_at=aware,
    )
    assert task.created_at.tzinfo is not None

    with pytest.raises(ValidationError):
        Task(id="", objective="x", workspace_id="ws")
    with pytest.raises(ValidationError):
        Run(id="run_bad", task_id="")
