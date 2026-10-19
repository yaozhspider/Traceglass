import asyncio
import json

import pytest
from agentscope.agent import Agent
from agentscope.exception import ToolGroupInactiveError
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.tool import FunctionTool, ToolChunk, Toolkit

from traceglass.integrations.rea_groups import ReaToolsMiddleware
from traceglass.integrations.tool_catalog import AnalysisToolsMiddleware


def tool(name):
    async def inspect(path: str) -> ToolChunk:
        """Inspect the given sample without executing it."""
        return ToolChunk(content=[TextBlock(text=path)])
    return FunctionTool(inspect, name=name, is_read_only=True)


@pytest.mark.parametrize('rea_connected', [True, False])
def test_default_tools_are_reduced_without_losing_fallbacks(rea_connected):
    async def run():
        identity = tool('mcp__reverse__hash_file')
        fallback = tool('mcp__reverse__decompile_function')
        dynamic = tool('mcp__reverse__query_dynamic_events')
        raw_strings = tool('mcp__reverse__extract_strings')
        toolkit = Toolkit(tools=[identity, identity, fallback, dynamic, raw_strings])
        agent = Agent(name='fixture', system_prompt='', model=None, toolkit=toolkit)
        rea = ReaToolsMiddleware([tool('mcp__rea__procedure_pseudo_code')] if rea_connected else [])
        catalog = AnalysisToolsMiddleware()

        async def check(**kwargs):
            initial = {s['function']['name'] for s in await toolkit.get_tool_schemas()}
            assert 'SearchAnalysisTools' in initial
            assert (fallback.name in initial) is not rea_connected
            assert dynamic.name not in initial
            assert identity.name in initial and raw_strings.name in initial
            search = await toolkit.get_tool('SearchAnalysisTools')
            results = json.loads((await search(query='反编译')).content[0].text)
            assert results['matches'][0]['name'] == (
                'mcp__rea__procedure_pseudo_code' if rea_connected else fallback.name)
            assert results['audit']['duplicate_references_removed'] == 1
            exact = json.loads((await search(query=fallback.name)).content[0].text)['matches'][0]
            assert exact['name'] == fallback.name
            assert exact['group'] == ('reverse_native' if rea_connected else None)
            assert await toolkit.get_tool(fallback.name) is fallback
            if rea_connected:
                agent.state.tool_context.activated_groups = ['reverse_dynamic']
                with pytest.raises(ToolGroupInactiveError) as error:
                    await toolkit.check_tool_available(fallback.name, ['reverse_dynamic'])
                payload = json.loads(error.value.message)
                assert payload['reset_tools'] == {'reverse_dynamic': True, 'reverse_native': True}
                activate = ToolCallBlock(id='activate', name='reset_tools', input=json.dumps(payload['reset_tools']))
                async for _ in toolkit.call_tool(activate, agent.state):
                    pass
                activated = json.loads((await search(query=fallback.name)).content[0].text)['matches'][0]
                assert activated['active'] is True
                assert await toolkit.check_tool_available(fallback.name, agent.state.tool_context.activated_groups) is fallback
                assert (await fallback(path='/workspace/samples/a')).content[0].text == '/workspace/samples/a'
            assert sum(t is identity for g in toolkit.tool_groups for t in g.tools) == 1
            assert (await search(limit=21)).state == 'error'
            yield None

        async def apply_catalog(**kwargs):
            async for event in catalog.on_reply(agent, kwargs, check):
                yield event

        async for _ in rea.on_reply(agent, {}, apply_catalog):
            pass
        # Applying the policy a second time must not create duplicate entries/groups.
        async def second(**kwargs):
            names = [g.name for g in toolkit.tool_groups]
            assert len(names) == len(set(names))
            entries = json.loads((await (await toolkit.get_tool('SearchAnalysisTools'))()).content[0].text)
            assert entries['audit']['duplicate_references_removed'] == 0
            yield None
        async for _ in catalog.on_reply(agent, {}, second):
            pass

    asyncio.run(run())


def test_mcp_tools_are_materialized_without_replacing_dispatch_or_claiming_failed_connections():
    class Client:
        is_stateful = False

        def __init__(self, name, tools, fail=False):
            self.name, self.tools, self.fail = name, tools, fail

        async def list_tools(self):
            if self.fail:
                raise RuntimeError('endpoint detail must not leak')
            return self.tools

    async def run():
        backend = tool('mcp__reverse__decompile_function')
        client = Client('reverse', [backend])
        unavailable = Client('idapro', [], fail=True)
        toolkit = Toolkit(mcps=[client, client, unavailable])
        agent = Agent(name='fixture', system_prompt='', model=None, toolkit=toolkit)

        async def check(**kwargs):
            basic = next(g for g in toolkit.tool_groups if g.name == 'basic')
            assert client not in basic.mcps
            assert unavailable in basic.mcps
            assert await toolkit.get_tool(backend.name) is backend
            data = json.loads((await (await toolkit.get_tool('SearchAnalysisTools'))()).content[0].text)
            assert data['unavailable_servers'] == ['idapro']
            assert 'endpoint detail' not in json.dumps(data)
            assert data['total_matches'] == 1
            yield None

        async for _ in AnalysisToolsMiddleware().on_reply(agent, {}, check):
            pass

    asyncio.run(run())


def test_distinct_implementations_with_the_same_name_are_reported_not_deleted():
    async def run():
        first = tool('inspect_file')
        second = tool('inspect_file')
        toolkit = Toolkit(tools=[first, second])
        agent = Agent(name='fixture', system_prompt='', model=None, toolkit=toolkit)

        async def check(**kwargs):
            result = json.loads((await (await toolkit.get_tool('SearchAnalysisTools'))()).content[0].text)
            assert result['audit']['name_conflicts'] == ['inspect_file']
            assert result['audit']['duplicate_references_removed'] == 0
            basic = next(g for g in toolkit.tool_groups if g.name == 'basic')
            assert first in basic.tools and second in basic.tools
            yield None

        async for _ in AnalysisToolsMiddleware().on_reply(agent, {}, check):
            pass

    asyncio.run(run())
