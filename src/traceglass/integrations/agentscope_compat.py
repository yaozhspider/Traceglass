"""App-local compatibility for AgentScope 2.0.6's workspace cache and wake race.

The native dispatcher drops a wake received while a session is running, but
InboxMiddleware only drains before reasoning. A result arriving during the last
model call can therefore remain queued forever. Recheck after the native run's
task is done, then use its existing wakeup path. Execution stays in AgentScope.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import wraps
import logging
import json
import time
from typing import Any

from pydantic import PrivateAttr

from agentscope.app._bus_ops import enqueue_run_trigger
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.event import HintBlockEvent, UserInterruptEvent
from agentscope.message import HintBlock
from agentscope.middleware import MiddlewareBase
from agentscope.state import ToolContext
from agentscope.state._state import ReadCacheEntry

logger = logging.getLogger(__name__)


class _WorkspaceToolContext(ToolContext):
    """Keep native cache fields/LRU, but stat files in their own filesystem.

    Native 2.0.6 Read/Edit use a backend for bytes, while ToolContext uses
    host getmtime. Container-only paths consequently never enter its cache.
    The workspace reference is per-run and never part of persisted state.
    """

    _workspace: Any = PrivateAttr()

    async def _mtime(self, file_path: str) -> float | None:
        try:
            # stat_mtime's remote implementation truncates to whole seconds.
            # stat retains fractional timestamps for stale-cache detection.
            entry = await self._workspace.get_backend().stat(file_path)
            return entry.mtime if entry is not None and not entry.is_dir else None
        except Exception:
            return None

    async def get_cache(self, file_path: str) -> ReadCacheEntry | None:
        for entry in self.read_file_cache:
            if entry.file_path == file_path:
                if await self._mtime(file_path) == entry.updated_at:
                    return entry
                self.read_file_cache.remove(entry)
                return None
        return None

    async def cache_file(self, file_path: str, lines: list[str]) -> None:
        updated_at = await self._mtime(file_path)
        if updated_at is None:
            return
        # Preserve the pinned native cache's KiB accounting and eviction rules.
        size = sum(len(line.encode('utf-8')) for line in lines) / 1024
        self.read_file_cache = [entry for entry in self.read_file_cache if entry.file_path != file_path]
        while len(self.read_file_cache) >= self.max_cache_files:
            self.read_file_cache.pop(0)
        current_size = sum(entry.bytes for entry in self.read_file_cache)
        while self.read_file_cache and current_size + size > self.max_cache_bytes:
            current_size -= self.read_file_cache.pop(0).bytes
        self.read_file_cache.append(ReadCacheEntry(
            lines=lines, updated_at=updated_at, bytes=size, file_path=file_path,
        ))


class WorkspaceFileCacheMiddleware(MiddlewareBase):
    """Rebind the serialized native cache before each run, including approvals."""

    def __init__(self, workspace):
        self._workspace = workspace

    async def on_reply(self, agent, input_kwargs, next_handler):
        context = _WorkspaceToolContext.model_validate(agent.state.tool_context.model_dump())
        context._workspace = self._workspace
        agent.state.tool_context = context
        async for event in next_handler(**input_kwargs):
            yield event


class PendingInboxMessageBus(InMemoryMessageBus):
    """Add a non-consuming peek to the pinned native in-process queue format."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stopped_worker_resumes: dict[str, str] = {}
        self._paused_sessions: set[str] = set()

    def pause_session(self, session_id: str) -> None:
        self._paused_sessions.add(session_id)
        self.revoke_stopped_worker(session_id)

    def resume_session(self, session_id: str) -> None:
        self._paused_sessions.discard(session_id)
        self.revoke_stopped_worker(session_id)

    def is_paused(self, session_id: str) -> bool:
        return session_id in self._paused_sessions

    def has_pending_inbox(self, session_id: str) -> bool:
        now = time.monotonic()
        return any(expires is None or expires > now
                   for _, _, expires in self._queues.get(MessageBusKeys.inbox(session_id), ()))

    def allow_stopped_worker(self, session_id: str, reply_id: str) -> None:
        self._stopped_worker_resumes[session_id] = reply_id

    def revoke_stopped_worker(self, session_id: str) -> None:
        self._stopped_worker_resumes.pop(session_id, None)

    def consume_stopped_worker(self, session_id: str, reply_id: str) -> bool:
        return self._stopped_worker_resumes.pop(session_id, None) == reply_id


