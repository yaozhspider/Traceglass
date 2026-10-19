"""Model-authored coverage and structural review in native session state.

This checks declared coverage, never the truth of evidence or malware intent.
No parallel task store, scheduler, or classifier is introduced.
"""
from __future__ import annotations

import json
import asyncio
from typing import Annotated, Literal

from agentscope.message import TextBlock
from agentscope.state import AgentState
from agentscope.tool import ParamsBase, TaskUpdate, ToolBase, ToolChunk
from pydantic import ConfigDict, Field, StringConstraints, ValidationError
from traceglass.analysis_contract import Verdict, VulnerabilityClass, Exploitability, REVIEW_KEY, REFERENCE_POLICY_KEY
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Evidence(ParamsBase):
    model_config = ConfigDict(extra='forbid')
    reference: Text = Field(description='Original tool call/result ID plus artifact, function, address or line.')
    method: Literal['strings', 'imports', 'source', 'disassembly', 'decompilation', 'dynamic', 'dynamic-poc']
    evidence_id: str | None = Field(default=None, pattern=r'^tgev_[a-f0-9]{64}$',
        description='Runtime archive receipt ID, distinct from free-text reference and live engine handles.')
    locator: Text | None = Field(default=None, description='Address/offset/line present in the cited original output.')


class ExploitValidation(ParamsBase):
    model_config = ConfigDict(extra='forbid')
    evidence_reference: Text
    target_environment: Text = Field(description='Target version/architecture and mitigation configuration.')
    reproduction: Text = Field(description='Bounded authorized reproduction steps and saved input.')
    observed_effect: Text
    limitations: Text
    reproduced: bool = False


class BehaviorPath(ParamsBase):
    model_config = ConfigDict(extra='forbid')
    id: Text = Field(description='Stable path ID. Reuse it to update this external-input path.')
    source: Text = Field(description='External input entry, or explicit non-applicability with reason.')
    dispatch: Text = Field(description='Parsing/decoding and branches checked; write unknown if unresolved.')
    sink: Text = Field(description='High-impact operation or evidence-backed bounded safe handling.')
    authorization: Text = Field(description='Trigger, peer authentication and command authorization, or unknown.')
    privilege: Text = Field(description='Execution identity and sandbox boundary, or unknown.')
    status: Literal['open', 'blocked', 'resolved']
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)
    limitations: str = Field(default='', max_length=2000)
    remote_execution: bool = Field(default=False, description='True when evidence connects external input to code execution, INCLUDING authorized administration. This records capability, not malicious intent or runtime deployment verification.')
    controllability: Text = 'unknown'
    reachability_constraints: Text = 'unknown'
    vuln_class: VulnerabilityClass = 'unknown'
    exploitability: Exploitability = 'unknown'
    exploit_validation: ExploitValidation | None = None


class ReviewParams(ParamsBase):
    model_config = ConfigDict(extra='forbid')
    sample_sha256: str = Field(pattern=r'^[a-fA-F0-9]{64}$')
    paths: list[BehaviorPath] = Field(default_factory=list, max_length=64,
        description='Incremental updates. Omitted paths remain, including unresolved gaps.')
    scope: str | None = Field(default=None, max_length=2000,
        description='Explicit coverage boundaries, never an assertion that all unknown code is safe.')
    verdict: Verdict = 'unknown'


def review_findings(paths: list[dict], verdict: str, scope: str) -> list[str]:
    issues = []
    detailed = {'source', 'disassembly', 'decompilation', 'dynamic', 'dynamic-poc'}
    unknowns = {'unknown', '未知', '未验证', '待验证', ''}
    if verdict in {'benign_in_scope', 'remote_execution', 'vulnerable', 'hardened_in_scope'}:
        if not paths:
            issues.append('No external-input behavior paths have been recorded.')
        if not scope.strip():
            issues.append('An explicit analysis scope is required.')
    if verdict == 'benign_in_scope':
        for path in paths:
            if path['status'] != 'resolved':
                issues.append(f"{path['id']}: unresolved or blocked behavior path.")
            if any(path[field].strip().casefold() in {'unknown', '未知', '未验证', '待验证'}
                   for field in ('source', 'dispatch', 'sink')):
                issues.append(f"{path['id']}: an essential dataflow edge is explicitly unknown.")
            if not any(e['method'] in detailed for e in path['evidence']):
                issues.append(f"{path['id']}: strings/imports alone do not establish behavior.")
            if path['remote_execution']:
                issues.append(f"{path['id']}: remote code execution requires separate authorization/intent review; no benign approval.")
    if verdict == 'remote_execution' and not any(
        p['remote_execution'] and p['status'] == 'resolved' and
        any(e['method'] in detailed for e in p['evidence']) for p in paths
    ):
        issues.append('No resolved input-to-code execution chain with instruction/source/runtime evidence.')
    if verdict in {'vulnerable', 'hardened_in_scope'}:
        for path in paths:
            claimed = path.get('exploitability', 'unknown')
            if claimed in {'crash', 'control_flow', 'exploitable'}:
                validation = path.get('exploit_validation') or {}
                allowed_methods = detailed if claimed == 'control_flow' else {'dynamic', 'dynamic-poc'}
                if not validation or not any(
                    e['reference'] == validation.get('evidence_reference') and e['method'] in allowed_methods
                    for e in path['evidence']
                ) or (claimed in {'crash', 'exploitable'} and not validation.get('reproduced')):
                    issues.append(f"{path['id']}: exploitability claim lacks linked validation of the claimed effect.")
        if verdict == 'vulnerable' and not any(
            p['status'] == 'resolved' and p.get('vuln_class', 'unknown') not in {'none', 'unknown'} and
            p.get('exploitability', 'unknown') != 'none' and
            all(p.get(f, 'unknown').strip().casefold() not in unknowns for f in
                ('source', 'dispatch', 'sink', 'controllability', 'reachability_constraints')) and
            any(e['method'] in detailed for e in p['evidence']) for p in paths
        ):
            issues.append('No resolved reachable vulnerability with controllability and detailed evidence.')
        if verdict == 'hardened_in_scope':
            for p in paths:
                if (p['status'] != 'resolved' or p.get('vuln_class', 'unknown') != 'none' or
                    p.get('exploitability', 'unknown') != 'none' or
                    any(p.get(f, 'unknown').strip().casefold() in unknowns for f in
                        ('source', 'dispatch', 'sink', 'controllability', 'reachability_constraints')) or
                    not any(e['method'] in detailed for e in p['evidence'])):
                    issues.append(f"{p['id']}: scoped negative finding requires resolved constraints and no unknown vulnerability dimensions.")
    return issues


