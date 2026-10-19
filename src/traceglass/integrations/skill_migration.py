"""Explicit offline Skill rollout with snapshots and optimistic concurrency checks.

Never invoked at startup. The operator must verify the workspace is idle before
applying or rolling back; these filesystem locks do not lock live agent runs.
"""
from contextlib import contextmanager
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import frontmatter


def _snapshot(root: Path) -> dict[str, str]:
    if root.is_symlink():
        raise ValueError(f'Symlink is not supported: {root}')
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlink is not supported: {path}')
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            result[relative + '/'] = 'directory'
        elif path.is_file():
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            raise ValueError(f'Unsupported resource: {path}')
    return result


def _digest(snapshot: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def _save(path: Path, data: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def prepare_migration(workspace: Path, sources: list[Path], output: Path) -> Path:
    """Back up skills and prepare an overlay/diff without changing the workspace.

    Existing extra files and custom packages are retained. Same-path replacements
    are explicit in changes.diff and plan.json and must be reviewed before apply.
    """
    workspace, output = workspace.resolve(), output.resolve()
    skills = workspace / 'skills'
    if not skills.is_dir() or skills.is_symlink():
        raise ValueError('An existing, non-symlink skills directory is required')
    if output.is_relative_to(workspace) or workspace.is_relative_to(output):
        raise ValueError('Migration output must be outside the workspace')
    before = _snapshot(skills)
    selected = []
    names = set()
    for source in sources:
        source = Path(source)
        _snapshot(source)
        metadata = frontmatter.load(source / 'SKILL.md')
        name = metadata.get('name', '')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
            raise ValueError(f'Invalid Skill name: {name!r}')
        if name in names:
            raise ValueError(f'Duplicate Skill name: {name}')
        names.add(name)
        selected.append((name, source))
    output.mkdir(parents=True, exist_ok=False)
    backup, prepared = output / 'backup-skills', output / 'prepared-skills'
    shutil.copytree(skills, backup)
    if _snapshot(backup) != before or _snapshot(skills) != before:
        raise ValueError('Workspace changed while backing up; prepare a new migration')
    shutil.copytree(backup, prepared)
    for name, source in selected:
        destination = prepared / name
        if destination.is_dir():
            installed_name = frontmatter.load(destination / 'SKILL.md').get('name')
            if installed_name != name:
                raise ValueError(f'Skill directory belongs to another name: {destination}')
        shutil.copytree(source, destination, dirs_exist_ok=True)
    after = _snapshot(prepared)
    changes, diff = [], []
    for relative in sorted(after):
        if relative.endswith('/') or before.get(relative) == after[relative]:
            continue
        changes.append({'path': relative, 'before_sha256': before.get(relative),
                        'after_sha256': after[relative]})
        old = (backup / relative).read_bytes() if relative in before else b''
        new = (prepared / relative).read_bytes()
        try:
            diff.extend(difflib.unified_diff(old.decode('utf-8').splitlines(keepends=True),
                new.decode('utf-8').splitlines(keepends=True),
                fromfile='before/' + relative, tofile='after/' + relative))
        except UnicodeDecodeError:
            diff.append(f'Binary resource changed: {relative}\n')
    (output / 'changes.diff').write_text(''.join(diff), encoding='utf-8')
    plan = output / 'plan.json'
    _save(plan, {'version': 1, 'status': 'prepared', 'workspace': str(workspace),
                 'before_sha256': _digest(before), 'after_sha256': _digest(after),
                 'selected_skills': sorted(names), 'changes': changes})
    return plan


@contextmanager
def _migration_lock(workspace: Path):
    path = workspace / '.skill-rollout.lock'
    with path.open('x') as lock:
        lock.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def _swap(workspace: Path, source: Path, expected: str, target: str) -> None:
    """Build beside skills, recheck, then rename with recovery on swap failure."""
    with tempfile.TemporaryDirectory(prefix='.skill-rollout-', dir=workspace) as temporary:
        staging = Path(temporary)
        next_skills, previous = staging / 'next', staging / 'previous'
        shutil.copytree(source, next_skills)
        if _digest(_snapshot(next_skills)) != target:
            raise ValueError('Staged Skill contents changed during copy')
        skills = workspace / 'skills'
        if _digest(_snapshot(skills)) != expected:
            raise ValueError('Workspace changed since preparation; nothing applied')
        skills.rename(previous)
        try:
            next_skills.rename(skills)
        except BaseException:
            previous.rename(skills)
            raise


def _transition(plan_path: Path, *, rollback: bool) -> dict:
    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    if plan.get('version') != 1:
        raise ValueError('Unsupported Skill migration plan version')
    workspace = Path(plan['workspace'])
    expected = plan['after_sha256' if rollback else 'before_sha256']
    target = plan['before_sha256' if rollback else 'after_sha256']
    source = plan_path.parent / ('backup-skills' if rollback else 'prepared-skills')
    status = 'rolled_back' if rollback else 'applied'
    with _migration_lock(workspace):
        if _digest(_snapshot(source)) != target:
            raise ValueError('Migration snapshot changed; refusing replacement')
        actual = _digest(_snapshot(workspace / 'skills'))
        if actual != target:
            if actual != expected:
                raise ValueError('Workspace changed since preparation; refusing replacement')
            _swap(workspace, source, expected, target)
        if _digest(_snapshot(workspace / 'skills')) != target:
            raise ValueError('Workspace changed during migration; inspect retained backup')
        plan['status'] = status
        _save(plan_path, plan)
    return plan


def apply_migration(plan_path: Path) -> dict:
    """Apply a reviewed plan to an idle workspace; reject any intervening changes."""
    return _transition(plan_path, rollback=False)


def rollback_migration(plan_path: Path) -> dict:
    """Restore the backup only when the installed tree still matches the rollout."""
    return _transition(plan_path, rollback=True)
