"""Standalone guest worker. Standard library only, except optional x64dbg driver.

The host uploads this file and a fixed request into a unique task directory.
No listening socket, persistent guest service or shell command API is created.
"""
import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


PROFILES = ('die', 'rizin', 'upx_test', 'wasm2wat', 'jadx', 'execute', 'debug_x64', 'debug_x32')


def write_json(path, value):
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, default=serializable), encoding='utf-8')
    os.replace(temporary, path)


def serializable(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if hasattr(value, '_asdict'):
        return value._asdict()
    if isinstance(value, bytes):
        return {'base64': base64.b64encode(value).decode()}
    if hasattr(value, '__dict__'):
        return vars(value)
    return str(value)


def stop_tree(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run([os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'taskkill.exe'),
                        '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def run_command(argv, directory, timeout, *, max_output=32 * 1024**2):
    directory = Path(directory)
    with (directory / 'stdout.txt').open('wb') as out, (directory / 'stderr.txt').open('wb') as err:
        options = {'start_new_session': True} if os.name != 'nt' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        process = subprocess.Popen(argv, cwd=directory, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, shell=False, **options)
        write_json(directory / 'child.json', {'pid': process.pid})
        deadline = time.monotonic() + timeout
        state = None
        while process.poll() is None:
            if (directory / 'cancel').exists():
                state = 'cancelled'
            elif time.monotonic() >= deadline:
                state = 'timeout'
            elif os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > max_output:
                state = 'output_limit'
            if state:
                stop_tree(process)
                break
            time.sleep(.05)
        if not state and os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > max_output:
            state = 'output_limit'
        return {'state': state or ('success' if process.returncode == 0 else 'failed'),
                'exit_code': process.returncode, 'child_pid': process.pid}


def capabilities(tools):
    commands = {name: {'installed': Path(path).is_file(), 'path': path} for name, path in tools.items()}
    driver = importlib.util.find_spec('x64dbg_automate') is not None
    plugins = {bit: (Path(tools[key]).parent / 'plugins' / ('x64dbg-automate.' + extension)).is_file()
               if key in tools else False for bit, key, extension in
               (('x64', 'x64dbg', 'dp64'), ('x32', 'x32dbg', 'dp32'))}
    return {'state': 'success', 'python_version': sys.version.split()[0], 'tools': commands,
            'x64dbg_automation_client': driver,
            'x64dbg_plugin_files': plugins,
            'debugger_note': 'An installed client/executable does not prove plugin readiness. Open a new owned debug session to verify.',
            'profiles': {profile: all(commands.get(key, {}).get('installed', False) for key in keys)
                         for profile, keys in {'die': ['die'], 'rizin': ['rizin'], 'upx_test': ['upx'],
                             'wasm2wat': ['wasm2wat'], 'jadx': ['java', 'jadx_jar'],
                             'execute': ['python']}.items()},
            'debug_dependencies_present': {bit: driver and present and commands.get(bit + 'dbg', {}).get('installed', False)
                                           for bit, present in plugins.items()}}


@contextmanager
def debugger_environment(directory):
    # Each runner is a standalone process. Tool children inherit task TEMP;
    # debugger children and later operation runners share their owned rendezvous.
    previous = {key: os.environ.get(key) for key in ('TEMP', 'TMP')}
    try:
        if directory is not None:
            path = Path(directory).resolve()
            path.mkdir(parents=True, exist_ok=True)
            os.environ['TEMP'] = os.environ['TMP'] = str(path)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def debug(request, directory):
    with debugger_environment(request.get('debugger_temp')):
        return _debug(request, directory)


def _debug(request, directory):
    opened = False
    try:
        from x64dbg_automate import X64DbgClient
    except ImportError:
        return {'state': 'unavailable', 'error': 'x64dbg Automate Python client is not installed',
                'debugger_cleanup_confirmed': True}
    client = None
    action = request.get('action', 'open')
    stage = 'session_establishment'
    dump_attempted = False
    try:
        executable = request['tools']['x32dbg' if request['profile'] == 'debug_x32' else 'x64dbg']
        if action == 'open':
            client = X64DbgClient(executable)
            pid = client.start_session()
            opened = True
            stage = 'sample_load'
            if not client.load_executable(request['sample'], current_dir=str(directory)):
                raise RuntimeError('Failed to load executable')
            client.wait_cmd_ready()
        else:
            pid = int(request['debugger_pid'])
            client = X64DbgClient(executable)
            client.attach_session(pid)
        payload = {'debugger_pid': pid}
        if request.get('debugger_temp'):
            payload['debugger_temp'] = request['debugger_temp']
        stage = 'debugger_state'
        if action not in ('status', 'close') and not client.is_debugging():
            raise RuntimeError('Owned session has no active debuggee')
        if action in ('registers', 'breakpoint', 'step', 'memory', 'dump') and client.is_running():
            raise RuntimeError('Pause the owned debuggee before reading or stepping')
        stage = 'debugger_operation'
        if action == 'breakpoint':
            if not client.set_breakpoint(int(request['address'], 0)):
                raise RuntimeError('Breakpoint was rejected')
        elif action == 'step':
            if not client.stepi(step_count=request.get('count', 1), wait_timeout=10):
                raise RuntimeError('Step did not reach a paused state')
        elif action == 'continue':
            if not client.go():
                raise RuntimeError('Continue was rejected')
        elif action == 'pause':
            if not client.pause():
                raise RuntimeError('Pause was rejected')
        elif action in ('memory', 'dump'):
            stage = 'memory_read'
            dump_attempted = action == 'dump'
            data = client.read_memory(int(request['address'], 0), request.get('length', 256))
            if action == 'memory':
                payload['memory_base64'] = base64.b64encode(data).decode()
            else:
                stage = 'dump_write'
                (directory / 'memory.bin').write_bytes(data)
        elif action == 'close':
            client.terminate_session()
            client = None
            return {'state': 'success', 'closed': True, **payload}
        elif action not in ('open', 'status', 'registers'):
            raise ValueError('Unsupported debugger operation')
        payload['debugging'] = client.is_debugging()
        payload['running'] = client.is_running()
        if action in ('registers', 'open') and payload['debugging'] and not payload['running']:
            payload['registers'] = client.get_regs()
        return {'state': 'debugging', **payload}
    except Exception as exc:
        cleanup_confirmed = action == 'open' and client is None
        if action == 'open' and client is not None:
            try:
                if opened:
                    client.terminate_session()
                elif getattr(client, 'proc', None) is not None:
                    stop_tree(client.proc)
                process = getattr(client, 'proc', None)
                cleanup_confirmed = process is not None and process.poll() is not None
            except Exception:
                pass
        return {'state': 'failed', 'error_type': type(exc).__name__, 'error': str(exc)[:1000],
                'failure_stage': stage, 'memory_dump_attempted': dump_attempted,
                'debugger_cleanup_confirmed': cleanup_confirmed,
                'next_step': ('Debugger session was not established. Memory dumping has not been tested; '
                              'check plugin readiness and the shared task TEMP directory before retrying.'
                              if stage == 'session_establishment' else
                              'Inspect this operation failure; do not automatically replay target execution.')}
    finally:
        if client is not None:
            try:
                client.detach_session()
            except Exception:
                pass


def run(request, directory):
    with debugger_environment(request.get('task_temp')):
        return _run(request, directory)


def _run(request, directory):
    profile = request['profile']
    tools = request['tools']
    if profile == 'capabilities':
        return capabilities(tools)
    # Only the initial launch is bound to the uploaded bytes on disk. Later
    # operations control that owned process even if it edits/deletes its file.
    if profile in ('debug_x64', 'debug_x32') and request.get('action', 'open') != 'open':
        return debug(request, directory)
    sample = Path(request['sample'])
    with sample.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != request['sample_sha256']:
        raise ValueError('Uploaded sample SHA-256 mismatch')
    if profile in ('debug_x64', 'debug_x32'):
        return debug(request, directory)
    output = directory / 'output'
    output.mkdir(exist_ok=True)
    commands = {
        'die': lambda: [tools['die'], '-j', str(sample)],
        'rizin': lambda: [tools['rizin'], '-q', '-c', 'aaa;aflj', str(sample)],
        'upx_test': lambda: [tools['upx'], '-t', str(sample)],
        'wasm2wat': lambda: [tools['wasm2wat'], str(sample), '-o', str(output / 'module.wat')],
        'jadx': lambda: [tools['java'], '-jar', tools['jadx_jar'], '-d', str(output), str(sample)],
        'execute': lambda: [str(sample), *request.get('arguments', [])],
    }
    if profile not in commands:
        raise ValueError('Unsupported fixed tool profile')
    return run_command(commands[profile](), directory, request['timeout'])


def main():
    request_path = Path(sys.argv[1]).resolve()
    directory = request_path.parent
    request = json.loads(request_path.read_text(encoding='utf-8'))
    try:
        result = run(request, directory)
    except Exception as exc:
        result = {'state': 'failed', 'error_type': type(exc).__name__, 'error': str(exc)[:1000]}
    artifacts = []
    for path in sorted(directory.rglob('*')):
        if (path.is_file() and not path.is_symlink()
                and not {'debugger-temp', 'task-temp'}.intersection(path.relative_to(directory).parts) and path.name not in
                ('request.json', 'result.json', 'runner.py', 'sample.bin', 'child.json')
                and not path.name.startswith('input.')):
            artifacts.append({'path': path.relative_to(directory).as_posix(), 'bytes': path.stat().st_size})
    result.update(sample_sha256=request.get('sample_sha256'), artifacts=artifacts[:100])
    write_json(directory / 'result.json', result)


if __name__ == '__main__':
    main()
