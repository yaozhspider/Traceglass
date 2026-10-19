import asyncio
from pathlib import Path

import pytest

from agentscope.workspace import LocalWorkspace
from traceglass.integrations.skill_sync import sync_missing_skills


def test_existing_workspace_receives_missing_skill_without_overwriting_user_version(tmp_path):
    async def run():
        root = tmp_path/'workspace'
        root.mkdir()
        original = tmp_path/'original'
        original.mkdir()
        (original/'SKILL.md').write_text('---\nname: known\ndescription: User-owned version\n---\nKeep this edit')
        updated = tmp_path/'updated'
        updated.mkdir()
        (updated/'SKILL.md').write_text('---\nname: known\ndescription: Different package\n---\nDo not overwrite')
        addition = tmp_path/'addition'
        addition.mkdir()
        (addition/'SKILL.md').write_text('---\nname: newly-configured\ndescription: New Skill\n---\nNew workflow')
        (addition/'reference.md').write_text('Reference copied with the Skill')
        workspace = LocalWorkspace(workspace_id='fixture',workdir=str(root),skill_paths=[str(original)])
        await workspace.initialize()
        try:
            await sync_missing_skills(workspace,[str(updated),str(addition)])
            await sync_missing_skills(workspace,[str(updated),str(addition)])
            skills = await workspace.list_skills()
            assert sorted(s.name for s in skills) == ['known','newly-configured']
            assert (root/'skills/known/SKILL.md').read_text().endswith('Keep this edit')
            assert (root/'skills/newly-configured/reference.md').is_file()
        finally:
            await workspace.close()
    asyncio.run(run())


@pytest.mark.parametrize('name', ['unpacking', 'vuln-discovery'])
@pytest.mark.parametrize('existing', [False, True], ids=['new-workspace', 'existing-workspace'])
def test_bundled_skill_includes_offline_references_and_preserves_edits(tmp_path, name, existing):
    from agentscope.state import AgentState
    from agentscope.tool import Toolkit
    from traceglass.config import Settings, materialize_skill_paths

    async def run():
        root = tmp_path / 'workspace'
        root.mkdir()
        paths = materialize_skill_paths(Settings(), tmp_path / 'cache')
        source = next((Path(p) for p in paths if Path(p).name == name), None)
        assert source is not None, f'{name} must be bundled without an external checkout'
        workspace = LocalWorkspace(workspace_id=name, workdir=str(root),
                                   skill_paths=[] if existing else paths)
        await workspace.initialize()
        try:
            await sync_missing_skills(workspace, paths)
            skills = await workspace.list_skills()
            assert [s.name for s in skills].count(name) == 1
            destination = root / 'skills' / name
            resources = [p for p in source.rglob('*') if p.is_file()]
            assert any(p.parent.name == 'sources' for p in resources)
            for resource in resources:
                assert (destination / resource.relative_to(source)).read_bytes() == resource.read_bytes()
            toolkit = Toolkit(skills_or_loaders=skills)
            viewed = await toolkit.builtin_skill_viewer.tool.call(name, AgentState())
            assert viewed.content[0].text == next(s.markdown for s in skills if s.name == name)
            reference = next(destination.glob('references/sources/*.md'))
            reference.write_text(reference.read_text() + '\nUser reference annotation.')
            manifest = destination / 'SKILL.md'
            manifest.write_text(manifest.read_text() + '\nUser annotation retained.')
            await sync_missing_skills(workspace, paths)
            assert manifest.read_text().endswith('User annotation retained.')
            assert reference.read_text().endswith('User reference annotation.')
            assert [s.name for s in await workspace.list_skills()].count(name) == 1
        finally:
            await workspace.close()
    asyncio.run(run())
