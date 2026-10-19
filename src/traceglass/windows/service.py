"""Durable caller-scoped jobs with shared admission and safe VM recovery."""
from contextlib import closing, contextmanager
from functools import wraps
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sqlite3
import stat
import subprocess
import threading
import time
import uuid

from . import guest
from .transport import VSphereTransport
from .scheduler import WindowsScheduler
from traceglass.workspaces.files import open_file

PROFILES = set(guest.PROFILES)
DYNAMIC = {'execute', 'debug_x64', 'debug_x32'}
ACTIVE = {'starting', 'running', 'debugging', 'unknown'}


def serialized(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        with self._lock:
            return function(self, *args, **kwargs)
    return call


class WindowsService:
    def __init__(self, config, state_dir, *, transport=None):
        self.config = config
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.transport = transport or VSphereTransport(config)
        self._lock = threading.RLock()
        self.scheduler = WindowsScheduler(self.state_dir / 'jobs.sqlite3', config.max_concurrent_jobs)

    def _db(self):
        return sqlite3.connect(self.state_dir / 'jobs.sqlite3', timeout=20)

    def _authorize(self, user_id):
        if not self.config.allowed(user_id):
            raise PermissionError('Windows tools are not enabled for this account')

    def _load(self, user_id, workspace_id, job_id):
        self._authorize(user_id)
        with closing(self._db()) as db:
            row = db.execute('SELECT record,revision FROM jobs WHERE id=? AND user_id=? AND workspace_id=?',
                             (job_id, user_id, workspace_id)).fetchone()
        if not row:
            raise LookupError('Windows job not found in this workspace')
        return {**json.loads(row[0]), '_revision': row[1]}

    def _save(self, job, *, release=False, dirty=False):
        self.scheduler.save(job, release=release, dirty=dirty)

    @staticmethod
    def _public(job):
        return {k: v for k, v in job.items() if k not in ('user_id', 'workspace_id', 'root', '_revision')}

    def _new(self, user_id, workspace_id, root, profile, digest=None, *, isolation='shared'):
        job_id = 'win_' + uuid.uuid4().hex
        job = dict(job_id=job_id, user_id=user_id, workspace_id=workspace_id,
                   root=str(Path(root).resolve()), profile=profile, sample_sha256=digest,
                   guest_directory=str(PureWindowsPath(self.config.guest_root) / job_id),
                   state='starting', launch_attempted=False, created_at=time.time(), isolation=isolation)
        self.scheduler.reserve(job)
        return job

    def _launch(self, directory, request, *, job, before_start=None):
        self.transport.mkdir(directory)
        runner = str(PureWindowsPath(directory) / 'runner.py')
        spec = str(PureWindowsPath(directory) / 'request.json')
        self.transport.put(runner, Path(guest.__file__).read_bytes())
        self.transport.put(spec, json.dumps(request).encode())
        with self.scheduler.launch(job):
            if before_start is not None:
                before_start()
            return self.transport.start(self.config.tools['python'],
                                        subprocess.list2cmdline([runner, spec]), directory)

    @serialized
    def submit(self, user_id, workspace_id, root, sample_path, profile, *, timeout=120, arguments=None, isolation=None):
        self._authorize(user_id)
        if profile not in PROFILES | {'capabilities'} or not 1 <= timeout <= self.config.max_timeout:
            raise ValueError('Invalid Windows profile or timeout')
        if isolation not in (None, 'shared', 'exclusive'):
            raise ValueError('isolation must be shared or exclusive')
        isolation = isolation or ('exclusive' if profile in DYNAMIC else 'shared')
        if profile in DYNAMIC and not self.config.allow_execution:
            raise PermissionError('Windows target execution is disabled by the administrator')
        if profile in DYNAMIC and not self.config.baseline_snapshot:
            raise PermissionError('Dynamic execution requires a configured clean baseline snapshot')
        if profile in DYNAMIC:
            self.transport.check_baseline(self.config.baseline_snapshot)
        arguments = arguments or []
        if (arguments and profile != 'execute') or len(arguments) > 32 or any(
                not isinstance(v, str) or len(v) > 4096 or '\0' in v for v in arguments):
            raise ValueError('Arguments are literal strings for the execute profile only')
        root = Path(root).resolve()
        source = None
        data = None
        if profile != 'capabilities':
            raw = Path(sample_path)
            if raw.is_absolute():
                try:
                    raw = raw.relative_to('/workspace')
                except ValueError:
                    raise ValueError('Windows input must be in the current workspace') from None
            source = (root / raw).resolve()
            if not source.is_relative_to(root) or not source.is_file() or ':' in str(raw) or '\\' in str(raw):
                raise ValueError('Windows input must be a file in the current workspace')
            try:
                stream, _info = open_file(root, raw.as_posix())
                with stream:
                    data = stream.read(self.config.max_input_bytes + 1)
            except OSError:
                raise ValueError('Windows input must be a single-link regular file in this workspace') from None
            if len(data) > self.config.max_input_bytes:
                raise ValueError('Windows input exceeds the upload budget')
            if profile in DYNAMIC and (source.suffix.lower() not in ('.exe', '.com') or not data.startswith(b'MZ')):
                raise ValueError('Dynamic profiles require a Windows PE executable (.exe or .com), not a command script')
        digest = hashlib.sha256(data).hexdigest() if data is not None else None
        # Readiness failures must not create an unqueryable exclusive lease.
        self.transport.probe()
        job = self._new(user_id, workspace_id, root, profile, digest, isolation=isolation)
        request = dict(profile=profile, tools=self.config.tools, timeout=timeout,
                       sample_sha256=digest, arguments=arguments)
        job['task_temp'] = request['task_temp'] = str(PureWindowsPath(job['guest_directory']) / 'task-temp')
        if profile in ('debug_x64', 'debug_x32'):
            request['debugger_temp'] = str(PureWindowsPath(job['guest_directory']) / 'debugger-temp')
            job['debugger_temp'] = request['debugger_temp']
        try:
            self.transport.mkdir(job['guest_directory'])
            if source:
                suffix = source.suffix if re.fullmatch(r'\.[A-Za-z0-9]{1,12}', source.suffix) else '.bin'
                destination = str(PureWindowsPath(job['guest_directory']) / ('input' + suffix))
                self.transport.put(destination, data)
                request['sample'] = destination
                job['sample_path'] = source.relative_to(root).as_posix()
                job['guest_sample'] = destination
            def before_start():
                # Persist before crossing the execution boundary: a lost start
                # response remains ambiguous even after a backend restart.
                job['launch_attempted'] = True
                self._save(job, dirty=profile in DYNAMIC)
            job['pid'] = self._launch(job['guest_directory'], request, job=job, before_start=before_start)
            job['state'] = 'running'
            self._save(job)
            return self._public(job)
        except Exception as exc:
            job['error_type'] = type(exc).__name__
            if job['launch_attempted']:
                # A start request may have reached ESXi when its response was lost.
                job['state'] = 'unknown'
                job['next_step'] = 'Query this job_id; the start response is ambiguous, never replay automatically'
                self._save(job)
            else:
                job['state'] = 'failed'
                job['next_step'] = 'No guest process start was attempted; lease released. Check guest readiness before submitting a new job.'
                self._save(job, release=True)
            return self._public(job)

    def _result(self, directory):
        value = json.loads(self.transport.get(str(PureWindowsPath(directory) / 'result.json'), 1024 * 1024))
        if not isinstance(value, dict) or value.get('state') not in {
                'success', 'failed', 'timeout', 'cancelled', 'output_limit', 'unavailable', 'debugging'}:
            raise ValueError('Invalid Windows result envelope')
        return value

    def _refresh_operation(self, job, operation):
        if operation['state'] not in ('starting', 'running', 'unknown'):
            return
        if operation.get('pid') and self.transport.process(operation['pid'])['running']:
            return
        try:
            result = self._result(operation['directory'])
            if result.get('sample_sha256') != job['sample_sha256']:
                raise ValueError('Debugger result sample binding mismatch')
            operation.update(state=result['state'], result=result)
            if (operation['action'] == 'close' and result['state'] == 'success' and result.get('closed') is True
                    and result.get('debugger_pid') == job.get('result', {}).get('debugger_pid')):
                job['state'] = 'success'
                job['debugger_closed'] = True
        except Exception:
            operation['state'] = 'unknown'

    @serialized
    def status(self, user_id, workspace_id, job_id):
        job = self._load(user_id, workspace_id, job_id)
        if job['state'] in {'starting', 'running', 'unknown'}:
            running = self.transport.process(job['pid'])['running'] if job.get('pid') else False
            if not running:
                try:
                    result = self._result(job['guest_directory'])
                    if result.get('sample_sha256') != job['sample_sha256']:
                        raise ValueError('Windows result sample binding mismatch')
                    job['state'] = result['state']
                    job['result'] = result
                except Exception:
                    job['state'] = 'unknown'
                self._save(job)
        for operation in job.get('operations', []):
            self._refresh_operation(job, operation)
        self._save(job)
        return self._public(job)

    def capabilities(self, user_id, workspace_id, root):
        self._authorize(user_id)
        host = self.transport.probe()
        job = self.submit(user_id, workspace_id, root, '', 'capabilities', timeout=30)
        for _ in range(30):
            status = self.status(user_id, workspace_id, job['job_id'])
            if status['state'] != 'running':
                if status.get('result'):
                    self.collect(user_id, workspace_id, root, job['job_id'])
                return {**host, 'allow_execution': self.config.allow_execution,
                        **self.capacity(user_id),
                        'job_id': job['job_id'], **status.get('result', {'state': status['state']})}
            time.sleep(.5)
        return {**host, 'job_id': job['job_id'], 'state': 'running',
                'next_step': 'Use WindowsJobStatus to retrieve the capability probe'}

    def capacity(self, user_id):
        self._authorize(user_id)
        return {'state': 'success', **self.scheduler.snapshot(),
                'auto_reset_on_idle': self.config.auto_reset_on_idle}

    @staticmethod
    def _destination(root, relative):
        path = root
        for part in relative.parts:
            path = path / part
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('Result destination escapes workspace')
        return path

    @staticmethod
    @contextmanager
    def _output_directory(root, parts):
        """Hold no-follow descriptors even if a workspace parent is swapped."""
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        except OSError:
            raise ValueError('Invalid Windows result destination') from None
        finally:
            os.close(fd)

    def _write_artifact(self, root, relative, data):
        digest = hashlib.sha256(data).hexdigest()
        with self._output_directory(root, relative.parts[:-1]) as parent:
            try:
                fd = os.open(relative.name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
            except FileExistsError:
                fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != len(data)
                            or hashlib.file_digest(stream, 'sha256').hexdigest() != digest):
                        raise ValueError('Existing collected artifact has different bytes')
            else:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
        return digest

    @serialized
    def collect(self, user_id, workspace_id, root, job_id):
        job = self._load(user_id, workspace_id, job_id)
        root = Path(root).resolve()
        if str(root) != job['root']:
            raise LookupError('Windows job does not belong to this workspace root')
        capacity = self.capacity(user_id)
        generation_changed = job.get('vm_generation') != self.scheduler.generation()
        if job.get('collection') and (generation_changed or capacity['recovery_state'] == 'reset_required'):
            saved = job['collection']
            # A revert can remove guest files even if readiness later fails.
            # Verify saved evidence before any call requiring a ready guest.
            for artifact in saved['artifacts']:
                stream, _ = open_file(root, artifact['path'])
                with stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != artifact['sha256']:
                        raise ValueError('Collected Windows artifact has changed')
            stream, _ = open_file(root, saved['manifest_path'])
            stream.close()
            return {**saved, 'slot_released': not self.scheduler.owns_lease(job_id),
                    'capacity': capacity,
                    'uncollected_operations': max(0, len(job.get('operations', [])) - job.get('collected_operations', 0)),
                    'recovery': {'state': 'reset' if generation_changed else 'reset_required'}}
        status = self.status(user_id, workspace_id, job_id)
        job = self._load(user_id, workspace_id, job_id)
        result = status.get('result')
        if result is None:
            raise RuntimeError('Windows result is not available; query job status first')
        if any(op['state'] in ('starting', 'running', 'unknown') for op in job.get('operations', [])):
            raise RuntimeError('A debugger operation is pending; query WindowsJobStatus before collecting')
        relative = Path('reports') / 'windows' / job_id
        output = self._destination(root, relative)
        with self._output_directory(root, relative.parts):
            pass
        artifacts, total = [], 0
        sources = [(job['guest_directory'], Path(), result)]
        for operation in job.get('operations', []):
            if operation.get('result'):
                sources.append((operation['directory'], Path(operation['operation_id']), operation['result']))
        for directory, prefix, envelope in sources:
            for item in envelope.get('artifacts', [])[:100]:
                name = item.get('path', '')
                path = PurePosixPath(name)
                if not name or path.is_absolute() or any(p in ('', '.', '..') for p in name.split('/')) or ':' in name or '\\' in name:
                    raise ValueError('Invalid guest artifact path')
                if len(artifacts) >= 100:
                    raise ValueError('Artifact count budget exceeded')
                data = self.transport.get(str(PureWindowsPath(directory).joinpath(*path.parts)),
                                          self.config.max_artifact_bytes - total)
                total += len(data)
                local = relative / prefix / Path(*path.parts)
                self._destination(root, local)
                digest = self._write_artifact(root, local, data)
                artifacts.append({'path': local.as_posix(), 'sha256': digest, 'bytes': len(data)})
        manifest = output / ('manifest-' + uuid.uuid4().hex[:8] + '.json')
        self._write_artifact(root, manifest.relative_to(root),
            json.dumps({'job': status, 'collected_artifacts': artifacts}, ensure_ascii=False, indent=2).encode())
        job['collected_operations'] = len(job.get('operations', []))
        job['collected_at'] = time.time()
        terminal = job['state'] not in ACTIVE
        debug_cleanup = job['profile'] not in {'debug_x64', 'debug_x32'} or (
            job.get('debugger_closed') is True or result.get('debugger_cleanup_confirmed') is True)
        release = terminal and (job['profile'] not in DYNAMIC or (
            (job.get('isolation') == 'shared' or self.config.auto_reset_on_idle) and debug_cleanup))
        collected = {'job_id': job_id, 'state': status['state'], 'sample_sha256': job['sample_sha256'],
                     'manifest_path': manifest.relative_to(root).as_posix(), 'artifacts': artifacts,
                     'slot_released': release,
                     'provenance': 'Guest outputs are observations; hashes establish artifact identity, not verdict correctness.'}
        job['collection'] = collected
        self._save(job, release=release)
        recovery = None
        if self.config.auto_reset_on_idle and release and self.scheduler.snapshot()['baseline_dirty']:
            recovery = self._restore(job, idle_only=True)
        return {**collected, 'capacity': self.capacity(user_id), 'recovery': recovery}

    @serialized
    def cancel(self, user_id, workspace_id, job_id):
        job = self._load(user_id, workspace_id, job_id)
        if not self.scheduler.owns_lease(job_id):
            raise RuntimeError('This task does not hold an active Windows VM lease')
        self.transport.put(str(PureWindowsPath(job['guest_directory']) / 'cancel'), b'cancel')
        return {'job_id': job_id, 'state': 'cancellation_requested',
                'next_step': 'Query WindowsJobStatus; close owned debug sessions via WindowsDebug, then collect. '
                             'Shared terminal jobs release only their slot; VM baseline recovery waits for all tasks.'}

    @serialized
    def debug(self, user_id, workspace_id, job_id, action, *, address='', length=256, count=1):
        status = self.status(user_id, workspace_id, job_id)
        job = self._load(user_id, workspace_id, job_id)
        if not self.scheduler.owns_lease(job_id):
            raise RuntimeError('This task does not hold an active Windows VM lease')
        if job['profile'] not in ('debug_x64', 'debug_x32') or status['state'] != 'debugging':
            raise ValueError('An owned ready Windows debug session is required')
        if any(op['state'] in ('starting', 'running', 'unknown') for op in job.get('operations', [])):
            raise RuntimeError('A debugger operation is pending; query WindowsJobStatus, do not replay')
        if len(job.get('operations', [])) >= 64:
            raise ValueError('Debugger operation budget reached; collect and reset this session')
        if action not in ('status', 'registers', 'breakpoint', 'step', 'continue', 'pause', 'memory', 'dump', 'close'):
            raise ValueError('Unsupported typed debugger action')
        if action in ('breakpoint', 'memory', 'dump') and not re.fullmatch(r'0x[0-9a-fA-F]{1,16}', address):
            raise ValueError('Address must be a hexadecimal integer')
        if not 1 <= length <= 65536 or not 1 <= count <= 100:
            raise ValueError('Debugger read/step budget exceeded')
        op_id = 'op_' + uuid.uuid4().hex
        directory = str(PureWindowsPath(job['guest_directory']) / op_id)
        request = dict(profile=job['profile'], action=action, tools=self.config.tools,
                       sample=job['guest_sample'], sample_sha256=job['sample_sha256'],
                       debugger_pid=status['result']['debugger_pid'], address=address, length=length, count=count)
        # Old live sessions without this field still use their original TEMP.
        # New sessions use only the host-owned persisted rendezvous path.
        if job.get('debugger_temp'):
            request['debugger_temp'] = job['debugger_temp']
        operation = dict(operation_id=op_id, directory=directory, action=action, state='starting')
        job.setdefault('operations', []).append(operation)
        self._save(job)
        try:
            operation.update(pid=self._launch(directory, request, job=job), state='running')
        except Exception as exc:
            operation['state'] = 'unknown'
            operation['error_type'] = type(exc).__name__
        finally:
            self._save(job)
        return {'job_id': job_id, 'state': operation['state'], 'operation': operation,
                'next_step': 'Use WindowsJobStatus to retrieve operations; never replay a pending operation'}

    @serialized
    def reset(self, user_id, workspace_id, job_id, *, discard_pending=False):
        self.status(user_id, workspace_id, job_id)
        job = self._load(user_id, workspace_id, job_id)
        if not self.config.baseline_snapshot:
            raise PermissionError('No administrator-approved baseline snapshot is configured')
        pending = any(op['state'] in ('starting', 'running', 'unknown') for op in job.get('operations', []))
        unresolved = pending or job['state'] in ACTIVE
        abandon = discard_pending and unresolved
        if unresolved and not abandon:
            raise RuntimeError('Collect Windows results and close the debugger before restoring the baseline; '
                               'discard_pending=true explicitly abandons unavailable or active results')
        if not abandon and job.get('result') and (not job.get('collected_at') or
                job.get('collected_operations', -1) != len(job.get('operations', []))):
            raise RuntimeError('Collect Windows results before restoring the baseline')
        self._restore(job)
        return {'job_id': job_id, 'state': 'reset', 'discarded_pending_results': bool(abandon),
                'note': 'Guest files and debug sessions reverted to the configured baseline. Collected workspace evidence is retained.'}

    def _restore(self, job, *, idle_only=False):
        if not self.scheduler.begin_reset(job, idle_only=idle_only):
            return {'state': 'deferred', 'message': 'Other Windows tasks still need to finish and collect results'}
        try:
            self.transport.restore(self.config.baseline_snapshot)
        except Exception as exc:
            self.scheduler.finish_reset(job, success=False)
            if not idle_only:
                raise
            return {'state': 'reset_required', 'error_type': type(exc).__name__,
                    'message': 'Evidence collected; baseline recovery failed. New jobs remain blocked.'}
        job['state'] = 'reset'
        job['reset_at'] = time.time()
        self.scheduler.finish_reset(job, success=True)
        return {'state': 'reset'}
