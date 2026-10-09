"""Business evidence contracts linked to native AgentScope session IDs.

Session, task, agent and execution state are owned exclusively by AgentScope.
These records describe analysis outputs; they do not schedule or replay work.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, ClassVar, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import InvariantViolation


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


class FindingDates(DomainModel):
    """Creation/update metadata for findings, not an agent lifecycle."""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def _validate_order(self) -> Self:
        if self.updated_at < self.created_at:
            raise InvariantViolation("updated_at cannot be earlier than created_at")
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
    session_id: str = Field(min_length=1)
    kind: EvidenceKind
    statement: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    locator: str | None = None
    quote: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id", "session_id", "statement", "source_ref")(_non_empty)
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


class Finding(FindingDates):
    """A claim whose support and contradictions are explicit."""

    id: str = Field(default_factory=lambda: new_id("finding"), min_length=1)
    session_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    status: FindingStatus = FindingStatus.OPEN
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    next_action: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id", "session_id", "claim")(_non_empty)
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
    session_id: str = Field(min_length=1)
    task_id: str | None = None
    name: str = Field(min_length=1)
    kind: ArtifactKind = ArtifactKind.OTHER
    content_hash: str = Field(min_length=64)
    uri: str = Field(min_length=1)
    mime_type: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_id = field_validator("id", "session_id", "name", "uri")(_non_empty)
    _validate_step = field_validator("task_id")(
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


__all__ = ['Artifact', 'ArtifactKind', 'DomainModel', 'Evidence', 'EvidenceKind', 'EvidenceRef', 'EvidenceRelationship', 'Finding', 'FindingStatus', 'new_id', 'utc_now']
