import asyncio
import json
from pathlib import Path
import sys

from agentscope.mcp import MCPClient, StdioMCPConfig

from traceglass.integrations.rea import ReaSessions, rea_client


def test_rea_launcher_mounts_only_this_workspace_and_separates_member_state(tmp_path):
    kit = tmp_path / 'kit'
    (kit / 'bin').mkdir(parents=True)
    (kit / 'bin/docker-cli').write_text('#!/bin/sh\n')
    (kit / 'MANIFEST.json').write_text(json.dumps({'image': {'image_id': 'sha256:' + 'a'*64}}))
    root = tmp_path / 'workspace'
    root.mkdir()
    a = rea_client(kit, root, 'alice', 'leader')
    b = rea_client(kit, root, 'alice', 'child')
    args = a.mcp_config.args
    assert f'type=bind,source={root},target=/workspace,readonly' in args
    assert args[args.index('--network')+1] == 'none'
    assert '/var/run/docker.sock' not in str(args)
    volumes_a = [x for x in args if x.startswith('type=volume')]
    volumes_b = [x for x in b.mcp_config.args if x.startswith('type=volume')]
    assert volumes_a != volumes_b
    assert a.execution_timeout == 600


def test_native_rea_connections_reuse_one_session_but_never_share_member_state(tmp_path, monkeypatch):
    import traceglass.integrations.rea as integration
    fixture = Path(__file__).parents[1] / 'fixtures/rea_state_mcp.py'
    monkeypatch.setattr(integration, 'rea_client', lambda *args: MCPClient(name='rea', is_stateful=True,
        mcp_config=StdioMCPConfig(command=sys.executable, args=[str(fixture)])))

    async def run():
        pool = ReaSessions(tmp_path)
        try:
            a, same, b = await asyncio.gather(
                pool.tools(tmp_path, 'alice', 'leader', 'workspace'),
                pool.tools(tmp_path, 'alice', 'leader', 'workspace'),
                pool.tools(tmp_path, 'alice', 'child', 'workspace'))
            first = await a[0]()
            second = await same[0]()
            separate = await b[0]()
            assert first.content[0].text == '1'
            assert second.content[0].text == '2'
            assert separate.content[0].text == '1'
            assert a[0].name == 'mcp__rea__state_probe'
            assert pool.status('workspace')[0]['status'] == 'connected'
        finally:
            await pool.close()
        assert pool.status('workspace')[0]['status'] != 'connected'
    asyncio.run(run())


def test_missing_rea_can_fail_without_disabling_analysis(tmp_path):
    async def run():
        pool = ReaSessions(tmp_path / 'missing')
        try:
            assert await pool.tools(tmp_path, 'alice', 'leader', 'workspace') == []
            assert pool.status('workspace')[0]['status'] == 'unavailable'
        finally:
            await pool.close()
    asyncio.run(run())


