"""Small SQLite catalog: names, uploaded samples and conversation associations.

AgentScope remains the only owner of conversation messages and execution state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from collections.abc import Callable


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workspaces (
                    workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    name TEXT NOT NULL, description TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    is_legacy INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, workspace_id));
                CREATE TABLE IF NOT EXISTS samples (
                    sample_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_samples (
                    session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL, sample_id TEXT);
                CREATE TABLE IF NOT EXISTS legacy_imports (
                    user_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                    truncated INTEGER NOT NULL,
                    PRIMARY KEY (user_id, workspace_id));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _workspace(row) -> dict:
        data = dict(row)
        data.pop("user_id", None)
        data["is_legacy"] = bool(data["is_legacy"])
        return data

    def create(self, user_id: str, workspace_id: str, name: str, description: str = "", *, legacy: bool = False) -> dict:
        now = timestamp()
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO workspaces VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (workspace_id, user_id, name, description, now, now, int(legacy)))
        return self.get(user_id, workspace_id)

    def get(self, user_id: str, workspace_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM workspaces WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)).fetchone()
        if row is None:
            raise LookupError("Workspace not found")
        return self._workspace(row)

    def list(self, user_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM workspaces WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [self._workspace(row) for row in rows]

    def samples(self, user_id: str, workspace_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM samples WHERE user_id=? AND workspace_id=? ORDER BY rowid DESC", (user_id, workspace_id)).fetchall()
        samples = [json.loads(row["payload"]) for row in rows]
        for sample in samples:
            sample.setdefault("reverse_sample_path", sample["sample_path"].removeprefix("samples/"))
        return samples

    def add_sample(self, user_id: str, workspace_id: str, sample: dict) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO samples VALUES (?, ?, ?, ?)",
                       (sample["sample_id"], user_id, workspace_id, json.dumps(sample, ensure_ascii=False)))
            db.execute("UPDATE workspaces SET updated_at=? WHERE user_id=? AND workspace_id=?", (timestamp(), user_id, workspace_id))

    def remove_sample(self, user_id: str, workspace_id: str, sample_id: str,
                      remove_bytes: Callable[[str], None]) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM samples WHERE user_id=? AND workspace_id=? AND sample_id=?",
                (user_id, workspace_id, sample_id),
            ).fetchone()
            if row is None:
                raise LookupError("Sample not found")
            remove_bytes(json.loads(row["payload"])["sample_path"])
            db.execute("DELETE FROM samples WHERE user_id=? AND workspace_id=? AND sample_id=?",
                       (user_id, workspace_id, sample_id))
            db.execute("UPDATE workspaces SET updated_at=? WHERE user_id=? AND workspace_id=?",
                       (timestamp(), user_id, workspace_id))

    def bind_task(self, user_id: str, workspace_id: str, session_id: str, sample_id: str | None) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO task_samples VALUES (?, ?, ?, ?)", (session_id, user_id, workspace_id, sample_id))
            db.execute("UPDATE workspaces SET updated_at=? WHERE user_id=? AND workspace_id=?", (timestamp(), user_id, workspace_id))

    def task_samples(self, user_id: str, workspace_id: str) -> dict[str, str | None]:
        with self.connect() as db:
            rows = db.execute("SELECT session_id, sample_id FROM task_samples WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)).fetchall()
        return {row["session_id"]: row["sample_id"] for row in rows}

    def legacy_import_status(self, user_id: str, workspace_id: str) -> bool | None:
        with self.connect() as db:
            row = db.execute("SELECT truncated FROM legacy_imports WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)).fetchone()
        return bool(row["truncated"]) if row else None

    def finish_legacy_import(self, user_id: str, workspace_id: str, samples: list[dict], truncated: bool) -> None:
        with self.connect() as db:
            for sample in samples:
                db.execute("INSERT OR IGNORE INTO samples VALUES (?, ?, ?, ?)",
                           (sample["sample_id"], user_id, workspace_id, json.dumps(sample, ensure_ascii=False)))
            db.execute("INSERT OR IGNORE INTO legacy_imports VALUES (?, ?, ?)", (user_id, workspace_id, int(truncated)))
