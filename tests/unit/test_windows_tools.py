"""Windows jobs keep caller scope, literal arguments and real terminal states."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def config(tmp_path, **changes):
    from traceglass.windows.config import WindowsConfig
    values = dict(host='192.0.2.5', vm_id='48', vm_name='yaozh',
                  esxi_user='root', esxi_password='host-private',
                  guest_user='YAOZH', guest_password='guest-private',
                  certificate=tmp_path / 'esxi.pem', allowed_users=('alice',),
                  tools={'python': r'C:\Tools\Python312\python.exe'},
                  guest_root=r'C:\Traceglass', allow_execution=False)
    values.update(changes)
    return WindowsConfig(**values)


class Guest:
    """External guest boundary; task persistence and input validation stay real."""
    def __init__(self):
        self.files = {}
        self.started = []
        self.states = {}

    def put(self, path, data):
        self.files[path] = data

    def get(self, path, limit):
        data = self.files[path]
        if len(data) > limit:
            raise ValueError('Transfer too large')
        return data

    def mkdir(self, path):
        pass

    def start(self, executable, arguments, cwd):
        self.started.append((executable, arguments, cwd))
        return 1234

    def process(self, pid):
        return self.states.get(pid, {'pid': pid, 'running': True, 'exit_code': None})

    def terminate(self, pid):
        self.states[pid] = {'pid': pid, 'running': False, 'exit_code': -1}

    def probe(self):
        return {'vm_name': 'yaozh', 'guest_operations_ready': True}


def test_admin_config_requires_explicit_users_and_hides_passwords(tmp_path, monkeypatch):
    from traceglass.windows.config import WindowsConfig
    monkeypatch.setenv('ESXI_TEST_PASSWORD', 'host-private')
    monkeypatch.setenv('GUEST_TEST_PASSWORD', 'guest-private')
    path = tmp_path / 'windows.json'
    record = {'host': '192.0.2.5', 'vm_id': '48', 'vm_name': 'yaozh',
              'esxi_user': 'root', 'esxi_password_env': 'ESXI_TEST_PASSWORD',
              'guest_user': 'YAOZH', 'guest_password_env': 'GUEST_TEST_PASSWORD',
              'certificate': 'esxi.pem', 'allowed_users': ['alice'],
              'tools': {'python': r'C:\Tools\Python312\python.exe'}}
    path.write_text(json.dumps(record))
    parsed = WindowsConfig.load(path)
    assert parsed.certificate == tmp_path / 'esxi.pem'
    assert parsed.allowed('alice') and not parsed.allowed('bob')
    assert 'host-private' not in repr(parsed) and 'guest-private' not in repr(parsed)
    record['allowed_users'] = ['*']
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        WindowsConfig.load(path)


def test_jobs_reject_escape_cross_workspace_and_disabled_execution(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ authored fixture, never run')
    guest = Guest()
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=guest)
    with pytest.raises(PermissionError):
        service.submit('bob', 'one', root, 'fixture.exe', 'die')
    with pytest.raises(ValueError):
        service.submit('alice', 'one', root, '../outside.exe', 'die')
    with pytest.raises(PermissionError):
        service.submit('alice', 'one', root, 'fixture.exe', 'execute')
    assert guest.started == []
    job = service.submit('alice', 'one', root, 'fixture.exe', 'die')
    with pytest.raises(LookupError):
        service.status('alice', 'two', job['job_id'])
    with pytest.raises(PermissionError):
        service.status('bob', 'one', job['job_id'])
    assert service.status('alice', 'one', job['job_id'])['state'] == 'running'
    with pytest.raises(RuntimeError, match='busy'):
        service.submit('alice', 'one', root, 'fixture.exe', 'die')
    # A restart cannot lose the exclusive VM lease or another task's identity.
    restarted = WindowsService(config(tmp_path), tmp_path / 'state', transport=guest)
    with pytest.raises(RuntimeError, match='busy'):
        restarted.submit('alice', 'one', root, 'fixture.exe', 'die')


def test_failed_guest_result_stays_failed_and_cannot_escape_collection(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ authored')
    guest = Guest()
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=guest)
    job = service.submit('alice', 'one', root, 'fixture.exe', 'die')
    result = {'state': 'failed', 'exit_code': 7, 'sample_sha256': job['sample_sha256'],
              'artifacts': [{'path': '../escape.txt'}], 'error': 'fixture failure'}
    guest.files[job['guest_directory'] + r'\result.json'] = json.dumps(result).encode()
    guest.states[1234] = {'pid': 1234, 'running': False, 'exit_code': 7}
    assert service.status('alice', 'one', job['job_id'])['state'] == 'failed'
    with pytest.raises(ValueError):
        service.collect('alice', 'one', root, job['job_id'])
    assert not (tmp_path / 'escape.txt').exists()


def test_native_tools_have_schemas_and_return_sanitized_errors(tmp_path):
    from traceglass.windows.service import WindowsService
    from traceglass.windows.tools import windows_tools
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=Guest())
    tools = {t.name: t for t in windows_tools(service, 'alice', 'one', tmp_path)}
    assert {'WindowsCapabilities', 'WindowsRunTool', 'WindowsJobStatus',
            'WindowsCollectResults', 'WindowsCancelJob', 'WindowsDebug'} <= tools.keys()
    assert 'sample_path' in tools['WindowsRunTool'].input_schema['properties']
    assert 'password' not in json.dumps([t.input_schema for t in tools.values()])
    async def run():
        result = await tools['WindowsJobStatus'](job_id='other-user-job')
        assert result.state == 'error'
        assert 'guest-private' not in json.dumps(result.model_dump(mode='json'))
    asyncio.run(run())


def test_guest_runner_preserves_literal_argv_and_reports_nonzero_exit(tmp_path):
    from traceglass.windows import guest
    fixture = tmp_path / 'tool.py'
    fixture.write_text('import json,sys; print(json.dumps(sys.argv[1:])); sys.exit(7)')
    result = guest.run_command([sys.executable, str(fixture), 'a & echo injected', 'x"y'], tmp_path, 5)
    assert result['state'] == 'failed' and result['exit_code'] == 7
    assert json.loads((tmp_path / 'stdout.txt').read_text()) == ['a & echo injected', 'x"y']


def test_guest_timeout_is_terminal_and_never_success(tmp_path):
    from traceglass.windows import guest
    result = guest.run_command([sys.executable, '-c', 'import time; time.sleep(10)'], tmp_path, .1)
    assert result['state'] == 'timeout'
    assert result['exit_code'] != 0


def test_transfer_url_never_accepts_another_origin_or_plain_http():
    from traceglass.windows.transport import transfer_url
    assert transfer_url('https://*/guestFile?id=1', '192.0.2.5') == 'https://192.0.2.5/guestFile?id=1'
    for url in ('http://192.0.2.5/guestFile', 'https://attacker.invalid/a',
                'https://192.0.2.5:444/a', 'https://user:pass@192.0.2.5/a'):
        with pytest.raises(ValueError):
            transfer_url(url, '192.0.2.5')


def finish(guest, job, **changes):
    result = dict(state='success', exit_code=0, sample_sha256=job['sample_sha256'], artifacts=[])
    result.update(changes)
    guest.files[job['guest_directory'] + r'\result.json'] = json.dumps(result).encode()
    guest.states[1234] = {'pid': 1234, 'running': False, 'exit_code': 0}


def test_collection_is_idempotent_bounded_and_rejects_symlink_parents(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.wasm').write_bytes(b'\0asm\x01\0\0\0')
    guest = Guest()
    service = WindowsService(config(tmp_path, max_artifact_bytes=4), tmp_path / 'state', transport=guest)
    job = service.submit('alice', 'one', root, 'fixture.wasm', 'wasm2wat')
    finish(guest, job, artifacts=[{'path': 'stdout.txt'}])
    guest.files[job['guest_directory'] + r'\stdout.txt'] = b'fine'
    first = service.collect('alice', 'one', root, job['job_id'])
    assert service.collect('alice', 'one', root, job['job_id'])['artifacts'] == first['artifacts']
    assert (root / first['artifacts'][0]['path']).read_bytes() == b'fine'
    guest.files[job['guest_directory'] + r'\stdout.txt'] = b'longer'
    with pytest.raises(ValueError):
        service.collect('alice', 'one', root, job['job_id'])
    other = tmp_path / 'other'
    other.mkdir()
    second_root = tmp_path / 'second'
    second_root.mkdir()
    (second_root / 'reports').symlink_to(other, target_is_directory=True)
    (second_root / 'fixture.wasm').write_bytes(b'fixture')
    second = service.submit('alice', 'two', second_root, 'fixture.wasm', 'wasm2wat')
    finish(guest, second)
    with pytest.raises(ValueError):
        service.collect('alice', 'two', second_root, second['job_id'])
    assert list(other.iterdir()) == []


def test_dynamic_jobs_hold_lease_until_collected_and_baseline_restored(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ authored fixture; fake guest does not execute')
    guest = Guest()
    guest.check_baseline = lambda name: None
    restored = []
    guest.restore = restored.append
    service = WindowsService(config(tmp_path, allow_execution=True, baseline_snapshot='clean'),
                             tmp_path / 'state', transport=guest)
    job = service.submit('alice', 'one', root, 'fixture.exe', 'execute')
    finish(guest, job)
    assert service.status('alice', 'one', job['job_id'])['state'] == 'success'
    with pytest.raises(RuntimeError, match='busy'):
        service.submit('alice', 'one', root, 'fixture.exe', 'die')
    with pytest.raises(RuntimeError, match='Collect'):
        service.reset('alice', 'one', job['job_id'])
    service.collect('alice', 'one', root, job['job_id'])
    assert service.reset('alice', 'one', job['job_id'])['state'] == 'reset'
    assert restored == ['clean']
    assert service.submit('alice', 'one', root, 'fixture.exe', 'die')['state'] == 'running'


def test_debug_operations_survive_restart_and_collect_memory_dump(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ authored fixture')
    guest = Guest()
    guest.check_baseline = lambda name: None
    cfg = config(tmp_path, allow_execution=True, baseline_snapshot='clean')
    service = WindowsService(cfg, tmp_path / 'state', transport=guest)
    job = service.submit('alice', 'one', root, 'fixture.exe', 'debug_x64')
    launch_request = json.loads(guest.files[job['guest_directory'] + r'\request.json'])
    assert launch_request['debugger_temp'] == job['guest_directory'] + r'\debugger-temp'
    # Guest output cannot redirect later runners into an arbitrary directory.
    finish(guest, job, state='debugging', debugger_pid=42, debugger_temp=r'C:\Untrusted')
    assert service.status('alice', 'one', job['job_id'])['state'] == 'debugging'
    guest.states.clear()
    operation = service.debug('alice', 'one', job['job_id'], 'dump', address='0x1000', length=4)
    operation_request = json.loads(guest.files[operation['operation']['directory'] + r'\request.json'])
    assert operation_request['debugger_temp'] == launch_request['debugger_temp']
    assert operation['operation']['state'] == 'running'
    restarted = WindowsService(cfg, tmp_path / 'state', transport=guest)
    with pytest.raises(RuntimeError, match='pending'):
        restarted.debug('alice', 'one', job['job_id'], 'step')
    with pytest.raises(RuntimeError, match='pending'):
        restarted.collect('alice', 'one', root, job['job_id'])
    directory = operation['operation']['directory']
    guest.files[directory + r'\result.json'] = json.dumps({
        'state': 'debugging', 'sample_sha256': job['sample_sha256'],
        'artifacts': [{'path': 'memory.bin'}]}).encode()
    guest.files[directory + r'\memory.bin'] = b'dump'
    guest.states[1234] = {'pid': 1234, 'running': False, 'exit_code': 0}
    assert restarted.status('alice', 'one', job['job_id'])['operations'][-1]['state'] == 'debugging'
    result = restarted.collect('alice', 'one', root, job['job_id'])
    assert len(result['artifacts']) == 1
    assert (root / result['artifacts'][0]['path']).read_bytes() == b'dump'
    with pytest.raises(ValueError):
        restarted.debug('alice', 'one', job['job_id'], 'breakpoint', address='evil;g')


def test_unconfirmed_start_or_missing_result_never_releases_vm(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'fixture')
    guest = Guest()
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=guest)
    job = service.submit('alice', 'one', root, 'fixture.exe', 'die')
    guest.states[1234] = {'pid': 1234, 'running': False, 'exit_code': 0}
    assert service.status('alice', 'one', job['job_id'])['state'] == 'unknown'
    with pytest.raises(RuntimeError, match='busy'):
        service.submit('alice', 'one', root, 'fixture.exe', 'die')


def test_guest_upload_hash_mismatch_prevents_tool_execution(tmp_path, monkeypatch):
    from traceglass.windows import guest
    sample = tmp_path / 'input.exe'
    sample.write_bytes(b'changed bytes')
    called = []
    monkeypatch.setattr(guest, 'run_command', lambda *a, **k: called.append(a))
    with pytest.raises(ValueError, match='SHA-256'):
        guest.run({'profile': 'die', 'sample': str(sample), 'sample_sha256': '0'*64,
                   'tools': {'die': 'not-executed.exe'}}, tmp_path)
    assert called == []


def test_transport_pins_certificate_rejects_redirect_and_uses_exact_vm(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import ssl
    from types import SimpleNamespace
    pytest.importorskip('pyVmomi')
    import pyVim.connect
    from pyVmomi import vim
    from traceglass.windows.transport import VSphereTransport, NoRedirect
    cfg = config(tmp_path)
    calls = []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    class VM:
        name = 'yaozh'
        runtime = SimpleNamespace(powerState='poweredOn')
        guest = SimpleNamespace(guestOperationsReady=True)
    si = SimpleNamespace(_stub='sdk-stub', RetrieveContent=lambda: SimpleNamespace(guestOperationsManager='manager'))
    monkeypatch.setattr(pyVim.connect, 'SmartConnect', lambda **kw: (calls.append(kw), si)[1])
    monkeypatch.setattr(pyVim.connect, 'Disconnect', lambda value: calls.append('disconnected'))
    monkeypatch.setattr(vim, 'VirtualMachine', lambda vm_id, stub: (calls.append((vm_id, stub)), VM())[1])
    transport = VSphereTransport(cfg)
    monkeypatch.setattr(transport, '_context', lambda: context)
    with transport._connection() as (manager, vm, auth):
        assert manager == 'manager' and vm.name == cfg.vm_name
        assert auth.username == cfg.guest_user and auth.interactiveSession is False
    assert calls[0]['sslContext'] is context
    assert calls[1] == ('48', 'sdk-stub') and calls[-1] == 'disconnected'
    VM.name = 'different-vm'
    with pytest.raises(RuntimeError):
        with transport._connection():
            pytest.fail('Wrong VM must not be used')
    with pytest.raises(ValueError, match='redirect'):
        NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid')


def test_fixed_execute_profile_rejects_command_scripts(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'input.cmd').write_text('echo fixture')
    guest = Guest()
    guest.check_baseline = lambda name: None
    service = WindowsService(config(tmp_path, allow_execution=True, baseline_snapshot='clean'),
                             tmp_path / 'state', transport=guest)
    with pytest.raises(ValueError, match='PE executable'):
        service.submit('alice', 'one', root, 'input.cmd', 'execute', arguments=['& echo injected'])
    assert guest.started == []


def test_worker_cancellation_and_output_budget_terminate_real_child(tmp_path):
    from traceglass.windows import guest
    (tmp_path / 'cancel').write_text('cancel')
    result = guest.run_command([sys.executable, '-c', 'import time; time.sleep(30)'], tmp_path, 10)
    assert result['state'] == 'cancelled' and result['exit_code'] != 0
    (tmp_path / 'cancel').unlink()
    result = guest.run_command([sys.executable, '-c', 'import sys,time; print("x"*2000,flush=True); time.sleep(30)'],
                               tmp_path, 10, max_output=1024)
    assert result['state'] == 'output_limit' and result['exit_code'] != 0


def test_wrong_result_hash_stays_unknown_and_guest_credentials_never_enter_request(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ fixture')
    guest = Guest()
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=guest)
    job = service.submit('alice', 'one', root, 'fixture.exe', 'die')
    request = guest.files[job['guest_directory'] + r'\request.json']
    assert b'host-private' not in request and b'guest-private' not in request
    finish(guest, job, sample_sha256='0'*64)
    assert service.status('alice', 'one', job['job_id'])['state'] == 'unknown'
    with pytest.raises(RuntimeError, match='busy'):
        service.submit('alice', 'one', root, 'fixture.exe', 'die')


def test_lost_start_response_returns_queryable_job_and_never_replays(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ fixture')
    guest = Guest()
    def lost_response(*_):
        raise OSError('connection lost after guest accepted start; guest-private')
    guest.start = lost_response
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=guest)
    job = service.submit('alice', 'one', root, 'fixture.exe', 'die')
    assert job['state'] == 'unknown' and job['job_id']
    assert 'guest-private' not in json.dumps(job)
    assert service.status('alice', 'one', job['job_id'])['state'] == 'unknown'
    finish(guest, job)
    assert service.status('alice', 'one', job['job_id'])['state'] == 'success'


def test_host_input_rejects_hardlinks_and_symlink_files(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'private.exe'
    outside.write_bytes(b'MZ outside workspace')
    os.link(outside, root / 'hard.exe')
    (root / 'sym.exe').symlink_to(outside)
    guest = Guest()
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=guest)
    for name in ('hard.exe', 'sym.exe'):
        with pytest.raises(ValueError):
            service.submit('alice', 'one', root, name, 'die')
    assert guest.started == []


def test_debugger_session_temp_is_shared_by_open_and_followups_without_leaking_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from traceglass.windows import guest
    monkeypatch.setenv('TEMP', str(tmp_path / 'system-temp'))
    monkeypatch.delenv('TMP', raising=False)
    shared = tmp_path / 'job' / 'debugger-temp'
    operations = []
    class Client:
        def __init__(self, executable): pass
        def start_session(self, *args, **kwargs):
            # Mirrors the plugin/client rendezvous: the process inherits TEMP.
            directory = Path(os.environ['TEMP'])
            (directory / 'xauto_session.42.lock').write_text('owned-session')
            operations.append(('start', args, kwargs))
            return 42
        def load_executable(self, sample, cmdline='', current_dir=''):
            operations.append(('load', sample))
            return True
        def wait_cmd_ready(self): pass
        def attach_session(self, pid):
            assert (Path(os.environ['TEMP']) / f'xauto_session.{pid}.lock').read_text() == 'owned-session'
        def detach_session(self): pass
        def terminate_session(self): (Path(os.environ['TEMP']) / 'xauto_session.42.lock').unlink()
        def is_debugging(self): return True
        def is_running(self): return False
        def get_regs(self): return {'rip': 0x1000}
        def read_memory(self, address, length): return b'ABCD'
    monkeypatch.setitem(sys.modules, 'x64dbg_automate', SimpleNamespace(X64DbgClient=Client))
    request = dict(profile='debug_x64', sample='fixture.exe', tools={'x64dbg': 'fixed-debugger.exe'},
                   debugger_temp=str(shared))
    assert guest.debug(request, tmp_path)['state'] == 'debugging'
    request.update(action='dump', debugger_pid=42, address='0x1000', length=4)
    followup = tmp_path / 'job' / 'op-one'
    followup.mkdir(parents=True)
    assert guest.debug(request, followup)['state'] == 'debugging'
    assert (followup / 'memory.bin').read_bytes() == b'ABCD'
    request['action'] = 'close'
    assert guest.debug(request, followup)['closed']
    assert operations == [('start', (), {}), ('load', 'fixture.exe')]
    assert os.environ['TEMP'] == str(tmp_path / 'system-temp') and 'TMP' not in os.environ


def test_debugger_startup_failure_is_distinct_from_memory_dump_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from traceglass.windows import guest
    class Client:
        def __init__(self, executable): pass
        def start_session(self, *args, **kwargs):
            raise TimeoutError('Session did not appear in a reasonable amount of time')
        def detach_session(self): pass
    monkeypatch.setitem(sys.modules, 'x64dbg_automate', SimpleNamespace(X64DbgClient=Client))
    result = guest.debug(dict(profile='debug_x64', sample='fixture.exe',
        tools={'x64dbg': 'fixed-debugger.exe'}), tmp_path)
    assert result['state'] == 'failed' and result['failure_stage'] == 'session_establishment'
    assert result['error_type'] == 'TimeoutError' and result['memory_dump_attempted'] is False


def test_guest_debug_actions_keep_owned_pid_and_close_after_target_deletes_itself(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from traceglass.windows import guest
    calls = []
    class Client:
        def __init__(self, executable): calls.append(('executable', executable))
        def attach_session(self, pid): calls.append(('attach', pid))
        def detach_session(self): calls.append(('detach',))
        def terminate_session(self): calls.append(('close',))
        def is_debugging(self): return True
        def is_running(self): return False
        def get_regs(self): return {'rip': 0x1000}
        def read_memory(self, address, length):
            calls.append(('memory', address, length))
            return b'1234'
    monkeypatch.setitem(sys.modules, 'x64dbg_automate', SimpleNamespace(X64DbgClient=Client))
    request = dict(profile='debug_x64', action='registers', debugger_pid=42,
        sample=str(tmp_path/'deleted.exe'), sample_sha256='a'*64, tools={'x64dbg': 'fixed-debugger.exe'})
    assert guest.run(request, tmp_path)['registers'] == {'rip': 0x1000}
    request.update(action='dump', address='0x1000', length=4)
    guest.run(request, tmp_path)
    assert (tmp_path/'memory.bin').read_bytes() == b'1234'
    request['action'] = 'close'
    assert guest.run(request, tmp_path)['closed']
    assert [item for item in calls if item[0] == 'attach'] == [('attach', 42)]*3


def test_unknown_debug_operation_can_only_abandon_results_via_owned_baseline_reset(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ fixture')
    guest = Guest()
    restored = []
    guest.check_baseline = lambda name: None
    guest.restore = restored.append
    service = WindowsService(config(tmp_path, allow_execution=True, baseline_snapshot='clean'),
                             tmp_path / 'state', transport=guest)
    job = service.submit('alice', 'one', root, 'fixture.exe', 'debug_x64')
    finish(guest, job, state='debugging', debugger_pid=42)
    def lost_response(*_): raise OSError('ambiguous start')
    guest.start = lost_response
    operation = service.debug('alice', 'one', job['job_id'], 'step')
    assert operation['state'] == 'unknown'
    with pytest.raises(RuntimeError, match='Collect'):
        service.reset('alice', 'one', job['job_id'])
    with pytest.raises(LookupError):
        service.reset('alice', 'other', job['job_id'], discard_pending=True)
    result = service.reset('alice', 'one', job['job_id'], discard_pending=True)
    assert result['discarded_pending_results'] and restored == ['clean']


def test_unready_guest_does_not_reserve_vm_before_submission(tmp_path):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ fixture')
    guest = Guest()
    def not_ready():
        raise RuntimeError('VMware Tools Guest Operations is not ready')
    guest.probe = not_ready
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=guest)
    with pytest.raises(RuntimeError, match='not ready'):
        service.submit('alice', 'one', root, 'fixture.exe', 'die')
    assert guest.started == []
    guest.probe = lambda: {'guest_operations_ready': True}
    assert service.submit('alice', 'one', root, 'fixture.exe', 'die')['state'] == 'running'


@pytest.mark.parametrize('failed_upload', ['input.exe', 'runner.py', 'request.json'])
def test_failure_before_start_releases_only_the_unstarted_job(tmp_path, failed_upload):
    from traceglass.windows.service import WindowsService
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ fixture')
    guest = Guest()
    put = guest.put
    def fail_upload(path, data):
        if path.endswith('\\' + failed_upload):
            raise OSError('upload failed; host-private')
        put(path, data)
    guest.put = fail_upload
    service = WindowsService(config(tmp_path), tmp_path / 'state', transport=guest)
    job = service.submit('alice', 'one', root, 'fixture.exe', 'die')
    assert job['state'] == 'failed'
    assert guest.started == []
    assert 'host-private' not in json.dumps(job)
    guest.put = put
    assert service.submit('alice', 'one', root, 'fixture.exe', 'die')['state'] == 'running'


@pytest.mark.parametrize('power, failure', [
    ('poweredOff', None), ('poweredOn', None), ('poweredOff', 'power_on'),
    ('poweredOn', 'guest_tools'), ('poweredOn', 'credentials'),
])
def test_reset_releases_lease_only_after_restored_guest_is_usable(tmp_path, monkeypatch, power, failure):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from traceglass.windows.service import WindowsService
    from traceglass.windows.transport import VSphereTransport
    from traceglass.windows import transport as transport_module
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ fixture')
    cfg = config(tmp_path, allow_execution=True, baseline_snapshot='clean')
    guest = Guest()
    guest.check_baseline = lambda name: None
    service = WindowsService(cfg, tmp_path / 'state', transport=guest)
    job = service.submit('alice', 'one', root, 'fixture.exe', 'execute')
    finish(guest, job)
    service.collect('alice', 'one', root, job['job_id'])
    clock = [0.0]
    class VM:
        runtime = SimpleNamespace(powerState=power)
        guest = SimpleNamespace(guestOperationsReady=False)
        def PowerOnVM_Task(self):
            if failure != 'power_on':
                self.runtime.powerState = 'poweredOn'
            return SimpleNamespace(info=SimpleNamespace(state='error' if failure == 'power_on' else 'success'))
    vm = VM()
    def sleep(seconds):
        clock[0] += seconds
        if vm.runtime.powerState == 'poweredOn' and clock[0] >= 1 and failure != 'guest_tools':
            vm.guest.guestOperationsReady = True
    monkeypatch.setattr(transport_module.time, 'sleep', sleep)
    monkeypatch.setattr(transport_module.time, 'monotonic', lambda: clock[0])
    class Snapshot:
        def RevertToSnapshot_Task(self):
            return SimpleNamespace(info=SimpleNamespace(state='success'))
    validated = []
    class Auth:
        def ValidateCredentialsInGuest(self, guest_vm, auth):
            if failure == 'credentials':
                raise RuntimeError('credentials unavailable')
            assert guest_vm.runtime.powerState == 'poweredOn'
            assert guest_vm.guest.guestOperationsReady
            validated.append(True)
    @contextmanager
    def connection(**kwargs):
        yield SimpleNamespace(authManager=Auth()), vm, object()
    transport = VSphereTransport(cfg)
    monkeypatch.setattr(transport, '_connection', connection)
    monkeypatch.setattr(transport, '_baseline', lambda *args: Snapshot())
    guest.restore = transport.restore
    if failure:
        with pytest.raises(RuntimeError):
            service.reset('alice', 'one', job['job_id'])
        with pytest.raises(RuntimeError, match='busy'):
            service.submit('alice', 'one', root, 'fixture.exe', 'die')
    else:
        assert service.reset('alice', 'one', job['job_id'])['state'] == 'reset'
        assert validated
        assert service.submit('alice', 'one', root, 'fixture.exe', 'die')['state'] == 'running'
