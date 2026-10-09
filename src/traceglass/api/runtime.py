"""Project native run registration during setup without owning execution."""
from __future__ import annotations

import asyncio
from typing import Literal

from agentscope.app.deps import (
    get_background_task_manager,
    get_chat_run_registry,
    get_chat_service,
    get_current_user_id,
    get_message_bus,
    get_resource_access_service,
    get_session_service,
    get_storage,
)
from agentscope.app.message_bus import MessageBusKeys
from agentscope.app._router._schema import UpdateSessionRequest
from agentscope.app._router._session import update_session
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from traceglass.workspaces.service import ProductWorkspaceService


class PermissionSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    permission_mode: Literal['default', 'bypass']


async def _team_member_statuses(storage, registry, bus, native_sessions,
                                user_id: str, agent_id: str, leader_id: str) -> dict[str, str]:
    leader = await storage.get_session(user_id, agent_id, leader_id)
    if leader is None or not leader.team_id:
        return {}
    team = await storage.get_team(user_id, leader.team_id)
    if team is None or team.session_id != leader_id:
        return {}
    statuses = {}
    for member in team.data.members:
        sid = member.session_id
        if member.owner_id != user_id or not sid or sid == leader_id:
            continue
        # Native status probes inspect the global run lock first. Validate the
        # child and its backlink before probing, even for stale roster entries.
        child = await storage.get_session(user_id, member.agent_id, sid)
        if child is None or child.team_id != team.id:
            continue
        task = registry.get(sid)
        if task is not None and not task.done() and not await bus.is_locked(MessageBusKeys.session_lock(sid)):
            statuses[sid] = 'preparing'
        else:
            status = await native_sessions.get_session_status(user_id, member.agent_id, sid)
            if status is not None:
                statuses[sid] = status
    return statuses


def create_runtime_router(service: ProductWorkspaceService, extra_integration_status=None) -> APIRouter:
    router = APIRouter(prefix='/product', tags=['product-runtime'])

    @router.patch('/sessions/{session_id}/permission')
    async def set_permission(session_id: str, body: PermissionSelection, agent_id: str = Query(...),
                             user_id: str = Depends(get_current_user_id),
                             access=Depends(get_resource_access_service), storage=Depends(get_storage),
                             registry=Depends(get_chat_run_registry), bus=Depends(get_message_bus)):
        if await storage.get_session(user_id, agent_id, session_id) is None:
            raise HTTPException(404, 'Session not found')
        task = registry.get(session_id)
        if task is not None and not task.done():
            raise HTTPException(409, 'Agent 正在执行，请暂停或等待本轮结束后切换权限。')
        # Keep AgentScope's permission context, rules and persistence semantics.
        # The extra registry guard covers setup before its native lock is held.
        return await update_session(
            session_id=session_id, body=UpdateSessionRequest(permission_mode=body.permission_mode),
            agent_id=agent_id, user_id=user_id, storage=storage, access=access, message_bus=bus,
        )

    @router.get('/runtime')
    async def runtime(workspace_id: str = Query(...), user_id: str = Depends(get_current_user_id),
                      registry=Depends(get_chat_run_registry), bus=Depends(get_message_bus),
                      native_sessions=Depends(get_session_service)):
        try:
            # This public product read checks ownership and includes only the
            # user's main sessions. It never starts the native workspace.
            detail = await service.detail(user_id, workspace_id)
        except LookupError as exc:
            raise HTTPException(404, 'Workspace not found') from exc
        sessions = {}
        members = {}
        for record in detail['tasks']:
            sid = record['session_id']
            task = registry.get(sid)
            if task is not None and not task.done():
                locked = await bus.is_locked(MessageBusKeys.session_lock(sid))
                if not task.done():
                    sessions[sid] = 'running' if locked else 'preparing'
            members.update(await _team_member_statuses(
                service.storage, registry, bus, native_sessions, user_id, record['agent_id'], sid,
            ))
        integrations = service.manager.integration_status(workspace_id)
        if extra_integration_status is not None:
            integrations += extra_integration_status(workspace_id)
        return {'sessions': sessions, 'members': members, 'integrations': integrations}

    @router.post('/sessions/{session_id}/interrupt', status_code=202)
    async def interrupt(session_id: str, agent_id: str = Query(...),
                        scope: Literal['session', 'team'] = Query('session'),
                        user_id: str = Depends(get_current_user_id),
                        access=Depends(get_resource_access_service), storage=Depends(get_storage),
                        registry=Depends(get_chat_run_registry), bus=Depends(get_message_bus),
                        chat=Depends(get_chat_service), background=Depends(get_background_task_manager),
                        native_sessions=Depends(get_session_service)):
        await access.resolve_agent(user_id, agent_id)
        leader = await storage.get_session(user_id, agent_id, session_id)
        if leader is None:
            raise HTTPException(404, 'Session not found')

        async def stop(sid, aid):
            if hasattr(bus, 'pause_session'):
                bus.pause_session(sid)
            # Native interrupt deliberately leaves offloaded tools running.
            # A product pause includes those session-owned background tasks.
            background.cancel_session_tasks(sid)
            task = registry.get(sid)
            if scope == 'team' and (task is None or task.done()):
                if await native_sessions.get_session_status(user_id, aid, sid) == 'idle':
                    return
            if task is not None and not task.done() and not await bus.is_locked(MessageBusKeys.session_lock(sid)):
                await bus.publish(MessageBusKeys.session_interrupt_channel(), {'session_id': sid})
            else:
                await chat.interrupt(user_id, sid, aid)
            if task is not None and not task.done():
                # Let native cancellation finish before re-reading the roster,
                # so a leader cannot create another member after enumeration.
                _, pending = await asyncio.wait({task}, timeout=3)
                if pending:
                    raise HTTPException(409, '暂停仍在处理中，请稍后重试。')

        try:
            await stop(session_id, agent_id)
            leader = await storage.get_session(user_id, agent_id, session_id)
            if scope == 'team' and leader is not None and leader.team_id:
                team = await storage.get_team(user_id, leader.team_id)
                if team is not None and team.session_id == session_id:
                    for member in team.data.members:
                        sid = member.session_id
                        if member.owner_id != user_id or not sid or sid == session_id:
                            continue
                        child = await storage.get_session(user_id, member.agent_id, sid)
                        if child is not None and child.team_id == team.id:
                            await stop(sid, member.agent_id)
        except LookupError as exc:
            raise HTTPException(404, 'Session not found') from exc
        return {'session_id': session_id}

    return router
