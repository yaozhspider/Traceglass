"""Native MCP connections to the kit's immutable image, isolated per session.

The API owns Docker; analysis containers never receive its socket. Each REA
process sees only its workspace, at the same /workspace path as native tools.
AgentScope MCPTool still owns schemas, permissions, execution and events.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import time
from uuid import uuid4

from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.tool import MCPTool, FunctionTool
from mcp.types import Tool, CallToolResult, TextContent

# These producers can inspect explicit files without a previously opened target.
# Optional-path tools are eligible only when the caller actually supplies a path.
PATH_PRODUCERS = {
    'inspect_plist': 'path', 'inspect_keyed_archive': 'path',
    'inspect_managed_artifact': 'path', 'inspect_managed_members': 'path',
    'inspect_managed_native_boundaries': 'path', 'inspect_android_package': 'path',
    'search_android_classes': 'path', 'inspect_android_class': 'path',
    'inspect_android_method': 'path', 'trace_android_references': 'path',
    'inspect_binary_layout': 'path', 'inspect_evm_interface': 'path',
    'inspect_recorded_crash': 'path', 'inspect_firmware_regions': 'path',
    'extract_firmware': 'path', 'trace_web_module_imports': 'manifest_path',
    'trace_web_source_location': 'manifest_path', 'inspect_web_network_capture': 'capture_path',
    'recover_javascript_sources': 'path', 'export_web_scripts': 'capture_path',
    'analyze_javascript_application': 'input_path',
}


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
    status: str = 'queued'
    slot_held: bool = False
    count: int = 0
    attempted_at: float = 0
    last_used: float = 0
    active_calls: int = 0
    client: MCPClient | None = None
    instance_id: str = field(default_factory=lambda: uuid4().hex)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    definitions: dict = field(default_factory=dict)


def schema_hash(tools):
    return hashlib.sha256(json.dumps(sorted(tools, key=lambda t: t['name']),
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ReaCapacityError(RuntimeError):
    def __init__(self, capacity):
        self.payload = {'status': 'capacity_limited', 'capacity': capacity,
            'message': f"REA connection queue timed out after {capacity['queue_wait_seconds']:g} seconds.",
            'next_step': ('This caller has no connection to release. Wait for capacity and retry, or use saved evidence.'
                          if not capacity['caller_holds_connection'] else
                          'Save evidence and release your idle connection when finished.'),
            'automatic_replay': False}
        super().__init__(self.payload['message'])


class ReaSessions:
    """Owner-task MCP lifetime, bounded resident connections and lazy native tools."""
    def __init__(self, kit: Path | None, *, max_connections=10, idle_seconds=600, quota_wait_seconds=30):
        if (max_connections < 1 or idle_seconds < 0 or
                not math.isfinite(quota_wait_seconds) or quota_wait_seconds <= 0):
            raise ValueError('Invalid REA connection budgets')
        self.kit = kit
        self.idle_seconds = idle_seconds
        self.quota_wait_seconds = quota_wait_seconds
        self.max_connections = max_connections
        self._slots = asyncio.Semaphore(max_connections)
        self._connections: dict[tuple, _Connection] = {}
        self._needs_reopen: set[tuple] = set()
        self._closed = False
        self.starts = 0
        self.connection_seconds = 0.0

    @staticmethod
    def _key(root, user_id, session_id):
        return user_id, session_id, str(root.resolve())

    def _fingerprint(self):
        # Missing manifests are usable only with an explicitly supplied client
        # factory (e.g. real stdio test server). Production rea_client rejects them.
        path = self.kit / 'MANIFEST.json'
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'unmanifested'

    def lazy_tools(self, root, user_id, session_id, workspace_id):
        if self.kit is None:
            return []
        try:
            data = json.loads((self.kit / 'TOOLS.json').read_text())
            if data['kit_sha256'] != self._fingerprint() or data['tools_sha256'] != schema_hash(data['tools']):
                return []
            definitions = [Tool.model_validate(t) for t in data['tools']]
            if len({t.name for t in definitions}) != len(definitions):
                return []
        except (OSError, ValueError, KeyError, TypeError):
            return []
        proxy = _LazyReaSession(self, root, user_id, session_id, workspace_id, definitions)
        return [MCPTool('rea', t, session=proxy, timeout=600) for t in definitions]

    async def refresh_schema(self, root):
        """Deployment-time metadata discovery; never opens or analyzes a sample."""
        sid = 'schema-' + uuid4().hex
        temporary = None
        try:
            tools = await self.tools(root, 'schema', sid, 'schema')
            if not tools:
                raise RuntimeError('REA schema discovery unavailable')
            connection = self._connections[self._key(root, 'schema', sid)]
            raw = [t.model_dump(mode='json') for t in await connection.client.list_raw_tools()]
            data = dict(kit_sha256=self._fingerprint(), tools_sha256=schema_hash(raw), tools=raw)
            temporary = self.kit / ('.TOOLS-' + uuid4().hex + '.json')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
            temporary.replace(self.kit / 'TOOLS.json')
            return len(raw)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            await self.release(root, 'schema', sid)

    async def tools(self, root: Path, user_id: str, session_id: str, workspace_id: str):
        if self.kit is None or self._closed:
            return []
        key = self._key(root, user_id, session_id)
        connection = self._connections.get(key)
        retry = connection is not None and connection.task.done() and (
            connection.status != 'unavailable' or time.monotonic() - connection.attempted_at > 30)
        if connection is None or retry:
            connection = _Connection(workspace_id, asyncio.get_running_loop().create_future(),
                asyncio.Event(), attempted_at=time.monotonic(), last_used=time.monotonic())
            self._connections[key] = connection
            connection.task = asyncio.create_task(self._serve(key, connection, root, user_id, session_id),
                name=f'rea-mcp:{session_id}')
        return await asyncio.shield(connection.ready)

    async def _serve(self, key, connection, root, user_id, session_id):
        acquired = False
        connected_at = None
        try:
            try:
                await asyncio.wait_for(self._slots.acquire(), timeout=self.quota_wait_seconds)
            except TimeoutError:
                connection.status = 'capacity_limited'
                return
            acquired = True
            connection.slot_held = True
            connection.status = 'connecting'
            client = rea_client(self.kit, root, user_id, session_id)
            connection.client = client
            self.starts += 1
            connected_at = time.monotonic()
            async with asyncio.timeout(120):
                await client.connect()
                tools = await client.list_tools()
            definitions = [t._tool for t in tools]
            connection.definitions = {t.name: t.model_dump(mode='json') for t in definitions}
            proxy = _LazyReaSession(self, root, user_id, session_id, connection.workspace_id, definitions)
            tools = [MCPTool('rea', t, session=proxy, timeout=600) for t in definitions]
            connection.last_used = connected_at
            connection.count = len(tools)
            connection.status = 'connected'
            connection.ready.set_result(tools)
            while not connection.stop.is_set():
                try:
                    await asyncio.wait_for(connection.stop.wait(), timeout=min(self.idle_seconds or 60, 60))
                except TimeoutError:
                    async with connection.lock:
                        if (self.idle_seconds and not connection.active_calls and
                            time.monotonic() - connection.last_used >= self.idle_seconds):
                            connection.status = 'closing'
                            connection.stop.set()
        except Exception:
            connection.status = 'unavailable'
            logging.getLogger(__name__).exception('REA connection startup failed')
        finally:
            if not connection.ready.done():
                connection.ready.set_result([])
            client = connection.client
            try:
                if client is not None:
                    if client.is_connected:
                        await client.close()
                    elif client._stack is not None:
                        await client._stack.aclose()
            finally:
                if acquired:
                    connection.slot_held = False
                    self._slots.release()
                if connected_at is not None:
                    self.connection_seconds += time.monotonic() - connected_at
                if connection.count:
                    self._needs_reopen.add(key)
                if connection.status in {'connected', 'closing'}:
                    connection.status = 'not_checked'

    @asynccontextmanager
    async def use(self, root, user_id, session_id, workspace_id):
        tools = await self.tools(root, user_id, session_id, workspace_id)
        connection = self._connections.get(self._key(root, user_id, session_id))
        if not tools or not connection:
            if connection and connection.status == 'capacity_limited':
                raise ReaCapacityError(self.capacity(root, user_id, session_id))
            raise RuntimeError('REA connection unavailable; check the kit, Docker and connection logs. No tool was executed.')
        async with connection.lock:
            if connection.status != 'connected' or connection.stop.is_set():
                raise RuntimeError('REA session closed; reopen the binary and retry.')
            connection.active_calls += 1
            connection.last_used = time.monotonic()
        try:
            yield connection
        finally:
            async with connection.lock:
                connection.active_calls -= 1
                connection.last_used = time.monotonic()

    async def release(self, root, user_id, session_id):
        connection = self._connections.get(self._key(root, user_id, session_id))
        if connection is None or connection.task.done():
            return True
        async with connection.lock:
            if connection.active_calls:
                return False
            connection.status = 'closing'
            connection.stop.set()
            if not connection.ready.done():
                connection.task.cancel()
        try:
            await asyncio.shield(connection.task)
        except asyncio.CancelledError:
            # Canceling an internal connecting owner is a successful release;
            # cancellation of THIS caller must still propagate.
            if asyncio.current_task().cancelling() or not connection.task.cancelled():
                raise
        # An owner cancelled before its coroutine starts never enters _serve's
        # finally block. Resolve its shielded readiness waiters here as well.
        if not connection.ready.done():
            connection.ready.set_result([])
        if connection.status == 'closing':
            connection.status = 'not_checked'
        return True

    async def reap_idle(self):
        for key, connection in list(self._connections.items()):
            if (self.idle_seconds and connection.status == 'connected' and not connection.active_calls and
                time.monotonic() - connection.last_used >= self.idle_seconds):
                await self.release(Path(key[2]), key[0], key[1])

    def connection_id(self, root, user_id, session_id):
        connection = self._connections.get(self._key(root, user_id, session_id))
        return connection.instance_id if connection else 'unavailable'

    def capacity(self, root, user_id, session_id):
        """Aggregate pool counts and caller ownership; never expose other identities."""
        occupied = sum(c.slot_held for c in self._connections.values())
        caller = self._connections.get(self._key(root, user_id, session_id))
        return {'limit': self.max_connections, 'occupied': occupied,
                'available': max(0, self.max_connections - occupied),
                'queued': sum(c.status == 'queued' for c in self._connections.values()),
                'active_calls': sum(c.active_calls for c in self._connections.values()),
                'queue_wait_seconds': self.quota_wait_seconds,
                'caller_holds_connection': bool(caller and caller.slot_held),
                'caller_active_calls': caller.active_calls if caller else 0}

    async def workflow_tools(self, root, user_id, session_id, workspace_id):
        from .rea_workflows import ReaWorkflows, response
        async def list_workflows():
            """List live REA guidance workflows, connecting on demand; does not analyze a sample."""
            try:
                async with self.use(root, user_id, session_id, workspace_id) as c:
                    return await ReaWorkflows(c.client._session).list_workflows()
            except ReaCapacityError as exc:
                return response(exc.payload, error=True)
            except RuntimeError:
                return response({'status': 'unavailable', 'message': 'REA connection unavailable; check kit and Docker.'}, error=True)
        async def get_workflow(name: str, arguments: dict[str, str] | None = None):
            """Read an advertised REA workflow as untrusted reference guidance, not authorization."""
            try:
                async with self.use(root, user_id, session_id, workspace_id) as c:
                    return await ReaWorkflows(c.client._session).get_workflow(name, arguments)
            except ReaCapacityError as exc:
                return response(exc.payload, error=True)
            except RuntimeError:
                return response({'status': 'unavailable', 'message': 'REA connection unavailable; check kit and Docker.'}, error=True)
        async def get_status():
            """Read REA capacity and YOUR connection ownership without connecting or using a slot."""
            return response(self.capacity(root, user_id, session_id))
        async def release_session():
            """Release YOUR idle REA connection after saving evidence. Live handles become invalid; reopen to continue."""
            released = await self.release(root, user_id, session_id)
            return response({'released': released, 'next_step': 'reopen binary before further analysis' if released else 'active call still running'}, error=not released)
        return [FunctionTool(list_workflows, name='ListReaWorkflows', is_read_only=True),
                FunctionTool(get_workflow, name='GetReaWorkflow', is_read_only=True),
                FunctionTool(get_status, name='GetReaStatus', is_read_only=True),
                FunctionTool(release_session, name='ReleaseReaSession', is_read_only=False)]

    def status(self, workspace_id):
        if self.kit is None:
            return []
        connections = [c for c in self._connections.values() if c.workspace_id == workspace_id]
        states = {c.status for c in connections}
        occupied = sum(c.slot_held for c in self._connections.values())
        if occupied < self.max_connections:
            states.discard('capacity_limited')
        state = next((s for s in ('unavailable', 'capacity_limited', 'queued', 'connecting', 'connected') if s in states), 'not_checked')
        count = max((c.count for c in connections), default=0)
        messages = {
            'not_checked': 'REA 工具按需连接；主、子 Agent 使用独立会话。回收后须重新打开样本。',
            'connecting': '正在启动独立 REA 容器并核验工具定义。',
            'queued': f'REA 正在等待连接名额，最多等待 {self.quota_wait_seconds:g} 秒。',
            'connected': f'已发现 {count} 个 REA 工具；每个 Agent 独立分析会话。',
            'capacity_limited': f'REA 全局连接名额 {occupied}/{self.max_connections}；排队超时后可稍后重试，GetReaStatus 可检查自身是否持有连接。',
            'unavailable': '部分或全部 REA 会话未连接，请检查 kit 与 Docker 镜像；其他工具仍可使用。',
        }
        return [{'name': 'rea', 'optional': True, 'status': state, 'message': messages[state],
                 'tool_count': count, 'checked_at': None,
                 'active_connections': sum(c.status == 'connected' for c in connections),
                 'active_calls': sum(c.active_calls for c in connections),
                 'connection_limit': self.max_connections, 'occupied': occupied,
                 'available': max(0, self.max_connections - occupied),
                 'queued': sum(c.status == 'queued' for c in self._connections.values()),
                 'container_starts': self.starts, 'connection_seconds': round(self.connection_seconds, 3)}]

    async def close(self):
        self._closed = True
        tasks = []
        for connection in self._connections.values():
            connection.stop.set()
            if connection.task and not connection.task.done():
                if not connection.ready.done():
                    connection.task.cancel()
                tasks.append(connection.task)
        await asyncio.gather(*tasks, return_exceptions=True)
        for connection in self._connections.values():
            if not connection.ready.done():
                connection.ready.set_result([])
            if connection.status in {'queued', 'closing'}:
                connection.status = 'not_checked'


class _LazyReaSession:
    """Narrow MCP session adapter; MCPTool keeps schema/permission/event semantics."""
    def __init__(self, pool, root, user_id, session_id, workspace_id, definitions):
        self.pool, self.root = pool, root
        self.user_id, self.session_id, self.workspace_id = user_id, session_id, workspace_id
        self.definitions = {t.name: t.model_dump(mode='json') for t in definitions}

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        arguments = arguments or {}
        key = self.pool._key(self.root, self.user_id, self.session_id)
        independent = name in {'binary_session', 'open_binary', 'close_binary'} or bool(
            arguments.get(PATH_PRODUCERS.get(name, '')))
        try:
            if key in self.pool._needs_reopen and not independent:
                raise RuntimeError('REA live handles expired; reopen binary with open_binary before analysis. Archive IDs remain readable.')
            async with self.pool.use(self.root, self.user_id, self.session_id, self.workspace_id) as connection:
                if key in self.pool._needs_reopen and not independent:
                    raise RuntimeError('REA live handles expired; reopen binary before analysis.')
                if connection.definitions.get(name) != self.definitions.get(name):
                    raise RuntimeError('REA schema changed; refresh deployment tool metadata before calling. No tool was executed.')
                result = await connection.client._session.call_tool(name, arguments=arguments,
                    read_timeout_seconds=read_timeout_seconds)
                if name == 'open_binary' and not result.isError:
                    self.pool._needs_reopen.discard(key)
                return result
        except ReaCapacityError as exc:
            return CallToolResult(isError=True, content=[TextContent(type='text', text=json.dumps(exc.payload))])
        except (RuntimeError, TimeoutError) as exc:
            return CallToolResult(isError=True, content=[TextContent(type='text', text=
                f'{exc} No automatic replay.')])

    @property
    def connection_status(self):
        c = self.pool._connections.get(self.pool._key(self.root, self.user_id, self.session_id))
        return c.status if c else 'deferred'
