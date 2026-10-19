"""Read-only Skill provenance and workspace drift inspection."""
from collections import Counter
import hashlib
from pathlib import Path

import frontmatter

from traceglass.config import Settings, skill_paths


def package_digest(root: Path) -> str:
    """Hash packaged resources using the native loader's nested-Skill layout."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        relative = path.relative_to(root)
        if path.suffix in {'.pyc', '.pyo'} or (path.name == 'SKILL.md' and len(relative.parts) > 1):
            continue
        digest.update(relative.as_posix().encode('utf-8') + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def inspect_skills(settings: Settings, *, workspace_root: Path | None = None) -> dict:
    """Inspect source packages and optional installed copies; never stage or update."""
    builtins = (Path(__file__).parents[1] / 'resources/skills').resolve()
    legacy = (settings.legacy_project / 'skills').resolve() if settings.legacy_project else None
    explicit = [root.resolve() for root in settings.skill_roots]
    packages = []
    for source in map(Path, skill_paths(settings)):
        metadata = frontmatter.load(source / 'SKILL.md')
        name = metadata.get('name', source.name)
        digest = package_digest(source)
        if any(source.is_relative_to(root) for root in explicit):
            origin = 'explicit'
        elif source.is_relative_to(builtins):
            origin = 'project'
        elif legacy and source.is_relative_to(legacy):
            origin = 'legacy'
        else:
            origin = 'explicit'
        row = {
            'name': name, 'package': source.name, 'source': str(source), 'origin': origin,
            'revision': metadata.get('metadata', {}).get('traceglass_revision'),
            'package_sha256': digest, 'workspace_status': 'not_checked',
        }
        if workspace_root is not None:
            installed = workspace_root / 'skills' / name
            row['workspace_status'] = 'not_installed'
            if (installed / 'SKILL.md').is_file():
                installed_digest = package_digest(installed)
                row.update(workspace_sha256=installed_digest,
                           workspace_status='matches_source' if installed_digest == digest
                           else 'differs_from_source')
        packages.append(row)
    names = Counter(p['name'] for p in packages)
    # A materialized loader may qualify duplicate names. Do not imply that an
    # unqualified installed directory identifies either colliding package.
    duplicate_names = sorted(name for name, count in names.items() if count > 1)
    for row in packages:
        if workspace_root is not None and row['name'] in duplicate_names:
            row['workspace_status'] = 'ambiguous_name'
    return {
        'read_only': True, 'package_count': len(packages),
        'origins': dict(Counter(p['origin'] for p in packages)),
        'workspace_statuses': dict(Counter(p['workspace_status'] for p in packages)),
        'duplicate_names': duplicate_names,
        'packages': sorted(packages, key=lambda p: (p['name'], p['source'])),
    }
