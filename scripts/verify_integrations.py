"""Exercise native sandbox + real domain MCP + Skills without an external model call."""
import asyncio
import hashlib
import json
from pathlib import Path
import uuid

from agentscope.app.workspace_manager import DockerWorkspaceManager, IsolationPolicy
from traceglass.config import Settings, mcp_clients, materialize_skill_paths


async def main():
    settings = Settings.from_env()
    if settings.workspace_backend != 'docker' or not settings.legacy_project:
        raise SystemExit('Configure TRACEGLASS_LEGACY_PROJECT and the Docker workspace first')
    result = {}
    # A workspace keeps its gateway process and MCP configuration for its
    # lifetime. Reusing a fixed id after changing IDA/MCP settings can make
    # this verifier exercise a stale gateway (or return a 404 for an old exec
    # instance), which looks like a broken integration. Give every verification
    # run a fresh native workspace so the result reflects the current config.
    workspace_id = f"traceglass-integration-smoke-{uuid.uuid4().hex[:12]}"
    async with DockerWorkspaceManager(
        basedir=str(settings.data_dir / 'docker-workspaces'), isolation=IsolationPolicy.PER_SESSION,
        base_image=settings.sandbox_image, extra_pip=['agentscope==2.0.6'],
        default_mcps=mcp_clients(settings), skill_paths=materialize_skill_paths(settings, settings.data_dir / 'skill-cache'),
    ) as manager:
        workspace = await manager.get_workspace(
            'local', 'traceglass-analyst', workspace_id, workspace_id=workspace_id,
        )
        backend = workspace.get_backend()
        probe = await backend.exec_shell(['sh', '-c', 'uname -s; test ! -S /var/run/docker.sock; mkdir -p /workspace/samples; cp /bin/true /workspace/samples/traceglass-smoke.elf'], timeout=30)
        assert probe.exit_code == 0 and b'Linux' in probe.stdout
        sample = await backend.read_file('/workspace/samples/traceglass-smoke.elf')
        digest = hashlib.sha256(sample).hexdigest()
        result['sandbox'] = {'backend':'agentscope-docker','verified':True,'fixture_sha256':digest}
        skills = await workspace.list_skills()
        result['skills'] = {'count':len(skills),'names':[s.name for s in skills]}
        clients = {client.name:client for client in await workspace.list_mcps()}
        reverse = clients['reverse']
        tools = await reverse.list_tools()
        result['reverse'] = {'tool_count':len(tools),'names':[t.name for t in tools]}
        # Native MCP wrappers expose a namespaced ToolBase; keep their real names.
        async def call(name, **arguments):
            tool = next(t for t in tools if t.name.endswith(name))
            response = await tool(**arguments)
            payload = response.model_dump(mode='json')
            assert payload.get('state') != 'error', payload
            return json.dumps(payload, ensure_ascii=False)
        assert digest in await call('hash_file', sample_path='traceglass-smoke.elf')
        assert 'ELF' in await call('identify_file', sample_path='traceglass-smoke.elf')
        metadata = await call('binary_metadata', sample_path='traceglass-smoke.elf')
        assert 'elf' in metadata.lower()
        resource = await call('read_skill_resource', skill_name='ida-reverse', relative_path='../tool-index.md', limit=500)
        assert len(resource) > 100
        result['reverse']['verified_calls'] = ['hash_file','identify_file','binary_metadata','read_skill_resource']
        if 'idapro' in clients:
            ida_tools = await clients['idapro'].list_tools()
            result['ida'] = {'connected':True,'tool_count':len(ida_tools)}
    target = settings.data_dir / 'integration-verification.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
