import asyncio
from pathlib import Path

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
