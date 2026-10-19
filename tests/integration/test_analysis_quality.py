"""Native model/tool/state boundary; only the external model is scripted."""
import asyncio
import json
from pathlib import Path

from agentscope.agent import Agent
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse, OpenAIChatModel
from agentscope.credential import OpenAICredential
from agentscope.tool import Toolkit
from agentscope.state import AgentState
from traceglass.analysis_policy import AnalysisQualityMiddleware
from traceglass.tools.review import AnalysisReview, REVIEW_KEY


def test_existing_custom_agent_gets_review_and_coverage_survives_context_replacement(monkeypatch):
    async def provider(self, model_name, messages, tools=None, **kwargs):
        # The middleware reaches actual model inputs even for an old custom role.
        system='\n'.join(m.get_text_content() for m in messages if m.role=='system')
        assert '保留我的角色' in system
        assert 'behavior-chain-review' in system
        outputs=[b for m in messages for b in m.content if isinstance(b,ToolResultBlock)]
        if not outputs:
            return ChatResponse(content=[ToolCallBlock(id='review',name='AnalysisReview',input=json.dumps({
                'sample_sha256':'a'*64,'verdict':'benign_in_scope','scope':'client receiver',
                'paths':[dict(id='receiver',source='recv',dispatch='unknown',sink='unknown',
                authorization='unknown',privilege='unknown',status='blocked',limitations='IDA file not found')]}))],is_last=True)
        assert json.loads(outputs[-1].output[0].text)['review_passed'] is False
        assert 'receiver' in system
        return ChatResponse(content=[TextBlock(text='UNKNOWN_WITH_GAPS')],is_last=True)
    monkeypatch.setattr(OpenAIChatModel,'_call_api',provider)
    async def run():
        model=OpenAIChatModel(OpenAICredential(api_key='fixture'),model='fixture')
        agent=Agent(name='existing-worker',system_prompt='保留我的角色',model=model,
                    toolkit=Toolkit(tools=[AnalysisReview()]),middlewares=[AnalysisQualityMiddleware()])
        response=await agent.reply(Msg(name='user',role='user',content=[TextBlock(text='Assess sample')]))
        assert response.get_text_content().endswith('UNKNOWN_WITH_GAPS')
        restored=AgentState.model_validate_json(agent.state.model_dump_json())
        # Emulate replacement of message history by compression; coverage lives
        # in native middle_context and must not depend on the old tool transcript.
        restored.context=[]
        other=Agent(name='restored',system_prompt='保留我的角色',model=model,state=restored,
                    toolkit=Toolkit(tools=[AnalysisReview()]),middlewares=[AnalysisQualityMiddleware()])
        prompt=await AnalysisQualityMiddleware().on_system_prompt(other,'保留我的角色')
        assert 'receiver' in prompt
        assert restored.middle_context[REVIEW_KEY]['a'*64]['review_passed'] is False
        await model.client.close()
    asyncio.run(run())


def test_old_policy_is_replaced_once_while_custom_role_and_following_instructions_survive():
    from types import SimpleNamespace
    from traceglass.analysis_policy import ANALYSIS_POLICY, POLICY_VERSION
    old = '[Traceglass analysis-quality policy 2026-10-09.1]\n旧质量指引\n'
    role = '保留我的角色\n' + old + '[Traceglass unified tool catalog]\n保留工具指引'
    agent = SimpleNamespace(state=AgentState())
    middleware = AnalysisQualityMiddleware()
    first = asyncio.run(middleware.on_system_prompt(agent, role))
    second = asyncio.run(middleware.on_system_prompt(agent, first))
    assert first == second
    assert '旧质量指引' not in first
    assert first.count('[Traceglass analysis-quality policy ') == 1
    assert POLICY_VERSION in first and ANALYSIS_POLICY.strip() in first
    assert '保留我的角色' in first and '保留工具指引' in first


def test_legacy_policy_without_outer_whitespace_keeps_user_suffix():
    from traceglass.analysis_policy import LEGACY_ANALYSIS_POLICY, upgrade_policy
    prompt = LEGACY_ANALYSIS_POLICY.strip() + '\nUSER CUSTOM INSTRUCTION: write reports in English.'
    upgraded = upgrade_policy(prompt, append=False)
    assert 'USER CUSTOM INSTRUCTION: write reports in English.' in upgraded


