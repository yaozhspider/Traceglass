#!/usr/bin/env python3
"""Compile auditable synthetic Linux ELF objects. Never links or executes samples."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FLAGS = ['--target=x86_64-linux-gnu', '-O0', '-g', '-fno-builtin', '-fno-stack-protector', '-c']


def build(output, compiler):
    output.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([compiler, '--version'], check=True, capture_output=True, text=True).stdout
    for source in sorted((ROOT / 'evals/static/sources').glob('*.c')):
        target = output / (source.stem + '.o')
        manifest = output / (source.stem + '.json')
        if target.exists() or manifest.exists():
            raise ValueError(f'Refusing overwrite: {target}')
        command = [compiler, *FLAGS, str(source), '-o', str(target)]
        subprocess.run(command, check=True, capture_output=True, text=True)
        target.chmod(0o444)
        data = dict(id=source.stem, binary=target.name, sample_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            source=str(source.relative_to(ROOT)), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            analysis_goal='vulnerability', task='Assess the handle_request external-input path in this object only. '
            'External runtime definitions, deployment authorization and actual exploit effects are outside scope.',
            build=dict(compiler=compiler, compiler_version=version, flags=FLAGS, command=command,
                       target='x86_64-linux-gnu', object_only=True, target_executed=False),
            rubric=json.loads((ROOT / 'evals/static/rubrics' / (source.stem + '.json')).read_text()))
        manifest.write_text(json.dumps(data, indent=2) + '\n')
        print(json.dumps(dict(case=data['id'], sample_sha256=data['sample_sha256'], manifest=str(manifest))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'traceglass-data/eval-fixtures')
    parser.add_argument('--compiler', default=shutil.which('clang'))
    args = parser.parse_args()
    if not args.compiler:
        parser.error('clang unavailable; no fixtures built')
    build(args.output.resolve(), args.compiler)


if __name__ == '__main__':
    main()
