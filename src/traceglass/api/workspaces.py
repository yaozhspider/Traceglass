"""Product file/navigation endpoints; AgentScope retains all chat execution."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from agentscope.app.deps import get_current_user_id, get_resource_access_service
from agentscope.app.storage import ChatModelConfig
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.routing import APIRoute
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from traceglass.workspaces.files import stream_file
from traceglass.workspaces import files as workspace_files
from traceglass.workspaces.service import ProductWorkspaceService


class NewWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class NewWorkspaceSession(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="新的分析", min_length=1, max_length=200)
    sample_id: str | None = None
    chat_model_config: ChatModelConfig | None = None
    permission_mode: Literal['default', 'bypass'] = 'default'


class WorkspaceRoute(APIRoute):
    """Bound request bytes before multipart parsing, including chunked uploads."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            if request.method == "POST" and self.path.endswith("/samples"):
                maximum = workspace_files.MAX_UPLOAD_BYTES + 64 * 1024
                length = request.headers.get("content-length")
                if length is not None:
                    try:
                        size = int(length)
                    except ValueError as exc:
                        raise HTTPException(400, "Invalid Content-Length") from exc
                    # Allow multipart framing; the actual file is independently
                    # bounded while copying into its workspace destination.
                    if size > maximum:
                        raise HTTPException(413, "File exceeds the 100 MiB upload limit")
                received = 0
                async def bounded_receive():
                    nonlocal received
                    message = await request.receive()
                    received += len(message.get("body", b""))
                    if received > maximum:
                        # This exception also makes Starlette close already
                        # spooled multipart files before the route rejects it.
                        raise MultiPartException("Workspace upload body limit exceeded")
                    return message

                bounded = Request(request.scope, receive=bounded_receive)
                try:
                    return await original(bounded)
                except StarletteHTTPException as exc:
                    if exc.status_code == 400 and exc.detail == "Workspace upload body limit exceeded":
                        raise HTTPException(413, "File exceeds the 100 MiB upload limit") from exc
                    raise
            return await original(request)

        return handler


async def _checked(operation):
    try:
        return await operation
    except OverflowError as exc:
        raise HTTPException(413, "File exceeds the 100 MiB upload limit") from exc
    except LookupError as exc:
        raise HTTPException(404, "Workspace, sample or file not found") from exc
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError) as exc:
        raise HTTPException(404, "Workspace file not found or unavailable") from exc
    except ValueError as exc:
        raise HTTPException(400, "Invalid workspace path or filename") from exc
    except RuntimeError as exc:
        raise HTTPException(409, "File changed while reading; try again") from exc


def create_workspace_router(service: ProductWorkspaceService) -> APIRouter:
    router = APIRouter(prefix="/product/workspaces", tags=["product-workspaces"], route_class=WorkspaceRoute)

    @router.get("")
    async def list_workspaces(user_id: str = Depends(get_current_user_id)):
        return await _checked(service.list(user_id))

    @router.post("", status_code=201)
    async def create_workspace(body: NewWorkspace, user_id: str = Depends(get_current_user_id)):
        return await _checked(service.create(user_id, body.name, body.description))

    @router.get("/{workspace_id}")
    async def workspace_detail(workspace_id: str, user_id: str = Depends(get_current_user_id)):
        return await _checked(service.detail(user_id, workspace_id))

    @router.post("/{workspace_id}/sessions", status_code=201)
    async def create_session(workspace_id: str, body: NewWorkspaceSession,
                             user_id: str = Depends(get_current_user_id),
                             access=Depends(get_resource_access_service)):
        await access.resolve_agent(user_id, service.agent_id)
        if body.chat_model_config is not None:
            await access.resolve_credential(user_id, body.chat_model_config.credential_id)
        return await _checked(service.create_session(user_id, workspace_id, body.name, body.sample_id,
                                                     body.chat_model_config, body.permission_mode))

    @router.post("/{workspace_id}/samples", status_code=201)
    async def upload_sample(workspace_id: str, file: UploadFile = File(...),
                            user_id: str = Depends(get_current_user_id)):
        try:
            return await _checked(service.upload(user_id, workspace_id, file))
        finally:
            await file.close()

    @router.get("/{workspace_id}/files")
    async def list_files(workspace_id: str, path: str = Query(default=""),
                          user_id: str = Depends(get_current_user_id)):
        return await _checked(service.list_files(user_id, workspace_id, path))

    @router.get("/{workspace_id}/preview")
    async def preview(workspace_id: str, path: str = Query(...),
                       user_id: str = Depends(get_current_user_id)):
        return await _checked(service.preview(user_id, workspace_id, path))

    @router.get("/{workspace_id}/download")
    async def download(workspace_id: str, path: str = Query(...),
                        user_id: str = Depends(get_current_user_id)):
        stream, info = await _checked(service.download(user_id, workspace_id, path))
        return StreamingResponse(stream_file(stream), media_type="application/octet-stream", headers={
            "Content-Length": str(info.st_size),
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(PurePosixPath(path).name),
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        })

    return router
