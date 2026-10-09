import asyncio
from pathlib import Path
import sys

from agentscope.mcp import MCPClient, StdioMCPConfig

from traceglass.integrations.rea import ReaSessions


def test_prompts_use_live_member_session_and_do_not_execute_tools(tmp_path, monkeypatch):
    import traceglass.integrations.rea as integration
    fixture = Path(__file__).parents[1] / 'fixtures/rea_state_mcp.py'
    monkeypatch.setattr(integration, 'rea_client', lambda *args: MCPClient(name='rea', is_stateful=True,
        mcp_config=StdioMCPConfig(command=sys.executable, args=[str(fixture)])))

    async def run():
        pool = ReaSessions(tmp_path)
        try:
            tools = await pool.tools(tmp_path, 'alice', 'leader', 'workspace')
            assert hasattr(pool, 'workflow_tools'), 'REA workflow discovery is not exposed'
            workflows = {t.name: t for t in await pool.workflow_tools(tmp_path, 'alice', 'leader', 'workspace')}
            catalog = await workflows['ListReaWorkflows']()
            assert 'investigate_feature' in catalog.content[0].text
            prompt = await workflows['GetReaWorkflow'](name='investigate_feature', arguments={'feature': 'recv'})
            assert 'recv' in prompt.content[0].text and 'counter=0' in prompt.content[0].text
            assert (await tools[0]()).content[0].text == '1'
            prompt = await workflows['GetReaWorkflow'](name='investigate_feature', arguments={'feature': 'exec'})
            assert 'counter=1' in prompt.content[0].text
            child = {t.name: t for t in await pool.workflow_tools(tmp_path, 'alice', 'child', 'workspace')}
            assert 'counter=0' in (await child['GetReaWorkflow'](
                name='investigate_feature', arguments={'feature': 'verify'})).content[0].text
            invalid = await workflows['GetReaWorkflow'](name='investigate_feature', arguments={})
            assert invalid.state == 'error'
            unknown = await workflows['GetReaWorkflow'](name='not-a-workflow', arguments={})
            assert unknown.state == 'error'
        finally:
            await pool.close()
    asyncio.run(run())


def test_missing_prompt_capability_has_explicit_fallback():
    from traceglass.integrations.rea_workflows import ReaWorkflows
    class Session:
        def get_server_capabilities(self):
            from mcp.types import ServerCapabilities
            return ServerCapabilities()
    async def run():
        workflows = {t.name: t for t in ReaWorkflows(Session()).tools()}
        result = await workflows['ListReaWorkflows']()
        assert 'unsupported' in result.content[0].text
    asyncio.run(run())
