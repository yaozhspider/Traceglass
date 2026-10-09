"""Deterministic rubric checks; semantic correctness still requires review."""
from typing import Literal
from pydantic import BaseModel, Field, create_model


class Assessment(BaseModel):
    verdict: Literal['unknown', 'suspicious', 'remote_execution', 'benign_in_scope']
    evidence_refs: list[str] = Field(description='Exact supplied evidence IDs, or real tool IDs/addresses for binary runs.')
    dataflow: str = Field(description='Connected input-to-operation chain, or specific missing edges.')
    authorization: str
    privilege: str
    gaps: list[str]
    next_steps: list[str]
    explanation: str


def replay_schema(case: dict):
    """Keep citations to supplied evidence separate from model-created path IDs."""
    evidence_id = Literal[tuple(e['id'] for e in case['evidence'])]
    return create_model('ReplayAssessment', __base__=Assessment,
        evidence_refs=(list[evidence_id], Field(description='Only supplied original evidence IDs; no AnalysisReview path IDs.')))


def score(case: dict, assessment: dict) -> dict:
    result = Assessment.model_validate(assessment)
    rubric = case['rubric']
    failures = []
    if result.verdict not in rubric['allowed_verdicts']:
        failures.append('wrong_verdict')
    provided = {e['id'] for e in case['evidence']}
    used = set(result.evidence_refs)
    if not used or not used <= provided:
        failures.append('missing_or_invented_evidence_refs')
    if not set(rubric.get('required_evidence', [])) <= used:
        failures.append('key_evidence_not_cited')
    if rubric.get('requires_gaps') and not result.gaps:
        failures.append('missing_coverage_gaps')
    if rubric.get('requires_next_steps') and not result.next_steps:
        failures.append('missing_next_steps')
    for field in ('dataflow','authorization','privilege','explanation'):
        if not getattr(result,field).strip():
            failures.append('missing_'+field)
    return {'passed':not failures, 'failures':failures,
            'scoring_scope':'rubric_structure_and_labels; manually review semantics and evidence correspondence'}
