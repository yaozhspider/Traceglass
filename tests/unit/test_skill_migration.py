"""Offline Skill rollout must preserve custom resources and reject stale plans."""
from pathlib import Path

import pytest

from traceglass.integrations.skill_migration import prepare_migration, apply_migration, rollback_migration


def fixture(tmp_path):
    workspace = tmp_path / 'workspace'
    installed = workspace / 'skills/example'
    installed.mkdir(parents=True)
    (installed / 'SKILL.md').write_text('---\nname: example\ndescription: Old\n---\nOld instructions')
    (installed / 'custom.md').write_text('User resource')
    source = tmp_path / 'source/example'
    source.mkdir(parents=True)
    (source / 'SKILL.md').write_text('---\nname: example\ndescription: Current\n---\nCurrent instructions')
    (source / 'reference.md').write_text('Current reference')
    return workspace, source


def test_prepare_is_reviewable_without_changing_workspace(tmp_path):
    workspace, source = fixture(tmp_path)
    plan = prepare_migration(workspace, [source], tmp_path / 'migration')
    assert (workspace / 'skills/example/SKILL.md').read_text().endswith('Old instructions')
    assert not (workspace / 'skills/example/reference.md').exists()
    assert (plan.parent / 'backup-skills/example/custom.md').read_text() == 'User resource'
    assert 'Current instructions' in (plan.parent / 'changes.diff').read_text()


def test_apply_preserves_custom_files_and_rollback_restores_originals(tmp_path):
    workspace, source = fixture(tmp_path)
    plan = prepare_migration(workspace, [source], tmp_path / 'migration')
    apply_migration(plan)
    assert (workspace / 'skills/example/SKILL.md').read_bytes() == (source / 'SKILL.md').read_bytes()
    assert (workspace / 'skills/example/custom.md').read_text() == 'User resource'
    assert (workspace / 'skills/example/reference.md').exists()
    rollback_migration(plan)
    assert (workspace / 'skills/example/SKILL.md').read_text().endswith('Old instructions')
    assert (workspace / 'skills/example/custom.md').read_text() == 'User resource'
    assert not (workspace / 'skills/example/reference.md').exists()


def test_apply_rejects_workspace_changes_since_review(tmp_path):
    workspace, source = fixture(tmp_path)
    plan = prepare_migration(workspace, [source], tmp_path / 'migration')
    (workspace / 'skills/example/custom.md').write_text('New user edit')
    with pytest.raises(ValueError, match='changed'):
        apply_migration(plan)
    assert (workspace / 'skills/example/SKILL.md').read_text().endswith('Old instructions')
    assert (workspace / 'skills/example/custom.md').read_text() == 'New user edit'


def test_rollback_rejects_new_user_edits(tmp_path):
    workspace, source = fixture(tmp_path)
    plan = prepare_migration(workspace, [source], tmp_path / 'migration')
    apply_migration(plan)
    (workspace / 'skills/example/reference.md').write_text('New annotation')
    with pytest.raises(ValueError, match='changed'):
        rollback_migration(plan)
    assert (workspace / 'skills/example/reference.md').read_text() == 'New annotation'


def test_apply_rejects_modified_staged_content(tmp_path):
    workspace, source = fixture(tmp_path)
    plan = prepare_migration(workspace, [source], tmp_path / 'migration')
    (plan.parent / 'prepared-skills/example/SKILL.md').write_text('Unexpected replacement')
    with pytest.raises(ValueError, match='changed'):
        apply_migration(plan)
    assert (workspace / 'skills/example/SKILL.md').read_text().endswith('Old instructions')


def test_symlinked_skill_resources_are_not_followed(tmp_path):
    workspace, source = fixture(tmp_path)
    outside = tmp_path / 'private.txt'
    outside.write_text('Private resource')
    (workspace / 'skills/example/link').symlink_to(outside)
    with pytest.raises(ValueError, match='[Ss]ymlink'):
        prepare_migration(workspace, [source], tmp_path / 'migration')
    assert outside.read_text() == 'Private resource'


def test_apply_rejects_snapshot_changed_during_staging(tmp_path, monkeypatch):
    import traceglass.integrations.skill_migration as migration
    workspace, source = fixture(tmp_path)
    plan = prepare_migration(workspace, [source], tmp_path / 'migration')
    original = migration.shutil.copytree

    def changed_copy(*args, **kwargs):
        result = original(*args, **kwargs)
        if Path(args[0]).name == 'prepared-skills':
            for root in [Path(args[0]), Path(args[1])]:
                (root / 'example/reference.md').write_text('Changed during copy')
        return result

    monkeypatch.setattr(migration.shutil, 'copytree', changed_copy)
    with pytest.raises(ValueError, match='changed'):
        apply_migration(plan)
    assert (workspace / 'skills/example/SKILL.md').read_text().endswith('Old instructions')


def test_failed_swap_restores_old_tree(tmp_path, monkeypatch):
    workspace, source = fixture(tmp_path)
    plan = prepare_migration(workspace, [source], tmp_path / 'migration')
    rename = Path.rename

    def fail_new_tree(path, target):
        if path.name == 'next':
            raise OSError('Fixture rename failure')
        return rename(path, target)

    monkeypatch.setattr(Path, 'rename', fail_new_tree)
    with pytest.raises(OSError, match='Fixture'):
        apply_migration(plan)
    assert (workspace / 'skills/example/SKILL.md').read_text().endswith('Old instructions')
    assert not (workspace / '.skill-rollout.lock').exists()


def test_missing_package_is_added_once_and_removed_on_clean_rollback(tmp_path):
    workspace, source = fixture(tmp_path)
    addition = tmp_path / 'source/new-example'
    addition.mkdir()
    (addition / 'SKILL.md').write_text('---\nname: new-example\ndescription: Added\n---\nNew guidance')
    plan = prepare_migration(workspace, [source, addition], tmp_path / 'migration')
    apply_migration(plan)
    apply_migration(plan)
    assert sorted(p.name for p in (workspace / 'skills').iterdir()) == ['example', 'new-example']
    rollback_migration(plan)
    rollback_migration(plan)
    assert not (workspace / 'skills/new-example').exists()
    assert (workspace / 'skills/example/custom.md').read_text() == 'User resource'
