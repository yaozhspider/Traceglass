#!/usr/bin/env python3
"""Build the pinned REA release over the existing engine kit, without activating it."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-kit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    release = json.loads((repo / 'src/traceglass/resources/rea-release.json').read_text())
    base = json.loads((args.base_kit / 'MANIFEST.json').read_text())
    docker = str((args.base_kit / 'bin/docker-cli').resolve(strict=True))
    image = base['image']['image_tag']
    actual = subprocess.check_output([docker, 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
    if actual != base['image']['image_id']:
        raise SystemExit('Base image differs from its pinned kit manifest')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    context = output / 'build'
    context.mkdir(exist_ok=True)
    archive = context / f"rea-agents-{release['version']}.tgz"
    if not archive.exists():
        subprocess.run(['curl', '-fL', '--retry', '3', '--retry-all-errors', '--max-time', '120',
                        release['tarball'], '-o', str(archive)], check=True)
    digest = 'sha512-' + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
    if digest != release['integrity']:
        raise SystemExit('REA archive integrity mismatch; no image was built')
    shutil.copy2(repo / 'containers/rea/Dockerfile', context / 'Dockerfile')
    requirements = repo / 'containers/rea/pwntools-requirements.txt'
    shutil.copy2(requirements, context / requirements.name)
    tag = f"local/traceglass-rea:{release['version']}-pwntools"
    subprocess.run([docker, 'build', '--platform', 'linux/amd64', '--build-arg', f'BASE_IMAGE={image}',
                    '--build-arg', f'REA_ARCHIVE={archive.name}',
                    '-t', tag, str(context)], check=True)
    image_id = subprocess.check_output([docker, 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()
    version = subprocess.check_output([docker, 'run', '--rm', '--network', 'none', '--platform', 'linux/amd64',
                                       image_id, '--version'], text=True).strip()
    if version != release['version']:
        raise SystemExit('Built REA version does not match release lock')
    expected = dict(line.split('==') for line in requirements.read_text().splitlines() if line.strip())
    probe = ('import json, importlib.metadata as m; import pwnlib, elftools, unicorn, pefile, capstone; '
             f'print(json.dumps({{name: m.version(name) for name in {list(expected)!r}}}))')
    installed = json.loads(subprocess.check_output([
        docker, 'run', '--rm', '--network', 'none', '--platform', 'linux/amd64',
        '--entrypoint', '/opt/rea-pwntools/bin/python', image_id, '-c', probe,
    ], text=True))
    if installed != expected:
        raise SystemExit('Built pwntools provider versions do not match pinned requirements')
    (output / 'bin').mkdir(exist_ok=True)
    shutil.copy2(args.base_kit / 'bin/docker-cli', output / 'bin/docker-cli')
    manifest = {'name': 'traceglass-rea-kit', 'version': version, 'platform': 'linux/amd64',
        'image': {'image_tag': tag, 'image_id': image_id}, 'base_image_id': actual,
        'versions': {**base.get('versions', {}), 'rea': version, **installed}, 'package': release,
        'compatibility': 'Inherited Rosetta/Hopper patches applied after upstream module SHA256 checks.'}
    (output / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    # Native MCP metadata is bound to this exact kit fingerprint. Runtime
    # registration uses this cache without starting analysis containers.
    import asyncio
    import tempfile
    from traceglass.integrations.rea import ReaSessions
    async def cache_metadata():
        pool = ReaSessions(output)
        try:
            with tempfile.TemporaryDirectory(prefix='traceglass-schema-') as empty:
                await pool.refresh_schema(Path(empty))
        finally:
            await pool.close()
    asyncio.run(cache_metadata())
    print(f'Verified REA {version}: {image_id}\nKit: {output}\nNot activated; set TRACEGLASS_REA_KIT after smoke checks.')


if __name__ == '__main__':
    main()
