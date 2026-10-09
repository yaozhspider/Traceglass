"""Import legacy analysis inputs into a new native Docker workspace."""
from __future__ import annotations

from pathlib import Path
import shutil

from traceglass.agents import AGENT_ID
from traceglass.config import Settings


def import_legacy_assets(settings: Settings) -> dict[str, int]:
    if not settings.legacy_project or settings.workspace_backend != 'docker':
        raise ValueError('Configure the legacy project and Docker workspace first')
    destination = settings.data_dir / 'docker-workspaces' / settings.user_id / AGENT_ID
    counts: dict[str, int] = {}
    for category in ('samples', 'rules', 'reports'):
        root = settings.legacy_project / category
        counts[category] = 0
        if not root.is_dir():
            continue
        for source in root.rglob('*'):
            relative = source.relative_to(root)
            if not source.is_file() or source.is_symlink() or any(p.startswith('.') for p in relative.parts):
                continue
            if not source.resolve().is_relative_to(root.resolve()):
                continue
            target = destination / category / relative
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('Workspace input path escapes its root')
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with source.open('rb') as incoming, target.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
            except FileExistsError:
                continue
            counts[category] += 1
    return counts
