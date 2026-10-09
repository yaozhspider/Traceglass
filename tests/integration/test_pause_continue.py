"""Native interruption and continuation preserve the existing analysis."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse, OpenAIChatModel

from traceglass.api.app import create_app
from traceglass.config import Settings


@pytest.mark.parametrize('parked', [False, True])
def test_native_continue_after_iteration_limit_or_permission_pause(tmp_path, monkeypatch, parked):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / 'never-write.txt'

    async def provider(self, model_name, messages, tools=None, **kwargs):
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        choice = kwargs.get('tool_choice')
        if choice is not None and choice.mode == 'none':
            return ChatResponse(content=[TextBlock(text='BUDGET_SUMMARY_WITH_GAPS')], is_last=True)
        if len(results) < (1 if parked else 3):
            name, args = ('Write', {'file_path': str(path), 'content': 'must not write'}) if parked else ('TaskList', {})
            return ChatResponse(content=[ToolCallBlock(id=f'call-{len(results)}', name=name,
                                input=json.dumps(args))], is_last=True)
        return ChatResponse(content=[TextBlock(text='CONTINUED_WITH_HISTORY')], is_last=True)

    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)

    async def run():
        app = create_app(settings=Settings(data_dir=tmp_path / 'data', model_name='fixture', api_key='fixture', max_iters=3))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                         headers={'X-User-ID': 'local'}) as client:
                cfg = (await client.get('/product/config')).json()
                aid = cfg['agent_id']
                created = await client.post('/sessions/', json={'agent_id': aid, 'name': 'pause fixture',
                                                               'chat_model_config': cfg['model_config']})
                sid = created.json()['session_id']
                await app.state.chat_service.run('local', sid, aid,
                    Msg(name='user', role='user', content=[TextBlock(text='fixture')]))
                if parked:
                    response = await client.post(f'/product/sessions/{sid}/interrupt',
                                                 params={'agent_id': aid, 'scope': 'team'})
                    assert response.status_code == 202, response.text
                    async def stopped():
                        while True:
                            messages, _ = await app.state.storage.list_messages('local', sid, limit=20)
                            if any(m.finished_reason == 'interrupted' for m in messages):
                                return
                            await asyncio.sleep(0.01)
                    await asyncio.wait_for(stopped(), timeout=3)
                    assert not path.exists()
                else:
                    messages, _ = await app.state.storage.list_messages('local', sid, limit=20)
                    assert any('BUDGET_SUMMARY_WITH_GAPS' in m.get_text_content() for m in messages)
                    assert any(b.type == 'hint' and b.source == 'traceglass.iteration_closeout'
                               for m in messages for b in m.content)
                # Same input:null that the Continue button submits.
                await app.state.chat_service.run('local', sid, aid, None)
                messages, _ = await app.state.storage.list_messages('local', sid, limit=20)
                assert any(m.finished_reason == 'completed' and 'CONTINUED_WITH_HISTORY' in m.get_text_content() for m in messages)
                calls = [b for m in messages for b in m.content if isinstance(b, ToolCallBlock)]
                assert len(calls) == (1 if parked else 3)
                assert not path.exists()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['ignores_tool_choice', 'provider_error'])
def test_closeout_failure_never_executes_new_tool_or_fabricates_summary(tmp_path, monkeypatch, failure):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / 'must-not-exist.txt'

    async def provider(self, model_name, messages, tools=None, **kwargs):
        if failure == 'provider_error':
            raise ValueError('fixture closeout failure')
        return ChatResponse(content=[ToolCallBlock(id='forbidden-write', name='Write',
            input=json.dumps({'file_path': str(path), 'content': 'unexpected'}))], is_last=True)

    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)

    async def run():
        app = create_app(settings=Settings(data_dir=tmp_path / 'data', model_name='fixture', api_key='fixture', max_iters=1))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                         headers={'X-User-ID': 'local'}) as client:
                cfg = (await client.get('/product/config')).json()
                aid = cfg['agent_id']
                created = await client.post('/sessions/', json={'agent_id': aid, 'name': 'closeout failure',
                    'chat_model_config': cfg['model_config'], 'permission_mode': 'bypass'})
                sid = created.json()['session_id']
                await app.state.chat_service.run('local', sid, aid, Msg(name='user', role='user', content=[TextBlock(text='fixture')]))
                messages, _ = await app.state.storage.list_messages('local', sid, limit=20)
                assert not path.exists()
                assert any(m.finished_reason == ('error' if failure == 'provider_error' else 'exceed_max_iters') for m in messages)
                assert not any(m.finished_reason == 'completed' for m in messages)
    asyncio.run(run())
