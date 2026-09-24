"""Stable HTTP request/response schemas for the trace API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from traceglass.domain.models import AgentRole, ToolCall, TraceEventType


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    run_id: str | None = None
    workspace_id: str = Field(default="default", min_length=1)
    parent_run_id: str | None = None
    attempt: int = Field(default=1, ge=1)


class AppendEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str | None = None
    type: TraceEventType
    title: str | None = None
    content: str | None = None
    parent_event_id: str | None = None
    agent_run_id: str | None = None
    agent_type: AgentRole | None = None
    tool: ToolCall | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    raw_payload_ref: str | None = None


class CreateSubRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subrun_id: str | None = None
    role: AgentRole
    prompt: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)


class TracePageResponse(BaseModel):
    events: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool

