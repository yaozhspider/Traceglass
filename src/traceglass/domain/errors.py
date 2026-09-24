"""Errors raised by Traceglass domain contracts.

The domain layer intentionally exposes its own small error vocabulary so that
API and execution adapters can map lifecycle violations to stable responses
without depending on Pydantic's implementation details.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all expected Traceglass domain errors."""


class InvariantViolation(DomainError):
    """A domain invariant (for example, sub-agent depth) was violated."""


class InvalidStateTransition(DomainError):
    """An entity was asked to move to a lifecycle state it cannot reach."""

    def __init__(self, entity: str, current: object, target: object) -> None:
        self.entity = entity
        self.current = current
        self.target = target
        super().__init__(f"{entity} cannot transition from {current!s} to {target!s}")


class DuplicateEvent(DomainError):
    """An event id has already been persisted for a run."""


class UnknownEvidence(DomainError):
    """A finding or report refers to evidence that is not registered."""


class SecondLevelSubRun(DomainError):
    """An agent attempted to create a child of a child agent."""
