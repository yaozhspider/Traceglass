"""Read-before-Edit must use the workspace filesystem across native pauses."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from agentscope.app.storage import SessionConfig
from agentscope.event import UserConfirmResultEvent
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse, OpenAIChatModel
from agentscope.tool._builtin._backend import LocalBackend

from traceglass.api.app import create_app
from traceglass.config import Settings


class RemoteFileBackend(LocalBackend):
    """Real file IO behind a remote path that does not exist on the host."""

    def __init__(self, remote: str, local: Path):
        super().__init__()
        self.remote, self.local = remote, str(local)

    def local_path(self, path):
        return self.local if path == self.remote else path

    async def file_exists(self, path):
        return await super().file_exists(self.local_path(path))

    async def is_dir(self, path):
        return await super().is_dir(self.local_path(path))

    async def read_file(self, path):
        return await super().read_file(self.local_path(path))

    async def write_file(self, path, data):
        return await super().write_file(self.local_path(path), data)

    async def stat(self, path):
        return await super().stat(self.local_path(path))


@pytest.mark.parametrize('after_read', ['unchanged', 'changed', 'deleted', 'denied'])
def test_native_remote_read_edit_after_permission_pause(tmp_path, monkeypatch, after_read):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    remote = '/traceglass-remote-test-only/evidence.md'
    assert not Path(remote).exists()
    local = tmp_path / 'evidence.md'
    local.write_bytes('标题\r\nold value\r\n'.encode())
    # Preserve sub-second mtime precision rather than backend stat -c %Y.
    os.utime(local, (1700000000.125, 1700000000.125))

    async def provider(self, model_name, messages, tools=None, **kwargs):
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        if not results:
            name, arguments = 'Read', {'file_path': remote}
        elif len(results) == 1:
            name, arguments = 'Edit', {'file_path': remote, 'old_string': 'old value', 'new_string': 'new value'}
        else:
            return ChatResponse(content=[TextBlock(text='done')], is_last=True)
        return ChatResponse(content=[ToolCallBlock(id=f'file-{len(results)}', name=name,
                            input=json.dumps(arguments))], is_last=True)

    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)

    async def run():
        app = create_app(settings=Settings(data_dir=tmp_path / 'data', model_name='fixture', api_key='fixture'))
        async with app.router.lifespan_context(app):
            config = SessionConfig(workspace_id='remote-file-test', chat_model_config={'type': 'openai_chat', 'model': 'fixture', 'credential_id': 'traceglass-model', 'parameters': {}})
            session = await app.state.storage.upsert_session('local', 'traceglass-analyst', config)
            workspace = await app.state.workspace_manager.get_workspace('local', 'traceglass-analyst', session.id, config.workspace_id)
            workspace._backend = RemoteFileBackend(remote, local)
            await app.state.chat_service.run('local', session.id, 'traceglass-analyst',
                Msg(name='user', role='user', content=[TextBlock(text='Read, then edit the report.')]))
            messages, _ = await app.state.storage.list_messages('local', session.id, limit=10)
            reply = next(m for m in messages if m.role == 'assistant')
            read = next(b for b in reply.content if isinstance(b, ToolResultBlock) and b.name == 'Read')
            assert read.state == 'success'
            pending = next(b for b in reply.content if isinstance(b, ToolCallBlock) and b.name == 'Edit')
            assert pending.state == 'asking'
            assert local.read_bytes() == '标题\r\nold value\r\n'.encode()
            # Loading from SQL proves the context survives serialization; the
            # next native run reconstructs the agent from this persisted state.
            saved = await app.state.storage.get_session('local', 'traceglass-analyst', session.id)
            if after_read == 'unchanged':
                assert len(saved.state.tool_context.read_file_cache) == 1
                assert saved.state.tool_context.read_file_cache[0].updated_at == 1700000000.125
            if after_read == 'changed':
                local.write_text('标题\nexternal value\n')
                os.utime(local, (1700000000.375, 1700000000.375))
            elif after_read == 'deleted':
                local.unlink()
            await app.state.chat_service.run('local', session.id, 'traceglass-analyst',
                UserConfirmResultEvent(reply_id=reply.id, confirm_results=[
                    {'tool_call': pending, 'confirmed': after_read != 'denied'},
                ]))
            messages, _ = await app.state.storage.list_messages('local', session.id, limit=10)
            edit = next(b for m in messages for b in m.content if isinstance(b, ToolResultBlock) and b.name == 'Edit')
            if after_read == 'unchanged':
                assert edit.state == 'success', str(edit.output)
                assert local.read_bytes() == '标题\nnew value\n'.encode()
            elif after_read == 'changed':
                assert edit.state == 'error'
                assert 'first read' in str(edit.output)
                assert local.read_text() == '标题\nexternal value\n'
            elif after_read == 'deleted':
                assert edit.state == 'error'
                assert not local.exists()
            else:
                assert edit.state != 'success'
                assert local.read_bytes() == '标题\r\nold value\r\n'.encode()

    asyncio.run(run())
