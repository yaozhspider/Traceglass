"""Application service for durable, inspectable execution traces.

The service owns the small amount of orchestration state needed to validate
parent/child links and lifecycle transitions.  Every externally visible event
is written through the injected :class:`TraceStore`; callers can therefore
replay a run after a process restart or an SSE disconnect.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from traceglass.domain.errors import InvariantViolation
from traceglass.domain.models import (
    AgentRole,
    Artifact,
    Evidence,
    Run,
    RunStatus,
    SubRun,
    SubRunStatus,
    ToolCall,
    TraceEvent,
    TraceEventType,
    new_id,
)
from traceglass.infrastructure.trace_store import TracePage, TraceStore


class TraceService:
    """Create lifecycle records and append replayable trace events."""

    def __init__(self, store: TraceStore):
        self.store = store
        self._runs: dict[str, Run] = {}
        self._subruns: dict[str, SubRun] = {}
        self._evidence: dict[str, Evidence] = {}
        self._artifacts: dict[str, Artifact] = {}
        self._tools: dict[tuple[str, str], ToolCall] = {}

    def create_run(
        self,
        *,
        task_id: str,
        run_id: str | None = None,
        workspace_id: str = "default",
        parent_run_id: str | None = None,
        attempt: int = 1,
    ) -> Run:
        run = Run(
            id=run_id or new_id("run"),
            task_id=task_id,
            workspace_id=workspace_id,
            parent_run_id=parent_run_id,
            attempt=attempt,
        )
        existing = self._runs.get(run.id)
        if existing is not None:
            if existing.task_id == run.task_id and existing.workspace_id == run.workspace_id:
                return existing
            raise InvariantViolation(f"run id already registered: {run.id}")
        hydrated = self._hydrate_run(run.id)
        if hydrated is not None:
            if hydrated.task_id == run.task_id and hydrated.workspace_id == run.workspace_id:
                self._runs[run.id] = hydrated
                return hydrated
            raise InvariantViolation(f"run id already persisted: {run.id}")
        if run.id in self._subruns:
            raise InvariantViolation(f"run id already registered: {run.id}")
        self._runs[run.id] = run
        self.emit(
            run.id,
            event_type=TraceEventType.RUN_CREATED,
            title="创建分析 Run",
            content="Run 已持久化，等待主 Agent 执行。",
            payload={
                "task_id": run.task_id,
                "workspace_id": run.workspace_id,
                "attempt": run.attempt,
                "parent_run_id": run.parent_run_id,
                "status": run.status.value,
            },
        )
        return run

    def get_run(self, run_id: str) -> Run:
        hydrated = self._hydrate_run(run_id)
        if hydrated is not None:
            self._runs[run_id] = hydrated
            return hydrated
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise KeyError(f"unknown run: {run_id}") from exc

    def _hydrate_run(self, run_id: str) -> Run | None:
        if run_id in self._runs:
            return self._runs[run_id]
        page = self.store.read(run_id)
        hydrated: Run | None = None
        for event in page.events:
            event_type = event.data.get("type")
            payload = event.data.get("payload") or {}
            if event_type == TraceEventType.RUN_CREATED.value and hydrated is None:
                hydrated = Run(
                    id=run_id,
                    task_id=str(payload["task_id"]),
                    workspace_id=str(payload.get("workspace_id", "default")),
                    attempt=int(payload.get("attempt", 1)),
                    parent_run_id=payload.get("parent_run_id"),
                    status=RunStatus(payload.get("status", RunStatus.CREATED.value)),
                )
            elif event_type == TraceEventType.RUN_STATUS_CHANGED.value and hydrated is not None:
                target = RunStatus(payload.get("status", hydrated.status.value))
                if target is not hydrated.status:
                    hydrated.transition_to(target)
        return hydrated

    def start_run(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        if run.status is RunStatus.RUNNING:
            return run
        run.transition_to(RunStatus.RUNNING)
        self.emit(
            run_id,
            event_type=TraceEventType.RUN_STATUS_CHANGED,
            title="主 Agent 开始执行",
            content=run.status.value,
            payload={"status": run.status.value},
        )
        return run

    def complete_run(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        if run.status is RunStatus.COMPLETED:
            return run
        run.transition_to(RunStatus.COMPLETED)
        self.emit(
            run_id,
            event_type=TraceEventType.RUN_STATUS_CHANGED,
            title="主 Agent 完成执行",
            content=run.status.value,
            payload={"status": run.status.value},
        )
        return run

    def create_subrun(
        self,
        *,
        parent_run_id: str,
        subrun_id: str | None = None,
        role: AgentRole | str,
        prompt: str,
        capabilities: Sequence[str] | None = None,
    ) -> SubRun:
        if parent_run_id not in self._runs:
            if parent_run_id in self._subruns:
                raise InvariantViolation("sub-agent cannot create another sub-agent")
            raise KeyError(f"unknown parent run: {parent_run_id}")
        subrun = SubRun(
            id=subrun_id or new_id("subrun"),
            parent_run_id=parent_run_id,
            role=AgentRole(role),
            prompt=prompt,
            capabilities=list(capabilities or []),
        )
        if subrun.id in self._runs or subrun.id in self._subruns:
            raise InvariantViolation(f"subrun id already registered: {subrun.id}")
        self._subruns[subrun.id] = subrun
        self.emit(
            parent_run_id,
            event_type=TraceEventType.SUBAGENT_CREATED,
            title=f"创建子 Agent：{subrun.role.value}",
            content=subrun.prompt,
            agent_run_id=subrun.id,
            agent_type=subrun.role,
            payload={
                "subrun_id": subrun.id,
                "role": subrun.role.value,
                "capabilities": list(subrun.capabilities),
            },
        )
        return subrun

    def get_subrun(self, subrun_id: str) -> SubRun:
        hydrated = self._hydrate_subrun(subrun_id)
        if hydrated is not None:
            self._subruns[subrun_id] = hydrated
            return hydrated
        try:
            return self._subruns[subrun_id]
        except KeyError as exc:
            raise KeyError(f"unknown subrun: {subrun_id}") from exc

    def _hydrate_subrun(self, subrun_id: str) -> SubRun | None:
        if subrun_id in self._subruns:
            return self._subruns[subrun_id]
        events = self.store.find_by_agent_run(subrun_id)
        hydrated: SubRun | None = None
        for event in events:
            event_type = event.data.get("type")
            payload = event.data.get("payload") or {}
            if event_type == TraceEventType.SUBAGENT_CREATED.value and hydrated is None:
                hydrated = SubRun(
                    id=subrun_id,
                    parent_run_id=event.run_id,
                    role=AgentRole(payload["role"]),
                    prompt=str(event.data.get("content") or ""),
                    capabilities=list(payload.get("capabilities", [])),
                )
            elif hydrated is not None and event_type == TraceEventType.SUBAGENT_STARTED.value:
                hydrated.transition_to(SubRunStatus.RUNNING)
            elif hydrated is not None and event_type == TraceEventType.SUBAGENT_COMPLETED.value:
                hydrated.transition_to(SubRunStatus.COMPLETED)
        return hydrated

    def start_subrun(self, subrun_id: str) -> SubRun:
        subrun = self.get_subrun(subrun_id)
        subrun.transition_to(SubRunStatus.RUNNING)
        self.emit(
            subrun.parent_run_id,
            event_type=TraceEventType.SUBAGENT_STARTED,
            title=f"子 Agent 开始：{subrun.role.value}",
            content=subrun.prompt,
            agent_run_id=subrun.id,
            agent_type=subrun.role,
            payload={"subrun_id": subrun.id, "status": subrun.status.value},
        )
        return subrun

    def complete_subrun(self, subrun_id: str, *, evidence_ids: Sequence[str] = ()) -> SubRun:
        subrun = self.get_subrun(subrun_id)
        subrun.transition_to(SubRunStatus.COMPLETED)
        self.emit(
            subrun.parent_run_id,
            event_type=TraceEventType.SUBAGENT_COMPLETED,
            title=f"子 Agent 完成：{subrun.role.value}",
            content="子 Agent 已返回结构化证据。",
            agent_run_id=subrun.id,
            agent_type=subrun.role,
            evidence_ids=list(evidence_ids),
            payload={"subrun_id": subrun.id, "status": subrun.status.value},
        )
        return subrun

    def emit(
        self,
        run_id: str,
        *,
        event_type: TraceEventType | str,
        title: str | None = None,
        content: str | None = None,
        parent_event_id: str | None = None,
        agent_run_id: str | None = None,
        agent_type: AgentRole | str | None = None,
        tool: ToolCall | None = None,
        payload: Mapping[str, Any] | None = None,
        evidence_ids: Sequence[str] = (),
        artifact_ids: Sequence[str] = (),
        event_id: str | None = None,
        raw_payload_ref: str | None = None,
    ) -> TraceEvent:
        self.get_run(run_id)
        # A client may retry after losing the HTTP response.  Reuse the
        # persisted timestamp/sequence for an explicitly idempotent event ID;
        # the store still rejects a retry whose semantic fields differ.
        existing = self.store.get(event_id) if event_id else None
        existing_event = TraceEvent.model_validate(existing.to_dict()) if existing else None
        event_kwargs: dict[str, Any] = {
            "event_id": event_id or new_id("evt"),
            "run_id": run_id,
            "sequence": existing_event.sequence if existing_event else 1,
            "type": TraceEventType(event_type),
            "title": title,
            "content": content,
            "parent_event_id": parent_event_id,
            "agent_run_id": agent_run_id,
            "agent_type": AgentRole(agent_type) if agent_type is not None else None,
            "tool": tool,
            "payload": dict(payload or {}),
            "evidence_ids": list(evidence_ids),
            "artifact_ids": list(artifact_ids),
            "raw_payload_ref": raw_payload_ref,
        }
        if existing_event is not None:
            event_kwargs["timestamp"] = existing_event.timestamp
        event = TraceEvent(**event_kwargs)
        stored = self.store.append(event.model_dump(mode="json"))
        return TraceEvent.model_validate(stored.to_dict())

    def tool_started(
        self,
        run_id: str,
        *,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        call_id: str,
        agent_run_id: str | None = None,
        agent_type: AgentRole | str | None = None,
    ) -> TraceEvent:
        tool = ToolCall(name=tool_name, arguments=dict(arguments or {}))
        self._tools[(run_id, call_id)] = tool
        return self.emit(
            run_id,
            event_type=(
                TraceEventType.SUBAGENT_TOOL_CALL
                if agent_run_id is not None
                else TraceEventType.TOOL_STARTED
            ),
            title=f"调用工具：{tool_name}",
            tool=tool,
            agent_run_id=agent_run_id,
            agent_type=agent_type,
            payload={"call_id": call_id},
        )

    def tool_completed(
        self,
        run_id: str,
        *,
        call_id: str,
        result: Any = None,
        duration_ms: int | None = None,
        raw_payload_ref: str | None = None,
        agent_run_id: str | None = None,
        agent_type: AgentRole | str | None = None,
    ) -> TraceEvent:
        previous = self._tools.get((run_id, call_id))
        if previous is None:
            raise KeyError(f"unknown tool call: {call_id}")
        tool = ToolCall(
            name=previous.name,
            arguments=previous.arguments,
            result=result,
            duration_ms=duration_ms,
            raw_payload_ref=raw_payload_ref,
        )
        return self.emit(
            run_id,
            event_type=TraceEventType.TOOL_COMPLETED,
            title=f"工具完成：{tool.name}",
            tool=tool,
            agent_run_id=agent_run_id,
            agent_type=agent_type,
            payload={"call_id": call_id},
        )

    def register_evidence(self, evidence: Evidence) -> Evidence:
        if evidence.run_id not in self._runs:
            raise KeyError(f"unknown run: {evidence.run_id}")
        if evidence.id in self._evidence:
            return self._evidence[evidence.id]
        self._evidence[evidence.id] = evidence
        self.emit(
            evidence.run_id,
            event_type=TraceEventType.EVIDENCE_CREATED,
            title=f"证据 {evidence.id}",
            content=evidence.statement,
            evidence_ids=[evidence.id],
            payload={"source_ref": evidence.source_ref, "kind": evidence.kind.value},
        )
        return evidence

    def register_artifact(self, artifact: Artifact) -> Artifact:
        if artifact.run_id not in self._runs:
            raise KeyError(f"unknown run: {artifact.run_id}")
        if artifact.id in self._artifacts:
            return self._artifacts[artifact.id]
        self._artifacts[artifact.id] = artifact
        self.emit(
            artifact.run_id,
            event_type=TraceEventType.ARTIFACT_CREATED,
            title=f"产物：{artifact.name}",
            artifact_ids=[artifact.id],
            evidence_ids=artifact.evidence_ids,
            payload={"uri": artifact.uri, "content_hash": artifact.content_hash},
        )
        return artifact

    def replay(self, run_id: str, *, after: str | int | None = None, limit: int | None = None) -> TracePage:
        self.get_run(run_id)
        page = self.store.read(run_id, after=after, limit=limit)
        return TracePage(
            tuple(TraceEvent.model_validate(event.to_dict()) for event in page.events),
            page.next_cursor,
            page.has_more,
        )


__all__ = ["TraceService"]
