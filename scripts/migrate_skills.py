#!/usr/bin/env python3
"""Prepare, explicitly apply or roll back Skill updates. Apply only while idle."""
import argparse
import json
from pathlib import Path

from traceglass.config import Settings, materialize_skill_paths
from traceglass.integrations.skill_migration import prepare_migration, apply_migration, rollback_migration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    prepare = actions.add_parser('prepare', help='Create backup, candidate and diff; do not update workspace')
    prepare.add_argument('--workspace', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True, help='New directory outside the workspace')
    for action in ('apply', 'rollback'):
        command = actions.add_parser(action, help='Requires the workspace to be idle')
        command.add_argument('plan', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        # Reuse the native layout, including separate nested Skills. Do not
        # overwrite an explicitly selected user version with a project fallback.
        staging = args.output.parent / (args.output.name + '-source-layout')
        if staging.exists() or args.output.exists():
            raise ValueError('Use a new output directory')
        from traceglass.integrations.skill_inventory import inspect_skills
        settings = Settings.from_env()
        project_names = {row['name'] for row in inspect_skills(settings)['packages'] if row['origin'] == 'project'}
        import frontmatter
        paths = [Path(p) for p in materialize_skill_paths(settings, staging)
                 if frontmatter.load(Path(p) / 'SKILL.md').get('name') in project_names]
        plan = prepare_migration(args.workspace, paths, args.output)
        print(json.dumps({'plan': str(plan), 'diff': str(plan.parent / 'changes.diff')}, ensure_ascii=False))
    else:
        operation = apply_migration if args.action == 'apply' else rollback_migration
        plan = operation(args.plan)
        print(json.dumps({'status': plan['status'], 'workspace': plan['workspace'],
                          'changed_files': len(plan['changes'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
