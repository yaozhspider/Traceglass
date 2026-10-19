"""Shared analysis labels; capabilities, intent and exploitability are distinct."""
from typing import Literal

Verdict = Literal['unknown', 'suspicious', 'remote_execution', 'benign_in_scope',
                  'vulnerable', 'hardened_in_scope']
VulnerabilityClass = Literal['unknown', 'none', 'stack_overflow', 'heap_overflow',
    'use_after_free', 'integer_overflow', 'format_string', 'command_injection',
    'sql_injection', 'path_traversal', 'unsafe_deserialization', 'other']
Exploitability = Literal['none', 'crash', 'control_flow', 'exploitable', 'unknown']

REVIEW_KEY = 'traceglass.analysis_reviews.v1'
REFERENCE_POLICY_KEY = 'traceglass.reference_policy.v1'


def reference_mode(state, *, strict: bool) -> str:
    """Choose a server-side session mode; text citations cannot opt out of it."""
    if not strict:
        return 'compatible'
    saved = state.middle_context.get(REFERENCE_POLICY_KEY)
    if saved in {'strict', 'compatible'}:
        return saved
    # Only records predating the reference contract identify genuine legacy
    # sessions. New submissions always carry reference_mode, even without IDs.
    coverage = state.middle_context.get(REVIEW_KEY, {})
    legacy = any('reference_mode' not in case for case in coverage.values())
    return 'compatible' if legacy else 'strict'
