import asyncio
import hashlib
import json
import sqlite3

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.state import AgentState
from agentscope.tool import ToolResponse

from traceglass.evidence.store import EvidenceStore
from traceglass.tools.review import AnalysisReview
from test_analysis_review import path

SHA = 'a' * 64


def capture(store, **changes):
    return store.capture_call(user_id='alice', workspace_id='w', session_id='child', agent_id='worker',
        connection_id='connection', call_id='read', tool_name='mcp__rea__procedure_assembly',
        arguments={'procedure': '0x1000'}, raw='0x1000: call memcpy', state='success',
        sample_sha256=SHA, **changes)


def test_call_archive_is_idempotent_bounded_and_scoped_after_restart(tmp_path):
    store = EvidenceStore(tmp_path / 'ledger.db')
    first = capture(store)
    assert capture(store)['id'] == first['id']
    record = EvidenceStore(store.path).get('alice', 'w', first['id'])
    assert record['raw'] == '0x1000: call memcpy'
    assert record['raw_sha256'] == hashlib.sha256(record['raw'].encode()).hexdigest()
    assert record['call_state'] == 'success' and record['sample_sha256'] == SHA
    assert store.get('bob', 'w', first['id']) is None
    assert store.get('alice', 'other', first['id']) is None
    truncated = capture(store, max_bytes=4)
    saved = store.get('alice', 'w', truncated['id'])
    assert saved['truncated'] and len(saved['raw'].encode()) <= 4


def test_strict_review_separates_structure_and_provenance_and_detects_mutated_bytes(tmp_path):
    store = EvidenceStore(tmp_path / 'ledger.db')
    receipt = capture(store)
    tool = AnalysisReview(store=store, user_id='alice', workspace_id='w', strict_references=True)
    candidate = path(evidence=[{'reference': 'assembly at copy', 'evidence_id': receipt['id'],
                               'locator': '0x1000', 'method': 'disassembly'}])
    def review(candidate, **changes):
        response = asyncio.run(tool(_agent_state=AgentState(), sample_sha256=SHA,
            paths=[candidate], scope='receiver', verdict='benign_in_scope', **changes))
        return json.loads(response.content[0].text)
    result = review(candidate)
    assert result['review_passed'] and result['references_passed']
    assert result['recommended_verdict'] == 'benign_in_scope'
    candidate['evidence'][0]['evidence_id'] = 'tgev_' + '0' * 64
    result = review(candidate)
    assert result['review_passed'] and not result['references_passed']
    assert result['recommended_verdict'] == 'unknown'
    candidate['evidence'][0]['evidence_id'] = receipt['id']
    candidate['evidence'][0]['locator'] = '0x123456'
    assert not review(candidate)['references_passed']
    candidate['evidence'][0]['locator'] = '0x1000'
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE rea_observations SET raw=? WHERE id=?', ('changed', receipt['id']))
    assert not review(candidate)['references_passed']


def test_reference_validation_rejects_foreign_sample_errors_and_truncation(tmp_path):
    store = EvidenceStore(tmp_path / 'ledger.db')
    receipt = capture(store, max_bytes=5)
    assert store.validate_reference('alice', 'w', receipt['id'], SHA)['status'] == 'truncated'
    assert store.validate_reference('bob', 'w', receipt['id'], SHA)['status'] == 'not_found'
    ordinary = capture(store)
    assert store.validate_reference('alice', 'w', ordinary['id'], 'b'*64)['status'] == 'sample_mismatch'


def test_generic_middleware_records_error_and_success_without_changing_original_output(tmp_path):
    from traceglass.integrations.evidence_ledger import EvidenceLedgerMiddleware
    store = EvidenceStore(tmp_path / 'ledger.db')
    middleware = EvidenceLedgerMiddleware(store, 'alice', 'w', 's', 'worker')
    async def run(state):
        call = ToolCallBlock(id=state, name='mcp__reverse__decompile_function', input='{}')
        async def handler(**kwargs):
            response = ToolResponse(state=state, content=[TextBlock(text='original')])
            yield response
        items = [i async for i in middleware.on_acting(None, {'tool_call': call}, handler)]
        assert items[0].content[0].text == 'original' and items[0].state == state
        return items[0].metadata['traceglass_archive']
    for state in ('success', 'error'):
        receipt = asyncio.run(run(state))
        saved = store.get('alice', 'w', receipt['traceglass_evidence'][0]['id'])
        assert saved['call_state'] == state and saved['sample_sha256'] is None
    assert store.list('alice', 'w', kind='tool_call')['total'] == 2


