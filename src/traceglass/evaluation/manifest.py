"""Independent evaluator-only manifests, outside the agent workspace."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .scoring import score_binary


def load_manifest(path: Path):
    path = path.resolve(strict=True)
    case = json.loads(path.read_text())
    if case.get('analysis_goal') not in {'behavior', 'vulnerability'}:
        raise ValueError('Manifest requires an analysis_goal')
    if not case.get('id') or not case.get('task') or not case.get('rubric', {}).get('allowed_verdicts'):
        raise ValueError('Manifest requires id, task and independent rubric')
    binary = (path.parent / case['binary']).resolve(strict=True)
    actual = hashlib.sha256(binary.read_bytes()).hexdigest()
    if actual != case.get('sample_sha256'):
        raise ValueError('Manifest binary hash mismatch')
    return case, binary


def static_request(case, sample_path):
    # Explicit allowlist: never serialize rubric, source, build or evaluator paths.
    return (f"Independent static analysis of {sample_path}; SHA256={case['sample_sha256']}. "
        f"Assessment goal: {case.get('analysis_goal', 'behavior')}. {case.get('task', '')} "
        'Read behavior-chain-review and, for vulnerability goals, vuln-discovery and vulnerability-hunting. '
        'Report observed facts with category, statement and original archive evidence IDs. '
        'Connect source, propagation, sink, constraints and controllability; separate vulnerability '
        'from runtime exploitability. Call AnalysisReview before reporting; inspect original evidence '
        'using QueryAnalysisEvidence and ReadAnalysisEvidence. Preserve gaps. '
        'Static-only authorization: never execute the sample or invoke shell/debugger/emulator tools.')


def grade_static(case, assessment, store, user_id, workspace_id, coverage, *, require_review=True):
    refs = set(assessment.get('evidence_refs', [])) | {
        ref for fact in assessment.get('facts', []) for ref in fact.get('evidence_refs', [])}
    checks = [store.validate_reference(user_id, workspace_id, ref, case['sample_sha256']) for ref in sorted(refs)]
    verified = {item['id'] for item in checks if item['status'] == 'verified'}
    result = score_binary(case, assessment, verified)
    result['reference_checks'] = checks
    review = coverage.get(case['sample_sha256'], {})
    if require_review and (review.get('reference_mode') != 'strict' or not review.get('review_passed')
                           or not review.get('references_passed')):
        result['failures'].append('strict_review_not_passed')
        result['passed'] = False
    if require_review and (review.get('proposed_verdict') != assessment.get('verdict')
                           or review.get('recommended_verdict') != assessment.get('verdict')):
        result['failures'].append('assessment_review_verdict_mismatch')
        result['passed'] = False
    return result
