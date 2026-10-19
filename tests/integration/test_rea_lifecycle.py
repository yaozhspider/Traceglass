import asyncio
import json
from pathlib import Path
import sys

import pytest
from agentscope.mcp import MCPClient, StdioMCPConfig

from traceglass.integrations.rea import ReaSessions


def client_factory(monkeypatch):
    import traceglass.integrations.rea as integration
    fixture = Path(__file__).parents[1] / 'fixtures/rea_state_mcp.py'
    created = []
    def factory(*args):
        created.append(args)
        return MCPClient(name='rea', is_stateful=True,
            mcp_config=StdioMCPConfig(command=sys.executable, args=[str(fixture)]))
    monkeypatch.setattr(integration, 'rea_client', factory)
    return created


def test_lazy_metadata_does_not_launch_and_first_parallel_calls_share_connection(tmp_path, monkeypatch):
    created = client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path, max_connections=2)
        try:
            await pool.refresh_schema(tmp_path)
            initial = len(created)
            tools = pool.lazy_tools(tmp_path, 'alice', 'child', 'w')
            assert len(created) == initial
            assert pool.status('w')[0]['status'] != 'connected'
            probe = next(t for t in tools if t.name.endswith('__state_probe'))
            responses = await asyncio.gather(probe(), probe())
            assert {r.content[0].text for r in responses} == {'1', '2'}
            assert len(created) == initial + 1
            assert pool.status('w')[0]['active_connections'] == 1
        finally:
            await pool.close()
    asyncio.run(run())


def test_release_reconnects_with_new_identity_and_does_not_reuse_live_state(tmp_path, monkeypatch):
    client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path)
        try:
            await pool.refresh_schema(tmp_path)
            tools = {t.name: t for t in pool.lazy_tools(tmp_path, 'alice', 's', 'w')}
            await tools['mcp__rea__state_probe']()
            before = pool.connection_id(tmp_path, 'alice', 's')
            assert await pool.release(tmp_path, 'alice', 's') is True
            stale = await tools['mcp__rea__state_probe']()
            assert stale.state == 'error' and 'reopen' in stale.content[0].text
            opened = await tools['mcp__rea__open_binary'](path='/workspace/fixture')
            assert opened.state != 'error' and 'opened' in opened.content[0].text
            assert pool.connection_id(tmp_path, 'alice', 's') != before
            assert (await tools['mcp__rea__state_probe']()).content[0].text == '1'
        finally:
            await pool.close()
    asyncio.run(run())


def test_connection_quota_releases_after_explicit_close(tmp_path, monkeypatch):
    client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path, max_connections=1, quota_wait_seconds=0.05)
        try:
            await pool.refresh_schema(tmp_path)
            a = {t.name: t for t in pool.lazy_tools(tmp_path, 'alice', 'a', 'w')}
            b = {t.name: t for t in pool.lazy_tools(tmp_path, 'alice', 'b', 'w')}
            assert (await a['mcp__rea__state_probe']()).content[0].text == '1'
            assert (await b['mcp__rea__state_probe']()).state == 'error'
            await pool.release(tmp_path, 'alice', 'a')
            assert (await b['mcp__rea__state_probe']()).content[0].text == '1'
        finally:
            await pool.close()
    asyncio.run(run())


def test_schema_cache_with_wrong_image_or_hash_is_not_registered(tmp_path, monkeypatch):
    client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path)
        try:
            await pool.refresh_schema(tmp_path)
            cache = tmp_path / 'TOOLS.json'
            data = json.loads(cache.read_text())
            data['tools'][0]['inputSchema'] = {'type': 'object', 'properties': {'made_up': {'type': 'string'}}}
            cache.write_text(json.dumps(data))
            new = ReaSessions(tmp_path)
            assert new.lazy_tools(tmp_path, 'alice', 'a', 'w') == []
        finally:
            await pool.close()
    asyncio.run(run())


def test_active_call_prevents_idle_release_and_is_reaped_only_after_completion(tmp_path, monkeypatch):
    client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path, idle_seconds=0.03)
        try:
            await pool.refresh_schema(tmp_path)
            tools = {t.name: t for t in pool.lazy_tools(tmp_path, 'alice', 's', 'w')}
            pending = asyncio.create_task(tools['mcp__rea__slow_probe']())
            async with asyncio.timeout(3):
                while pool.status('w')[0]['active_calls'] == 0:
                    await asyncio.sleep(0.005)
            await asyncio.sleep(0.05)
            assert await pool.release(tmp_path, 'alice', 's') is False
            await pool.reap_idle()
            assert (await pending).content[0].text == 'finished'
            async with asyncio.timeout(3):
                while pool.status('w')[0]['active_connections']:
                    await asyncio.sleep(0.01)
            assert pool.status('w')[0]['status'] == 'not_checked'
        finally:
            await pool.close()
    asyncio.run(run())


def test_cancelled_readiness_waiter_does_not_cancel_other_waiters(tmp_path, monkeypatch):
    created = client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path)
        try:
            first = asyncio.create_task(pool.tools(tmp_path, 'alice', 's', 'w'))
            await asyncio.sleep(0)
            second = asyncio.create_task(pool.tools(tmp_path, 'alice', 's', 'w'))
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            tools = await second
            assert tools and len(created) == 1
            assert (await tools[0]()).content[0].text == '1'
        finally:
            await pool.close()
    asyncio.run(run())


