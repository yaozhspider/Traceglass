"""Model-authored coverage and structural review in native session state.

This checks declared coverage, never the truth of evidence or malware intent.
No parallel task store, scheduler, or classifier is introduced.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal

from agentscope.message import TextBlock
from agentscope.state import AgentState
from agentscope.tool import ParamsBase, TaskUpdate, ToolBase, ToolChunk
from pydantic import ConfigDict, Field, StringConstraints, ValidationError

REVIEW_KEY = 'traceglass.analysis_reviews.v1'
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Verdict = Literal['unknown', 'suspicious', 'remote_execution', 'benign_in_scope']


class Evidence(ParamsBase):
    model_config = ConfigDict(extra='forbid')
    reference: Text = Field(description='Original tool call/result ID plus artifact, function, address or line.')
    method: Literal['strings', 'imports', 'source', 'disassembly', 'decompilation', 'dynamic']


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
    detailed = {'source', 'disassembly', 'decompilation', 'dynamic'}
    if verdict in {'benign_in_scope', 'remote_execution'}:
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
            indexed = {p['id']: p for p in old.get('paths', [])}
            indexed.update({p.id: p.model_dump() for p in params.paths})
            if len(indexed) > 64 or (sha not in cases and len(cases) >= 32):
                raise ValueError('Coverage record limit reached; use a separate analysis session.')
            scope = params.scope if params.scope is not None else old.get('scope', '')
            issues = review_findings(list(indexed.values()), params.verdict, scope)
            result = dict(sample_sha256=sha, scope=scope, paths=list(indexed.values()),
                proposed_verdict=params.verdict, review_passed=not issues, issues=issues,
                recommended_verdict='unknown' if issues else params.verdict,
                verification_scope='structure_only_not_semantic_proof', revision=old.get('revision', 0)+1)
            cases[sha] = result
            _agent_state.middle_context[REVIEW_KEY] = cases
            return ToolChunk(content=[TextBlock(text=json.dumps(result, ensure_ascii=False))])
        except (ValidationError, ValueError) as exc:
            return ToolChunk(state='error', content=[TextBlock(text=json.dumps({'error':str(exc)}, ensure_ascii=False))])
