import json
from pathlib import Path

from traceglass.config import Settings, mcp_clients, skill_paths


def fixture_project(tmp_path: Path) -> Path:
    root = tmp_path / 'legacy'
    (root / 'config').mkdir(parents=True)
    (root / 'skills' / 'sample-skill').mkdir(parents=True)
    (root / 'skills' / 'sample-skill' / 'SKILL.md').write_text('---\nname: sample-skill\ndescription: sample\n---\nTest')
    (root / '.env').write_text('LLM_MODEL=legacy-model\nLLM_API_KEY=fixture-secret\nLLM_BASE_URL=https://model.invalid/v1\nIDA_ENABLED=true\nIDA_MCP_URL=http://host.docker.internal:8745/mcp\n')
    (root / 'config' / 'agents.yaml').write_text('model:\n  provider: openai_compatible\n  model_name: ignored\n  parameters:\n    thinking_enable: true\n    parallel_tool_calls: false\n')
    return root


def test_legacy_project_loads_model_skills_and_container_mcp_without_copying_secrets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = fixture_project(tmp_path)
    monkeypatch.setenv('TRACEGLASS_LEGACY_PROJECT', str(root))
    for name in ('LLM_MODEL', 'LLM_API_KEY', 'LLM_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert settings.model_name == 'legacy-model'
    assert settings.api_key == 'fixture-secret'
    assert settings.model_parameters['thinking_enable'] is True
    assert settings.workspace_backend == 'docker'
    assert str(root / 'skills' / 'sample-skill') in skill_paths(settings)
    clients = mcp_clients(settings)
    assert {c.name for c in clients} == {'reverse', 'idapro'}
    assert 'fixture-secret' not in repr(settings)
    assert 'fixture-secret' not in json.dumps([c.model_dump(mode='json') for c in clients])
    assert not (tmp_path / '.env').exists()


def test_explicit_environment_overrides_legacy_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = fixture_project(tmp_path)
    monkeypatch.setenv('TRACEGLASS_LEGACY_PROJECT', str(root))
    monkeypatch.setenv('LLM_MODEL', 'explicit-model')
    monkeypatch.setenv('LLM_API_KEY', 'explicit-secret')
    settings = Settings.from_env()
    assert settings.model_name == 'explicit-model'
    assert settings.api_key == 'explicit-secret'


def test_local_env_file_can_reference_legacy_project_without_containing_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = fixture_project(tmp_path)
    monkeypatch.delenv('TRACEGLASS_LEGACY_PROJECT', raising=False)
    for name in ('LLM_MODEL', 'LLM_API_KEY', 'LLM_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / '.env').write_text(f'TRACEGLASS_LEGACY_PROJECT={root}\n')
    assert Settings.from_env().model_configured
    assert 'fixture-secret' not in (tmp_path / '.env').read_text()
