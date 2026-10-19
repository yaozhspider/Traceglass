"""Deterministic rubric checks; semantic correctness still requires review."""
from typing import Literal
import re
from pydantic import BaseModel, Field, create_model
from traceglass.analysis_contract import Verdict, VulnerabilityClass, Exploitability


class Fact(BaseModel):
    category: Literal['entry', 'sink', 'constraint', 'constant', 'capability']
    statement: str
    evidence_refs: list[str]


class Assessment(BaseModel):
    verdict: Verdict
    evidence_refs: list[str] = Field(description='Exact supplied evidence IDs, or real tool IDs/addresses for binary runs.')
    dataflow: str = Field(description='Connected input-to-operation chain, or specific missing edges.')
    authorization: str
    privilege: str
    gaps: list[str]
    next_steps: list[str]
    explanation: str
    vuln_classes: list[VulnerabilityClass] = Field(default_factory=list)
    exploitability: Exploitability = 'unknown'
    facts: list[Fact] = Field(default_factory=list,
        description='Specific observed entry/sink/constraint/constant/capability facts with original evidence IDs.')


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
    if 'allowed_exploitability' in rubric and result.exploitability not in rubric['allowed_exploitability']:
        failures.append('unsupported_exploitability')
    if not set(rubric.get('required_vuln_classes', [])) <= set(result.vuln_classes):
        failures.append('missing_vulnerability_class')
    for field in ('dataflow','authorization','privilege','explanation'):
        if not getattr(result,field).strip():
            failures.append('missing_'+field)
    return {'passed':not failures, 'failures':failures,
            'scoring_scope':'rubric_structure_and_labels; manually review semantics and evidence correspondence'}


def score_binary(case, assessment, verified_evidence_ids):
    """Independent labels/patterns plus original citations; semantics need human review."""
    output = Assessment.model_validate(assessment)
    rubric = case['rubric']
    all_refs = set(output.evidence_refs) | {ref for f in output.facts for ref in f.evidence_refs}
    unsupported = all_refs - set(verified_evidence_ids)
    failures = []
    if output.verdict not in rubric['allowed_verdicts']:
        failures.append('wrong_verdict')
    if 'allowed_exploitability' in rubric and output.exploitability not in rubric['allowed_exploitability']:
        failures.append('unsupported_exploitability')
    if not set(rubric.get('required_vuln_classes', [])) <= set(output.vuln_classes):
        failures.append('missing_vulnerability_class')
    if rubric.get('requires_gaps') and not output.gaps:
        failures.append('missing_coverage_gaps')
    if rubric.get('requires_next_steps') and not output.next_steps:
        failures.append('missing_next_steps')
    for field in ('dataflow', 'authorization', 'privilege', 'explanation'):
        if not getattr(output, field).strip():
            failures.append('missing_' + field)
    if not all_refs or unsupported:
        failures.append('missing_or_unsupported_citations')
    matched = []
    for expected in rubric.get('required_facts', []):
        matched.append(any(f.category == expected['category'] and f.evidence_refs and
            set(f.evidence_refs) <= set(verified_evidence_ids) and
            re.search(expected['pattern'], f.statement, flags=re.IGNORECASE) for f in output.facts))
    if not all(matched):
        failures.append('missing_key_facts')
    return dict(passed=not failures, failures=failures,
        key_fact_recall=sum(matched)/len(matched) if matched else None,
        unsupported_citation_count=len(unsupported),
        unsupported_citation_rate=len(unsupported)/len(all_refs) if all_refs else None,
        unsupported_assertion_rate=None,
        scoring_scope='independent_labels_fact_patterns_and_verified_origins; semantic correspondence requires review')
