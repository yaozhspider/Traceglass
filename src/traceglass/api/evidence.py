"""Archive reads require product workspace ownership; no engine starts on reads."""
import asyncio
from typing import Literal

from agentscope.app.deps import get_current_user_id
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response


def create_evidence_router(service, store):
    router = APIRouter(prefix='/product/workspaces', tags=['rea-evidence'])

    async def check(user_id, workspace_id):
        try:
            await service.detail(user_id, workspace_id)
        except LookupError as exc:
            raise HTTPException(404, 'Workspace not found') from exc

    @router.get('/{workspace_id}/analysis-evidence')
    @router.get('/{workspace_id}/rea-evidence')
    async def list_evidence(workspace_id: str, sample_sha256: str | None = None,
                            kind: Literal['evidence', 'unknown', 'tool_call'] | None = None,
                            cursor: int | None = Query(None, ge=0), limit: int = Query(30, ge=1, le=100),
                            user_id: str = Depends(get_current_user_id)):
        await check(user_id, workspace_id)
        return await asyncio.to_thread(store.list, user_id, workspace_id,
            sample_sha256=sample_sha256, kind=kind, cursor=cursor, limit=limit)

    @router.get('/{workspace_id}/analysis-evidence/{record_id}')
    @router.get('/{workspace_id}/rea-evidence/{record_id}')
    async def read_evidence(workspace_id: str, record_id: str, user_id: str = Depends(get_current_user_id)):
        await check(user_id, workspace_id)
        result = await asyncio.to_thread(store.get, user_id, workspace_id, record_id)
        if result is None:
            raise HTTPException(404, 'Evidence not found')
        return result

    @router.get('/{workspace_id}/analysis-evidence/{record_id}/attachments/{attachment_id}')
    async def read_attachment(workspace_id: str, record_id: str, attachment_id: str,
                              user_id: str = Depends(get_current_user_id)):
        await check(user_id, workspace_id)
        result = await asyncio.to_thread(store.get_attachment, user_id, workspace_id, record_id, attachment_id)
        if result is None:
            raise HTTPException(404, 'Attachment not found or integrity check failed')
        data, metadata = result
        return Response(data, media_type='application/octet-stream', headers={
            'Content-Disposition': f'attachment; filename="{attachment_id}"',
            'X-Content-SHA256': metadata['sha256'], 'X-Content-Type-Options': 'nosniff'})

    return router
