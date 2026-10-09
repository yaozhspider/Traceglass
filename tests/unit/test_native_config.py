from pathlib import Path
import json

import pytest
from agentscope.mcp import MCPClient

from traceglass.config import Settings, mcp_clients, skill_paths


def test_duplicate_skill_directories_are_rejected_before_workspace_copy(tmp_path: Path):
    roots=[]
    for parent in ('one','two'):
        root=tmp_path/parent
        (root/'duplicate').mkdir(parents=True)
        (root/'duplicate'/'SKILL.md').write_text('---\nname: duplicate\ndescription: Test\n---\nInstructions')
        roots.append(root)
    with pytest.raises(ValueError,match='Duplicate Skill'):
        skill_paths(Settings(skill_roots=tuple(roots)))


def test_mcp_config_uses_native_schema_and_expands_env_without_connecting(tmp_path: Path,monkeypatch):
    config=tmp_path/'mcp.json'
    config.write_text(json.dumps([{
        'name':'fixture','is_stateful':False,
        'mcp_config':{'type':'http_mcp','url':'http://127.0.0.1:9/mcp','headers':{'Authorization':'Bearer ${FIXTURE_MCP_TOKEN}'}},
    }]))
    monkeypatch.setenv('FIXTURE_MCP_TOKEN','fixture-token')
    clients=mcp_clients(Settings(mcp_config_path=config))
    assert isinstance(clients[0],MCPClient)
    assert clients[0].mcp_config.headers['Authorization']=='Bearer fixture-token'
    monkeypatch.delenv('FIXTURE_MCP_TOKEN')
    with pytest.raises(ValueError,match='Missing MCP environment'):
        mcp_clients(Settings(mcp_config_path=config))


def test_settings_redact_key_and_validate_iteration_limit():
    assert 'fixture-secret' not in repr(Settings(api_key='fixture-secret'))
    with pytest.raises(ValueError,match='positive'):
        Settings(max_iters=0)


def test_core_analysis_skills_work_without_legacy_checkout():
    paths = {Path(p).name: Path(p) for p in skill_paths(Settings())}
    assert {'dynamic-malware-analysis', 'malware-analysis', 'crypto-obfuscation-analysis',
            'sandbox-dynamic-debugging', 'reverse-engineering', 'ghidra-reverse',
            'key-function-analysis', 'protocol-reverse', 'rea-analysis'} <= paths.keys()
    assert (paths['dynamic-malware-analysis'] / 'scripts/dynamic_report.py').is_file()
    assert (paths['crypto-obfuscation-analysis'] / 'references/semantic-ir-schema.md').is_file()


def test_explicit_skill_root_overrides_bundled_fallback(tmp_path):
    custom = tmp_path / 'malware-analysis'
    custom.mkdir()
    (custom / 'SKILL.md').write_text('---\nname: malware-analysis\ndescription: User version\n---\nCustom')
    paths = [Path(p) for p in skill_paths(Settings(skill_roots=(tmp_path,)))]
    assert [p for p in paths if p.name == 'malware-analysis'] == [custom]
