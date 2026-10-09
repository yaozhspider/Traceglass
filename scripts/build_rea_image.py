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
    tag = f"local/traceglass-rea:{release['version']}"
    subprocess.run([docker, 'build', '--platform', 'linux/amd64', '--build-arg', f'BASE_IMAGE={image}',
                    '-t', tag, str(context)], check=True)
    image_id = subprocess.check_output([docker, 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()
    version = subprocess.check_output([docker, 'run', '--rm', '--network', 'none', '--platform', 'linux/amd64',
                                       image_id, '--version'], text=True).strip()
    if version != release['version']:
        raise SystemExit('Built REA version does not match release lock')
    (output / 'bin').mkdir(exist_ok=True)
    shutil.copy2(args.base_kit / 'bin/docker-cli', output / 'bin/docker-cli')
    manifest = {'name': 'traceglass-rea-kit', 'version': version, 'platform': 'linux/amd64',
        'image': {'image_tag': tag, 'image_id': image_id}, 'base_image_id': actual,
        'versions': {**base.get('versions', {}), 'rea': version}, 'package': release,
        'compatibility': 'Inherited hash-guarded Rosetta/Hopper patches; both upstream module hashes unchanged in 6.1.0.'}
    (output / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Verified REA {version}: {image_id}\nKit: {output}\nNot activated; set TRACEGLASS_REA_KIT after smoke checks.')


if __name__ == '__main__':
    main()