def test_large_rea_envelope_cannot_bypass_call_budget(tmp_path):
    from traceglass.integrations.evidence_ledger import EvidenceLedgerMiddleware
    from test_rea_evidence import envelope
    store = EvidenceStore(tmp_path / 'ledger.db')
    payload = envelope()
    payload['evidence']['normalized_result'] = {'code': 'x' * 1_100_000}
    raw = json.dumps(payload)
    call = ToolCallBlock(id='large', name='mcp__rea__procedure_pseudo_code', input='{}')
    async def run():
        async def handler(**kwargs):
            yield ToolResponse(state='success', content=[TextBlock(text=raw)])
        return [i async for i in EvidenceLedgerMiddleware(store, 'alice', 'w', 's', 'a').on_acting(
            None, {'tool_call': call}, handler)][0]
    response = asyncio.run(run())
    assert response.metadata['traceglass_archive']['archive_status'] == 'partial'
    assert len(response.content[0].text) < 4000 and 'ReadAnalysisEvidence' in response.content[0].text
    records = store.list('alice', 'w')['items']
    assert len(records) == 2
    for entry in records:
        saved = store.get('alice', 'w', entry['id'])
        assert len(saved['raw'].encode()) <= 1_048_576
        assert saved['truncated']
        assert store.validate_reference('alice', 'w', entry['id'], 'b'*64)['status'] == 'truncated'
    with sqlite3.connect(store.path) as db:
        total = db.execute('SELECT sum(length(cast(raw AS blob))) FROM rea_observations').fetchone()[0]
        record_total = db.execute('SELECT sum(length(cast(record AS blob))) FROM rea_observations').fetchone()[0]
    assert total <= 1_048_576 and record_total <= 1_048_576


def test_path_binding_hashes_real_workspace_file_and_rejects_outside_or_changed_input(tmp_path):
    from traceglass.integrations.evidence_ledger import EvidenceLedgerMiddleware
    from types import SimpleNamespace
    root = tmp_path / 'workspace'
    root.mkdir()
    sample = root / 'sample.c'
    sample.write_text('int guarded_copy;')
    outside = tmp_path / 'outside.c'
    outside.write_text('outside')
    store = EvidenceStore(tmp_path / 'ledger.db')
    middleware = EvidenceLedgerMiddleware(store, 'alice', 'w', 's', 'a', workspace_root=root)
    async def run(path, number, mutate=False):
        call = ToolCallBlock(id=str(number), name='Read', input=json.dumps({'file_path': str(path), 'sample_sha256': 'a'*64}))
        async def handler(**kwargs):
            if mutate:
                sample.write_text('changed during read')
            yield ToolResponse(state='success', content=[TextBlock(text='observed source')])
        agent = SimpleNamespace(state=AgentState())
        response = [i async for i in middleware.on_acting(agent, {'tool_call': call}, handler)][0]
        eid = response.metadata['traceglass_archive']['traceglass_evidence'][0]['id']
        return store.get('alice', 'w', eid)
    expected = hashlib.sha256(sample.read_bytes()).hexdigest()
    assert asyncio.run(run(sample, 1))['sample_sha256'] == expected
    assert asyncio.run(run(outside, 2))['sample_sha256'] is None
    assert asyncio.run(run(sample, 3, mutate=True))['sample_sha256'] is None


def test_raised_failure_timeout_and_cancel_are_archived_without_swallowing(tmp_path):
    import pytest
    from traceglass.integrations.evidence_ledger import EvidenceLedgerMiddleware
    store = EvidenceStore(tmp_path / 'ledger.db')
    async def run(exc):
        middleware = EvidenceLedgerMiddleware(store, 'alice', 'w', 's', 'a')
        call = ToolCallBlock(id=type(exc).__name__, name='mcp__reverse__decompile_function', input='{}')
        async def handler(**kwargs):
            raise exc
            yield
        with pytest.raises(type(exc)):
            _ = [i async for i in middleware.on_acting(None, {'tool_call': call}, handler)]
    for exc in (RuntimeError('synthetic failure'), TimeoutError('timeout'), asyncio.CancelledError()):
        asyncio.run(run(exc))
    states = {r['call_state'] for r in store.list('alice', 'w')['items']}
    assert states == {'error', 'timeout', 'cancelled'}


def test_new_session_cannot_disable_strict_references_with_text_evidence_on_restart():
    from traceglass.analysis_contract import reference_mode
    from traceglass.tools.review import REVIEW_KEY
    state = AgentState()
    assert reference_mode(state, strict=True) == 'strict'
    state.middle_context[REVIEW_KEY] = {SHA: {'reference_mode': 'strict',
        'paths': [{'evidence': [{'reference': 'legacy-looking text'}]}]}}
    restored = AgentState.model_validate_json(state.model_dump_json())
    assert reference_mode(restored, strict=True) == 'strict'
    restored.middle_context[REVIEW_KEY][SHA].pop('reference_mode')
    assert reference_mode(restored, strict=True) == 'compatible'


