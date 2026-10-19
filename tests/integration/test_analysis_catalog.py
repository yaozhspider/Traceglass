"""Grouping preserves the real MCP connection and native invocation contract."""
import asyncio
import json
import sys

from agentscope.agent import Agent
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.message import ToolCallBlock
from agentscope.tool import Toolkit

from traceglass.integrations.tool_catalog import AnalysisToolsMiddleware


def test_search_activate_and_execute_over_real_mcp_transport(tmp_path):
    fixture = tmp_path / 'catalog_mcp.py'
    fixture.write_text('''from mcp.server.fastmcp import FastMCP
server = FastMCP("catalog fixture")
@server.tool()
def query_dynamic_events(report_path: str) -> str:
    return "fixture report: " + report_path
server.run(transport="stdio")
''')

    async def run():
        client = MCPClient(name='reverse', is_stateful=True,
            mcp_config=StdioMCPConfig(command=sys.executable, args=[str(fixture)]))
        await client.connect()
        try:
            toolkit = Toolkit(mcps=[client])
            agent = Agent(name='fixture', system_prompt='', model=None, toolkit=toolkit)

            async def check(**kwargs):
                search = await toolkit.get_tool('SearchAnalysisTools')
                match = json.loads((await search(query='query_dynamic_events')).content[0].text)['matches'][0]
                assert match['server'] == 'reverse'
                assert match['group'] == 'reverse_dynamic'
                assert match['active'] is False
                assert 'mcp__reverse__query_dynamic_events' not in {
                    t['function']['name'] for t in await toolkit.get_tool_schemas()}
                async for _ in toolkit.call_tool(ToolCallBlock(id='activate', name='reset_tools',
                        input=json.dumps(match['reset_tools'])), agent.state):
                    pass
                function = await toolkit.check_tool_available(match['name'], agent.state.tool_context.activated_groups)
                assert function.input_schema['required'] == ['report_path']
                chunks = [c async for c in toolkit.call_tool(ToolCallBlock(id='query', name=match['name'],
                    input='{"report_path":"reports/fixture.json"}'), agent.state)]
                assert chunks[-1].state == 'success'
                assert chunks[-1].content[0].text == 'fixture report: reports/fixture.json'
                assert client.is_connected
                yield None

            async for _ in AnalysisToolsMiddleware().on_reply(agent, {}, check):
                pass
        finally:
            await client.close()

    asyncio.run(run())
