import asyncio
import json

from agentscope.tool import MCPTool, Toolkit
from mcp.types import Tool

from traceglass.integrations.rea_groups import rea_tool_groups


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
