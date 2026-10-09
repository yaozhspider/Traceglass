import asyncio
import json

from agentscope.state import AgentState
from traceglass.tools.review import AnalysisReview

SHA = 'a' * 64


def path(status='resolved', method='disassembly', **changes):
    return {'id': 'network-receiver', 'source': 'Session.recv', 'dispatch': 'prefix branch',
            'sink': 'display only', 'authorization': 'peer authentication checked',
            'privilege': 'client process token', 'status': status,
            'evidence': [{'reference': 'Read:call-42/client.asm:18', 'method': method}],
            'limitations': '', **changes}


def invoke(state, **kwargs):
    result = asyncio.run(AnalysisReview()(_agent_state=state, sample_sha256=SHA, **kwargs))
    return result, json.loads(result.content[0].text)


def test_incomplete_or_string_only_analysis_cannot_support_benign_verdict():
    state = AgentState()
    _, result = invoke(state, verdict='benign_in_scope')
    assert not result['review_passed']
    _, result = invoke(state, paths=[path(status='blocked', limitations='IDA input missing')], verdict='benign_in_scope')
    assert not result['review_passed']
    _, result = invoke(state, paths=[path(method='strings')], verdict='benign_in_scope')
    assert not result['review_passed']
    assert result['recommended_verdict'] == 'unknown'


def test_partial_updates_preserve_other_paths_and_native_state_roundtrip():
    state = AgentState()
    invoke(state, paths=[path(), path(id='plugin-entry', status='open')])
    state = AgentState.model_validate_json(state.model_dump_json())
    _, result = invoke(state, paths=[path(id='network-receiver')], verdict='benign_in_scope')
    assert not result['review_passed']
    assert {p['id'] for p in result['paths']} == {'network-receiver', 'plugin-entry'}
    asyncio.run(AnalysisReview()(_agent_state=state, sample_sha256='b'*64, paths=[path()]))
    _, result = invoke(state)
    assert len(result['paths']) == 2


def test_supported_safe_control_and_confirmed_execution_are_not_conflated():
    state = AgentState()
    _, result = invoke(state, paths=[path()], verdict='benign_in_scope',
                       scope='Network receiver only; static review of the full dispatch function')
    assert result['review_passed']
    assert result['verification_scope'] == 'structure_only_not_semantic_proof'
    _, result = invoke(state, paths=[path(sink='exec(payload)', remote_execution=True)], verdict='benign_in_scope')
    assert not result['review_passed']
    _, result = invoke(state, verdict='remote_execution')
    assert result['review_passed']


def test_invalid_update_is_atomic_and_unknown_does_not_require_invented_evidence():
    state = AgentState()
    invoke(state, paths=[path()])
    before = state.model_dump_json()
    result, body = invoke(state, paths=[path(id='x'), path(id='x')])
    assert result.state == 'error'
    assert state.model_dump_json() == before
    result, body = invoke(AgentState(), verdict='unknown')
    assert body['review_passed']


def test_marking_unknown_dispatch_resolved_does_not_satisfy_benign_review():
    _, result = invoke(AgentState(), paths=[path(dispatch='unknown')],
                       scope='receiver', verdict='benign_in_scope')
    assert not result['review_passed']
