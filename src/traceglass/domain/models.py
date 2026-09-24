"""Dependency-light domain contracts for Traceglass.

The models in this module are the boundary between planning/execution and the
persistence/API layers. They contain validation and lifecycle invariants, while
I/O (model providers, filesystems, and databases) remains outside the domain.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, ClassVar, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import InvalidStateTransition, InvariantViolation


# ---------------------------------------------------------------------------
# Shared helpers


def utc_now() -> datetime:
    """Return an aware UTC timestamp for durable event ordering."""

    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Create a human-readable opaque identifier for a domain entity."""

    return f"{prefix}_{uuid4().hex}"


def _non_empty(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("value must not be blank")
    return value.strip()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _unique_ids(values: list[str], field_name: str) -> list[str]:
    cleaned = [_non_empty(value) for value in values]
    if len(cleaned) != len(set(cleaned)):
        raise InvariantViolation(f"{field_name} must contain unique ids")
    return cleaned


class DomainModel(BaseModel):
    """Base model with strict fields and JSON-friendly serialization."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
        str_strip_whitespace=False,
    )

    _normalize_timestamps: ClassVar[bool] = True


# ---------------------------------------------------------------------------
# Enums


class TaskStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(str, Enum):
    CREATED = "created"
    PENDING = "pending"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class SubRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRole(str, Enum):
    ANALYST = "analyst"
    PREPARE = "prepare"
    STATIC_REVERSE = "static-reverse"
    DYNAMIC_EVIDENCE = "dynamic-evidence"
    VERIFIER = "verifier"
    EXPERIMENT = "experiment"
    REPORTER = "reporter"


class TraceEventType(str, Enum):
    ASSISTANT_MESSAGE = "assistant.message"
    THINKING_UPDATE = "thinking.update"
    TODO_CREATED = "todo.created"
    TODO_UPDATED = "todo.updated"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    SUBAGENT_CREATED = "subagent.created"
    SUBAGENT_STARTED = "subagent.started"
    SUBAGENT_MESSAGE = "subagent.message"
    SUBAGENT_TOOL_CALL = "subagent.tool_call"
    SUBAGENT_COMPLETED = "subagent.completed"
    ARTIFACT_CREATED = "artifact.created"
    EVIDENCE_CREATED = "evidence.created"
    CHECKPOINT_CREATED = "checkpoint.created"
    USER_INPUT_REQUIRED = "user_input_required"
    RUN_CREATED = "run.created"
    RUN_STATUS_CHANGED = "run.status_changed"


class EvidenceKind(str, Enum):
    STATIC = "static"
    DYNAMIC = "dynamic"
    OBSERVATION = "observation"
    ARTIFACT = "artifact"
    EXTERNAL_REFERENCE = "external_reference"
    HYPOTHESIS = "hypothesis"


class EvidenceRelationship(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class FindingStatus(str, Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"
    RESOLVED = "resolved"


class ArtifactKind(str, Enum):
    REPORT = "report"
    FILE = "file"
    JSON = "json"
    LOG = "log"
    SCREENSHOT = "screenshot"
    DIFF = "diff"
    PATCH = "patch"
    BINARY = "binary"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Lifecycle mixin and records


class LifecycleModel(DomainModel):
    """Common timestamps and transition helper for mutable lifecycle records."""

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _validate_created_updated = model_validator(mode="after")(
        lambda self: self._validate_time_order()
    )

    def _validate_time_order(self) -> Self:
        if self.updated_at < self.created_at:
            raise InvariantViolation("updated_at cannot be earlier than created_at")
        return self

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return _aware(value)

    def _touch(self) -> None:
        self.updated_at = utc_now()

    def __setattr__(self, name: str, value: Any) -> None:
        # Pydantic validates an assigned enum value, but it cannot know the
        # lifecycle graph. Enforce the graph for direct assignments as well as
        # through ``transition_to`` so callers cannot skip audit transitions.
        if name == "status" and "status" in self.__dict__:
            current = self.__dict__["status"]
            status_type = type(current)
            try:
                target = status_type(value)
            except (TypeError, ValueError):
                # Let Pydantic report an invalid enum value in its normal
                # structured ValidationError shape.
                super().__setattr__(name, value)
                return
            transitions = getattr(type(self), "_ALLOWED_TRANSITIONS", {})
            if transitions and target != current and target not in transitions.get(current, set()):
                raise InvalidStateTransition(
                    type(self).__name__.lower(),
                    getattr(current, "value", current),
                    getattr(target, "value", target),
                )
        super().__setattr__(name, value)


class Task(LifecycleModel):
    """A durable user objective bound to one workspace."""

    id: str = Field(default_factory=lambda: new_id("task"), min_length=1)
    objective: str = Field(min_length=1)
    workspace_id: str = Field(default="default", min_length=1)
    workspace_path: str | None = None
    status: TaskStatus = TaskStatus.DRAFT
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id", "workspace_id", "objective")(_non_empty)


class Run(LifecycleModel):
    """One attempt, continuation, retry, or recovery of a task."""

    id: str = Field(default_factory=lambda: new_id("run"), min_length=1)
    task_id: str = Field(min_length=1)
    status: RunStatus = RunStatus.CREATED
    attempt: int = Field(default=1, ge=1)
    parent_run_id: str | None = None
    workspace_id: str = Field(default="default", min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id", "task_id", "workspace_id")(_non_empty)
    _validate_parent = field_validator("parent_run_id")(
        lambda value: _non_empty(value) if value is not None else value
    )

    _ALLOWED_TRANSITIONS: ClassVar[dict[RunStatus, set[RunStatus]]] = {
        RunStatus.CREATED: {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.CANCELLED},
        RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.CANCELLED},
        RunStatus.RUNNING: {
            RunStatus.WAITING_INPUT,
            RunStatus.PAUSED,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        },
        RunStatus.WAITING_INPUT: {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.CANCELLED},
        RunStatus.PAUSED: {RunStatus.RUNNING, RunStatus.CANCELLED},
        RunStatus.COMPLETED: set(),
        RunStatus.FAILED: set(),
        RunStatus.CANCELLED: set(),
    }

    def transition_to(self, target: RunStatus) -> Self:
        target = RunStatus(target)
        if target is self.status:
            return self
        if target not in self._ALLOWED_TRANSITIONS[self.status]:
            raise InvalidStateTransition("run", self.status.value, target.value)
        self.status = target
        self._touch()
        return self


class Step(LifecycleModel):
    """A bounded executable unit in a run."""

    id: str = Field(default_factory=lambda: new_id("step"), min_length=1)
    run_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    position: int = Field(default=0, ge=0)
    status: StepStatus = StepStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    attempt: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id", "run_id", "title")(_non_empty)
    _validate_dependencies = field_validator("depends_on", "completion_criteria")(_unique_ids)

    @model_validator(mode="after")
    def _validate_dependency_graph(self) -> Self:
        if self.id in self.depends_on:
            raise InvariantViolation("step cannot depend on itself")
        return self

    _ALLOWED_TRANSITIONS: ClassVar[dict[StepStatus, set[StepStatus]]] = {
        StepStatus.PENDING: {StepStatus.RUNNING, StepStatus.SKIPPED, StepStatus.CANCELLED},
        StepStatus.RUNNING: {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED},
        StepStatus.SUCCEEDED: set(),
        StepStatus.FAILED: {StepStatus.PENDING, StepStatus.RUNNING, StepStatus.CANCELLED},
        StepStatus.SKIPPED: set(),
        StepStatus.CANCELLED: set(),
    }

    def transition_to(self, target: StepStatus) -> Self:
        target = StepStatus(target)
        if target is self.status:
            return self
        if target not in self._ALLOWED_TRANSITIONS[self.status]:
            raise InvalidStateTransition("step", self.status.value, target.value)
        self.status = target
        self._touch()
        return self


class SubRun(LifecycleModel):
    """An explicit one-level child-agent execution.

    A child agent returns evidence and artifacts to its parent run. It cannot
    create another child, mutate task/run lifecycle, or write the final report.
    """

    id: str = Field(default_factory=lambda: new_id("subrun"), min_length=1)
    parent_run_id: str = Field(min_length=1)
    role: AgentRole
    prompt: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    status: SubRunStatus = SubRunStatus.CREATED
    depth: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id", "parent_run_id", "prompt")(_non_empty)
    _validate_capabilities = field_validator("capabilities")(_unique_ids)

    @model_validator(mode="after")
    def _validate_one_level(self) -> Self:
        if self.depth != 1:
            raise InvariantViolation("sub-agent depth must be exactly one")
        if self.id == self.parent_run_id:
            raise InvariantViolation("sub-agent cannot be its own parent")
        return self

    _ALLOWED_TRANSITIONS: ClassVar[dict[SubRunStatus, set[SubRunStatus]]] = {
        SubRunStatus.CREATED: {SubRunStatus.RUNNING, SubRunStatus.CANCELLED},
        SubRunStatus.RUNNING: {
            SubRunStatus.WAITING_INPUT,
            SubRunStatus.COMPLETED,
            SubRunStatus.FAILED,
            SubRunStatus.CANCELLED,
        },
        SubRunStatus.WAITING_INPUT: {
            SubRunStatus.RUNNING,
            SubRunStatus.CANCELLED,
        },
        SubRunStatus.COMPLETED: set(),
        SubRunStatus.FAILED: set(),
        SubRunStatus.CANCELLED: set(),
    }

    def transition_to(self, target: SubRunStatus) -> Self:
        target = SubRunStatus(target)
        if target is self.status:
            return self
        if target not in self._ALLOWED_TRANSITIONS[self.status]:
            raise InvalidStateTransition("subrun", self.status.value, target.value)
        self.status = target
        self._touch()
        return self


# ---------------------------------------------------------------------------
# Trace and evidence records


class ToolCall(DomainModel):
    """Inspectable tool invocation attached to a trace event."""

    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    duration_ms: int | None = Field(default=None, ge=0)
    raw_payload_ref: str | None = None

    _validate_name = field_validator("name")(_non_empty)
    _validate_payload_ref = field_validator("raw_payload_ref")(
        lambda value: _non_empty(value) if value is not None else value
    )


class TraceEvent(DomainModel):
    """Ordered, replayable event in a parent/child execution tree."""

    event_id: str = Field(default_factory=lambda: new_id("evt"), min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int
    type: TraceEventType
    timestamp: datetime = Field(default_factory=utc_now)
    parent_event_id: str | None = None
    agent_run_id: str | None = None
    agent_type: AgentRole | None = None
    title: str | None = None
    content: str | None = None
    tool: ToolCall | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    raw_payload_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("event_id", "run_id")(_non_empty)
    _validate_refs = field_validator("parent_event_id", "agent_run_id", "raw_payload_ref")(
        lambda value: _non_empty(value) if value is not None else value
    )
    _validate_title_content = field_validator("title", "content")(
        lambda value: value if value is None or value.strip() else None
    )
    _validate_timestamp = field_validator("timestamp")(_aware)
    _validate_evidence_ids = field_validator("evidence_ids", "artifact_ids")(_unique_ids)

    @model_validator(mode="after")
    def _validate_tree_links(self) -> Self:
        if self.sequence < 1:
            raise InvariantViolation("trace event sequence must be >= 1")
        if self.parent_event_id == self.event_id:
            raise InvariantViolation("trace event cannot be its own parent")
        if self.type in {
            TraceEventType.TOOL_STARTED,
            TraceEventType.TOOL_COMPLETED,
            TraceEventType.TOOL_FAILED,
            TraceEventType.SUBAGENT_TOOL_CALL,
        } and self.tool is None:
            raise InvariantViolation(f"{self.type.value} requires a tool payload")
        return self


class Evidence(DomainModel):
    """Immutable source-backed fact or observation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=False,
    )

    id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    kind: EvidenceKind
    statement: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    locator: str | None = None
    quote: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id", "run_id", "statement", "source_ref")(_non_empty)
    _validate_optional_text = field_validator("locator", "quote")(
        lambda value: value if value is None or value.strip() else None
    )
    _validate_timestamp = field_validator("created_at")(_aware)


