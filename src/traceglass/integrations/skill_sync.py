"""Install newly configured skills through the native workspace API."""
from pathlib import Path

import frontmatter


async def sync_missing_skills(workspace, paths: list[str]) -> None:
    # Native Docker startup seeds only an empty skill directory. Existing
    # analyses must receive newly configured packages too, without replacing
    # edited copies or producing a second Skill with a suffixed name.
    names = {skill.name for skill in await workspace.list_skills()}
    for path in paths:
        metadata = frontmatter.load(Path(path) / 'SKILL.md')
        name = metadata.get('name')
        if name and name not in names:
            await workspace.add_skill(path)
            names.add(name)
