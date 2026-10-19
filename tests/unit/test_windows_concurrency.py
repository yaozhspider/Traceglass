"""Single VM admission and recovery use durable, caller-scoped task leases."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import PureWindowsPath
import sqlite3
import sys
import threading

import pytest

from test_windows_tools import Guest, config
from traceglass.windows.service import WindowsService


class ParallelGuest(Guest):
    def __init__(self):
        super().__init__()
        self.restored = []
        self.guard = threading.Lock()

    def start(self, executable, arguments, cwd):
        with self.guard:
            self.started.append((executable, arguments, cwd))
            return 1000 + len(self.started)

    def check_baseline(self, name):
        assert name == 'clean'

    def restore(self, name):
        self.restored.append(name)

    def finish(self, job, **changes):
        result = dict(state='success', sample_sha256=job['sample_sha256'], artifacts=[])
        result.update(changes)
        self.files[str(PureWindowsPath(job['guest_directory']) / 'result.json')] = json.dumps(result).encode()
        self.states[job['pid']] = {'pid': job['pid'], 'running': False, 'exit_code': 0}


def setup(tmp_path, **changes):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'fixture.exe').write_bytes(b'MZ authored fixture; not executed')
    cfg = config(tmp_path, allow_execution=True, baseline_snapshot='clean',
                 max_concurrent_jobs=8, **changes)
    guest = ParallelGuest()
    service = WindowsService(cfg, tmp_path / 'state', transport=guest)
    return root, cfg, guest, service


def test_eight_shared_jobs_survive_restart_ninth_waits_and_collection_frees_one(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    jobs = [service.submit('alice', f'w{i}', root, 'fixture.exe', 'die') for i in range(8)]
    assert len({j['guest_directory'] for j in jobs}) == 8
    assert len({j['task_temp'] for j in jobs}) == 8
    restarted = WindowsService(cfg, tmp_path / 'state', transport=guest)
    with pytest.raises(RuntimeError, match='busy'):
        restarted.submit('alice', 'ninth', root, 'fixture.exe', 'die')
    guest.finish(jobs[0])
    restarted.collect('alice', 'w0', root, jobs[0]['job_id'])
    assert restarted.submit('alice', 'ninth', root, 'fixture.exe', 'die')['state'] == 'running'
    assert len(guest.started) == 9
    with pytest.raises(LookupError):
        restarted.cancel('alice', 'ninth', jobs[1]['job_id'])


def test_multiple_service_instances_cannot_overbook_eight_slots(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    services = [service] + [WindowsService(cfg, tmp_path / 'state', transport=guest) for _ in range(11)]
    barrier = threading.Barrier(12)
    def submit(index):
        barrier.wait(timeout=10)
        try:
            return services[index].submit('alice', str(index), root, 'fixture.exe', 'die')['state']
        except RuntimeError as exc:
            assert 'busy' in str(exc)
            return 'busy'
    with ThreadPoolExecutor(max_workers=12) as pool:
        states = list(pool.map(submit, range(12)))
    assert states.count('running') == 8 and states.count('busy') == 4
    assert len(guest.started) == 8


def test_dynamic_default_exclusive_but_shared_debug_sessions_have_separate_temps(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    first = service.submit('alice', 'a', root, 'fixture.exe', 'debug_x64', isolation='shared')
    second = service.submit('alice', 'b', root, 'fixture.exe', 'debug_x32', isolation='shared')
    assert first['debugger_temp'] != second['debugger_temp']
    with pytest.raises(RuntimeError, match='busy'):
        service.submit('alice', 'c', root, 'fixture.exe', 'execute')
    with pytest.raises(RuntimeError, match='other tasks'):
        service.reset('alice', 'a', first['job_id'], discard_pending=True)
    assert guest.restored == []


def test_shared_terminal_collection_defers_reset_until_last_job(tmp_path):
    root, cfg, guest, service = setup(tmp_path, auto_reset_on_idle=True)
    first = service.submit('alice', 'a', root, 'fixture.exe', 'execute', isolation='shared')
    second = service.submit('alice', 'b', root, 'fixture.exe', 'die')
    guest.finish(first)
    collected = service.collect('alice', 'a', root, first['job_id'])
    assert (root / collected['manifest_path']).is_file()
    assert guest.restored == []
    # Slot release must not forget this batch's dirty system state.
    restarted = WindowsService(cfg, tmp_path / 'state', transport=guest)
    guest.finish(second)
    restarted.collect('alice', 'b', root, second['job_id'])
    assert guest.restored == ['clean']
    assert restarted.submit('alice', 'c', root, 'fixture.exe', 'die')['state'] == 'running'
    with pytest.raises(RuntimeError):
        restarted.reset('alice', 'a', first['job_id'], discard_pending=True)


def test_without_automatic_reset_last_shared_job_can_restore_but_not_overwrite_sibling(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    first = service.submit('alice', 'a', root, 'fixture.exe', 'execute', isolation='shared')
    second = service.submit('alice', 'b', root, 'fixture.exe', 'execute', isolation='shared')
    guest.finish(first)
    service.collect('alice', 'a', root, first['job_id'])
    with pytest.raises(RuntimeError, match='other tasks'):
        service.reset('alice', 'a', first['job_id'])
    guest.finish(second)
    service.collect('alice', 'b', root, second['job_id'])
    with pytest.raises(RuntimeError, match='busy'):
        service.submit('alice', 'c', root, 'fixture.exe', 'die')
    service.reset('alice', 'b', second['job_id'])
    assert guest.restored == ['clean']


def test_failed_legacy_exclusive_lease_is_migrated_never_silently_released(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    state = tmp_path / 'legacy'
    state.mkdir()
    record = dict(job_id='old', user_id='alice', workspace_id='a', root=str(root),
                  profile='debug_x64', state='failed', launch_attempted=True,
                  result={'state': 'failed'}, guest_directory=r'C:\Traceglass\old', sample_sha256=None)
    with sqlite3.connect(state / 'jobs.sqlite3') as db:
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY,user_id TEXT,workspace_id TEXT,record TEXT)')
        db.execute('INSERT INTO jobs VALUES (?,?,?,?)', ('old', 'alice', 'a', json.dumps(record)))
        db.execute('CREATE TABLE lease (singleton INTEGER PRIMARY KEY CHECK(singleton=1),job_id TEXT)')
        db.execute('INSERT INTO lease VALUES (1,?)', ('old',))
    migrated = WindowsService(cfg, state, transport=guest)
    assert migrated.status('alice', 'a', 'old')['state'] == 'failed'
    with pytest.raises(RuntimeError, match='busy'):
        migrated.submit('alice', 'b', root, 'fixture.exe', 'die')
    assert guest.restored == []


@pytest.mark.parametrize('value', [0, -1, 65, True, 1.5, '8'])
def test_invalid_concurrency_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        config(tmp_path, max_concurrent_jobs=value)


def test_close_one_debugger_collect_release_keeps_sibling_session_alive(tmp_path):
    root, cfg, guest, service = setup(tmp_path, auto_reset_on_idle=True)
    first = service.submit('alice', 'a', root, 'fixture.exe', 'debug_x64', isolation='shared')
    second = service.submit('alice', 'b', root, 'fixture.exe', 'debug_x64', isolation='shared')
    guest.finish(first, state='debugging', debugger_pid=41)
    guest.finish(second, state='debugging', debugger_pid=42)
    # Collecting intermediate observations must retain the open debugger slot.
    assert service.collect('alice', 'a', root, first['job_id'])['slot_released'] is False
    close = service.debug('alice', 'a', first['job_id'], 'close')['operation']
    guest.finish({'pid': close['pid'], 'guest_directory': close['directory'],
                  'sample_sha256': first['sample_sha256']}, closed=True, debugger_pid=41)
    assert service.collect('alice', 'a', root, first['job_id'])['slot_released'] is True
    assert guest.restored == []
    registers = service.debug('alice', 'b', second['job_id'], 'registers')['operation']
    request = json.loads(guest.files[registers['directory'] + r'\request.json'])
    assert request['debugger_pid'] == 42 and request['debugger_temp'] == second['debugger_temp']
    with pytest.raises(RuntimeError, match='other tasks'):
        service.reset('alice', 'a', first['job_id'])


def test_failed_debug_open_releases_only_after_confirmed_owned_process_cleanup(tmp_path):
    root, cfg, guest, service = setup(tmp_path, auto_reset_on_idle=True)
    first = service.submit('alice', 'a', root, 'fixture.exe', 'debug_x64', isolation='shared')
    second = service.submit('alice', 'b', root, 'fixture.exe', 'debug_x64', isolation='shared')
    guest.finish(first, state='failed', debugger_cleanup_confirmed=True)
    guest.finish(second, state='failed', debugger_cleanup_confirmed=False)
    assert service.collect('alice', 'a', root, first['job_id'])['slot_released'] is True
    assert service.collect('alice', 'b', root, second['job_id'])['slot_released'] is False
    assert guest.restored == []
    service.reset('alice', 'b', second['job_id'])
    assert guest.restored == ['clean']


def test_recovery_failure_keeps_evidence_and_blocks_admission_until_retry(tmp_path):
    root, cfg, guest, service = setup(tmp_path, auto_reset_on_idle=True)
    first = service.submit('alice', 'a', root, 'fixture.exe', 'execute', isolation='shared')
    guest.finish(first)
    def failed_restore(name):
        raise OSError('Guest readiness failure; private host details')
    guest.restore = failed_restore
    collected = service.collect('alice', 'a', root, first['job_id'])
    assert (root / collected['manifest_path']).is_file()
    assert collected['recovery']['state'] == 'reset_required'
    assert 'private host details' not in json.dumps(collected)
    restarted = WindowsService(cfg, tmp_path / 'state', transport=guest)
    assert restarted.capacity('alice')['available_slots'] == 0
    with pytest.raises(RuntimeError, match='busy'):
        restarted.submit('alice', 'b', root, 'fixture.exe', 'die')
    guest.restore = guest.restored.append
    restarted.reset('alice', 'a', first['job_id'])
    assert restarted.submit('alice', 'b', root, 'fixture.exe', 'die')['state'] == 'running'


def test_snapshot_recovery_barrier_prevents_cross_instance_start_or_second_restore(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    other = WindowsService(cfg, tmp_path / 'state', transport=guest)
    first = service.submit('alice', 'a', root, 'fixture.exe', 'execute', isolation='shared')
    guest.finish(first)
    service.collect('alice', 'a', root, first['job_id'])
    entered, finish = threading.Event(), threading.Event()
    def restore(name):
        entered.set()
        assert finish.wait(timeout=10)
        guest.restored.append(name)
    guest.restore = restore
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(service.reset, 'alice', 'a', first['job_id'])
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(RuntimeError, match='busy'):
                other.submit('alice', 'b', root, 'fixture.exe', 'die')
            with pytest.raises(RuntimeError, match='in progress'):
                other.reset('alice', 'a', first['job_id'])
        finally:
            finish.set()
        assert task.result(timeout=10)['state'] == 'reset'
    assert guest.restored == ['clean'] and len(guest.started) == 1


def test_after_idle_restore_results_are_retrievable_from_collected_local_evidence(tmp_path):
    root, cfg, guest, service = setup(tmp_path, auto_reset_on_idle=True)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'execute', isolation='shared')
    guest.finish(job, artifacts=[{'path': 'stdout.txt'}])
    guest.files[job['guest_directory'] + r'\stdout.txt'] = b'kept evidence'
    first = service.collect('alice', 'a', root, job['job_id'])
    guest.files.clear()  # snapshot revert removed the remote files
    again = service.collect('alice', 'a', root, job['job_id'])
    assert again['manifest_path'] == first['manifest_path']
    assert (root / again['artifacts'][0]['path']).read_bytes() == b'kept evidence'


def test_capacity_is_read_only_scoped_and_does_not_expose_other_task_ids(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'die')
    status = service.capacity('alice')
    assert status['occupied_slots'] == 1 and status['available_slots'] == 7
    assert job['job_id'] not in json.dumps(status)
    assert len(guest.started) == 1
    with pytest.raises(PermissionError):
        service.capacity('bob')


def test_guest_debug_open_failure_reports_whether_cleanup_was_confirmed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import sys
    from traceglass.windows import guest
    class Process:
        done = False
        def poll(self): return 1 if self.done else None
    class Client:
        def __init__(self, executable): self.proc = Process()
        def start_session(self): raise TimeoutError('not discovered')
        def detach_session(self): pass
    def stop(process): process.done = True
    monkeypatch.setitem(sys.modules, 'x64dbg_automate', SimpleNamespace(X64DbgClient=Client))
    monkeypatch.setattr(guest, 'stop_tree', stop)
    result = guest.debug({'profile': 'debug_x64', 'sample': 'fixture.exe',
                          'tools': {'x64dbg': 'fixed.exe'}}, tmp_path)
    assert result['state'] == 'failed' and result['debugger_cleanup_confirmed'] is True
    def failed_stop(process): raise OSError('cannot kill')
    monkeypatch.setattr(guest, 'stop_tree', failed_stop)
    assert guest.debug({'profile': 'debug_x64', 'sample': 'fixture.exe',
                        'tools': {'x64dbg': 'fixed.exe'}}, tmp_path)['debugger_cleanup_confirmed'] is False


def test_success_envelope_without_debug_close_does_not_release_session(tmp_path):
    root, cfg, guest, service = setup(tmp_path, auto_reset_on_idle=True)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'debug_x64', isolation='shared')
    guest.finish(job, state='success', debugger_pid=42)
    assert service.collect('alice', 'a', root, job['job_id'])['slot_released'] is False
    assert guest.restored == []


def test_running_job_requires_explicit_abandon_before_reset(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'execute')
    with pytest.raises(RuntimeError, match='Collect'):
        service.reset('alice', 'a', job['job_id'])
    assert guest.restored == []
    assert service.reset('alice', 'a', job['job_id'], discard_pending=True)['state'] == 'reset'


def test_new_tools_expose_shared_mode_and_capacity_without_touching_guest(tmp_path):
    import asyncio
    from traceglass.windows.tools import windows_tools
    root, cfg, guest, service = setup(tmp_path)
    tools = {t.name: t for t in windows_tools(service, 'alice', 'a', root)}
    assert tools['WindowsVmStatus'].is_read_only
    assert tools['WindowsRunTool'].input_schema['properties']['isolation']['enum'] == ['auto', 'shared', 'exclusive']
    async def run():
        capacity = json.loads((await tools['WindowsVmStatus']()).content[0].text)
        assert capacity['available_slots'] == 8
        await tools['WindowsRunTool'](sample_path='fixture.exe', profile='debug_x64', isolation='shared')
        await tools['WindowsRunTool'](sample_path='fixture.exe', profile='debug_x32', isolation='shared')
        capacity = json.loads((await tools['WindowsVmStatus']()).content[0].text)
        assert capacity['occupied_slots'] == 2
    asyncio.run(run())
    assert len(guest.started) == 2


def test_backend_crash_during_restore_allows_recovery_retry_but_not_new_jobs(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'execute', isolation='shared')
    guest.finish(job)
    service.collect('alice', 'a', root, job['job_id'])
    assert service.scheduler.begin_reset(service._load('alice', 'a', job['job_id']))
    with sqlite3.connect(tmp_path / 'state' / 'jobs.sqlite3') as db:
        db.execute('UPDATE windows_vm SET reset_pid=-1')  # old backend no longer exists
    restarted = WindowsService(cfg, tmp_path / 'state', transport=guest)
    assert restarted.capacity('alice')['recovery_state'] == 'reset_required'
    with pytest.raises(RuntimeError, match='busy'):
        restarted.submit('alice', 'b', root, 'fixture.exe', 'die')
    restarted.reset('alice', 'a', job['job_id'])
    assert guest.restored == ['clean']


def test_busy_tool_response_is_structured_and_does_not_start_ninth_job(tmp_path):
    import asyncio
    from traceglass.windows.tools import windows_tools
    root, cfg, guest, service = setup(tmp_path)
    for i in range(8):
        service.submit('alice', f'w{i}', root, 'fixture.exe', 'die')
    tools = {t.name: t for t in windows_tools(service, 'alice', 'ninth', root)}
    async def run():
        result = await tools['WindowsRunTool'](sample_path='fixture.exe', profile='die')
        data = json.loads(result.content[0].text)
        assert result.state == 'error' and data['state'] == 'busy'
        assert data['execution_started'] is False and data['capacity']['available_slots'] == 0
        assert data['capacity']['occupied_slots'] == 8
        assert 'job_id' not in data
    asyncio.run(run())
    assert len(guest.started) == 8


def test_migrated_static_job_does_not_require_a_dynamic_baseline(tmp_path):
    root, _, guest, _ = setup(tmp_path)
    state = tmp_path / 'old-static'
    state.mkdir()
    record = dict(job_id='old-static', user_id='alice', workspace_id='a', root=str(root),
                  profile='die', state='success', launch_attempted=True,
                  result={'state': 'success', 'artifacts': []},
                  guest_directory=r'C:\Traceglass\old-static', sample_sha256=None)
    with sqlite3.connect(state / 'jobs.sqlite3') as db:
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY,user_id TEXT,workspace_id TEXT,record TEXT)')
        db.execute('INSERT INTO jobs VALUES (?,?,?,?)', ('old-static', 'alice', 'a', json.dumps(record)))
        db.execute('CREATE TABLE lease (singleton INTEGER PRIMARY KEY CHECK(singleton=1),job_id TEXT)')
        db.execute('INSERT INTO lease VALUES (1,?)', ('old-static',))
    migrated = WindowsService(config(tmp_path, max_concurrent_jobs=8), state, transport=guest)
    migrated.collect('alice', 'a', root, 'old-static')
    assert migrated.capacity('alice')['baseline_dirty'] is False
    assert migrated.submit('alice', 'b', root, 'fixture.exe', 'die')['state'] == 'running'


def test_stale_status_cannot_erase_a_new_debug_operation_from_another_instance(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'debug_x64', isolation='shared')
    guest.finish(job, state='debugging', debugger_pid=42)
    service.status('alice', 'a', job['job_id'])
    other = WindowsService(cfg, tmp_path / 'state', transport=guest)
    loaded, resume = threading.Event(), threading.Event()
    load = other._load
    def paused_load(*args):
        record = load(*args)
        loaded.set()
        assert resume.wait(timeout=10)
        return record
    other._load = paused_load
    with ThreadPoolExecutor(max_workers=1) as pool:
        stale_status = pool.submit(other.status, 'alice', 'a', job['job_id'])
        try:
            assert loaded.wait(timeout=10)
            service.debug('alice', 'a', job['job_id'], 'continue')
        finally:
            resume.set()
        with pytest.raises(RuntimeError, match='changed concurrently'):
            stale_status.result(timeout=10)
    assert len(service.status('alice', 'a', job['job_id'])['operations']) == 1
    with pytest.raises(RuntimeError, match='pending'):
        other.debug('alice', 'a', job['job_id'], 'close')


def test_stale_job_cannot_cross_reset_barrier_or_reset_over_newer_observations(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'execute', isolation='shared')
    guest.finish(job)
    service.collect('alice', 'a', root, job['job_id'])
    stale = service._load('alice', 'a', job['job_id'])
    service.status('alice', 'a', job['job_id'])
    with pytest.raises(RuntimeError, match='changed concurrently'):
        service.scheduler.begin_reset(stale)
    current = service._load('alice', 'a', job['job_id'])
    service.scheduler.begin_reset(current)
    with pytest.raises(RuntimeError, match='busy'):
        service._save(current)


def test_old_operation_cannot_launch_after_reset_and_new_exclusive_job(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'debug_x64', isolation='shared')
    guest.finish(job, state='debugging', debugger_pid=42)
    service.status('alice', 'a', job['job_id'])
    other = WindowsService(cfg, tmp_path / 'state', transport=guest)
    entered, resume = threading.Event(), threading.Event()
    launch = service._launch
    def paused_launch(*args, **kwargs):
        entered.set()
        assert resume.wait(timeout=10)
        return launch(*args, **kwargs)
    service._launch = paused_launch
    with ThreadPoolExecutor(max_workers=1) as pool:
        operation = pool.submit(service.debug, 'alice', 'a', job['job_id'], 'continue')
        try:
            assert entered.wait(timeout=10)
            other.reset('alice', 'a', job['job_id'], discard_pending=True)
            other.submit('alice', 'b', root, 'fixture.exe', 'execute')
        finally:
            resume.set()
        with pytest.raises(RuntimeError):
            operation.result(timeout=10)
    assert len(guest.started) == 2  # initial task and new task only


def test_reset_waits_for_in_flight_guest_start_even_when_discard_is_requested(tmp_path):
    root, cfg, guest, service = setup(tmp_path)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'debug_x64', isolation='shared')
    guest.finish(job, state='debugging', debugger_pid=42)
    service.status('alice', 'a', job['job_id'])
    other = WindowsService(cfg, tmp_path / 'state', transport=guest)
    entered, resume = threading.Event(), threading.Event()
    start = guest.start
    def paused_start(*args, **kwargs):
        entered.set()
        assert resume.wait(timeout=10)
        return start(*args, **kwargs)
    guest.start = paused_start
    with ThreadPoolExecutor(max_workers=1) as pool:
        operation = pool.submit(service.debug, 'alice', 'a', job['job_id'], 'continue')
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(RuntimeError, match='launch is in progress'):
                other.reset('alice', 'a', job['job_id'], discard_pending=True)
            assert guest.restored == []
        finally:
            resume.set()
        with pytest.raises(RuntimeError, match='changed concurrently'):
            operation.result(timeout=10)


def test_static_worker_children_inherit_task_temp_and_parent_environment_is_restored(tmp_path, monkeypatch):
    import hashlib
    import os
    from traceglass.windows import guest
    sample = tmp_path / 'fixture.wasm'
    sample.write_text('import os; print(os.environ["TEMP"]); print(os.environ["TMP"])')
    temporary = tmp_path / 'task-temp'
    monkeypatch.setenv('TEMP', str(tmp_path / 'original'))
    monkeypatch.delenv('TMP', raising=False)
    result = guest.run(dict(profile='wasm2wat', sample=str(sample),
                            sample_sha256=hashlib.sha256(sample.read_bytes()).hexdigest(),
                            tools={'wasm2wat': sys.executable}, timeout=5, task_temp=str(temporary)), tmp_path)
    assert result['state'] == 'success'
    assert (tmp_path / 'stdout.txt').read_text().splitlines() == [str(temporary), str(temporary)]
    assert os.environ['TEMP'] == str(tmp_path / 'original') and 'TMP' not in os.environ


def test_partial_snapshot_failure_returns_saved_evidence_without_guest_access(tmp_path):
    root, cfg, guest, service = setup(tmp_path, auto_reset_on_idle=True)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'execute', isolation='shared')
    guest.finish(job, artifacts=[{'path': 'stdout.txt'}])
    guest.files[job['guest_directory'] + r'\stdout.txt'] = b'preserved before revert'
    def partial_restore(name):
        guest.files.clear()
        raise OSError('Snapshot reverted but guest readiness failed')
    guest.restore = partial_restore
    first = service.collect('alice', 'a', root, job['job_id'])
    assert first['recovery']['state'] == 'reset_required'
    def unavailable(*args): raise OSError('Guest is not ready')
    guest.get = guest.process = unavailable
    again = service.collect('alice', 'a', root, job['job_id'])
    assert again['manifest_path'] == first['manifest_path']
    assert again['recovery']['state'] == 'reset_required'
    assert again['slot_released'] is True
    assert (root / again['artifacts'][0]['path']).read_bytes() == b'preserved before revert'


@pytest.mark.parametrize('restore_fails', [False, True])
def test_saved_debug_evidence_survives_discarded_pending_operation_and_unready_guest(tmp_path, restore_fails):
    root, cfg, guest, service = setup(tmp_path)
    job = service.submit('alice', 'a', root, 'fixture.exe', 'debug_x64')
    guest.finish(job, state='debugging', debugger_pid=42)
    first = service.collect('alice', 'a', root, job['job_id'])
    service.debug('alice', 'a', job['job_id'], 'continue')
    def restore(name):
        guest.files.clear()
        if restore_fails:
            raise OSError('Snapshot reverted but guest readiness failed')
    guest.restore = restore
    if restore_fails:
        with pytest.raises(OSError):
            service.reset('alice', 'a', job['job_id'], discard_pending=True)
    else:
        service.reset('alice', 'a', job['job_id'], discard_pending=True)
    def unavailable(*args): raise OSError('Guest is not ready')
    guest.get = guest.process = unavailable
    again = service.collect('alice', 'a', root, job['job_id'])
    assert again['manifest_path'] == first['manifest_path']
    assert again['recovery']['state'] == ('reset_required' if restore_fails else 'reset')
    assert again['slot_released'] is not restore_fails
    assert again['uncollected_operations'] == 1
