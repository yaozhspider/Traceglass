"""Native MCP connections to the kit's immutable image, isolated per session.

The API owns Docker; analysis containers never receive its socket. Each REA
process sees only its workspace, at the same /workspace path as native tools.
AgentScope MCPTool still owns schemas, permissions, execution and events.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import time
from uuid import uuid4

from agentscope.mcp import MCPClient, StdioMCPConfig


def rea_client(kit: Path, root: Path, user_id: str, session_id: str) -> MCPClient:
    kit, root = kit.resolve(strict=True), root.resolve(strict=True)
    manifest = json.loads((kit / 'MANIFEST.json').read_text())
    image = manifest['image']['image_id']
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('REA kit must identify an immutable Docker image')
    launcher = kit / 'bin/docker-cli'
    if not launcher.is_file() or not root.is_dir() or ',' in str(root):
        raise ValueError('Invalid REA launcher or workspace mount')
    identity = hashlib.sha256(f'{user_id}\0{session_id}\0{root}'.encode()).hexdigest()[:32]
    return MCPClient(name='rea', is_stateful=True, execution_timeout=600,
        mcp_config=StdioMCPConfig(command=str(launcher), args=[
            'run', '--rm', '-i', '--name', f'traceglass-rea-{identity}-{uuid4().hex[:8]}',
            '--label', 'traceglass.integration=rea', '--platform', 'linux/amd64',
            '--network', 'none', '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL',
            '--mount', f'type=bind,source={root},target=/workspace,readonly',
            '--mount', f'type=volume,source=traceglass-rea-{identity},target=/home/rea',
            '--workdir', '/workspace', image, 'mcp',
        ]))


@dataclass
class _Connection:
    workspace_id: str
    ready: asyncio.Future
    stop: asyncio.Event
    task: asyncio.Task | None = None
    status: str = 'connecting'
    count: int = 0
    attempted_at: float = 0
    client: MCPClient | None = None
    instance_id: str = field(default_factory=lambda: uuid4().hex)


class ReaSessions:
    """Hold a native stateful MCP client for each analysis session.

    The owner task opens AND closes MCP's AnyIO scopes. Pauses and approval
    waits retain the current binary; service shutdown closes the connections.
    Analysis caches persist in separate Docker volumes, not the source tree.
    """

    def __init__(self, kit: Path | None):
        self.kit = kit
        self._connections: dict[tuple, _Connection] = {}

    async def tools(self, root: Path, user_id: str, session_id: str, workspace_id: str):
        if self.kit is None:
            return []
        key = (user_id, session_id, str(root.resolve()))
        connection = self._connections.get(key)
        if connection is None or (connection.task.done() and time.monotonic() - connection.attempted_at > 30):
            connection = _Connection(workspace_id, asyncio.get_running_loop().create_future(),
                                     asyncio.Event(), attempted_at=time.monotonic())
            self._connections[key] = connection
            connection.task = asyncio.create_task(self._serve(connection, root, user_id, session_id),
                                                   name=f'rea-mcp:{session_id}')
        # Cancellation of one caller must not cancel a shared readiness Future.
        return await asyncio.shield(connection.ready)

    async def _serve(self, connection, root, user_id, session_id):
        client = None
        try:
            client = rea_client(self.kit, root, user_id, session_id)
            connection.client = client
            async with asyncio.timeout(120):
                await client.connect()
                tools = await client.list_tools()
            connection.count = len(tools)
            connection.status = 'connected'
            connection.ready.set_result(tools)
            await connection.stop.wait()
        except Exception:
            # A missing image or failed REA must not take down other tools, nor
            # disclose environment values/raw subprocess errors in product APIs.
            connection.status = 'unavailable'
        finally:
            if not connection.ready.done():
                connection.ready.set_result([])
            if client is not None:
                if client.is_connected:
                    await client.close()
                elif client._stack is not None:
                    # Native connect does not catch cancellation during setup.
                    await client._stack.aclose()
            if connection.status == 'connected':
                connection.status = 'not_checked'

    def connection_id(self, root: Path, user_id: str, session_id: str) -> str:
        connection = self._connections.get((user_id, session_id, str(root.resolve())))
        return connection.instance_id if connection else 'unavailable'

    async def workflow_tools(self, root: Path, user_id: str, session_id: str, workspace_id: str):
        from .rea_workflows import ReaWorkflows
        await self.tools(root, user_id, session_id, workspace_id)
        connection = self._connections.get((user_id, session_id, str(root.resolve())))
        # AgentScope 2.0.6 exposes only MCP tools publicly. Keep this one
        # compatibility boundary narrow; MCP ClientSession owns prompt protocol.
        client = connection.client if connection and connection.status == 'connected' else None
        return ReaWorkflows(client._session if client else None).tools()

    def status(self, workspace_id: str) -> list[dict]:
        if self.kit is None:
            return []
        connections = [c for c in self._connections.values() if c.workspace_id == workspace_id]
        states = {c.status for c in connections}
        state = next((s for s in ('unavailable', 'connecting', 'connected') if s in states), 'not_checked')
        count = max((c.count for c in connections), default=0)
        messages = {
            'not_checked': 'REA 将在分析启动时连接；主、子 Agent 使用独立会话。',
            'connecting': '正在启动独立 REA 容器并发现工具。',
            'connected': f'已发现 {count} 个 REA 工具；每个 Agent 独立分析会话。',
            'unavailable': '部分或全部 REA 会话未连接，请检查 kit 与 Docker 镜像；其他工具仍可使用。',
        }
        return [{'name': 'rea', 'optional': True, 'status': state, 'message': messages[state],
                 'tool_count': count, 'checked_at': None}]

    async def close(self):
        tasks = []
        for connection in self._connections.values():
            connection.stop.set()
            if connection.task and not connection.task.done():
                if not connection.ready.done():
                    connection.task.cancel()
                tasks.append(connection.task)
        await asyncio.gather(*tasks, return_exceptions=True)