class DirectedWorkerResumeMiddleware(MiddlewareBase):
    """Let a leader's explicit named TeamSay resume its stopped member once.

    Native TeamSay still delivers the inbox hint and owns wake scheduling.
    Late worker reports and broadcasts never grant this resume permission.
    """

    def __init__(self, storage, bus, user_id, agent_id, session_id):
        self.storage, self.bus = storage, bus
        self.user_id, self.agent_id, self.session_id = user_id, agent_id, session_id
        self._informed_reply = None

    async def on_reasoning(self, agent, input_kwargs, next_handler):
        if self._informed_reply != agent.state.reply_id:
            self._informed_reply = agent.state.reply_id
            leader = await self.storage.get_session(self.user_id, self.agent_id, self.session_id)
            team = await self.storage.get_team(self.user_id, leader.team_id) if leader and leader.team_id else None
            stopped = []
            if team and team.session_id == self.session_id:
                for member in team.data.members:
                    if member.owner_id != self.user_id or member.role != 'created' or not member.session_id:
                        continue
                    child = await self.storage.get_session(self.user_id, member.agent_id, member.session_id)
                    if child is None or child.team_id != team.id:
                        continue
                    if await self.bus.is_locked(MessageBusKeys.session_lock(child.id)):
                        continue
                    messages, _ = await self.storage.list_messages(self.user_id, child.id, limit=1)
                    previous = messages[0] if messages else None
                    reason = previous.finished_reason if previous and previous.role == 'assistant' else None
                    if self.bus.is_paused(child.id):
                        reason = 'interrupted'
                    if reason not in {'interrupted', 'error', 'exceed_max_iters'}:
                        continue
                    record = await self.storage.get_agent(self.user_id, member.agent_id)
                    if record:
                        stopped.append({'name': record.data.name, 'last_reply_status': reason})
            if stopped:
                hint = HintBlock(source='traceglass.team_resume', hint=(
                    '以下成员的最近执行已停止，不要继续声称它们正在分析。'
                    '用户要求继续分析且对应 Todo 未完成时，可用 TeamSay(to=成员名, content=明确的后续任务)'
                    '恢复原成员及其历史，无需新建 Agent。普通广播不会解除暂停；等待授权仍需处理原生授权请求。'
                    '\n成员状态数据：' + json.dumps(stopped, ensure_ascii=False)))
                agent.state.append_context(agent.name, [hint])
                yield HintBlockEvent(reply_id=agent.state.reply_id, block_id=hint.id,
                                     source=hint.source, hint=hint.hint)
        async for event in next_handler(**input_kwargs):
            yield event

    async def on_acting(self, agent, input_kwargs, next_handler):
        call = input_kwargs.get('tool_call')
        target_sid = None
        if call is not None and call.name == 'TeamSay':
            try:
                args = json.loads(call.input) if isinstance(call.input, str) else call.input
            except (ValueError, TypeError):
                args = {}
            target = args.get('to') if isinstance(args, dict) else None
            leader = await self.storage.get_session(self.user_id, self.agent_id, self.session_id)
            team = await self.storage.get_team(self.user_id, leader.team_id) if leader and leader.team_id else None
            if target and team and team.session_id == self.session_id:
                for member in team.data.members:
                    if member.owner_id != self.user_id or not member.session_id or member.session_id == self.session_id:
                        continue
                    record = await self.storage.get_agent(self.user_id, member.agent_id)
                    if record is None or member.role != 'created' or record.data.name != target:
                        continue
                    child = await self.storage.get_session(self.user_id, member.agent_id, member.session_id)
                    if child is None or child.team_id != team.id:
                        continue
                    messages, _ = await self.storage.list_messages(self.user_id, child.id, limit=1)
                    previous = messages[0] if messages else None
                    if self.bus.is_paused(child.id) or (previous and
                            previous.role == 'assistant' and previous.finished_reason in {'interrupted', 'error'}):
                        target_sid = child.id
                        self.bus.allow_stopped_worker(child.id, previous.id if previous else '')
                    break
        try:
            async for event in next_handler(**input_kwargs):
                if target_sid and getattr(event, 'state', None) == 'error':
                    self.bus.revoke_stopped_worker(target_sid)
                yield event
        except BaseException:
            if target_sid:
                self.bus.revoke_stopped_worker(target_sid)
            raise