def test_legacy_dynamic_instruction_is_replaced_at_runtime_without_editing_custom_roles():
    from types import SimpleNamespace
    from traceglass.agents import INTEGRATED_PROMPT_V3, ANALYST_PROMPT, WORKER_PROMPT, VERIFIER_PROMPT
    middleware = AnalysisQualityMiddleware()
    agent = SimpleNamespace(state=AgentState())
    custom = INTEGRATED_PROMPT_V3 + '\nUSER CUSTOM ROLE SUFFIX'
    for template in (custom, ANALYST_PROMPT, WORKER_PROMPT, VERIFIER_PROMPT):
        prompt = asyncio.run(middleware.on_system_prompt(agent, template))
        assert '可在容器内进行用户授权的受控验证' not in prompt
        assert '无适用的授权动态通道时保留动态分支 blocked' in prompt
    assert custom.endswith('USER CUSTOM ROLE SUFFIX')
    assert 'USER CUSTOM ROLE SUFFIX' in asyncio.run(middleware.on_system_prompt(agent, custom))


def test_vulnerability_discovery_routing_upgrades_persisted_policy_without_losing_custom_role():
    from types import SimpleNamespace
    from traceglass.analysis_policy import upgrade_policy
    old = ('CUSTOM ROLE\n[Traceglass analysis-quality policy 2026-10-14.1]\n'
           '涉及漏洞定位/可利用性时读取 vulnerability-hunting。\n'
           '[End Traceglass analysis-quality policy]\nCUSTOM SUFFIX')
    upgraded = asyncio.run(AnalysisQualityMiddleware().on_system_prompt(
        SimpleNamespace(state=AgentState()), old))
    assert 'vuln-discovery' in upgraded and 'vulnerability-hunting' in upgraded
    assert 'CUSTOM ROLE' in upgraded and 'CUSTOM SUFFIX' in upgraded
    assert upgraded == upgrade_policy(upgraded)


def test_native_product_sends_short_archive_receipt_to_model_and_retains_full_history(tmp_path, monkeypatch):
    import httpx
    from agentscope.tool import FunctionTool, ToolChunk
    from traceglass.api.app import create_app
    from traceglass.config import Settings
    original = 'x' * 30_000 + 'ORIGINAL_ARCHIVE_TAIL'
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(key, raising=False)
    async def large_static():
        return ToolChunk(content=[TextBlock(text=original)])
    async def provider(self, model_name, messages, tools=None, **kwargs):
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        if not results:
            return ChatResponse(content=[ToolCallBlock(id='large', name='large_static', input='{}')], is_last=True)
        output = results[0].output[0].text
        assert len(output) < 4000 and 'ORIGINAL_ARCHIVE_TAIL' not in output
        assert 'ReadAnalysisEvidence' in output and 'tgev_' in output
        return ChatResponse(content=[TextBlock(text='RECEIPT_VISIBLE')], is_last=True)
    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)
    async def run():
        app = create_app(settings=Settings(data_dir=tmp_path, model_name='fixture', api_key='fixture'))
        previous = app.state.extra_agent_tools
        async def extra(*args, **kwargs):
            return [*await previous(*args, **kwargs), FunctionTool(large_static, name='large_static', is_read_only=True)]
        app.state.extra_agent_tools = extra
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                config = (await client.get('/product/config')).json()
                wid = (await client.post('/product/workspaces', json={'name': 'archive'})).json()['workspace_id']
                session = (await client.post(f'/product/workspaces/{wid}/sessions', json={
                    'chat_model_config': config['model_config']})).json()
                sid, aid = session['session_id'], session['agent_id']
                await app.state.chat_service.run('local', sid, aid,
                    Msg(name='user', role='user', content=[TextBlock(text='Read static fixture')]))
                messages, _ = await app.state.storage.list_messages('local', sid, limit=30)
                results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
                assert original in [b.text for b in results[0].output if isinstance(b, TextBlock)]
                receipt = results[0].metadata['traceglass_archive']['traceglass_evidence'][0]
                assert app.state.rea_evidence.get('local', wid, receipt['id'])['raw'] == original
    asyncio.run(run())
