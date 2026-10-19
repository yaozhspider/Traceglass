"""Regression coverage for Skill selection and portable analysis entry points."""
from pathlib import Path

import pytest

from traceglass.config import Settings, materialize_skill_paths, skill_paths


def package(root: Path, name: str, body: str = 'User instructions') -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: Fixture\n---\n{body}', encoding='utf-8',
    )
    return directory


def test_automatic_legacy_import_cannot_shadow_project_analysis_contract(tmp_path):
    legacy = tmp_path / 'legacy'
    inherited = package(legacy / 'skills', 'malware-analysis', 'Old deployment contract')
    extra = package(legacy / 'skills', 'platform-fixture')
    settings = Settings(legacy_project=legacy, workspace_backend='docker')
    selected = {Path(p).name: Path(p) for p in skill_paths(settings)}
    assert selected['malware-analysis'] != inherited
    assert selected['malware-analysis'].is_relative_to(
        Path(__file__).parents[2] / 'src/traceglass/resources/skills')
    assert selected['platform-fixture'] == extra


def test_explicit_user_package_can_override_automatic_legacy_import(tmp_path):
    legacy = tmp_path / 'legacy'
    package(legacy / 'skills', 'platform-fixture', 'Inherited')
    custom = package(tmp_path / 'personal', 'platform-fixture', 'Personal version')
    settings = Settings(legacy_project=legacy, workspace_backend='docker',
                        skill_roots=(tmp_path / 'personal',))
    selected = {Path(p).name: Path(p) for p in skill_paths(settings)}
    assert selected['platform-fixture'] == custom


def test_explicit_legacy_root_can_reapply_user_selected_upstream_package(tmp_path):
    legacy = tmp_path / 'legacy'
    upstream = package(legacy / 'skills', 'malware-analysis', 'Explicit upstream choice')
    settings = Settings(legacy_project=legacy, workspace_backend='docker',
                        skill_roots=(legacy / 'skills',))
    selected = {Path(p).name: Path(p) for p in skill_paths(settings)}
    assert selected['malware-analysis'] == upstream
    from traceglass.integrations.skill_inventory import inspect_skills
    row = next(p for p in inspect_skills(settings)['packages'] if p['name'] == 'malware-analysis')
    assert row['origin'] == 'explicit'


def test_ambiguous_directories_inside_inherited_root_are_rejected(tmp_path):
    legacy = tmp_path / 'legacy'
    package(legacy / 'skills/one', 'same-package')
    package(legacy / 'skills/two', 'same-package')
    with pytest.raises(ValueError, match='Duplicate Skill package'):
        skill_paths(Settings(legacy_project=legacy, workspace_backend='docker'))


def test_browser_and_javascript_guidance_is_portable_without_legacy_checkout(tmp_path):
    selected = {Path(p).name: Path(p) for p in
                materialize_skill_paths(Settings(), tmp_path / 'staging')}
    for name, reference in (
        ('browser-extension-reverse', 'references/extension-analysis.md'),
        ('js-reverse', 'references/ast-deobfuscation.md'),
        ('docs-generator', 'references/security-report-templates.md'),
    ):
        assert name in selected, f'{name} needs a project-owned entry point'
        assert (selected[name] / reference).is_file()
    assert (selected['traceglass-analysis'] / 'references/task-routing.md').is_file()
    assert (selected['traceglass-analysis'] / 'references/evidence-contract.md').is_file()


def test_inventory_detects_changed_reference_without_mutating_workspace(tmp_path):
    import shutil
    from traceglass.integrations.skill_inventory import inspect_skills

    source = package(tmp_path / 'personal', 'personal-fixture')
    (source / 'reference.md').write_text('Original reference', encoding='utf-8')
    installed = tmp_path / 'workspace/skills/personal-fixture'
    shutil.copytree(source, installed)
    settings = Settings(skill_roots=(tmp_path / 'personal',))
    before = inspect_skills(settings, workspace_root=tmp_path / 'workspace')
    row = next(p for p in before['packages'] if p['name'] == 'personal-fixture')
    assert row['origin'] == 'explicit'
    assert row['workspace_status'] == 'matches_source'
    (installed / 'reference.md').write_text('User annotation', encoding='utf-8')
    changed = inspect_skills(settings, workspace_root=tmp_path / 'workspace')
    row = next(p for p in changed['packages'] if p['name'] == 'personal-fixture')
    assert row['workspace_status'] == 'differs_from_source'
    assert (installed / 'reference.md').read_text() == 'User annotation'
    assert (source / 'reference.md').read_text() == 'Original reference'
    missing = next(p for p in changed['packages'] if p['name'] == 'rea-analysis')
    assert missing['workspace_status'] == 'not_installed'


def test_inventory_matches_native_nested_package_layout(tmp_path):
    from traceglass.integrations.skill_inventory import inspect_skills
    import shutil

    paths = materialize_skill_paths(Settings(), tmp_path / 'staging')
    workspace = tmp_path / 'workspace'
    for path in map(Path, paths):
        shutil.copytree(path, workspace / 'skills' / path.name)
    result = inspect_skills(Settings(), workspace_root=workspace)
    assert all(p['workspace_status'] == 'matches_source' for p in result['packages'])


@pytest.mark.parametrize('name', ['js-reverse', 'browser-extension-reverse', 'docs-generator'])
def test_project_adapters_load_through_native_skill_viewer(tmp_path, name):
    import asyncio
    from agentscope.state import AgentState
    from agentscope.tool import Toolkit
    from agentscope.workspace import LocalWorkspace

    async def run():
        source = next(Path(p) for p in skill_paths(Settings()) if Path(p).name == name)
        workspace = LocalWorkspace(workspace_id=name, workdir=str(tmp_path / 'workspace'),
                                   skill_paths=[str(source)])
        await workspace.initialize()
        try:
            skills = await workspace.list_skills()
            assert [s.name for s in skills] == [name]
            toolkit = Toolkit(skills_or_loaders=skills)
            result = await toolkit.builtin_skill_viewer.tool.call(name, AgentState())
            assert result.content[0].text == skills[0].markdown
            for resource in source.rglob('*'):
                if resource.is_file():
                    installed = tmp_path / 'workspace/skills' / name / resource.relative_to(source)
                    assert installed.read_bytes() == resource.read_bytes()
        finally:
            await workspace.close()

    asyncio.run(run())
