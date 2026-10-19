#!/usr/bin/env python3
"""Probe an opted-in Windows guest; optionally analyze an authored empty WASM.

No guest snapshot is restored and no target executable is run. Passwords come
from configured environment references or terminal prompts, never arguments.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import time

from traceglass.windows.config import WindowsConfig
from traceglass.windows.service import WindowsService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--user', required=True, help='An explicitly allowed Traceglass user ID')
    parser.add_argument('--state-dir', required=True, type=Path, help='Separate private smoke-test state directory')
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--static-smoke', action='store_true', help='Run wasm2wat on a new authored eight-byte WASM')
    parser.add_argument('--discover-paths', action='store_true', help='Read fixed tool filenames under C:\\Tools and public/user Desktop')
    args = parser.parse_args()
    record = json.loads(args.config.read_text())
    for field in ('esxi_password_env', 'guest_password_env'):
        variable = record[field]
        if not os.environ.get(variable):
            os.environ[variable] = getpass.getpass(field.removesuffix('_env') + ': ')
    config = WindowsConfig.load(args.config)
    args.workspace.mkdir(parents=True, exist_ok=True)
    service = WindowsService(config, args.state_dir)
    result = service.capabilities(args.user, 'windows-smoke', args.workspace)
    print(json.dumps({'capabilities': result}, ensure_ascii=False, indent=2), flush=True)
    if result.get('state') != 'success':
        raise SystemExit('Capability probe not complete; inspect its persisted job before continuing')
    if args.discover_paths:
        discover(service)
    if args.static_smoke:
        fixture = args.workspace / 'authored-empty-module.wasm'
        with fixture.open('xb') as stream:
            stream.write(b'\0asm\x01\0\0\0')
        job = service.submit(args.user, 'windows-smoke', args.workspace, fixture.name, 'wasm2wat', timeout=20)
        deadline = time.monotonic() + 55
        while time.monotonic() < deadline:
            status = service.status(args.user, 'windows-smoke', job['job_id'])
            if status['state'] != 'running':
                break
            time.sleep(.5)
        print(json.dumps({'static_smoke': status}, ensure_ascii=False, indent=2), flush=True)
        if status['state'] != 'success':
            raise SystemExit('Static smoke did not succeed; task retained for inspection')
        collected = service.collect(args.user, 'windows-smoke', args.workspace, job['job_id'])
        wat = args.workspace / 'reports' / 'windows' / job['job_id'] / 'output' / 'module.wat'
        if not wat.is_file() or '(module' not in wat.read_text():
            raise SystemExit('Smoke output is missing or invalid')
        print(json.dumps({'collection': collected, 'wat': wat.read_text()}, ensure_ascii=False, indent=2))


def discover(service):
    """No arbitrary discovery command is exposed as a model tool."""
    import subprocess
    import uuid
    from pathlib import PureWindowsPath
    directory = str(PureWindowsPath(service.config.guest_root) / ('probe_' + uuid.uuid4().hex))
    script = r'''
import json, os
from pathlib import Path
names = {'diec.exe', 'die.exe', 'rizin.exe', 'upx.exe', 'wasm2wat.exe',
         'x64dbg.exe', 'x32dbg.exe', 'java.exe', 'adb.exe', 'VMPDump.exe',
         'xauto64.dp64', 'xauto32.dp32'}
roots = [Path(r'C:\Tools'), Path(os.environ['USERPROFILE'])/'Desktop', Path(r'C:\Users\Public\Desktop')]
found=[]
for root in roots:
    for directory, dirs, files in os.walk(root):
        for name in files:
            if name.lower() in {n.lower() for n in names} or (name.startswith('jadx') and name.endswith('.jar')):
                found.append(str(Path(directory)/name))
        if len(found)>150: break
Path(__file__).with_name('paths.json').write_text(json.dumps(found), encoding='utf-8')
'''
    transport = service.transport
    transport.mkdir(directory)
    remote = str(PureWindowsPath(directory) / 'paths.py')
    transport.put(remote, script.encode())
    pid = transport.start(service.config.tools['python'], subprocess.list2cmdline([remote]), directory)
    deadline = time.monotonic() + 55
    while time.monotonic() < deadline:
        if not transport.process(pid)['running']:
            data = transport.get(str(PureWindowsPath(directory) / 'paths.json'), 65536)
            print(json.dumps({'discovered_paths': json.loads(data)}, ensure_ascii=False, indent=2), flush=True)
            return
        time.sleep(.5)
    raise SystemExit('Path discovery is still running; do not repeat automatically')


if __name__ == '__main__':
    main()