@asynccontextmanager
async def preserve_inbox_wakeups(service, registry, storage, bus: PendingInboxMessageBus):
    """Wrap only this app's native service; undo it before native shutdown.

    Callback tasks only inspect completion and enqueue a native wake. They do
    not execute agents, move inbox messages, retry failures, or poll sessions.
    """
    original_run = service.run
    active = True
    checks: dict[str, asyncio.Task] = {}

    async def last_reply(user_id: str, session_id: str):
        messages, _ = await storage.list_messages(user_id, session_id, limit=1)
        return messages[0] if messages else None

    def busy(session_id: str) -> bool:
        task = registry.get(session_id)
        return task is not None and not task.done()

    async def wake_if_pending(user_id: str, session_id: str, agent_id: str) -> None:
        try:
            if not active or busy(session_id) or bus.is_paused(session_id) or not bus.has_pending_inbox(session_id):
                return
            reply = await last_reply(user_id, session_id)
            # Native run catches provider/setup errors. Persisted completion is
            # the authority; parked, interrupted and failed replies stay parked.
            if reply is None or reply.role != 'assistant' or reply.finished_reason != 'completed':
                return
            if not active or busy(session_id) or not bus.has_pending_inbox(session_id):
                return
            if await bus.is_locked(MessageBusKeys.session_lock(session_id)):
                return
            await enqueue_run_trigger(bus, user_id=user_id, session_id=session_id, agent_id=agent_id)
        except Exception:
            logger.exception('Could not restore native inbox wake for session %s', session_id)

    def after_done(task: asyncio.Task, user_id: str, session_id: str, agent_id: str) -> None:
        if not active or task.cancelled() or task.cancelling() or task.exception() is not None:
            return
        existing = checks.get(session_id)
        if existing is not None and not existing.done():
            return
        check = asyncio.create_task(wake_if_pending(user_id, session_id, agent_id),
                                    name=f'native-inbox-check:{session_id}')
        checks[session_id] = check

        def cleanup(done):
            if checks.get(session_id) is done:
                checks.pop(session_id, None)

        check.add_done_callback(cleanup)

    @wraps(original_run)
    async def run(user_id, session_id, agent_id, input_msg=None):
        current = asyncio.current_task()
        # The pinned native dispatcher names automatic inbox tasks "wake-run".
        # HTTP input=None is an explicit continuation and must keep working.
        automatic_wake = (input_msg is None and current is not None
                          and registry.get(session_id) is current
                          and current.get_name() == f'{MessageBusKeys.WAKEUP_KIND_WAKE}-run:{session_id}')
        if automatic_wake:
            # Native and repaired wakes can meet at the completion boundary.
            # The first native run drains the inbox; a duplicate has no work.
            if not bus.has_pending_inbox(session_id):
                return
            previous = await last_reply(user_id, session_id)
            if bus.is_paused(session_id) or (previous is not None and previous.finished_reason in {'interrupted', 'error'}):
                if not bus.consume_stopped_worker(session_id, previous.id if previous else ''):
                    return
            bus.resume_session(session_id)
        elif not isinstance(input_msg, UserInterruptEvent):
            # A user message/Continue is explicit; interruption bookkeeping is
            # not. Pausing also covers a currently idle leader awaiting workers.
            bus.resume_session(session_id)
        await original_run(user_id, session_id, agent_id, input_msg)
        if isinstance(input_msg, UserInterruptEvent) or (current is not None and current.cancelling()):
            return
        registered = registry.get(session_id)
        if registered is current:
            # ChatService.run has released the session lock, but its enclosing
            # task is still registered. A wake now would be dropped a second time.
            current.add_done_callback(lambda done: after_done(done, user_id, session_id, agent_id))
        else:
            # Direct API consumers may await run without the native registry.
            await wake_if_pending(user_id, session_id, agent_id)

    service.run = run
    try:
        yield
    finally:
        active = False
        service.run = original_run
        pending = list(checks.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