def test_release_during_connection_does_not_cancel_the_releasing_caller(tmp_path, monkeypatch):
    import traceglass.integrations.rea as integration
    class ConnectingClient:
        _stack = None
        is_connected = False
        async def connect(self):
            await asyncio.Event().wait()
    monkeypatch.setattr(integration, 'rea_client', lambda *args: ConnectingClient())
    async def run():
        pool = ReaSessions(tmp_path, max_connections=1)
        try:
            pending = asyncio.create_task(pool.tools(tmp_path, 'alice', 's', 'w'))
            async with asyncio.timeout(2):
                while not pool._connections or not next(iter(pool._connections.values())).client:
                    await asyncio.sleep(0)
            assert await pool.release(tmp_path, 'alice', 's') is True
            assert await pending == []
            assert not asyncio.current_task().cancelling()
            assert pool._slots._value == 1
        finally:
            await pool.close()
    asyncio.run(run())


def test_path_tools_can_reconnect_without_opening_unrelated_binary(tmp_path, monkeypatch):
    client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path)
        try:
            await pool.refresh_schema(tmp_path)
            tools = {t.name: t for t in pool.lazy_tools(tmp_path, 'alice', 's', 'w')}
            await tools['mcp__rea__state_probe']()
            assert await pool.release(tmp_path, 'alice', 's')
            response = await tools['mcp__rea__inspect_binary_layout'](path='/workspace/fixture')
            assert response.state != 'error' and '/workspace/fixture' in response.content[0].text
            assert (await tools['mcp__rea__state_probe']()).state == 'error'
        finally:
            await pool.close()
    asyncio.run(run())


def test_waiting_connection_queues_until_owner_releases_without_tool_replay(tmp_path, monkeypatch):
    client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path, max_connections=1, quota_wait_seconds=2)
        try:
            await pool.refresh_schema(tmp_path)
            a = {t.name: t for t in pool.lazy_tools(tmp_path, 'alice', 'a', 'w')}
            b = {t.name: t for t in pool.lazy_tools(tmp_path, 'alice', 'b', 'w')}
            await a['mcp__rea__state_probe']()
            pending = asyncio.create_task(b['mcp__rea__state_probe']())
            async with asyncio.timeout(1):
                while pool.status('w')[0]['status'] != 'queued':
                    await asyncio.sleep(0)
            assert not pending.done()
            await pool.release(tmp_path, 'alice', 'a')
            assert (await pending).content[0].text == '1'
        finally:
            await pool.close()
    asyncio.run(run())


def test_capacity_diagnostics_are_scoped_nonconnecting_and_refresh_after_release(tmp_path, monkeypatch):
    created = client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path, max_connections=1, quota_wait_seconds=.02)
        try:
            await pool.refresh_schema(tmp_path)
            a = {t.name: t for t in pool.lazy_tools(tmp_path, 'alice', 'private-owner', 'private-workspace')}
            b = {t.name: t for t in pool.lazy_tools(tmp_path, 'bob', 'waiting', 'w')}
            await a['mcp__rea__state_probe']()
            result = await b['mcp__rea__state_probe']()
            payload = json.loads(result.content[0].text)
            assert payload['status'] == 'capacity_limited'
            assert payload['capacity']['limit'] == 1
            assert payload['capacity']['occupied'] == 1
            assert payload['capacity']['caller_holds_connection'] is False
            assert 'private-owner' not in result.content[0].text
            assert 'private-workspace' not in result.content[0].text
            workflows = {t.name: t for t in await pool.workflow_tools(tmp_path, 'bob', 'waiting', 'w')}
            starts = len(created)
            status = json.loads((await workflows['GetReaStatus']()).content[0].text)
            assert status['occupied'] == 1 and status['available'] == 0
            assert len(created) == starts
            await pool.release(tmp_path, 'alice', 'private-owner')
            status = json.loads((await workflows['GetReaStatus']()).content[0].text)
            assert status['available'] == 1
            assert pool.status('w')[0]['status'] == 'not_checked'
        finally:
            await pool.close()
    asyncio.run(run())


def test_default_pool_serves_ten_independent_agents_and_queues_the_eleventh(tmp_path, monkeypatch):
    client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path, quota_wait_seconds=.02)
        try:
            for index in range(10):
                tools = await pool.tools(tmp_path, 'alice', str(index), 'w')
                assert tools
            assert await pool.tools(tmp_path, 'alice', 'eleventh', 'w') == []
            assert pool.status('w')[0]['active_connections'] == 10
            assert pool.capacity(tmp_path, 'alice', 'eleventh')['available'] == 0
        finally:
            await pool.close()
    asyncio.run(run())


def test_releasing_queued_owner_does_not_leak_capacity(tmp_path, monkeypatch):
    client_factory(monkeypatch)
    async def run():
        pool = ReaSessions(tmp_path, max_connections=1, quota_wait_seconds=2)
        try:
            await pool.tools(tmp_path, 'alice', 'owner', 'w')
            pending = asyncio.create_task(pool.tools(tmp_path, 'alice', 'waiting', 'w'))
            async with asyncio.timeout(1):
                while not pool.capacity(tmp_path, 'alice', 'waiting')['queued']:
                    await asyncio.sleep(0)
            assert await pool.release(tmp_path, 'alice', 'waiting')
            assert await pending == []
            await pool.release(tmp_path, 'alice', 'owner')
            assert await pool.tools(tmp_path, 'alice', 'next', 'w')
            status = pool.capacity(tmp_path, 'alice', 'next')
            assert status['occupied'] == 1 and status['queued'] == 0
        finally:
            await pool.close()
    asyncio.run(run())
