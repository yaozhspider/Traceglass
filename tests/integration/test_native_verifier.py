"""Independent verification must cross the real AgentCreate/runtime boundary."""
import asyncio
import hashlib
import json

import httpx
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse, OpenAIChatModel

from traceglass.api.app import create_app
from traceglass.config import Settings
from traceglass.tools.review import REVIEW_KEY


def test_native_verifier_receives_rewritten_originals_and_fresh_permission_context(tmp_path, monkeypatch):
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(key, raising=False)
    verifier_inputs = []
    sha = hashlib.sha256(b'fixture only').hexdigest()
    async def provider(self, model_name, messages, tools=None, **kwargs):
        if '新建独立复核成员' in messages[0].get_text_content():
            verifier_inputs.append(json.dumps([m.model_dump(mode='json') for m in messages], ensure_ascii=False))
            return ChatResponse(content=[TextBlock(text='VERIFIER_COMPLETE')], is_last=True)
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        script = [
            ('TodoWrite', {'todos': [{'subject': 'Independent verification'}]}),
            ('TeamCreate', {'name': 'verification', 'description': 'LEADER_DESCRIPTION_DO_NOT_TRANSFER'}),
            ('AgentCreate', {'name': 'reviewer', 'description': 'LEADER_REASONING_DO_NOT_TRANSFER',
                'subagent_type': 'verifier', 'prompt': f'task_id=1\nsample_sha256={sha}\nLEADER_REASONING_DO_NOT_TRANSFER'}),
        ]
        if len(results) < len(script):
            name, args = script[len(results)]
            return ChatResponse(content=[ToolCallBlock(id=f'v-{len(results)}', name=name, input=json.dumps(args))], is_last=True)
        return ChatResponse(content=[TextBlock(text='LEADER_COMPLETE')], is_last=True)
    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)
    async def run():
        app = create_app(settings=Settings(data_dir=tmp_path, model_name='fixture', api_key='fixture'))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                    headers={'X-User-ID': 'local'}) as client:
                config = (await client.get('/product/config')).json()
                response = await client.post('/sessions/', json={'agent_id': config['agent_id'],
                    'workspace_id': 'verifier-fixture', 'chat_model_config': config['model_config']})
                sid, aid = response.json()['session_id'], config['agent_id']
                saved = await app.state.storage.get_session('local', aid, sid)
                receipt = app.state.rea_evidence.capture_call(user_id='local', workspace_id='verifier-fixture',
                    session_id=sid, agent_id=aid, connection_id='native', call_id='original', tool_name='Read',
                    arguments={}, raw='ORIGINAL_OBSERVATION: guarded length before copy', state='success', sample_sha256=sha)
                saved.state.middle_context[REVIEW_KEY] = {sha: {'scope': 'receiver', 'proposed_verdict': 'hardened_in_scope',
                    'paths': [{'id': 'p', 'status': 'resolved', 'dispatch': 'LEADER_REASONING_DO_NOT_TRANSFER',
                        'evidence': [{'evidence_id': receipt['id']}]}]}}
                await app.state.storage.upsert_session('local', aid, saved.config, state=saved.state, session_id=sid)
                await app.state.chat_service.run('local', sid, aid,
                    Msg(name='user', role='user', content=[TextBlock(text='LEADER_HISTORY_DO_NOT_TRANSFER')]))
                async with asyncio.timeout(5):
                    while not verifier_inputs:
                        await asyncio.sleep(0.02)
                sessions = (await client.get('/sessions/', params={'agent_id': aid})).json()['sessions']
                member = sessions[0]['team']['members'][0]
                child = await app.state.storage.get_session('local', member['agent']['id'], member['session_id'])
                assert child.id != sid
                assert child.state.permission_context.mode == saved.state.permission_context.mode
                assert 'ORIGINAL_OBSERVATION' in verifier_inputs[0]
                assert receipt['id'] in verifier_inputs[0]
                assert 'UNTRUSTED DATA' in verifier_inputs[0]
                assert 'LEADER_HISTORY_DO_NOT_TRANSFER' not in verifier_inputs[0]
                assert 'LEADER_REASONING_DO_NOT_TRANSFER' not in verifier_inputs[0]
                assert 'LEADER_DESCRIPTION_DO_NOT_TRANSFER' not in verifier_inputs[0]
    asyncio.run(run())
