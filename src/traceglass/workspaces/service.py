"""Workspace/sample product operations over native AgentScope storage."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

from agentscope.app.storage import ChatModelConfig, SessionConfig, StorageBase
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.state import AgentState
from fastapi import UploadFile

from . import files
from .manager import ProductDockerWorkspaceManager, ProductLocalWorkspaceManager
from .store import WorkspaceStore, timestamp


class ProductWorkspaceService:
    def __init__(self, data_dir: Path, storage: StorageBase,
                 workspace_manager: ProductLocalWorkspaceManager | ProductDockerWorkspaceManager,
                 agent_id: str):
        self.data_dir = Path(data_dir)
        self.storage = storage
        self.manager = workspace_manager
        self.agent_id = agent_id
        self.catalog = WorkspaceStore(self.data_dir / "product-workspaces.sqlite3")
        self.manager.product_store = self.catalog
        self._import_locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def _sessions(self, user_id: str):
        sessions = await self.storage.list_sessions(user_id, self.agent_id)
        visible = []
        for session in sessions:
            if session.team_id:
                team = await self.storage.get_team(user_id, session.team_id)
                if team is not None and team.session_id != session.id:
                    continue
            visible.append(session)
            wid = session.config.workspace_id
            if not wid.startswith("tgws-"):
                await asyncio.to_thread(
                    self.catalog.create, user_id, wid,
                    "历史工作区 · " + session.config.name,
                    "历史会话沿用原有共享文件目录；新建工作区使用独立目录。",
                    legacy=True,
                )
        return visible

    async def _summary(self, user_id: str, workspace: dict, sessions: list) -> dict:
        samples = await asyncio.to_thread(self.catalog.samples, user_id, workspace["workspace_id"])
        related = [item for item in sessions if item.config.workspace_id == workspace["workspace_id"]]
        return {**workspace, "sample_count": len(samples), "task_count": len(related)}

    async def list(self, user_id: str) -> dict:
        sessions = await self._sessions(user_id)
        workspaces = await asyncio.to_thread(self.catalog.list, user_id)
        return {"workspaces": [await self._summary(user_id, item, sessions) for item in workspaces]}

    async def create(self, user_id: str, name: str, description: str = "") -> dict:
        workspace = await asyncio.to_thread(self.catalog.create, user_id, "tgws-" + uuid4().hex, name, description)
        root = self.workspace_root(user_id, workspace["workspace_id"])
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        return {**workspace, "sample_count": 0, "task_count": 0}

    async def detail(self, user_id: str, workspace_id: str) -> dict:
        sessions = await self._sessions(user_id)
        workspace = await asyncio.to_thread(self.catalog.get, user_id, workspace_id)
        samples_truncated = await self._import_legacy_samples(user_id, workspace_id) if workspace["is_legacy"] else False
        samples = await asyncio.to_thread(self.catalog.samples, user_id, workspace_id)
        associations = await asyncio.to_thread(self.catalog.task_samples, user_id, workspace_id)
        tasks = [{"session_id": item.id, "agent_id": item.agent_id, "name": item.config.name,
                  "sample_id": associations.get(item.id), "created_at": item.created_at.isoformat()}
                 for item in sessions if item.config.workspace_id == workspace_id]
        return {"workspace": {**workspace, "sample_count": len(samples), "task_count": len(tasks)},
                "samples": samples, "tasks": tasks, "samples_truncated": samples_truncated}

    async def _import_legacy_samples(self, user_id: str, workspace_id: str) -> bool:
        async with self._import_locks.setdefault((user_id, workspace_id), asyncio.Lock()):
            status = await asyncio.to_thread(self.catalog.legacy_import_status, user_id, workspace_id)
            if status is not None:
                return status
            root = await asyncio.to_thread(self.workspace_root, user_id, workspace_id)
            samples, truncated = await asyncio.to_thread(files.discover_legacy_samples, root)
            for sample in samples:
                key = f"{user_id}\0{workspace_id}\0{sample['sample_path']}"
                sample["sample_id"] = "sample-" + hashlib.sha256(key.encode()).hexdigest()[:32]
                sample["reverse_sample_path"] = sample["sample_path"].removeprefix("samples/")
                sample["sandbox_path"] = self.manager.agent_path(user_id, self.agent_id, workspace_id, sample["sample_path"])
            await asyncio.to_thread(self.catalog.finish_legacy_import, user_id, workspace_id, samples, truncated)
            return truncated

    def workspace_root(self, user_id: str, workspace_id: str) -> Path:
        self.catalog.get(user_id, workspace_id)
        root = self.manager.host_root(user_id, self.agent_id, workspace_id)
        # Historical Docker managers accepted user IDs as path components.
        # Product routes never allow a legacy path to escape the trusted base.
        if not root.resolve().is_relative_to(Path(self.manager._basedir).resolve()):
            raise LookupError("Workspace not found")
        return root

    async def create_session(self, user_id: str, workspace_id: str, name: str,
                             sample_id: str | None = None,
                             chat_model_config: ChatModelConfig | None = None,
                             permission_mode: str = 'default') -> dict:
        await asyncio.to_thread(self.catalog.get, user_id, workspace_id)
        if sample_id is not None:
            samples = await asyncio.to_thread(self.catalog.samples, user_id, workspace_id)
            if not any(sample["sample_id"] == sample_id for sample in samples):
                raise LookupError("Sample not found in this workspace")
        session = await self.storage.upsert_session(
            user_id=user_id, agent_id=self.agent_id,
            config=SessionConfig(workspace_id=workspace_id, name=name, chat_model_config=chat_model_config),
            state=AgentState(permission_context=PermissionContext(mode=PermissionMode(permission_mode))),
            session_id=uuid4().hex,
        )
        await asyncio.to_thread(self.catalog.bind_task, user_id, workspace_id, session.id, sample_id)
        return {"session_id": session.id, "agent_id": self.agent_id, "workspace_id": workspace_id}

    async def upload(self, user_id: str, workspace_id: str, upload: UploadFile) -> dict:
        root = await asyncio.to_thread(self.workspace_root, user_id, workspace_id)
        # Existing sessions can precede their first native workspace access.
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        sample = await files.upload_sample(root, upload)
        sample.update(sandbox_path=self.manager.agent_path(user_id, self.agent_id, workspace_id, sample["sample_path"]),
                      created_at=timestamp())
        try:
            await asyncio.to_thread(self.catalog.add_sample, user_id, workspace_id, sample)
        except Exception:
            # A failed catalog write must not return success or leave an
            # undiscoverable uploaded sample behind.
            await asyncio.to_thread((root / sample["sample_path"]).unlink, missing_ok=True)
            raise
        return sample

    async def list_files(self, user_id: str, workspace_id: str, path: str = "") -> dict:
        root = await asyncio.to_thread(self.workspace_root, user_id, workspace_id)
        if not root.exists() and path in {"", "."}:
            return {"path": "", "entries": [], "truncated": False}
        return await asyncio.to_thread(files.list_directory, root, path)

    async def preview(self, user_id: str, workspace_id: str, path: str) -> dict:
        root = await asyncio.to_thread(self.workspace_root, user_id, workspace_id)
        return await asyncio.to_thread(files.preview_file, root, path)

    async def download(self, user_id: str, workspace_id: str, path: str):
        root = await asyncio.to_thread(self.workspace_root, user_id, workspace_id)
        return await asyncio.to_thread(files.open_file, root, path)
