"""Product choices drive real native tools, persistence and confirmation resume."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from agentscope.event import UserConfirmResultEvent
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse, OpenAIChatModel
from agentscope.permission import PermissionBehavior, PermissionRule

from traceglass.api.app import create_app
from traceglass.config import Settings


@pytest.mark.parametrize('mode,rule', [('default', None), ('bypass', None), ('bypass', 'deny'), ('bypass', 'ask')])
def test_permission_selection_controls_real_writes_and_preserves_rules(tmp_path, monkeypatch, mode, rule):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    paths = []

    async def provider(self, model_name, messages, tools=None, **kwargs):
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        if len(results) < 2:
            return ChatResponse(content=[ToolCallBlock(id=f'write-{len(results)}', name='Write',
                input=json.dumps({'file_path': str(paths[len(results)]), 'content': 'verified'}))], is_last=True)
        return ChatResponse(content=[TextBlock(text='complete')], is_last=True)

    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)

    async def run():
        app = create_app(settings=Settings(data_dir=tmp_path, model_name='fixture', api_key='fixture'))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                         headers={'X-User-ID': 'local'}) as client:
                config = (await client.get('/product/config')).json()
                wid = (await client.post('/product/workspaces', json={'name': '权限行为验证'})).json()['workspace_id']
                response = await client.post(f'/product/workspaces/{wid}/sessions', json={
                    'permission_mode': mode, 'chat_model_config': config['model_config']})
                assert response.status_code == 201, response.text
                sid, aid = response.json()['session_id'], config['agent_id']
                workspace = await app.state.workspace_manager.get_workspace('local', aid, sid, wid)
                paths.extend([Path(workspace.workdir) / name for name in ['first.txt', 'second.txt']])
                if rule:
                    saved = await app.state.storage.get_session('local', aid, sid)
                    rules = {'Write': [PermissionRule(tool_name='Write', rule_content=None,
                             behavior=PermissionBehavior(rule), source='userSettings')]}
                    setattr(saved.state.permission_context, f'{rule}_rules', rules)
                    await app.state.storage.upsert_session('local', aid, saved.config, state=saved.state, session_id=sid)
                    # Changing mode must not discard explicit rules or other state.
                    changed = await client.patch(f'/product/sessions/{sid}/permission', params={'agent_id': aid},
                                                 json={'permission_mode': 'bypass'})
                    assert changed.status_code == 200, changed.text
                    assert changed.json()['state']['permission_context'][f'{rule}_rules']['Write'][0]['behavior'] == rule

                await app.state.chat_service.run('local', sid, aid,
                    Msg(name='user', role='user', content=[TextBlock(text='Write both test reports.')]))
                saved = await app.state.storage.get_session('local', aid, sid)
                assert saved.state.permission_context.mode.value == mode
                if mode == 'bypass' and rule is None:
                    assert [p.read_text() for p in paths] == ['verified', 'verified']
                elif rule == 'deny':
                    assert not any(p.exists() for p in paths)
                    messages, _ = await app.state.storage.list_messages('local', sid, limit=20)
                    results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
                    assert len(results) == 2 and all(b.state != 'success' for b in results)
                else:
                    assert not any(p.exists() for p in paths)
                    messages, _ = await app.state.storage.list_messages('local', sid, limit=20)
                    reply = next(m for m in messages if m.role == 'assistant')
                    pending = next(b for b in reply.content if isinstance(b, ToolCallBlock) and b.name == 'Write')
                    assert pending.state == 'asking'
                    if mode == 'default':
                        changed = await client.patch(f'/product/sessions/{sid}/permission', params={'agent_id': aid},
                                                     json={'permission_mode': 'bypass'})
                        assert changed.status_code == 200, changed.text
                        # A mode change is not implicit approval of an already-issued request.
                        assert not paths[0].exists()
                        await app.state.chat_service.run('local', sid, aid,
                            UserConfirmResultEvent(reply_id=reply.id, confirm_results=[
                                {'tool_call': pending, 'confirmed': True}]))
                        # The current write is confirmed; the next one runs without another ask.
                        assert [p.read_text() for p in paths] == ['verified', 'verified']

    asyncio.run(run())
