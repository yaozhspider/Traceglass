import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from traceglass.accounts.sandbox import AccountDockerWorkspace
from traceglass.api.app import create_app
from traceglass.config import Settings


def test_account_sandbox_creation_disables_network_and_limits_resources(tmp_path):
    container=SimpleNamespace(start=AsyncMock())
    create=AsyncMock(return_value=container)
    ws=AccountDockerWorkspace(host_workdir=str(tmp_path/'private'))
    ws._client=SimpleNamespace(containers=SimpleNamespace(create_or_replace=create))
    ws._image_tag='test-image'
    asyncio.run(ws._create_and_start_container())
    config=create.call_args.kwargs['config']
    host=config['HostConfig']
    assert host['NetworkMode']=='none'
    assert host['Binds']==[f'{tmp_path}/private:/workspace:rw']
    assert host['CapDrop']==['ALL']
    assert host['SecurityOpt']==['no-new-privileges:true']
    assert host['Memory']==4*1024**3 and host['PidsLimit']==512
    assert not host.get('PortBindings') and not host.get('Privileged')
    assert not config.get('Env')
    container.start.assert_awaited_once()


def test_shared_ida_is_not_available_through_legacy_bridge_in_account_mode(tmp_path):
    legacy=tmp_path/'legacy'
    (legacy/'skills').mkdir(parents=True)
    app=create_app(settings=Settings(data_dir=tmp_path/'data',workspace_backend='docker',auth_enabled=True,
                                    legacy_project=legacy,ida_mcp_url='http://host.docker.internal:13337/mcp'))
    clients=app.state.workspace_manager._default_mcps
    assert [client.name for client in clients]==['reverse']
    assert clients[0].mcp_config.env['IDA_ENABLED']=='false'
    assert clients[0].mcp_config.env['IDA_MCP_URL']==''
