"""Attach optional MCP services after the native gateway is already healthy.

AgentScope 2.0.6 treats every server in its startup file as required. Its native
runtime add_mcp API already isolates registration failures, so optional IDA is
kept in a sidecar and connected through that API after static tools start.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import time
from uuid import uuid4

from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.workspace import LocalWorkspace

OPTIONAL_NAMES = frozenset({"idapro"})
RETRY_SECONDS = 30.0


def _read_private_json(path: Path):
    if path.is_symlink():
        raise ValueError("MCP configuration must be a regular workspace file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            raise ValueError("MCP configuration must be a bounded regular workspace file")
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("MCP configuration must be a bounded regular workspace file")
    try:
        return json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        return None


def _write_private_json(path: Path, value) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.part")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class OptionalMCPs:
    def __init__(self, defaults: list[MCPClient]):
        self.defaults = {client.name: client for client in defaults if client.name in OPTIONAL_NAMES}
        self._status: dict[str, dict[str, dict]] = {}
        self._last_attempt: dict[tuple[str, str], float] = {}

    def prepare(self, root: Path) -> list[MCPClient]:
        """Migrate optional startup entries, preserving original client specs."""
        root.mkdir(parents=True, exist_ok=True)
        sidecar = root / ".traceglass-optional-mcps.json"
        candidates = dict(self.defaults)
        saved = _read_private_json(sidecar)
        if saved is not None:
            try:
                for record in saved if isinstance(saved, list) else []:
                    client = MCPClient.model_validate(record)
                    if client.name in OPTIONAL_NAMES:
                        candidates[client.name] = client
            except (OSError, ValueError):
                # Configured defaults remain a recoverable source of truth.
                pass
        startup = root / ".mcp"
        records = _read_private_json(startup)
        if records is not None:
            if isinstance(records, list):
                required = []
                for record in records:
                    if isinstance(record, dict) and record.get("name") in OPTIONAL_NAMES:
                        client = MCPClient.model_validate(record)
                        candidates[client.name] = client
                    else:
                        required.append(record)
                if len(required) != len(records):
                    # Save the optional specs before removing their startup copy.
                    _write_private_json(sidecar, [client.model_dump(mode="json") for client in candidates.values()])
                    _write_private_json(startup, required)
        if candidates:
            _write_private_json(sidecar, [client.model_dump(mode="json") for client in candidates.values()])
        return list(candidates.values())

    def status(self, workspace_id: str) -> list[dict]:
        values = {name: self._entry(name, "not_checked", "尚未连接；将在工作区启动时检查。")
                  for name in self.defaults}
        values.update(self._status.get(workspace_id, {}))
        return list(values.values())

    @staticmethod
    def _entry(name: str, status: str, message: str, *, checked: bool = False) -> dict:
        return {"name": name, "optional": True, "status": status, "message": message,
                "checked_at": datetime.now(timezone.utc).isoformat() if checked else None}

    async def connect(self, workspace, candidates: list[MCPClient], workspace_id: str) -> None:
        statuses = self._status.setdefault(workspace_id, {})
        registered = {client.name for client in await workspace.list_mcps()}
        for original in candidates:
            name = original.name
            previous = statuses.get(name)
            if name in registered:
                if previous is None or previous["status"] != "connected":
                    statuses[name] = self._entry(name, "connected", "已通过原生 MCP 工具发现验证。", checked=True)
                continue
            now = time.monotonic()
            if now - self._last_attempt.get((workspace_id, name), -RETRY_SECONDS) < RETRY_SECONDS:
                continue
            self._last_attempt[(workspace_id, name)] = now
            client = MCPClient.model_validate(original.model_dump(mode="json"))
            if isinstance(client.mcp_config, HttpMCPConfig):
                client.mcp_config.timeout = min(client.mcp_config.timeout or 3.0, 3.0)
            try:
                # LocalWorkspace.add_mcp does not probe stateless clients;
                # Docker's native gateway POST /mcps does this itself.
                if isinstance(workspace, LocalWorkspace):
                    if client.is_stateful:
                        await asyncio.wait_for(client.connect(), timeout=6.0)
                    await asyncio.wait_for(client.list_raw_tools(), timeout=6.0)
                await asyncio.wait_for(workspace.add_mcp(client), timeout=8.0)
            except Exception:
                # Do not emit endpoint URLs, credentials or raw exception trees.
                statuses[name] = self._entry(name, "unavailable", "连接或工具发现失败；静态分析仍可使用。下次分析会重试。", checked=True)
                if client.is_stateful and client.is_connected:
                    with suppress(Exception):
                        await client.close()
            else:
                statuses[name] = self._entry(name, "connected", "已通过原生 MCP 工具发现验证。", checked=True)
        if not hasattr(workspace, "_traceglass_base_instructions"):
            workspace._traceglass_base_instructions = workspace.instructions
        lines = [f"- {item['name']}: {item['status']}. {item['message']}" for item in self.status(workspace_id)]
        workspace.instructions = workspace._traceglass_base_instructions
        if lines:
            workspace.instructions += ("\n\nOptional MCP integration status:\n" + "\n".join(lines)
                                       + "\nUse the available tools. Do not claim an unavailable service was used.")
