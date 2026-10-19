"""Schema activation and permissions must retain the native product boundary."""
import asyncio
import json

import httpx
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse, OpenAIChatModel
from agentscope.permission import PermissionBehavior, PermissionRule

from traceglass.api.app import create_app
from traceglass.config import Settings
from traceglass.integrations.rea import ReaSessions
from test_rea_lifecycle import client_factory


def test_missing_cache_connects_metadata_and_activates_tools_in_same_reply(tmp_path, monkeypatch):
    created = client_factory(monkeypatch)
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(key, raising=False)
    async def provider(self, model_name, messages, tools=None, **kwargs):
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        activation = (json.loads(results[1].output[0].text)['matches'][0]['reset_tools']
                      if len(results) >= 2 else {})
        script = [('ConnectRea', {}), ('SearchAnalysisTools', {'query': 'state_probe'}),
                  ('reset_tools', activation), ('mcp__rea__state_probe', {})]
        if len(results) < len(script):
            name, args = script[len(results)]
            return ChatResponse(content=[ToolCallBlock(id=f'lazy-{len(results)}', name=name, input=json.dumps(args))], is_last=True)
        return ChatResponse(content=[TextBlock(text='LAZY_COMPLETE')], is_last=True)
    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)
    async def run():
        app = create_app(settings=Settings(data_dir=tmp_path/'data', rea_kit=tmp_path,
                                          model_name='fixture', api_key='fixture'))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                        headers={'X-User-ID': 'local'}) as client:
                config = (await client.get('/product/config')).json()
                response = await client.post('/product/workspaces', json={'name': 'lazy'})
                wid = response.json()['workspace_id']
                response = await client.post(f'/product/workspaces/{wid}/sessions', json={
                    'permission_mode': 'bypass', 'chat_model_config': config['model_config']})
                sid, aid = response.json()['session_id'], config['agent_id']
                assert created == []
                await app.state.chat_service.run('local', sid, aid,
                    Msg(name='user', role='user', content=[TextBlock(text='Discover metadata and call fixture probe')]))
                messages, _ = await app.state.storage.list_messages('local', sid, limit=30)
                results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
                assert len(results) == 4
                assert all(b.state == 'success' for b in results), results
                assert 'mcp__rea__state_probe' in json.dumps(results[1].model_dump(mode='json'))
                assert len(created) == 2  # Explicit metadata discovery, then this agent's independent connection.
    asyncio.run(run())


def test_native_permission_denial_does_not_start_lazy_connection(tmp_path, monkeypatch):
    created = client_factory(monkeypatch)
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(key, raising=False)
    async def provider(self, model_name, messages, tools=None, **kwargs):
        if not any(isinstance(b, ToolResultBlock) for m in messages for b in m.content):
            return ChatResponse(content=[ToolCallBlock(id='denied', name='mcp__rea__open_binary',
                input=json.dumps({'path': '/workspace/fixture'}))], is_last=True)
        return ChatResponse(content=[TextBlock(text='DENIED_COMPLETE')], is_last=True)
    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)
    async def run():
        pool = ReaSessions(tmp_path)
        await pool.refresh_schema(tmp_path)
        await pool.close()
        baseline = len(created)
        app = create_app(settings=Settings(data_dir=tmp_path/'data', rea_kit=tmp_path,
                                          model_name='fixture', api_key='fixture'))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                        headers={'X-User-ID': 'local'}) as client:
                config = (await client.get('/product/config')).json()
                response = await client.post('/sessions/', json={'agent_id': config['agent_id'],
                    'workspace_id': 'deny', 'chat_model_config': config['model_config']})
                sid, aid = response.json()['session_id'], config['agent_id']
                saved = await app.state.storage.get_session('local', aid, sid)
                saved.state.permission_context.deny_rules['mcp__rea__open_binary'] = [PermissionRule(
                    tool_name='mcp__rea__open_binary', rule_content=None,
                    behavior=PermissionBehavior.DENY, source='userSettings')]
                await app.state.storage.upsert_session('local', aid, saved.config, state=saved.state, session_id=sid)
                await app.state.chat_service.run('local', sid, aid,
                    Msg(name='user', role='user', content=[TextBlock(text='Try denied fixture tool')]))
                assert len(created) == baseline
                messages, _ = await app.state.storage.list_messages('local', sid, limit=30)
                results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
                assert len(results) == 1 and results[0].state != 'success'
    asyncio.run(run())
