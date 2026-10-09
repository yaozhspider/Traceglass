"""Validation errors for analysis evidence contracts."""

class DomainError(Exception):
    """Base error for invalid analysis records."""

class InvariantViolation(DomainError):
    """Evidence metadata violates a domain invariant."""
