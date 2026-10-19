#!/usr/bin/env python3
"""Install offline x64dbg Automate dependencies into an isolated guest venv.

Explicit administrator maintenance command: run only when no Windows job is
active. Existing differing plugin files are never overwritten. No target is
loaded; --install probes and closes a newly created empty debugger session.
Download both matching official plugin ZIPs and Windows Python 3.12 wheels
before use. Credentials are environment references or hidden terminal input.
"""
import argparse
import getpass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import time
import uuid
import zipfile

from traceglass.windows.config import WindowsConfig
from traceglass.windows.transport import VSphereTransport

CLIENT_VERSION = '0.9.2'

GUEST_SETUP = r'''
import hashlib, json, subprocess, sys, traceback, zipfile
from pathlib import Path
directory=Path(__file__).parent
result={'state':'failed'}
try:
    record=json.loads((directory/'setup.json').read_text())
    with zipfile.ZipFile(directory/'bundle.zip') as archive:
        for name, digest in record['hashes'].items():
            data=archive.read(name)
            if hashlib.sha256(data).hexdigest()!=digest:
                raise ValueError('Setup artifact digest mismatch')
            target=directory.joinpath(*name.split('/'))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    copies=[]
    for bit, executable in record['debuggers'].items():
        destination=Path(executable).parent/'plugins'
        for source in (directory/'plugins'/bit).iterdir():
            target=destination/source.name
            if target.exists() and target.read_bytes()!=source.read_bytes():
                raise ValueError('Existing plugin differs; refusing overwrite: '+str(target))
            copies.append((source,target))
    for source,target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with target.open('xb') as stream: stream.write(source.read_bytes())
    environment=Path(record['venv'])
    python=environment/'Scripts'/'python.exe'
    with (directory/'install.log').open('wb') as log:
        if not python.is_file():
            subprocess.run([sys.executable,'-m','venv',str(environment)], stdout=log,stderr=log,check=True,timeout=90)
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','--no-index',
            '--find-links',str(directory/'wheels'),'x64dbg_automate=='+record['version']],
            stdout=log,stderr=log,check=True,timeout=90)
    probe=r"""
import json, sys
from pathlib import Path
from x64dbg_automate import X64DbgClient, COMPAT_VERSION
client=X64DbgClient(sys.argv[1])
try:
    pid=client.start_session()
    ready=not client.is_debugging()
    client.terminate_session()
    Path(sys.argv[2]).write_text(json.dumps({'state':'success' if ready else 'failed',
        'empty_session_closed':True,'debugger_pid':pid,'compatibility':COMPAT_VERSION}),encoding='utf-8')
finally:
    if getattr(client,'proc',None) is not None and client.proc.poll() is None:
        client.proc.kill()
    client.detach_session()
"""
    (directory/'probe.py').write_text(probe,encoding='utf-8')
    probes={}
    with (directory/'probe.log').open('wb') as log:
        for bit, executable in record['debuggers'].items():
            output=directory/('probe-'+bit+'.json')
            subprocess.run([str(python),str(directory/'probe.py'),executable,str(output)],
                stdout=log,stderr=log,check=True,timeout=55)
            probes[bit]=json.loads(output.read_text())
    result={'state':'success','python':str(python),'client_version':record['version'],'probes':probes}
except Exception as exc:
    result={'state':'failed','error_type':type(exc).__name__,'error':str(exc),'traceback':traceback.format_exc()}
(directory/'setup-result.json').write_text(json.dumps(result),encoding='utf-8')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--wheels', required=True, type=Path)
    parser.add_argument('--plugin64', required=True, type=Path)
    parser.add_argument('--plugin32', required=True, type=Path)
    parser.add_argument('--install', action='store_true', help='Perform additive installation and empty-session probes')
    args = parser.parse_args()
    cfg_record = json.loads(args.config.read_text())
    files = {f'wheels/{path.name}': path.read_bytes() for path in sorted(args.wheels.glob('*.whl'))}
    if not any(name.startswith('wheels/x64dbg_automate-0.9.2-') for name in files):
        raise SystemExit('Pinned x64dbg_automate 0.9.2 wheel is required')
    for bit, archive in (('x64', args.plugin64), ('x32', args.plugin32)):
        with zipfile.ZipFile(archive) as zipped:
            for name in zipped.namelist():
                path = PurePosixPath(name.replace('\\', '/'))
                if path.is_absolute() or '..' in path.parts:
                    raise SystemExit('Invalid plugin ZIP member')
                if path.suffix.lower() in ('.dll', '.dp64', '.dp32'):
                    files[f'plugins/{bit}/{path.name}'] = zipped.read(name)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    print(json.dumps({'offline_files': hashes, 'install': args.install}, indent=2), flush=True)
    if not args.install:
        return
    for field in ('esxi_password_env', 'guest_password_env'):
        variable = cfg_record[field]
        if not os.environ.get(variable):
            os.environ[variable] = getpass.getpass(field.removesuffix('_env') + ': ')
    cfg = WindowsConfig.load(args.config)
    transport = VSphereTransport(cfg)
    directory = str(PureWindowsPath(cfg.guest_root) / ('setup_' + uuid.uuid4().hex))
    record = dict(version=CLIENT_VERSION, hashes=hashes,
        debuggers={bit: cfg.tools[key] for bit, key in (('x64', 'x64dbg'), ('x32', 'x32dbg'))},
        venv=str(PureWindowsPath(cfg.guest_root) / ('python-xauto-' + CLIENT_VERSION)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for name, data in files.items():
            zipped.writestr(name, data)
    transport.mkdir(directory)
    for name, data in {'bundle.zip': buffer.getvalue(), 'setup.json': json.dumps(record).encode(),
                       'setup.py': GUEST_SETUP.encode()}.items():
        transport.put(str(PureWindowsPath(directory) / name), data)
    pid = transport.start(cfg.tools['python'], subprocess.list2cmdline([str(PureWindowsPath(directory) / 'setup.py')]), directory)
    print(json.dumps({'setup_directory': directory, 'pid': pid}), flush=True)
    # This standalone maintenance CLI emits progress every ten seconds; model
    # calls use the short asynchronous jobs API instead.
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        if not transport.process(pid)['running']:
            result = json.loads(transport.get(str(PureWindowsPath(directory) / 'setup-result.json'), 65536))
            print(json.dumps(result, indent=2), flush=True)
            if result['state'] != 'success':
                for name in ('install.log', 'probe.log'):
                    try:
                        print(name, transport.get(str(PureWindowsPath(directory) / name), 65536).decode(errors='replace'))
                    except Exception:
                        pass
                raise SystemExit(1)
            return
        print('Guest setup is still running; pid=' + str(pid), flush=True)
        time.sleep(10)
    raise SystemExit('Setup status unknown; inspect the existing PID and setup directory before retrying')


if __name__ == '__main__':
    main()
