"""Product policy around AgentScope's native team tools, without scheduling."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from weakref import WeakValueDictionary

from agentscope.app.storage import StorageBase
from agentscope.app.storage._utils import _ensure_team_members
from agentscope.message import TextBlock, ToolResultState
from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolChunk, ToolResponse

MAX_SUBAGENTS = 8
_ADD_MEMBER = frozenset({'AgentCreate', 'AgentInvite'})
_TEAM_MUTATIONS = _ADD_MEMBER | {'TeamCreate', 'TeamDelete'}


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

    def __init__(self, storage: StorageBase):
        self.storage = storage
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
            if name == 'TeamDelete':
                for item in _rejection(
                    call.id, 'TeamDelete: 为保留可展开的子 Agent 会话、工具记录及证据历史，'
                    '不允许模型自动删除团队。请用 TeamSay 复用现有成员并分配后续任务。',
                ):
                    yield item
                return
            if name in _ADD_MEMBER:
                if not any(task.state != 'completed' for task in agent.state.tasks_context.tasks):
                    for item in _rejection(call.id, f'{name}: 请先用 TodoWrite 写入尚未完成的任务，再分配子 Agent。'):
                        yield item
                    return
                storage = self._policy.storage
                session = await storage.get_session(self._user_id, self._agent_id, self._session_id)
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
            async for item in next_handler(**input_kwargs):
                yield item
