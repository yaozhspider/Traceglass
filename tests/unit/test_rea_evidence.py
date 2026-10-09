import asyncio
import json

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.tool import ToolResponse


def envelope():
    return {'result': {'code': 'return value;'}, 'evidence_id': 'ev_' + 'a' * 64,
        'evidence': {'evidence_id': 'ev_' + 'a' * 64,
            'subject': {'name': 'sample', 'digest': {'sha256': 'b' * 64}, 'local_path': '/workspace/sample'},
            'provider': {'id': 'ghidra', 'name': 'Ghidra', 'version': '12.1.4'},
            'operation': 'procedure_pseudo_code', 'parameters': {'procedure': '0x1000'},
            'raw_result': None, 'normalized_result': {'code': 'return value;'},
            'confidence': 'derived', 'authority': 'shipped-artifact',
            'locations': [{'kind': 'address', 'address': '0x1000'}],
            'limitations': ['static only'], 'evidence_links': []}}


def test_archive_retains_raw_results_and_scopes_reads_across_restart(tmp_path):
    from traceglass.evidence.store import EvidenceStore
    store = EvidenceStore(tmp_path / 'evidence.db')
    raw = json.dumps(envelope(), indent=2)
    kwargs = dict(user_id='alice', workspace_id='w', session_id='child', agent_id='decoder',
                  connection_id='connection-a', call_id='call-a', tool_name='mcp__rea__procedure_pseudo_code',
                  arguments={'procedure': '0x1000'}, raw=raw)
    first = store.capture(**kwargs)
    assert len(first) == 1
    assert store.capture(**kwargs) == first  # Replay does not duplicate an invocation.
    other = store.capture(**{**kwargs, 'connection_id': 'connection-b'})
    assert other[0]['id'] != first[0]['id']  # Upstream IDs are not connection handles.
    saved = EvidenceStore(tmp_path / 'evidence.db')
    listed = saved.list('alice', 'w', sample_sha256='b' * 64, limit=1)
    assert listed['total'] == 2 and listed['next_cursor'] is not None
    assert 'raw' not in listed['items'][0]
    page2 = saved.list('alice', 'w', cursor=listed['next_cursor'], limit=1)
    assert len(page2['items']) == 1 and page2['next_cursor'] is None
    record = saved.get('alice', 'w', first[0]['id'])
    assert record['raw'] == raw and record['confidence'] == 'derived'
    assert record['limitations'] == ['static only'] and record['agent_id'] == 'decoder'
    assert saved.list('bob', 'w')['items'] == []
    assert saved.list('alice', 'other')['items'] == []
    assert saved.get('bob', 'w', first[0]['id']) is None
    assert store.capture(**{**kwargs, 'raw': '{"error": "failure"}'}) == []
    assert store.capture(**{**kwargs, 'raw': '{"evidence_id":"fake"}'}) == []


def test_unknown_updates_preserve_history_and_session_identity(tmp_path):
    from traceglass.evidence.store import EvidenceStore
    store = EvidenceStore(tmp_path / 'evidence.db')
    for revision, status in [(1, 'open'), (2, 'resolved')]:
        store.capture(user_id='alice', workspace_id='w', session_id='child', agent_id='worker',
            connection_id='conn', call_id=f'c{revision}', tool_name='mcp__rea__update_unknown', arguments={},
            raw=json.dumps({'result': {'unknown_id': 'unk_test', 'revision': revision,
                                       'status': status, 'summary': 'Protocol unverified'}}))
    records = store.list('alice', 'w', kind='unknown')['items']
    assert len(records) == 2 and records[0]['status'] == 'resolved'
    assert records[1]['status'] == 'open'


def test_acting_archive_preserves_errors_and_reports_save_failures(tmp_path):
    from traceglass.evidence.store import EvidenceStore
    from traceglass.integrations.rea_evidence import ReaEvidenceMiddleware
    store = EvidenceStore(tmp_path / 'evidence.db')
    middleware = ReaEvidenceMiddleware(store, 'alice', 'w', 'child', 'worker', 'conn')
    call = ToolCallBlock(id='call', name='mcp__rea__procedure_pseudo_code', input='{}')
    original = json.dumps(envelope())
    async def run(state):
        async def handler(**kwargs):
            yield ToolResponse(state=state, content=[TextBlock(text=original)])
        return [item async for item in middleware.on_acting(None, {'tool_call': call}, handler)][0]
    error = asyncio.run(run('error'))
    assert len(error.content) == 1 and store.list('alice', 'w')['total'] == 0
    success = asyncio.run(run('success'))
    assert success.content[0].text == original
    assert 'traceglass_evidence' in success.content[-1].text
    assert success.metadata['traceglass_archive'] == json.loads(success.content[-1].text)
    assert store.list('alice', 'w')['total'] == 1
    class BrokenStore:
        def capture(self, **kwargs):
            raise OSError('fixture disk full')
    middleware.store = BrokenStore()
    failed = asyncio.run(run('success'))
    assert failed.content[0].text == original and failed.state == 'success'
    assert 'archive_failed' in failed.content[-1].text
    assert failed.metadata['traceglass_archive']['archive_status'] == 'archive_failed'


def test_agent_evidence_reads_are_scoped_and_explicitly_paged(tmp_path):
    from traceglass.evidence.store import EvidenceStore
    from traceglass.tools.rea_evidence import evidence_tools
    store = EvidenceStore(tmp_path / 'archive.db')
    raw = json.dumps(envelope())
    saved = store.capture(user_id='alice', workspace_id='w', session_id='child', agent_id='worker',
        connection_id='conn', call_id='call', tool_name='mcp__rea__procedure_pseudo_code', arguments={}, raw=raw)
    async def run():
        tools = {t.name: t for t in evidence_tools(store, 'alice', 'w')}
        listed = json.loads((await tools['QueryReaEvidence']()).content[0].text)
        assert listed['items'][0]['id'] == saved[0]['id']
        page = json.loads((await tools['ReadReaEvidence'](record_id=saved[0]['id'], offset=0, limit=10)).content[0].text)
        assert page['raw_page'] == raw[:10] and page['next_offset'] == 10
        foreign = {t.name: t for t in evidence_tools(store, 'bob', 'w')}
        assert (await foreign['ReadReaEvidence'](record_id=saved[0]['id'])).state == 'error'
    asyncio.run(run())
