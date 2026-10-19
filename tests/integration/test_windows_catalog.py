"""The model discovers Windows schemas through the native catalog and ledger."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

from agentscope.agent import Agent
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.state import AgentState
from agentscope.tool import Toolkit, ToolResponse

from traceglass.evidence.store import EvidenceStore
from traceglass.integrations.evidence_ledger import EvidenceLedgerMiddleware
from traceglass.integrations.tool_catalog import AnalysisToolsMiddleware


def config(tmp_path):
    from traceglass.windows.config import WindowsConfig
    return WindowsConfig(host='192.0.2.5', vm_id='48', vm_name='yaozh', esxi_user='root',
        esxi_password='test-private', guest_user='YAOZH', guest_password='test-private',
        certificate=tmp_path / 'esxi.pem', allowed_users=('alice',),
        tools={'python': r'C:\Tools\Python312\python.exe'})


def test_windows_tools_are_opt_in_searchable_and_keep_native_schemas(tmp_path):
    from traceglass.windows.service import WindowsService
    from traceglass.windows.tools import windows_tools
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=SimpleNamespace(started=[]))
    assert windows_tools(service, 'bob', 'w', tmp_path) == []
    async def run():
        toolkit = Toolkit(tools=windows_tools(service, 'alice', 'w', tmp_path))
        agent = Agent(name='fixture', system_prompt='', model=None, toolkit=toolkit)
        async def check(**kwargs):
            search = await toolkit.get_tool('SearchAnalysisTools')
            result = json.loads((await search(query='WindowsDebug')).content[0].text)
            match = result['matches'][0]
            assert match['group'] == 'windows_analysis' and not match['active']
            assert service.transport.started == []  # discovery never touches VM
            async for _ in toolkit.call_tool(ToolCallBlock(id='activate', name='reset_tools',
                    input=json.dumps(match['reset_tools'])), agent.state):
                pass
            tool = await toolkit.check_tool_available('WindowsDebug', agent.state.tool_context.activated_groups)
            assert 'breakpoint' in tool.input_schema['properties']['action']['enum']
            assert 'password' not in json.dumps(tool.input_schema)
            capacity_match = json.loads((await search(query='WindowsVmStatus')).content[0].text)['matches'][0]
            assert capacity_match['group'] == 'windows_analysis'
            capacity_tool = await toolkit.check_tool_available('WindowsVmStatus', agent.state.tool_context.activated_groups)
            capacity = json.loads((await capacity_tool()).content[0].text)
            assert capacity_tool.is_read_only and capacity['occupied_slots'] == 0
            assert service.transport.started == []
            yield None
        async for _ in AnalysisToolsMiddleware().on_reply(agent, {}, check):
            pass
    asyncio.run(run())


def test_dynamic_policy_recognizes_only_the_current_accounts_registered_windows_route(tmp_path):
    from traceglass.analysis_policy import AnalysisQualityMiddleware
    from traceglass.windows.service import WindowsService
    from traceglass.windows.tools import windows_tools
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=SimpleNamespace())
    middleware = AnalysisQualityMiddleware()
    original = 'CUSTOM ROLE\n动态验证须走授权 sandbox MCP。\nCUSTOM SUFFIX'

    async def run():
        allowed = Agent(name='allowed', system_prompt='', model=None,
            toolkit=Toolkit(tools=windows_tools(service, 'alice', 'w', tmp_path)))
        prompt = await middleware.on_system_prompt(allowed, original)
        assert 'WindowsRunTool/WindowsDebug' in prompt
        assert 'WindowsCapabilities' in prompt and 'allow_execution' in prompt
        assert 'CUSTOM ROLE' in prompt and 'CUSTOM SUFFIX' in prompt
        assert '普通工作区 Bash 不能执行样本' in prompt
        assert await middleware.on_system_prompt(allowed, prompt) == prompt

        denied = Agent(name='denied', system_prompt='', model=None,
            toolkit=Toolkit(tools=windows_tools(service, 'bob', 'w', tmp_path)))
        for candidate in (original, prompt):
            restricted = await middleware.on_system_prompt(denied, candidate)
            assert 'WindowsRunTool/WindowsDebug' not in restricted
            assert '无适用的授权动态通道时保留动态分支 blocked' in restricted
    asyncio.run(run())


def test_persisted_windows_route_is_refreshed_or_removed_for_the_current_account(tmp_path):
    from traceglass.analysis_policy import AnalysisQualityMiddleware
    from traceglass.windows.service import WindowsService
    from traceglass.windows.tools import windows_tools
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=SimpleNamespace())
    original = ('CUSTOM ROLE\n[Traceglass Windows dynamic route]\n'
                'STALE ROUTE: reset automatically without collecting results\n'
                '[End Traceglass Windows dynamic route]\nCUSTOM SUFFIX')

    async def run():
        middleware = AnalysisQualityMiddleware()
        allowed = Agent(name='allowed', system_prompt='', model=None,
            toolkit=Toolkit(tools=windows_tools(service, 'alice', 'w', tmp_path)))
        denied = Agent(name='denied', system_prompt='', model=None,
            toolkit=Toolkit(tools=windows_tools(service, 'bob', 'w', tmp_path)))
        refreshed = await middleware.on_system_prompt(allowed, original)
        assert 'STALE ROUTE' not in refreshed
        assert refreshed.count('[Traceglass Windows dynamic route]') == 1
        assert 'CUSTOM ROLE' in refreshed and 'CUSTOM SUFFIX' in refreshed
        assert await middleware.on_system_prompt(allowed, refreshed) == refreshed
        restricted = await middleware.on_system_prompt(denied, refreshed)
        assert '[Traceglass Windows dynamic route]' not in restricted
        assert 'CUSTOM ROLE' in restricted and 'CUSTOM SUFFIX' in restricted

    asyncio.run(run())


def test_windows_receipts_bind_to_uploaded_hash_and_survive_sample_changes(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    sample = root / 'fixture.exe'
    sample.write_bytes(b'authored immutable snapshot')
    digest = hashlib.sha256(sample.read_bytes()).hexdigest()
    store = EvidenceStore(tmp_path / 'evidence.db')
    middleware = EvidenceLedgerMiddleware(store, 'alice', 'w', 's', 'a', workspace_root=root)
    agent = SimpleNamespace(state=AgentState())
    async def call(name, arguments, output, number):
        async def handler(**kwargs):
            yield ToolResponse(content=[TextBlock(text=json.dumps(output))])
        tool_call = ToolCallBlock(id=str(number), name=name, input=json.dumps(arguments))
        result = [r async for r in middleware.on_acting(agent, {'tool_call': tool_call}, handler)][0]
        receipt = result.metadata['traceglass_archive']['traceglass_evidence'][0]['id']
        return store.get('alice', 'w', receipt)
    async def run():
        first = await call('WindowsRunTool', {'sample_path': 'fixture.exe', 'profile': 'die'},
                           {'job_id': 'win_owned', 'sample_sha256': digest, 'state': 'running'}, 1)
        assert first['sample_sha256'] == digest
        sample.write_bytes(b'changed after upload')
        # Binding follows the verified immutable upload, not a later file hash.
        second = await call('WindowsJobStatus', {'job_id': 'win_owned'}, {'state': 'success'}, 2)
        assert second['sample_sha256'] == digest
        forged = await call('WindowsJobStatus', {'job_id': 'win_other'}, {'sample_sha256': digest}, 3)
        assert forged['sample_sha256'] is None
    asyncio.run(run())


def test_windows_setting_is_optional_and_composition_is_lazy(tmp_path, monkeypatch):
    from traceglass.api.app import create_app
    from traceglass.config import Settings
    from traceglass.windows.transport import VSphereTransport
    monkeypatch.setattr(VSphereTransport, 'probe', lambda *_: (_ for _ in ()).throw(AssertionError('not lazy')))
    app = create_app(settings=Settings(data_dir=tmp_path / 'disabled'))
    assert app.state.windows_service is None
    monkeypatch.setenv('TEST_ESXI_SECRET', 'test-private')
    monkeypatch.setenv('TEST_GUEST_SECRET', 'test-private')
    record = dict(host='192.0.2.5', vm_id='48', vm_name='yaozh', esxi_user='root',
                  esxi_password_env='TEST_ESXI_SECRET', guest_user='YAOZH', guest_password_env='TEST_GUEST_SECRET',
                  certificate='esxi.pem', allowed_users=['alice'], tools={'python': r'C:\Tools\Python312\python.exe'})
    path = tmp_path / 'windows.json'
    path.write_text(json.dumps(record))
    app = create_app(settings=Settings(data_dir=tmp_path / 'enabled', windows_config=path))
    assert app.state.windows_service.config.allowed('alice')
    monkeypatch.setenv('TRACEGLASS_WINDOWS_CONFIG', str(path))
    assert Settings.from_env().windows_config == path


def test_app_registers_windows_tools_for_owned_docker_workspace_without_starting_docker(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from traceglass.api.app import create_app
    from traceglass.config import Settings
    from traceglass.windows.config import WindowsConfig
    monkeypatch.setattr(WindowsConfig, 'load', lambda _: config(tmp_path))
    app = create_app(settings=Settings(data_dir=tmp_path, user_id='alice', workspace_backend='docker',
                                      windows_config=tmp_path / 'unused-config.json'))
    with TestClient(app) as client:
        workspace = client.post('/product/workspaces', json={'name': 'windows fixture'}).json()
        session = client.post(f"/product/workspaces/{workspace['workspace_id']}/sessions", json={}).json()
        tools = client.portal.call(app.state.extra_agent_tools, 'alice', session['agent_id'], session['session_id'])
        assert 'WindowsRunTool' in {t.name for t in tools}
        import pytest
        with pytest.raises(LookupError):
            client.portal.call(app.state.extra_agent_tools, 'bob', session['agent_id'], session['session_id'])


def test_windows_ledger_recovers_owned_job_binding_in_a_new_session(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'sample.exe').write_bytes(b'MZ immutable guest snapshot')
    class GuestFiles:
        def probe(self): return {'guest_operations_ready': True}
        def mkdir(self, *_): pass
        def put(self, *_): pass
        def start(self, *_): return 123
    service = WindowsService(config(tmp_path), tmp_path / 'jobs', transport=GuestFiles())
    job = service.submit('alice', 'w', root, 'sample.exe', 'die')
    (root / 'sample.exe').unlink()
    middleware = EvidenceLedgerMiddleware(EvidenceStore(tmp_path / 'ledger.db'), 'alice', 'w', 'new', 'a',
        workspace_root=root, windows_service=service)
    assert middleware._file_association('WindowsJobStatus', {'job_id': job['job_id']}, None) == job['sample_sha256']
    foreign = EvidenceLedgerMiddleware(middleware.store, 'alice', 'other', 'new', 'a',
        workspace_root=root, windows_service=service)
    assert foreign._file_association('WindowsJobStatus', {'job_id': job['job_id']}, None) is None