class AnalysisReview(ToolBase):
    name = 'AnalysisReview'
    description = '''Record external-input behavior coverage independently of Todo and check a proposed scoped verdict.
Call early with open/blocked paths, update after evidence, and call before the final analysis report.
Omitted paths remain. Data persists per sample in YOUR native session (not automatically shared
between members); workers return paths through TeamSay and the leader records reviewed findings.
review_passed checks STRUCTURE ONLY: it never proves evidence true, authorizes execution, or certifies safety.
Unknown/suspicious findings can be reported with gaps. Do not invent evidence to satisfy this check.'''
    input_schema = ReviewParams.model_json_schema()
    is_state_injected = True
    is_read_only = False
    is_concurrency_safe = False

    def __init__(self, *, store=None, user_id=None, workspace_id=None, strict_references=False):
        super().__init__()
        self.store = store
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.strict_references = strict_references

    async def check_permissions(self, tool_input, context):
        return await TaskUpdate().check_permissions(tool_input, context)

    async def call(self, _agent_state: AgentState, sample_sha256: str, paths=None,
                   scope=None, verdict='unknown') -> ToolChunk:
        try:
            params = ReviewParams.model_validate(dict(sample_sha256=sample_sha256, paths=paths or [],
                                                      scope=scope, verdict=verdict))
            if len({p.id for p in params.paths}) != len(params.paths):
                raise ValueError('Duplicate behavior path IDs in batch.')
            cases = dict(_agent_state.middle_context.get(REVIEW_KEY, {}))
            sha = params.sample_sha256.lower()
            old = cases.get(sha, {})
            indexed = {p['id']: BehaviorPath.model_validate(p).model_dump() for p in old.get('paths', [])}
            indexed.update({p.id: p.model_dump() for p in params.paths})
            if len(indexed) > 64 or (sha not in cases and len(cases) >= 32):
                raise ValueError('Coverage record limit reached; use a separate analysis session.')
            scope = params.scope if params.scope is not None else old.get('scope', '')
            issues = review_findings(list(indexed.values()), params.verdict, scope)
            reference_checks = []
            for path in indexed.values():
                for evidence in path['evidence']:
                    eid = evidence.get('evidence_id')
                    if self.store and eid:
                        check = await asyncio.to_thread(self.store.validate_reference, self.user_id,
                            self.workspace_id, eid, sha, evidence.get('locator'))
                    else:
                        check = {'id': eid, 'status': 'missing_archive_id' if not eid else 'archive_unavailable'}
                    reference_checks.append({'path_id': path['id'], **check})
            reference_issues = [c for c in reference_checks if c['status'] != 'verified']
            references_passed = bool(reference_checks) and not reference_issues if self.store else None
            reference_blocked = self.strict_references and not references_passed
            result = dict(sample_sha256=sha, scope=scope, paths=list(indexed.values()),
                proposed_verdict=params.verdict, review_passed=not issues, issues=issues,
                structural_issues=issues, reference_issues=reference_issues,
                references_passed=references_passed, reference_checks=reference_checks,
                reference_mode='strict' if self.strict_references else 'compatible',
                recommended_verdict='unknown' if issues or reference_blocked else params.verdict,
                verification_scope='structure_only_not_semantic_proof', revision=old.get('revision', 0)+1)
            cases[sha] = result
            _agent_state.middle_context.setdefault(REFERENCE_POLICY_KEY, result['reference_mode'])
            _agent_state.middle_context[REVIEW_KEY] = cases
            return ToolChunk(content=[TextBlock(text=json.dumps(result, ensure_ascii=False))])
        except (ValidationError, ValueError) as exc:
            return ToolChunk(state='error', content=[TextBlock(text=json.dumps({'error':str(exc)}, ensure_ascii=False))])
