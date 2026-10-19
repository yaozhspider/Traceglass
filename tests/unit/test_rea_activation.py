"""Recovery from an inactive REA call through the native Agent execution loop."""
import asyncio
import json

import pytest
from agentscope.agent import Agent, ReActConfig
from agentscope.credential import OpenAICredential
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse, OpenAIChatModel
from agentscope.permission import PermissionBehavior, PermissionRule
from agentscope.tool import FunctionTool, ToolChunk, ToolGroup, Toolkit

from traceglass.integrations.rea_groups import ReaToolsMiddleware
from traceglass.integrations.tool_catalog import AnalysisToolsMiddleware


@pytest.mark.parametrize('deny', [False, True])
@pytest.mark.parametrize('tool_name,group_name', [
    ('mcp__rea__inspect_managed_artifact', 'rea_formats'),
    ('mcp__reverse__query_dynamic_events', 'reverse_dynamic'),
])
def test_inactive_tool_recovery_preserves_groups_validation_and_permissions(monkeypatch, deny, tool_name, group_name):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    calls = []

    async def inspect(path: str) -> ToolChunk:
        """Inspect a managed sample at the required path."""
        calls.append(path)
        return ToolChunk(content=[TextBlock(text='managed sample inspected')])

    tool = FunctionTool(inspect, name=tool_name, is_read_only=True)
    is_rea = tool_name.startswith('mcp__rea__')
    toolkit = Toolkit(tools=[] if is_rea else [tool],
        tool_groups=[ToolGroup(name='other_analysis', description='Other analysis', tools=[])])
    original_check = toolkit.check_tool_available
    observed = []

    async def provider(self, model_name, messages, tools=None, **kwargs):
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        names = {t['function']['name'] for t in tools}
        if not results:
            assert tool.name not in names
            return ChatResponse(content=[ToolCallBlock(id='premature', name=tool.name, input='{}')], is_last=True)
        if len(results) == 1:
            # Native ToolNotFoundError is misleading here: this tool IS connected.
            error = json.loads(results[-1].output)
            assert error['error'] == 'tool_group_inactive'
            assert error['required_group'] == group_name
            assert error['reset_tools'] == {'other_analysis': True, group_name: True}
            assert calls == []
            return ChatResponse(content=[ToolCallBlock(id='activate', name='reset_tools',
                input=json.dumps(error['reset_tools']))], is_last=True)
        if len(results) == 2:
            assert tool.name in names
            return ChatResponse(content=[ToolCallBlock(id='missing-path', name=tool.name, input='{}')], is_last=True)
        if len(results) == 3:
            assert results[-1].state == 'error'
            assert 'path' in results[-1].output
            assert calls == []
            return ChatResponse(content=[ToolCallBlock(id='valid', name=tool.name,
                input='{"path":"/workspace/samples/test.dll"}')], is_last=True)
        observed.extend(results)
        return ChatResponse(content=[TextBlock(text='complete')], is_last=True)

    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)

    async def run():
        model = OpenAIChatModel(credential=OpenAICredential(api_key='fixture'), model='fixture')
        agent = Agent(name='analyst', system_prompt='', model=model, toolkit=toolkit,
            middlewares=[ReaToolsMiddleware([tool] if is_rea else []), AnalysisToolsMiddleware()],
            react_config=ReActConfig(max_iters=8))
        agent.state.tool_context.activated_groups = ['other_analysis']
        if deny:
            agent.state.permission_context.deny_rules = {tool.name: [PermissionRule(
                tool_name=tool.name, rule_content=None, behavior=PermissionBehavior.DENY, source='userSettings')]}
        await agent.reply(Msg(name='user', role='user', content=[TextBlock(text='Inspect sample')]))
        assert len(observed) == 4
        assert observed[-1].state == ('denied' if deny else 'success')
        assert calls == ([] if deny else ['/workspace/samples/test.dll'])
        assert set(agent.state.tool_context.activated_groups) == {'other_analysis', group_name}
        assert toolkit.check_tool_available == original_check

    asyncio.run(run())


def test_unknown_rea_tool_has_search_hint_and_other_tool_errors_are_unchanged():
    from agentscope.exception import ToolNotFoundError

    async def run():
        toolkit = Toolkit()
        agent = Agent(name='fixture', system_prompt='', model=None, toolkit=toolkit)
        async def inspect(path: str) -> ToolChunk:
            return ToolChunk(content=[TextBlock(text=path)])
        middleware = ReaToolsMiddleware([
            FunctionTool(inspect, name='mcp__rea__inspect_managed_members', is_read_only=True)])
        original_check = toolkit.check_tool_available

        async def next_handler(**kwargs):
            with pytest.raises(ToolNotFoundError) as unknown:
                await toolkit.check_tool_available('mcp__rea__invented', [])
            payload = json.loads(unknown.value.message)
            assert payload['error'] == 'tool_not_found'
            assert 'SearchAnalysisTools' in payload['next_step']
            assert 'reset_tools' not in payload
            with pytest.raises(ToolNotFoundError, match="The tool named 'unrelated' doesn't exist"):
                await toolkit.check_tool_available('unrelated', [])
            raise RuntimeError('reply interrupted')
            yield

        with pytest.raises(RuntimeError, match='reply interrupted'):
            async for _ in middleware.on_reply(agent, {}, next_handler):
                pass
        assert toolkit.check_tool_available == original_check

    asyncio.run(run())
