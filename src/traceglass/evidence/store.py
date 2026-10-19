"""Scoped, append-only tool observations; provenance is not semantic proof."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def observations(payload, tool_name):
    """Recognize released REA envelopes, not arbitrary nested target strings."""
    if not isinstance(payload, dict) or 'error' in payload:
        return []
    evidence = payload.get('evidence', payload)
    records = []
    if (isinstance(evidence, dict) and
        re.fullmatch(r'ev_[a-f0-9]{64}', str(evidence.get('evidence_id', ''))) and
        'normalized_result' in evidence and isinstance(evidence.get('provider'), dict) and
        isinstance(evidence.get('operation'), str)):
        records.append(('evidence', evidence))
    result = payload.get('result', payload)
    if tool_name.endswith('__get_evidence_bundle') and isinstance(result, dict):
        for record in result.get('records', []):
            records.extend(observations(record, 'bundle_record'))
        unknowns = result.get('unknowns', [])
    elif tool_name.endswith('__list_unknowns') and isinstance(result, dict):
        unknowns = result.get('items', [])
    elif tool_name.endswith(('__record_unknown', '__update_unknown')):
        unknowns = [result]
    else:
        unknowns = []
    if isinstance(unknowns, list):
        records.extend(('unknown', u) for u in unknowns if isinstance(u, dict)
                       and isinstance(u.get('unknown_id'), str) and isinstance(u.get('revision'), int))
    return records


class EvidenceStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS rea_observations (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                kind TEXT NOT NULL, sample_sha256 TEXT, summary TEXT NOT NULL,
                raw TEXT NOT NULL, record TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS rea_scope ON rea_observations(user_id, workspace_id, seq)')

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def capture(self, *, user_id, workspace_id, session_id, agent_id, connection_id,
                call_id, tool_name, arguments, raw, max_bytes=1_048_576, raw_record_id=None):
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return []
        found = observations(payload, tool_name)
        if max_bytes < 1:
            raise ValueError('Invalid capture size')
        encoded = raw.encode()
        stored = encoded[:max_bytes].decode('utf-8', errors='ignore')
        shared = self.get(user_id, workspace_id, raw_record_id) if raw_record_id else None
        if raw_record_id and (not shared or shared['kind'] != 'tool_call' or shared['call_id'] != call_id):
            raise ValueError('Invalid shared call archive')
        raw_hash = shared['raw_sha256'] if shared else hashlib.sha256(encoded).hexdigest()
        stored_hash = shared['stored_sha256'] if shared else hashlib.sha256(stored.encode()).hexdigest()
        truncated = shared['truncated'] if shared else len(encoded) > max_bytes
        receipts = []
        with closing(self._connect()) as db, db:
            for index, (kind, record) in enumerate(found):
                key = json.dumps([user_id, workspace_id, session_id, connection_id, call_id, index,
                                  hashlib.sha256(raw.encode()).hexdigest()])
                identity = 'tgev_' + hashlib.sha256(key.encode()).hexdigest()
                subject = record.get('subject') or {}
                sha256 = (subject.get('digest') or {}).get('sha256')
                summary = {
                    'id': identity, 'kind': kind, 'upstream_id': record.get('evidence_id', record.get('unknown_id')),
                    'session_id': session_id, 'agent_id': agent_id, 'connection_id': connection_id,
                    'call_id': call_id, 'tool_name': tool_name, 'arguments': arguments,
                    'raw_sha256': raw_hash, 'stored_sha256': stored_hash,
                    'original_bytes': shared['original_bytes'] if shared else len(encoded),
                    'truncated': truncated, 'call_state': 'success',
                    'raw_record_id': raw_record_id,
                    'sample_sha256': sha256, 'subject': subject, 'provider': record.get('provider'),
                    'operation': record.get('operation', tool_name.removeprefix('mcp__rea__')),
                    'confidence': record.get('confidence'), 'authority': record.get('authority'),
                    'limitations': record.get('limitations', []), 'locations': record.get('locations', []),
                    'evidence_links': record.get('evidence_links', []),
                    'status': record.get('status'), 'revision': record.get('revision'),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                }
                if len(json.dumps(summary, ensure_ascii=False).encode()) > max_bytes:
                    for field in ('arguments', 'subject', 'provider', 'locations', 'limitations', 'evidence_links'):
                        summary[field] = None if field in {'arguments', 'subject', 'provider'} else []
                    summary['metadata_truncated'] = True
                record_json = json.dumps(record, ensure_ascii=False)
                if truncated or len(record_json.encode()) > max_bytes:
                    record_json = '{}'
                db.execute('''INSERT OR IGNORE INTO rea_observations
                    (id,user_id,workspace_id,kind,sample_sha256,summary,raw,record) VALUES (?,?,?,?,?,?,?,?)''',
                    (identity, user_id, workspace_id, kind, sha256, json.dumps(summary, ensure_ascii=False),
                     '' if shared else stored, record_json))
                receipts.append({'id': identity, 'upstream_id': summary['upstream_id'], 'kind': kind})
        return receipts

    def capture_call(self, *, user_id, workspace_id, session_id, agent_id, connection_id,
                     call_id, tool_name, arguments, raw, state, sample_sha256=None,
                     locations=(), attachments=(), max_bytes=1_048_576):
        """Capture a completed call, including failures, without trusting model SHA claims.

        sample_sha256 is supplied by the runtime's file/tool metadata binding.
        A truncated record retains the full-output hash and is not usable as a
        fully verified citation. Identity includes the capture policy so a later
        complete copy never silently replaces a partial one.
        """
        if max_bytes < 1 or not isinstance(raw, str):
            raise ValueError('Invalid capture size or raw output')
        if sample_sha256 and not re.fullmatch(r'[a-fA-F0-9]{64}', sample_sha256):
            raise ValueError('Invalid sample digest')
        encoded = raw.encode('utf-8')
        stored = encoded[:max_bytes].decode('utf-8', errors='ignore')
        raw_hash = hashlib.sha256(encoded).hexdigest()
        arguments_raw = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        arguments_hash = hashlib.sha256(arguments_raw.encode()).hexdigest()
        key = json.dumps([user_id, workspace_id, session_id, connection_id, call_id,
                          tool_name, state, raw_hash, arguments_hash, max_bytes])
        identity = 'tgev_' + hashlib.sha256(key.encode()).hexdigest()
        summary = dict(id=identity, kind='tool_call', session_id=session_id, agent_id=agent_id,
            connection_id=connection_id, call_id=call_id, tool_name=tool_name,
            arguments=arguments if len(arguments_raw.encode()) <= max_bytes else None,
            arguments_sha256=arguments_hash, arguments_truncated=len(arguments_raw.encode()) > max_bytes,
            sample_sha256=sample_sha256.lower() if sample_sha256 else None,
            raw_sha256=raw_hash, stored_sha256=hashlib.sha256(stored.encode()).hexdigest(),
            original_bytes=len(encoded), truncated=len(encoded) > max_bytes,
            call_state=str(state), locations=list(locations)[:100], attachments=list(attachments)[:32],
            created_at=datetime.now(timezone.utc).isoformat())
        with closing(self._connect()) as db, db:
            db.execute('''INSERT OR IGNORE INTO rea_observations
                (id,user_id,workspace_id,kind,sample_sha256,summary,raw,record) VALUES (?,?,?,?,?,?,?,?)''',
                (identity, user_id, workspace_id, 'tool_call', summary['sample_sha256'],
                 json.dumps(summary, ensure_ascii=False), stored, '{}'))
        return {'id': identity, 'kind': 'tool_call', 'call_state': str(state),
                'truncated': summary['truncated'], 'sample_sha256': summary['sample_sha256']}

    def validate_reference(self, user_id, workspace_id, record_id, sample_sha256, locator=None):
        """Verify accessible captured bytes and bounded location, not dataflow semantics."""
        record = self.get(user_id, workspace_id, record_id)
        status = 'verified'
        if record is None:
            status = 'not_found'
        elif not record.get('stored_sha256'):
            status = 'legacy_unhashed'
        elif hashlib.sha256(record['raw'].encode()).hexdigest() != record['stored_sha256']:
            status = 'integrity_mismatch'
        elif record.get('truncated'):
            status = 'truncated'
        elif record.get('call_state') != 'success':
            status = 'unsuccessful_call'
        elif any(self.get_attachment(user_id, workspace_id, record_id, a['id']) is None
                 for a in record.get('attachments', [])):
            status = 'attachment_integrity_mismatch'
        elif not record.get('sample_sha256'):
            status = 'sample_unbound'
        elif record['sample_sha256'].lower() != sample_sha256.lower():
            status = 'sample_mismatch'
        elif locator and not self._location_matches(record, locator):
            status = 'location_unverified'
        return {'id': record_id, 'status': status,
                'scope': 'accessible_origin_content_integrity_and_locator_only'}

    def _attachment_root(self, user_id, workspace_id):
        scope = hashlib.sha256(json.dumps([user_id, workspace_id]).encode()).hexdigest()
        return self.path.parent / 'evidence-attachments' / scope

    def capture_attachment(self, user_id, workspace_id, data, media_type, *, max_bytes=8_388_608):
        if not isinstance(data, bytes) or len(data) > max_bytes:
            raise ValueError('Attachment exceeds the 8 MiB archive budget')
        digest = hashlib.sha256(data).hexdigest()
        identity = 'tgat_' + digest
        root = self._attachment_root(user_id, workspace_id)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / digest
        try:
            with path.open('xb') as stream:
                stream.write(data)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('Existing attachment integrity mismatch')
        return dict(id=identity, sha256=digest, bytes=len(data), media_type=str(media_type)[:200])

    def get_attachment(self, user_id, workspace_id, record_id, attachment_id):
        record = self.get(user_id, workspace_id, record_id)
        if not record:
            return None
        attachment = next((a for a in record.get('attachments', []) if a['id'] == attachment_id), None)
        if not attachment or not re.fullmatch(r'tgat_[a-f0-9]{64}', attachment_id):
            return None
        try:
            data = (self._attachment_root(user_id, workspace_id) / attachment_id[5:]).read_bytes()
        except OSError:
            return None
        return (data, attachment) if hashlib.sha256(data).hexdigest() == attachment['sha256'] else None

    @staticmethod
    def _location_matches(record, locator):
        structured = json.dumps(record.get('locations', []), ensure_ascii=False)
        if re.fullmatch(r'0x[0-9a-fA-F]+', locator):
            value = int(locator, 16)
            return any(int(address, 16) == value for address in
                       re.findall(r'(?<!\w)0x[0-9a-fA-F]+(?!\w)', structured + '\n' + record['raw']))
        # Text-only matches are a weaker fallback, never a proof of association.
        return locator in structured or locator in record['raw']

    def list(self, user_id, workspace_id, *, sample_sha256=None, kind=None, cursor=None, limit=30, kinds=None):
        if not 1 <= limit <= 100 or cursor is not None and cursor < 0:
            raise ValueError('Invalid evidence pagination')
        where, params = 'user_id=? AND workspace_id=?', [user_id, workspace_id]
        if sample_sha256:
            where += ' AND sample_sha256=?'
            params.append(sample_sha256)
        if kind:
            where += ' AND kind=?'
            params.append(kind)
        elif kinds:
            where += ' AND kind IN (' + ','.join('?' for _ in kinds) + ')'
            params.extend(kinds)
        with closing(self._connect()) as db:
            total = db.execute('SELECT COUNT(*) FROM rea_observations WHERE ' + where, params).fetchone()[0]
            if cursor is not None:
                where += ' AND seq<?'
                params.append(cursor)
            rows = db.execute('SELECT seq,summary FROM rea_observations WHERE ' + where +
                              ' ORDER BY seq DESC LIMIT ?', [*params, limit + 1]).fetchall()
        return {'items': [json.loads(r['summary']) for r in rows[:limit]], 'total': total,
                'next_cursor': rows[limit - 1]['seq'] if len(rows) > limit else None}

    def get(self, user_id, workspace_id, record_id):
        with closing(self._connect()) as db:
            row = db.execute('SELECT summary,raw,record FROM rea_observations WHERE user_id=? AND workspace_id=? AND id=?',
                             (user_id, workspace_id, record_id)).fetchone()
            if row:
                summary = json.loads(row['summary'])
                shared_id = summary.get('raw_record_id')
                shared = db.execute('SELECT raw FROM rea_observations WHERE user_id=? AND workspace_id=? AND id=?',
                    (user_id, workspace_id, shared_id)).fetchone() if shared_id else None
                return {**summary, 'raw': shared['raw'] if shared else row['raw'], 'record': json.loads(row['record'])}
        return None
