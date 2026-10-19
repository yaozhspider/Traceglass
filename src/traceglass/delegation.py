"""Product policy around AgentScope's native team tools, without scheduling."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from weakref import WeakValueDictionary

from agentscope.app.storage import StorageBase
from agentscope.app.storage._utils import _ensure_team_members
from agentscope.app._tool._agent_invite import _display_name
from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolChunk, ToolResponse, TaskUpdate
from agentscope.permission import PermissionBehavior

MAX_SUBAGENTS = 8
_ADD_MEMBER = frozenset({'AgentCreate', 'AgentInvite'})
_TEAM_MUTATIONS = _ADD_MEMBER | {'TeamCreate', 'TeamDelete', 'TeamSay', 'TodoWrite', 'TaskUpdate'}
logger = logging.getLogger(__name__)


def _rejection(call_id: str, message: str) -> Iterator[ToolChunk | ToolResponse]:
    # Native SSE/history consumes chunk deltas; the completed response feeds
    # model context. Both are needed so user and model see the same reason.
    chunk = ToolChunk(id=call_id, state=ToolResultState.ERROR, content=[TextBlock(text=message)])
    yield chunk
    yield ToolResponse(id=call_id).append_chunk(chunk)


class DelegationPolicy:
    """Create per-session guards sharing the native leader's mutation lock.

    The current service uses one process and an InMemoryMessageBus. A deployment
    with multiple service processes must provide a shared lock at that boundary.
    Member counts always come from persistent native storage, never this object.
    """

    def __init__(self, storage: StorageBase, evidence_store=None, release_completed=None):
        self.storage = storage
        self.evidence_store = evidence_store
        self.release_completed = release_completed
        self._locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = WeakValueDictionary()

    def for_session(self, user_id: str, agent_id: str, session_id: str) -> MiddlewareBase:
        return _DelegationMiddleware(self, user_id, agent_id, session_id)

    def _session_lock(self, user_id: str, session_id: str) -> asyncio.Lock:
        key = (user_id, session_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock


class _DelegationMiddleware(MiddlewareBase):
    def __init__(self, policy: DelegationPolicy, user_id: str, agent_id: str, session_id: str):
        self._policy = policy
        self._user_id = user_id
        self._agent_id = agent_id
        self._session_id = session_id

    async def _leader_members(self):
        storage = self._policy.storage
        session = await storage.get_session(self._user_id, self._agent_id, self._session_id)
        team = await storage.get_team(self._user_id, session.team_id) if session and session.team_id else None
        if not team or team.session_id != self._session_id:
            return {}
        result = {}
        for member in await _ensure_team_members(storage, self._user_id, team):
            record = await storage.get_agent(member.owner_id, member.agent_id)
            if record:
                display = _display_name(record.data.name, record.id) if member.role == 'invited' else record.data.name
                result[display] = member
        return result

    async def _release_accepted(self, agent, previous):
        if not self._policy.release_completed:
            return
        tasks = agent.state.tasks_context.tasks
        owners = {t.owner for t in tasks if t.state == 'completed' and t.owner and
                  previous.get(t.id) != 'completed'}
        members = await self._leader_members() if owners else {}
        for owner in owners:
            member = members.get(owner)
            if not member or any(t.owner == owner and t.state != 'completed' for t in tasks):
                continue
            try:
                await self._policy.release_completed(member.owner_id, member.agent_id, member.session_id)
            except Exception:
                logger.warning('Accepted worker REA release failed for session %s', member.session_id)

    async def on_acting(
        self, agent: Any, input_kwargs: dict,
        next_handler: Callable[..., AsyncIterator[ToolChunk | ToolResponse]],
    ) -> AsyncIterator[ToolChunk | ToolResponse]:
        call = input_kwargs.get('tool_call')
        name = call.name if call is not None else ''
        if name not in _TEAM_MUTATIONS:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        # AgentScope only attaches mutation tools to leaders. Hold the same lock
        # across the check AND native call, which writes the team's whole roster.
        async with self._policy._session_lock(self._user_id, self._session_id):
            if name in {'TodoWrite', 'TaskUpdate'}:
                previous = {t.id: t.state for t in agent.state.tasks_context.tasks}
                async for item in next_handler(**input_kwargs):
                    if isinstance(item, ToolResponse) and item.state == ToolResultState.SUCCESS:
                        await self._release_accepted(agent, previous)
                    yield item
                return
            if name == 'TeamSay':
                try:
                    args = json.loads(call.input or '{}')
                except (ValueError, TypeError):
                    args = {}
                match = re.match(r'^task_id=([^\s=]+)(?:\n|$)', args.get('content', ''))
                members = await self._leader_members() if match else {}
                if not match or not members:
                    async for item in next_handler(**input_kwargs):
                        yield item
                    return
                target = args.get('to')
                tasks = {t.id: t for t in agent.state.tasks_context.tasks}
                task = tasks.get(match.group(1))
                if (target not in members or task is None or task.state == 'completed' or
                    task.owner not in (None, target) or
                    any(dep not in tasks or tasks[dep].state != 'completed' for dep in task.blocked_by) or
                    any(t.id != task.id and t.owner == target and t.state != 'completed' for t in tasks.values())):
                    for item in _rejection(call.id, 'TeamSay: 新任务须指向一个明确成员和依赖已满足、无冲突 owner 的未完成 task_id。'):
                        yield item
                    return
                if hasattr(agent, '_check_permission'):
                    update_args = {'task_id': task.id, 'owner': target, 'status': 'in_progress'}
                    update_call = ToolCallBlock(id=call.id + '-task-binding', name='TaskUpdate',
                                                input=json.dumps(update_args))
                    permission = await agent._check_permission(update_call, TaskUpdate(), update_args)
                    if permission.behavior != PermissionBehavior.ALLOW:
                        for item in _rejection(call.id, 'TeamSay: 任务分配还需原生 TaskUpdate 权限；先通过原生任务工具完成授权与分配。'):
                            yield item
                        return
                previous = task.owner, task.state
                task.owner, task.state = target, 'in_progress'
                succeeded = False
                try:
                    async for item in next_handler(**input_kwargs):
                        if isinstance(item, ToolResponse):
                            succeeded = item.state == ToolResultState.SUCCESS
                        yield item
                finally:
                    if not succeeded:
                        task.owner, task.state = previous
                return
            if name == 'TeamDelete':
                for item in _rejection(
                    call.id, 'TeamDelete: 为保留可展开的子 Agent 会话、工具记录及证据历史，'
                    '不允许模型自动删除团队。请用 TeamSay 复用现有成员并分配后续任务。',
                ):
                    yield item
                return
            if name in _ADD_MEMBER:
                try:
                    args = json.loads(call.input or '{}')
                except (ValueError, TypeError):
                    args = {}
                match = re.match(r'^task_id=([^\s=]+)(?:\n|$)', args.get('prompt', ''))
                tasks = {t.id: t for t in agent.state.tasks_context.tasks}
                task = tasks.get(match.group(1)) if match else None
                if (task is None or task.state == 'completed' or task.owner or
                    any(dep not in tasks or tasks[dep].state != 'completed' for dep in task.blocked_by)):
                    for item in _rejection(call.id, f'{name}: 用 TodoWrite 建立无 owner、未完成且依赖已满足的任务；'
                                           'prompt 首行须为 task_id=<原生任务ID>。'):
                        yield item
                    return
                storage = self._policy.storage
                session = await storage.get_session(self._user_id, self._agent_id, self._session_id)
                if args.get('subagent_type') == 'verifier' and session:
                    from traceglass.verification import verification_prompt
                    sha = re.search(r'^sample_sha256=([a-fA-F0-9]{64})$', args.get('prompt', ''), re.MULTILINE)
                    try:
                        if not sha:
                            raise ValueError('Verifier prompt needs sample_sha256=<hash> on its own line.')
                        prompt = await asyncio.to_thread(verification_prompt, agent.state,
                            self._policy.evidence_store, self._user_id, session.config.workspace_id,
                            sha.group(1).lower(), task.id)
                    except ValueError as exc:
                        for item in _rejection(call.id, str(exc)):
                            yield item
                        return
                    args = {**args, 'prompt': prompt, 'description': 'Independent scoped evidence verifier.'}
                    call = call.model_copy(update={'input': json.dumps(args, ensure_ascii=False)})
                    input_kwargs = {**input_kwargs, 'tool_call': call}
                if session is not None and session.team_id is not None:
                    team = await storage.get_team(self._user_id, session.team_id)
                    if team is not None:
                        members = await _ensure_team_members(storage, self._user_id, team)
                        count = sum(member.session_id != team.session_id for member in members)
                        if count >= MAX_SUBAGENTS:
                            for item in _rejection(
                                call.id, f'{name}: 团队最多使用 {MAX_SUBAGENTS} 个子 Agent（不含主 Agent），'
                                '当前已达到上限。请用 TeamSay 给现有成员分配后续任务。',
                            ):
                                yield item
                            return
            if name not in _ADD_MEMBER:
                async for item in next_handler(**input_kwargs):
                    yield item
                return
            previous = (task.owner, task.state)
            task.owner = args.get('name') if name == 'AgentCreate' else args.get('target')
            task.state = 'in_progress'
            succeeded = False
            try:
                async for item in next_handler(**input_kwargs):
                    if isinstance(item, ToolResponse):
                        succeeded = item.state == ToolResultState.SUCCESS
                        if not succeeded:
                            task.owner, task.state = previous
                        elif name == 'AgentInvite':
                            handle = str(args.get('target', '')).rsplit('@', 1)[-1]
                            for display, member in (await self._leader_members()).items():
                                if member.role == 'invited' and member.agent_id.startswith(handle):
                                    task.owner = display
                                    break
                    yield item
            finally:
                if not succeeded:
                    task.owner, task.state = previous
