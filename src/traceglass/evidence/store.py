"""Scoped, append-only copies of REA outputs; never an authority on their truth."""
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
                call_id, tool_name, arguments, raw):
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return []
        found = observations(payload, tool_name)
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
                    'sample_sha256': sha256, 'subject': subject, 'provider': record.get('provider'),
                    'operation': record.get('operation', tool_name.removeprefix('mcp__rea__')),
                    'confidence': record.get('confidence'), 'authority': record.get('authority'),
                    'limitations': record.get('limitations', []), 'locations': record.get('locations', []),
                    'evidence_links': record.get('evidence_links', []),
                    'status': record.get('status'), 'revision': record.get('revision'),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                }
                db.execute('''INSERT OR IGNORE INTO rea_observations
                    (id,user_id,workspace_id,kind,sample_sha256,summary,raw,record) VALUES (?,?,?,?,?,?,?,?)''',
                    (identity, user_id, workspace_id, kind, sha256, json.dumps(summary, ensure_ascii=False),
                     raw, json.dumps(record, ensure_ascii=False)))
                receipts.append({'id': identity, 'upstream_id': summary['upstream_id'], 'kind': kind})
        return receipts

    def list(self, user_id, workspace_id, *, sample_sha256=None, kind=None, cursor=None, limit=30):
        if not 1 <= limit <= 100 or cursor is not None and cursor < 0:
            raise ValueError('Invalid evidence pagination')
        where, params = 'user_id=? AND workspace_id=?', [user_id, workspace_id]
        if sample_sha256:
            where += ' AND sample_sha256=?'
            params.append(sample_sha256)
        if kind:
            where += ' AND kind=?'
            params.append(kind)
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
        return {**json.loads(row['summary']), 'raw': row['raw'], 'record': json.loads(row['record'])} if row else None
