"""Transactional admission, legacy migration and whole-VM recovery barriers."""
from contextlib import closing, contextmanager
import json
import os
import sqlite3
import uuid


class WindowsScheduler:
    def __init__(self, database, limit):
        self.database = database
        self.limit = limit
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, user_id TEXT, workspace_id TEXT, record TEXT)')
            if 'revision' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE TABLE IF NOT EXISTS windows_leases (job_id TEXT PRIMARY KEY, mode TEXT, generation INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS windows_launches (job_id TEXT PRIMARY KEY, token TEXT, pid INTEGER)')
            for job_id, pid in db.execute('SELECT job_id,pid FROM windows_launches').fetchall():
                if not self._process_alive(pid):
                    db.execute('DELETE FROM windows_launches WHERE job_id=?', (job_id,))
            db.execute("CREATE TABLE IF NOT EXISTS windows_vm (singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation INTEGER, dirty INTEGER, state TEXT)")
            db.execute("INSERT OR IGNORE INTO windows_vm (singleton,generation,dirty,state) VALUES (1,0,0,'ready')")
            if 'reset_pid' not in {r[1] for r in db.execute('PRAGMA table_info(windows_vm)')}:
                db.execute('ALTER TABLE windows_vm ADD COLUMN reset_pid INTEGER')
            owner = db.execute("SELECT reset_pid FROM windows_vm WHERE state='resetting'").fetchone()
            if owner and not self._process_alive(owner[0]):
                # A dead backend may have sent the revert. Never assume it
                # completed; permit only an owned recovery retry, not new work.
                db.execute("UPDATE windows_vm SET state='reset_required',reset_pid=NULL WHERE singleton=1")
            # An old lease is always exclusive, even if its saved job failed.
            # Never infer a clean guest from a terminal application state.
            legacy = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lease'").fetchone()
            if legacy:
                row = db.execute('SELECT job_id FROM lease WHERE singleton=1').fetchone()
                if row:
                    job_id = row[0]
                    saved = db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
                    generation = db.execute('SELECT generation FROM windows_vm').fetchone()[0]
                    if saved:
                        job = json.loads(saved[0])
                        job.update(isolation='exclusive', vm_generation=generation)
                        db.execute('UPDATE jobs SET record=? WHERE id=?', (json.dumps(job), job_id))
                    db.execute('INSERT OR IGNORE INTO windows_leases VALUES (?,?,?)', (job_id, 'exclusive', generation))
                    if not saved or job['profile'] in {'execute', 'debug_x64', 'debug_x32'}:
                        db.execute('UPDATE windows_vm SET dirty=1 WHERE singleton=1')
                db.execute('DROP TABLE lease')

    def connect(self):
        return sqlite3.connect(self.database, timeout=20)

    @staticmethod
    def _process_alive(pid):
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def reserve(self, job):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            generation, dirty, state = db.execute('SELECT generation,dirty,state FROM windows_vm WHERE singleton=1').fetchone()
            modes = [row[0] for row in db.execute('SELECT mode FROM windows_leases')]
            if state != 'ready' or (dirty and not modes):
                raise RuntimeError('Windows VM is busy: baseline reset is required or in progress')
            if len(modes) >= self.limit or 'exclusive' in modes or (modes and job['isolation'] == 'exclusive'):
                raise RuntimeError('Windows VM is busy: capacity is full or an exclusive task holds the guest')
            job['vm_generation'] = generation
            job['_revision'] = 0
            db.execute('INSERT INTO jobs (id,user_id,workspace_id,record) VALUES (?,?,?,?)',
                       (job['job_id'], job['user_id'], job['workspace_id'], json.dumps(job)))
            db.execute('INSERT INTO windows_leases VALUES (?,?,?)', (job['job_id'], job['isolation'], generation))

    def save(self, job, *, release=False, dirty=False):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT state FROM windows_vm WHERE singleton=1').fetchone()[0] == 'resetting':
                raise RuntimeError('Windows VM is busy: baseline recovery is already in progress')
            if dirty:
                if not db.execute('SELECT 1 FROM windows_leases WHERE job_id=?', (job['job_id'],)).fetchone():
                    raise RuntimeError('This task does not hold a Windows VM lease')
                db.execute('UPDATE windows_vm SET dirty=1 WHERE singleton=1')
            revision = job['_revision']
            updated = {**job, '_revision': revision + 1}
            cursor = db.execute('UPDATE jobs SET record=?,revision=revision+1 WHERE id=? AND revision=?',
                                (json.dumps(updated), job['job_id'], revision))
            if cursor.rowcount != 1:
                raise RuntimeError('Windows job changed concurrently; query current status before retrying. Do not replay execution.')
            if release:
                db.execute('DELETE FROM windows_leases WHERE job_id=?', (job['job_id'],))
        job['_revision'] = revision + 1

    def owns_lease(self, job_id):
        with closing(self.connect()) as db:
            return bool(db.execute('SELECT 1 FROM windows_leases WHERE job_id=?', (job_id,)).fetchone())

    @contextmanager
    def launch(self, job):
        token = uuid.uuid4().hex
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            generation, state = db.execute('SELECT generation,state FROM windows_vm WHERE singleton=1').fetchone()
            if state != 'ready':
                raise RuntimeError('Windows VM is busy: baseline recovery is already in progress')
            if generation != job['vm_generation'] or not db.execute(
                    'SELECT 1 FROM windows_leases WHERE job_id=?', (job['job_id'],)).fetchone():
                raise RuntimeError('This task does not hold an active Windows VM lease')
            if db.execute('SELECT revision FROM jobs WHERE id=?', (job['job_id'],)).fetchone()[0] != job['_revision']:
                raise RuntimeError('Windows job changed concurrently; query current status before retrying. Do not replay execution.')
            if db.execute('SELECT 1 FROM windows_launches WHERE job_id=?', (job['job_id'],)).fetchone():
                raise RuntimeError('A debugger operation is pending; a guest launch is in progress')
            db.execute('INSERT INTO windows_launches VALUES (?,?,?)', (job['job_id'], token, os.getpid()))
        try:
            yield
        finally:
            with closing(self.connect()) as db, db:
                db.execute('DELETE FROM windows_launches WHERE job_id=? AND token=?', (job['job_id'], token))

    def generation(self):
        with closing(self.connect()) as db:
            return db.execute('SELECT generation FROM windows_vm WHERE singleton=1').fetchone()[0]

    def snapshot(self):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN')
            dirty, state = db.execute('SELECT dirty,state FROM windows_vm WHERE singleton=1').fetchone()
            modes = [r[0] for r in db.execute('SELECT mode FROM windows_leases')]
        available = state == 'ready' and 'exclusive' not in modes and not (dirty and not modes)
        return {'max_concurrent_jobs': self.limit, 'occupied_slots': len(modes),
                'available_slots': max(0, self.limit - len(modes)) if available else 0,
                'exclusive_task': 'exclusive' in modes, 'baseline_dirty': bool(dirty),
                'recovery_state': state, 'baseline_reset_required': bool(dirty and not modes)}

    def begin_reset(self, job, *, idle_only=False):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            generation, dirty, state = db.execute('SELECT generation,dirty,state FROM windows_vm WHERE singleton=1').fetchone()
            leases = [r[0] for r in db.execute('SELECT job_id FROM windows_leases')]
            if job.get('vm_generation') != generation or (job['job_id'] not in leases and not dirty):
                if idle_only:
                    return False
                raise RuntimeError('This task does not hold a current Windows VM lease or baseline recovery claim')
            saved_revision = db.execute('SELECT revision FROM jobs WHERE id=?', (job['job_id'],)).fetchone()
            if not saved_revision or saved_revision[0] != job['_revision']:
                if idle_only:
                    return False
                raise RuntimeError('Windows job changed concurrently; query current status before retrying. Do not replay execution.')
            if state == 'resetting':
                if idle_only:
                    return False
                raise RuntimeError('Windows VM is busy: baseline recovery is already in progress')
            if any(job_id != job['job_id'] for job_id in leases) or (idle_only and leases):
                if idle_only:
                    return False
                raise RuntimeError('Windows VM is busy with other tasks; close and collect all tasks before baseline reset')
            if db.execute('SELECT 1 FROM windows_launches LIMIT 1').fetchone():
                if idle_only:
                    return False
                raise RuntimeError('Windows VM is busy: a guest launch is in progress; query status before recovery')
            db.execute("UPDATE windows_vm SET state='resetting',reset_pid=? WHERE singleton=1", (os.getpid(),))
            return True

    def finish_reset(self, job, *, success):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if success:
                job['_revision'] += 1
                db.execute('UPDATE jobs SET record=?,revision=? WHERE id=?',
                           (json.dumps(job), job['_revision'], job['job_id']))
                db.execute('DELETE FROM windows_leases WHERE generation=?', (job['vm_generation'],))
                db.execute("UPDATE windows_vm SET generation=generation+1,dirty=0,state='ready',reset_pid=NULL WHERE singleton=1")
            else:
                db.execute("UPDATE windows_vm SET state='reset_required',reset_pid=NULL WHERE singleton=1")
