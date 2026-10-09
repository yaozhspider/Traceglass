"""Archive reads require product workspace ownership; no engine starts on reads."""
import asyncio
from typing import Literal

from agentscope.app.deps import get_current_user_id
from fastapi import APIRouter, Depends, HTTPException, Query


def create_evidence_router(service, store):
    router = APIRouter(prefix='/product/workspaces', tags=['rea-evidence'])

    async def check(user_id, workspace_id):
        try:
            await service.detail(user_id, workspace_id)
        except LookupError as exc:
            raise HTTPException(404, 'Workspace not found') from exc

    @router.get('/{workspace_id}/rea-evidence')
    async def list_evidence(workspace_id: str, sample_sha256: str | None = None,
                            kind: Literal['evidence', 'unknown'] | None = None,
                            cursor: int | None = Query(None, ge=0), limit: int = Query(30, ge=1, le=100),
                            user_id: str = Depends(get_current_user_id)):
        await check(user_id, workspace_id)
        return await asyncio.to_thread(store.list, user_id, workspace_id,
            sample_sha256=sample_sha256, kind=kind, cursor=cursor, limit=limit)

    @router.get('/{workspace_id}/rea-evidence/{record_id}')
    async def read_evidence(workspace_id: str, record_id: str, user_id: str = Depends(get_current_user_id)):
        await check(user_id, workspace_id)
        result = await asyncio.to_thread(store.get, user_id, workspace_id, record_id)
        if result is None:
            raise HTTPException(404, 'Evidence not found')
        return result

    return router
