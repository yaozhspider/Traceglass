"""One-level sub-agent orchestration and structured evidence handoff."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from traceglass.application.trace_service import TraceService
from traceglass.domain.models import AgentRole, SubRunStatus, TraceEventType


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


SubagentHandler = Callable[[str, list[str]], EvidenceBundle]


class SubagentManager:
    def __init__(self, service: TraceService):
        self.service = service
        self._handlers: dict[AgentRole, SubagentHandler] = {}

    def register_handler(self, role: AgentRole, handler: SubagentHandler) -> None:
        self._handlers[AgentRole(role)] = handler

    def run(
        self,
        *,
        parent_run_id: str,
        subrun_id: str,
        role: AgentRole,
        prompt: str,
        capabilities: Sequence[str] = (),
    ) -> EvidenceBundle:
        subrun = self.service.create_subrun(
            parent_run_id=parent_run_id,
            subrun_id=subrun_id,
            role=role,
            prompt=prompt,
            capabilities=list(capabilities),
        )
        handler = self._handlers.get(AgentRole(role))
        if handler is None:
            raise LookupError(f"no handler registered for {role}")
        self.service.start_subrun(subrun.id)
        try:
            bundle = handler(prompt, list(capabilities))
            if not isinstance(bundle, EvidenceBundle):
                bundle = EvidenceBundle.model_validate(bundle)
        except Exception:
            subrun.transition_to(SubRunStatus.FAILED)
            raise
        evidence_ids = [str(item["id"]) for item in bundle.evidence if item.get("id")]
        self.service.emit(
            parent_run_id,
            event_type=TraceEventType.SUBAGENT_MESSAGE,
            title=f"子 Agent 返回：{subrun.role.value}",
            content=bundle.summary,
            agent_run_id=subrun.id,
            agent_type=subrun.role,
            evidence_ids=evidence_ids,
            artifact_ids=[str(item["id"]) for item in bundle.artifacts if item.get("id")],
            payload={"evidence_bundle": bundle.model_dump(mode="json")},
        )
        self.service.complete_subrun(subrun.id, evidence_ids=evidence_ids)
        return bundle


__all__ = ["EvidenceBundle", "SubagentManager"]
