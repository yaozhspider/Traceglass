from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest

from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig
from agentscope.workspace import LocalWorkspace

from traceglass.integrations.optional_mcp import OptionalMCPs
from traceglass.workspaces.manager import ProductLocalWorkspaceManager


def ida() -> MCPClient:
    return MCPClient(name="idapro", is_stateful=False,
                     mcp_config=HttpMCPConfig(url="http://127.0.0.1:9/mcp", timeout=0.1))


def test_optional_mcp_is_removed_from_startup_but_retained_for_retry(tmp_path: Path):
    reverse = MCPClient(name="reverse", is_stateful=True,
                        mcp_config=StdioMCPConfig(command="python3", args=["server.py"]))
    (tmp_path / ".mcp").write_text(json.dumps([reverse.model_dump(mode="json"), ida().model_dump(mode="json")]))
    support = OptionalMCPs([ida()])
    optional = support.prepare(tmp_path)
    assert [item["name"] for item in json.loads((tmp_path / ".mcp").read_text())] == ["reverse"]
    assert [client.name for client in optional] == ["idapro"]
    # Deployment restart must preserve the optional endpoint without re-seeding
    # it into the native gateway's all-or-nothing startup file.
    restarted = OptionalMCPs([])
    assert [client.name for client in restarted.prepare(tmp_path)] == ["idapro"]


def test_unreachable_optional_http_mcp_does_not_block_native_local_workspace(tmp_path: Path):
    async def run():
        manager = ProductLocalWorkspaceManager(basedir=str(tmp_path), default_mcps=[ida()])
        try:
            workspace = await manager.get_workspace("owner", "analyst", "session", "tgws-test")
            assert isinstance(workspace, LocalWorkspace) and workspace.is_alive
            assert all(client.name != "idapro" for client in await workspace.list_mcps())
            status = manager.integration_status("tgws-test")
            assert status[0]["name"] == "idapro" and status[0]["status"] == "unavailable"
            assert "idapro" in workspace.instructions and "unavailable" in workspace.instructions
            result = await workspace.get_backend().exec_shell(["python3", "-c", "print('STATIC_WORKSPACE_OK')"])
            assert result.ok() and b"STATIC_WORKSPACE_OK" in result.stdout
        finally:
            await manager.close_all()
    asyncio.run(run())


def test_failed_optional_registration_keeps_required_clients_and_redacts_error():
    class Workspace:
        workspace_id = "test-workspace"
        instructions = "Native tools are available."
        async def list_mcps(self):
            return []
        async def add_mcp(self, client):
            raise RuntimeError("http://user:secret@private.invalid/?token=hidden")

    async def run():
        support = OptionalMCPs([ida()])
        await support.connect(Workspace(), [ida()], "test-workspace")
        result = support.status("test-workspace")
        assert result[0]["status"] == "unavailable"
        assert "secret" not in json.dumps(result) and "token=" not in json.dumps(result)
    asyncio.run(run())


@pytest.mark.parametrize("filename", [".mcp", ".traceglass-optional-mcps.json"])
def test_optional_config_migration_refuses_symlinks(tmp_path: Path, filename: str):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("[]")
    (root / filename).symlink_to(outside)
    with pytest.raises(ValueError, match="regular"):
        OptionalMCPs([ida()]).prepare(root)
    assert outside.read_text() == "[]"
