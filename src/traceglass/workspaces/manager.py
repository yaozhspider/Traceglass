"""Correct native 2.0.6's shared host path for new product workspace IDs.

Only the path argument is adapted. AgentScope still owns workspace creation,
container lifecycle, MCP, skills, caching and shutdown. Historical bindings keep
their original paths; product bindings share one root across all team agents.
"""
from __future__ import annotations

import hashlib
import asyncio
import time
from pathlib import Path

from agentscope.app.workspace_manager import DockerWorkspaceManager, LocalWorkspaceManager
from traceglass.integrations.optional_mcp import OPTIONAL_NAMES, OptionalMCPs
from traceglass.integrations.skill_sync import sync_missing_skills


def _path_key(user_id: str, workspace_id: str) -> str:
    return "tgws-" + hashlib.sha256(f"{user_id}\0{workspace_id}".encode()).hexdigest()


class _ProductPathMixin:
    def __init__(self, *args, **kwargs):
        defaults = list(kwargs.get("default_mcps") or [])
        self._optional_mcps = OptionalMCPs(defaults)
        kwargs["default_mcps"] = [client for client in defaults if client.name not in OPTIONAL_NAMES]
        self._product_init_locks: dict[str, asyncio.Lock] = {}
        self._optional_candidates: dict[str, list] = {}
        super().__init__(*args, **kwargs)

    def integration_status(self, workspace_id: str) -> list[dict]:
        return self._optional_mcps.status(workspace_id)

    def _mapped_agent(self, user_id: str, agent_id: str, workspace_id: str) -> str:
        if getattr(self, 'require_product_workspace', False):
            if not workspace_id.startswith('tgws-'):
                raise LookupError('Only owned product workspaces are available')
            self.product_store.get(user_id, workspace_id)
        if workspace_id.startswith("tgws-"):
            # Set by ProductWorkspaceService. Native session creation accepts an
            # explicit binding, so validate ownership even outside product APIs.
            if getattr(self, "product_store", None) is not None:
                self.product_store.get(user_id, workspace_id)
            return _path_key(user_id, workspace_id)
        return agent_id

    async def get_workspace(self, user_id: str, agent_id: str, session_id: str, workspace_id: str | None = None):
        workspace_id = workspace_id or self.assign_workspace_id(user_id=user_id, agent_id=agent_id, session_id=session_id)
        mapped = self._mapped_agent(user_id, agent_id, workspace_id) if workspace_id else agent_id
        owner = hashlib.sha256(user_id.encode()).hexdigest() if workspace_id and workspace_id.startswith("tgws-") else user_id
        async with self._product_init_locks.setdefault(workspace_id, asyncio.Lock()):
            cached = self._cache.get(workspace_id)
            needs_start = cached is None or not cached[0].is_alive or time.monotonic() - cached[1] >= self._ttl
            if needs_start:
                root = self.host_root(user_id, agent_id, workspace_id)
                if not root.resolve().is_relative_to(Path(self._basedir).resolve()):
                    raise LookupError("Workspace not found")
                self._optional_candidates[workspace_id] = await asyncio.to_thread(self._optional_mcps.prepare, root)
            workspace = await super().get_workspace(owner, mapped, session_id, workspace_id)
            if needs_start:
                await sync_missing_skills(workspace, self._skill_paths)
            await self._optional_mcps.connect(workspace, self._optional_candidates.get(workspace_id, []), workspace_id)
            return workspace


class ProductLocalWorkspaceManager(_ProductPathMixin, LocalWorkspaceManager):
    """Keep legacy local roots; isolate each new product workspace on disk."""

    def host_root(self, user_id: str, agent_id: str, workspace_id: str) -> Path:
        mapped = self._mapped_agent(user_id, agent_id, workspace_id)
        return Path(self._basedir) / mapped

    def agent_path(self, user_id: str, agent_id: str, workspace_id: str, relative: str) -> str:
        return str(self.host_root(user_id, agent_id, workspace_id) / relative)


class ProductDockerWorkspaceManager(_ProductPathMixin, DockerWorkspaceManager):
    """Use native Docker workspaces with distinct host bind mounts per product."""

    async def _build_and_start(self, *, workspace_id, user_id, agent_id):
        if not getattr(self, 'require_product_workspace', False):
            return await super()._build_and_start(workspace_id=workspace_id, user_id=user_id, agent_id=agent_id)
        from traceglass.accounts.sandbox import AccountDockerWorkspace
        workspace = AccountDockerWorkspace(
            workspace_id=workspace_id, host_workdir=self._workdir_for(user_id, agent_id),
            base_image=self._base_image, node_version=self._node_version, extra_pip=self._extra_pip,
            gateway_port=self._gateway_port, env=self._env,
            default_mcps=self._default_mcps, skill_paths=self._skill_paths,
        )
        try:
            await workspace.initialize()
        except BaseException:
            await workspace.close()
            raise
        workspace.instructions += ('\nAccount sandbox: runtime network access is disabled. '
            'Do not try to reach other containers or the host. Report when validation requires external connectivity.')
        return workspace

    def host_root(self, user_id: str, agent_id: str, workspace_id: str) -> Path:
        mapped = self._mapped_agent(user_id, agent_id, workspace_id)
        owner = hashlib.sha256(user_id.encode()).hexdigest() if workspace_id.startswith("tgws-") else user_id
        return Path(self._workdir_for(owner, mapped))

    def agent_path(self, user_id: str, agent_id: str, workspace_id: str, relative: str) -> str:
        return "/workspace/" + relative