def test_binary_attachments_survive_restart_and_are_hash_checked_and_scoped(tmp_path):
    import base64
    from agentscope.message import Base64Source, DataBlock
    from traceglass.integrations.evidence_ledger import EvidenceLedgerMiddleware
    store = EvidenceStore(tmp_path / 'ledger.db')
    data = b'bounded opaque attachment bytes'
    async def run():
        call = ToolCallBlock(id='attachment', name='static_image', input='{}')
        async def handler(**kwargs):
            yield ToolResponse(state='success', content=[TextBlock(text='original caption'),
                DataBlock(source=Base64Source(data=base64.b64encode(data).decode(), media_type='application/octet-stream'))])
        return [i async for i in EvidenceLedgerMiddleware(store, 'alice', 'w', 's', 'a').on_acting(
            None, {'tool_call': call}, handler)][0]
    response = asyncio.run(run())
    rid = response.metadata['traceglass_archive']['traceglass_evidence'][0]['id']
    restored = EvidenceStore(store.path)
    record = restored.get('alice', 'w', rid)
    attachment = record['attachments'][0]
    assert attachment['sha256'] == hashlib.sha256(data).hexdigest()
    assert restored.get_attachment('alice', 'w', rid, attachment['id'])[0] == data
    assert restored.get_attachment('bob', 'w', rid, attachment['id']) is None


def test_reverse_and_native_ida_bind_only_verified_files_and_explicit_session_handles(tmp_path):
    from types import SimpleNamespace
    from traceglass.integrations.evidence_ledger import EvidenceLedgerMiddleware
    root = tmp_path / 'workspace'
    (root / 'samples').mkdir(parents=True)
    sample = root / 'samples' / 'fixture.o'
    sample.write_bytes(b'authored static fixture')
    digest = hashlib.sha256(sample.read_bytes()).hexdigest()
    store = EvidenceStore(tmp_path / 'ledger.db')
    middleware = EvidenceLedgerMiddleware(store, 'alice', 'w', 's', 'a', workspace_root=root)
    agent = SimpleNamespace(state=AgentState())
    async def call(name, args, output, number):
        async def handler(**kwargs):
            yield ToolResponse(state='success', content=[TextBlock(text=json.dumps(output))])
        tool_call = ToolCallBlock(id=str(number), name=name, input=json.dumps(args))
        response = [i async for i in middleware.on_acting(agent, {'tool_call': tool_call}, handler)][0]
        rid = response.metadata['traceglass_archive']['traceglass_evidence'][0]['id']
        return store.get('alice', 'w', rid)
    assert asyncio.run(call('mcp__reverse__analyze', {'sample_path': 'fixture.o'}, {}, 1))['sample_sha256'] == digest
    asyncio.run(call('mcp__idapro__session_create', {'file_path': str(sample)},
        {'id': 'ida-one', 'file_path': str(sample), 'status': 'ready'}, 2))
    agent.state = AgentState.model_validate_json(agent.state.model_dump_json())
    assert asyncio.run(call('mcp__idapro__decompile', {'session_id': 'ida-one'}, {}, 3))['sample_sha256'] == digest
    assert asyncio.run(call('mcp__idapro__decompile', {}, {}, 4))['sample_sha256'] is None
    asyncio.run(call('mcp__idapro__session_create', {'file_path': str(sample)},
        {'id': 'ida-other', 'file_path': '/elsewhere/other.o', 'status': 'ready'}, 5))
    assert asyncio.run(call('mcp__idapro__decompile', {'session_id': 'ida-other'}, {}, 6))['sample_sha256'] is None
    asyncio.run(call('mcp__idapro__session_close', {'session_id': 'ida-one'}, {}, 7))
    assert asyncio.run(call('mcp__idapro__decompile', {'session_id': 'ida-one'}, {}, 8))['sample_sha256'] is None


def test_model_context_uses_short_receipt_but_original_and_archive_failures_are_preserved(tmp_path):
    from agentscope.message import Msg, ToolResultBlock
    from traceglass.integrations.evidence_ledger import EvidenceLedgerMiddleware
    store = EvidenceStore(tmp_path / 'ledger.db')
    original = 'x' * 30_000 + 'FULL_ORIGINAL_TAIL'
    receipt = capture(store)
    metadata = {'traceglass_archive': {'archive_status': 'saved', 'traceglass_evidence': [receipt]}}
    message = Msg(name='tool', role='assistant', content=[ToolResultBlock(id='read', name='static_read',
        output=[TextBlock(text=original)], state='success', metadata=metadata)])
    async def run():
        async def model(**kwargs):
            return kwargs['messages']
        return await EvidenceLedgerMiddleware(store, 'alice', 'w', 's', 'a').on_model_call(
            None, {'messages': [message]}, model)
    prepared = asyncio.run(run())
    output = prepared[0].content[0].output[0].text
    assert len(output) < 4000 and receipt['id'] in output and 'ReadAnalysisEvidence' in output
    assert message.content[0].output[0].text == original
    message.content[0].metadata['traceglass_archive']['archive_status'] = 'archive_failed'
    assert asyncio.run(run())[0].content[0].output[0].text == original