class EvidenceRef(DomainModel):
    """A typed link from a finding/artifact to immutable evidence."""

    evidence_id: str = Field(min_length=1)
    relationship: EvidenceRelationship = EvidenceRelationship.SUPPORTS
    note: str | None = None

    _validate_id = field_validator("evidence_id")(_non_empty)


class Finding(LifecycleModel):
    """A claim whose support and contradictions are explicit."""

    id: str = Field(default_factory=lambda: new_id("finding"), min_length=1)
    run_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    status: FindingStatus = FindingStatus.OPEN
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    next_action: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id", "run_id", "claim")(_non_empty)
    _validate_evidence_ids = field_validator("evidence_ids", "contradiction_ids")(_unique_ids)
    _validate_unknowns = field_validator("unknowns")(_unique_ids)

    @model_validator(mode="after")
    def _validate_references(self) -> Self:
        overlap = set(self.evidence_ids) & set(self.contradiction_ids)
        if overlap:
            raise InvariantViolation("evidence and contradiction references cannot overlap")
        return self


_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")


class Artifact(DomainModel):
    """Immutable content-addressed output linked to a run and evidence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=False,
    )

    id: str = Field(default_factory=lambda: new_id("artifact"), min_length=1)
    run_id: str = Field(min_length=1)
    step_id: str | None = None
    name: str = Field(min_length=1)
    kind: ArtifactKind = ArtifactKind.OTHER
    content_hash: str = Field(min_length=64)
    uri: str = Field(min_length=1)
    mime_type: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id", "run_id", "name", "uri")(_non_empty)
    _validate_step = field_validator("step_id")(
        lambda value: _non_empty(value) if value is not None else value
    )
    _validate_hash = field_validator("content_hash")(
        lambda value: value if _SHA256_RE.fullmatch(value) else (_raise_hash(value))
    )
    _validate_mime = field_validator("mime_type")(
        lambda value: _non_empty(value) if value is not None else value
    )
    _validate_evidence_ids = field_validator("evidence_ids")(_unique_ids)
    _validate_timestamp = field_validator("created_at")(_aware)


def _raise_hash(value: str) -> str:
    raise ValueError("content_hash must be a 64-character SHA-256 digest")


__all__ = [
    "AgentRole",
    "Artifact",
    "ArtifactKind",
    "DomainModel",
    "Evidence",
    "EvidenceKind",
    "EvidenceRef",
    "EvidenceRelationship",
    "Finding",
    "FindingStatus",
    "LifecycleModel",
    "Run",
    "RunStatus",
    "Step",
    "StepStatus",
    "SubRun",
    "SubRunStatus",
    "Task",
    "TaskStatus",
    "ToolCall",
    "TraceEvent",
    "TraceEventType",
    "new_id",
    "utc_now",
]