def test_rea_tools_and_analysis_skills_reach_native_leader_and_worker(tmp_path, monkeypatch):
    import httpx
    from agentscope.app.storage import SessionConfig, TeamData, TeamMember, TeamRecord
    from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
    from agentscope.model import ChatResponse, OpenAIChatModel
    from agentscope.permission import PermissionMode
    from traceglass.agents import AGENT_ID, analyst_record
    from traceglass.api.app import create_app
    from traceglass.config import Settings
    import traceglass.integrations.rea as integration

    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    fixture = Path(__file__).parents[1] / 'fixtures/rea_state_mcp.py'
    monkeypatch.setattr(integration, 'rea_client', lambda *args: MCPClient(name='rea', is_stateful=True,
        mcp_config=StdioMCPConfig(command=sys.executable, args=[str(fixture)])))

    async def provider(self, model_name, messages, tools=None, **kwargs):
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        schemas = {t['function']['name']: t for t in tools}
        if not results:
            assert 'SearchReaTools' in schemas
            assert '二进制语义分析优先使用 REA' in str(messages)
            return ChatResponse(content=[ToolCallBlock(id='discover', name='SearchReaTools',
                input='{"query":"state_probe"}')], is_last=True)
        discovery = json.loads(results[0].output[0].text)
        assert discovery['matches'][0]['reset_tools'] == {'rea_native': True}
        results = results[1:]
        if not results:
            assert 'mcp__rea__state_probe' not in schemas
            assert 'rea_native' in schemas['reset_tools']['function']['parameters']['properties']
            return ChatResponse(content=[ToolCallBlock(id='activate', name='reset_tools',
                input='{"rea_native": true, "rea_validation": true}')], is_last=True)
        if len(results) == 1:
            assert 'mcp__rea__state_probe' in schemas
            return ChatResponse(content=[ToolCallBlock(id='probe', name='mcp__rea__state_probe', input='{}')], is_last=True)
        if len(results) == 2:
            return ChatResponse(content=[ToolCallBlock(id='list-workflows', name='ListReaWorkflows', input='{}')], is_last=True)
        if len(results) == 3:
            assert 'investigate_feature' in results[-1].output[0].text
            return ChatResponse(content=[ToolCallBlock(id='workflow', name='GetReaWorkflow',
                input='{"name":"investigate_feature","arguments":{"feature":"fixture"}}')], is_last=True)
        if len(results) == 4:
            assert 'counter=1' in results[-1].output[0].text
            return ChatResponse(content=[ToolCallBlock(id='evidence', name='mcp__rea__evidence_probe', input='{}')], is_last=True)
        if len(results) == 5:
            assert results[-1].state == 'success', results[-1]
            assert any('traceglass_evidence' in b.text for b in results[-1].output)
            return ChatResponse(content=[ToolCallBlock(id='query', name='QueryReaEvidence', input='{}')], is_last=True)
        return ChatResponse(content=[TextBlock(text='REA_PROBE_DONE')], is_last=True)

    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)

    async def run():
        discovery_pool = ReaSessions(tmp_path)
        try:
            await discovery_pool.refresh_schema(tmp_path)
        finally:
            await discovery_pool.close()
        app = create_app(settings=Settings(data_dir=tmp_path/'data', rea_kit=tmp_path,
                                           model_name='fixture', api_key='fixture'))
        async with app.router.lifespan_context(app):
            storage = app.state.storage
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                         headers={'X-User-ID':'local'}) as client:
                cfg = (await client.get('/product/config')).json()
            leader = await storage.upsert_session('local', AGENT_ID, SessionConfig(
                workspace_id='test-workspace', chat_model_config=cfg['model_config']))
            record = analyst_record('local', 2000).model_copy(update={'id':'worker', 'source':'team'})
            record.data.name = 'worker'
            await storage.upsert_agent('local', record)
            child = await storage.upsert_session('local', record.id, leader.config)
            team = TeamRecord(user_id='local', session_id=leader.id, data=TeamData(name='fixture', members=[
                TeamMember(owner_id='local', agent_id=record.id, session_id=child.id, role='created')]))
            await storage.upsert_team('local', team)
            for aid, sid in [(AGENT_ID, leader.id), (record.id, child.id)]:
                await storage.set_session_team_id('local', sid, team.id)
                # Exercise the native permission gate with a test-only bypass
                # session, without changing any live user's permissions.
                saved = await storage.get_session('local', aid, sid)
                saved.state.permission_context.mode = PermissionMode.BYPASS
                await storage.upsert_session('local', aid, saved.config, session_id=sid, state=saved.state)
                await app.state.chat_service.run('local', sid, aid,
                    Msg(name='user', role='user', content=[TextBlock(text='Probe REA')]))
                assert app.state.rea_sessions.status('test-workspace')[0]['active_connections'] == 0
                messages, _ = await storage.list_messages('local', sid, limit=20)
                outputs = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
                assert len(outputs) == 7 and all(b.state == 'success' for b in outputs), outputs
                outputs = outputs[1:]
                assert outputs[1].output[0].text == '1'
                receipt = outputs[4].metadata['traceglass_archive']
                assert receipt['archive_status'] == 'saved'
                archived = json.loads(outputs[-1].output[0].text)
                assert archived['total'] == (1 if aid == AGENT_ID else 2)
                assert receipt['traceglass_evidence'][0]['id'] in {r['id'] for r in archived['items']}
                assert {r['session_id'] for r in archived['items']} >= {leader.id}
                workspace = await app.state.workspace_manager.get_workspace('local', aid, sid, 'test-workspace')
                names = {s.name for s in await workspace.list_skills()}
                assert {'dynamic-malware-analysis','malware-analysis','crypto-obfuscation-analysis',
                        'rea-analysis','reverse-engineer-anything','vuln-discovery',
                        'vulnerability-hunting'} <= names
    asyncio.run(run())
