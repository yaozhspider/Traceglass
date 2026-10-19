import asyncio
import json

import pytest

from agentscope.tool import MCPTool, Toolkit
from mcp.types import Tool

from traceglass.integrations.rea_groups import rea_tool_groups


@pytest.mark.parametrize('reason', ['completed', 'interrupted', 'error', 'exceed_max_iters'])
def test_reply_completion_releases_own_rea_connection_only_for_normal_finish(tmp_path, monkeypatch, reason):
    from agentscope.agent import Agent
    from agentscope.event import ReplyEndEvent
    from agentscope.mcp import MCPClient, StdioMCPConfig
    from traceglass.integrations.rea import ReaSessions
    from traceglass.integrations.rea_groups import ReaToolsMiddleware
    from pathlib import Path
    import sys
    import traceglass.integrations.rea as integration
    fixture = Path(__file__).parents[1] / 'fixtures/rea_state_mcp.py'
    monkeypatch.setattr(integration, 'rea_client', lambda *args: MCPClient(name='rea', is_stateful=True,
        mcp_config=StdioMCPConfig(command=sys.executable, args=[str(fixture)])))
    async def run():
        pool = ReaSessions(tmp_path)
        try:
            tools = await pool.tools(tmp_path, 'alice', 's', 'w')
            agent = Agent(name='fixture', system_prompt='', model=None, toolkit=Toolkit())
            middleware = ReaToolsMiddleware(tools,
                release_on_complete=lambda: pool.release(tmp_path, 'alice', 's'))
            async def next_handler(**kwargs):
                yield ReplyEndEvent(session_id='s', reply_id='r', finished_reason=reason)
            async for _ in middleware.on_reply(agent, {}, next_handler):
                pass
            assert pool.status('w')[0]['active_connections'] == (0 if reason == 'completed' else 1)
        finally:
            await pool.close()
    asyncio.run(run())


def test_large_mcp_contract_is_preserved_but_excluded_until_native_group_activation():
    def tool(name, description):
        return MCPTool('rea', Tool(name=name, description=description,
            inputSchema={'type': 'object', 'properties': {
                'payload': {'type': 'string', 'description': description}}}), session=object())

    core = tool('binary_session', 'Current binary state')
    large = tool('compare_web_captures', 'Long provider contract. ' * 15000)
    extra = tool('inspect_android_class', 'Read an Android class')
    basic, groups = rea_tool_groups([core, large, extra])
    toolkit = Toolkit(tools=basic, tool_groups=groups)

    async def run():
        initial = await toolkit.get_tool_schemas()
        assert len(json.dumps(initial)) < 10000
        assert {t['function']['name'] for t in initial} >= {'mcp__rea__binary_session', 'reset_tools'}
        assert 'mcp__rea__compare_web_captures' not in {t['function']['name'] for t in initial}
        active = await toolkit.get_tool_schemas(['rea_compare_web_captures'])
        assert 'mcp__rea__compare_web_captures' in {t['function']['name'] for t in active}
        assert 'mcp__rea__inspect_android_class' not in {t['function']['name'] for t in active}
        assert await toolkit.get_tool(large.name) is large
        assert {t.name for g in toolkit.tool_groups for t in g.tools} == {core.name, large.name, extra.name}
    asyncio.run(run())


def test_discovery_browses_all_connected_tools_with_bounded_pages():
    from traceglass.integrations.rea_discovery import ReaToolDiscovery
    tools = [MCPTool('rea', Tool(name=f'inspect_artifact_{i}', description='Artifact inspection ' * 1000,
        inputSchema={'type': 'object'}), session=object()) for i in range(31)]
    core, groups = rea_tool_groups(tools)
    discovery = ReaToolDiscovery(core, groups)

    async def run():
        seen, offset = set(), 0
        while offset is not None:
            page = json.loads((await discovery.search(offset=offset)).content[0].text)
            assert len(page['matches']) <= 8
            assert all(len(e['description']) <= 600 for e in page['matches'])
            seen.update(e['name'] for e in page['matches'])
            offset = page['next_offset']
        assert seen == {t.name for t in tools}
        assert (await discovery.search(limit=21)).state == 'error'
        assert (await discovery.search(offset=-1)).state == 'error'
        assert (await discovery.search(query='x' * 201)).state == 'error'
    asyncio.run(run())


def test_discovery_returns_real_activation_without_loading_large_schema():
    from traceglass.integrations.rea_groups import ReaToolsMiddleware
    from agentscope.agent import Agent

    large = MCPTool('rea', Tool(name='trace_native_values', description='Trace values from source to sink.',
        inputSchema={'type': 'object', 'properties': {'contract': {
            'type': 'string', 'description': 'provider details ' * 2000}}}), session=object())
    middleware = ReaToolsMiddleware([large])
    agent = Agent(name='fixture', system_prompt='', model=None, toolkit=Toolkit())

    async def run():
        async def next_handler(**kwargs):
            yield None
        async for _ in middleware.on_reply(agent, {}, next_handler):
            pass
        schemas = await agent.toolkit.get_tool_schemas()
        names = {s['function']['name'] for s in schemas}
        assert 'SearchReaTools' in names
        assert large.name not in names
        search = await agent.toolkit.get_tool('SearchReaTools')
        result = json.loads((await search(query='trace_native_values')).content[0].text)
        match = result['matches'][0]
        assert match['name'] == large.name
        assert match['reset_tools'] == {'rea_trace_native_values': True}
        assert 'provider details' not in json.dumps(result)
        assert len(json.dumps(result)) < 6000
        active = await agent.toolkit.get_tool_schemas(list(match['reset_tools']))
        assert large.name in {s['function']['name'] for s in active}
        assert await agent.toolkit.get_tool(large.name) is large
        assert json.loads((await search(query='not_a_real_tool')).content[0].text)['matches'] == []
    asyncio.run(run())


def test_runtime_routing_updates_old_prompts_and_handles_missing_rea():
    from traceglass.integrations.rea_groups import ReaToolsMiddleware
    old = '用户定制约束：仅静态分析。\n连接了 reverse MCP 时优先使用其原有分析工具，并以工具列表中的真实名称和schema为准。'
    tool = MCPTool('rea', Tool(name='binary_session', inputSchema={'type': 'object'}), session=object())

    async def run():
        prompt = await ReaToolsMiddleware([tool]).on_system_prompt(None, old)
        assert '优先使用其原有分析工具' not in prompt
        assert '二进制语义分析优先使用 REA' in prompt
        assert '用户定制约束：仅静态分析。' in prompt
        missing = await ReaToolsMiddleware([]).on_system_prompt(None, old)
        assert '本次会话未连接 REA' in missing
        assert 'use ListReaWorkflows' not in missing
    asyncio.run(run())
