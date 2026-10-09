import asyncio
import json
from pathlib import Path

import pytest


@pytest.fixture
def project():
    root = Path(__file__).resolve().parents[3] / 'reverse'
    if not (root / 'src/re_agents/tools.py').is_file():
        pytest.skip('Legacy checkout is not installed')
    return root


def test_mcp_exposes_real_legacy_tools_and_preserves_skill_resources(project, tmp_path):
    from traceglass.integrations.legacy_mcp import build_server
    server = build_server(project, tmp_path)

    async def exercise():
        tools = await server.list_tools()
        names = {tool.name for tool in tools}
        assert {'hash_file', 'reverse_tool_call', 'read_skill_resource', 'record_crypto_experiment', 'save_analysis_artifact'} <= names
        (tmp_path / 'samples' / 'fixture.bin').write_bytes(b'\x00https://example.test/evidence\x00')
        result = await server.call_tool('extract_ioc_candidates', {'sample_path':'fixture.bin'})
        content = result[0] if isinstance(result, tuple) else result
        assert 'https://example.test/evidence' in content[0].text
        result = await server.call_tool('read_skill_resource', {'skill_name':'ida-reverse', 'relative_path':'../tool-index.md', 'limit':500})
        content = result[0] if isinstance(result, tuple) else result
        assert len(content[0].text) > 100
        assert len(names) == 41
    asyncio.run(exercise())


def test_legacy_archive_writes_only_new_workspace(project, tmp_path):
    from traceglass.integrations.legacy_mcp import build_server
    server = build_server(project, tmp_path)

    async def exercise():
        await server.call_tool('save_analysis_artifact', {'artifact_path':'artifacts/evidence.txt','content':'verified fixture'})
        assert (tmp_path / 'artifacts/cases/default/artifacts/evidence.txt').read_text() == 'verified fixture'
        with pytest.raises(Exception, match='relative|outside|escape'):
            await server.call_tool('save_analysis_artifact', {'artifact_path':'../../outside.txt','content':'reject'})
    asyncio.run(exercise())
