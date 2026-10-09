"""Product runtime is a read-only projection of native run registration."""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from agentscope.app import create_app
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import AsyncSQLAlchemyStorage, SessionConfig, TeamData, TeamMember, TeamRecord
from agentscope.message import Msg, ToolCallBlock, ToolCallState
from fastapi.testclient import TestClient

from traceglass.agents import AGENT_ID, analyst_record
from traceglass.api.runtime import create_runtime_router
from traceglass.api.workspaces import create_workspace_router
from traceglass.workspaces import ProductLocalWorkspaceManager, ProductWorkspaceService


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch):
    storage = AsyncSQLAlchemyStorage(url=f"sqlite+aiosqlite:///{tmp_path / 'native.db'}")
    manager = ProductLocalWorkspaceManager(basedir=str(tmp_path / 'files'))
    service = ProductWorkspaceService(tmp_path, storage, manager, AGENT_ID)
    app = create_app(storage=storage, message_bus=InMemoryMessageBus(), workspace_manager=manager)
    app.include_router(create_workspace_router(service))
    app.include_router(create_runtime_router(service))

    def no_workspace_start(*args, **kwargs):
        raise AssertionError('Runtime projection must not initialize a workspace')

    monkeypatch.setattr(manager, 'get_workspace', no_workspace_start)
    with TestClient(app) as client:
        client.portal.call(storage.upsert_agent, 'owner', analyst_record('owner', 4))
        client.headers['X-User-ID'] = 'owner'
        workspace = client.post('/product/workspaces', json={'name': '运行状态'}).json()
        yield client, app, service, manager, workspace['workspace_id']


def _session(client, workspace_id):
    response = client.post(f'/product/workspaces/{workspace_id}/sessions', json={'name': 'fixture'})
    assert response.status_code == 201, response.text
    return response.json()['session_id']


async def _register(app, session_id, locked=False):
    """Hold a real native registry task at setup or at the running lock."""
    started, release = asyncio.Event(), asyncio.Event()

    async def hold():
        if locked:
            async with app.state.message_bus.acquire_lock(MessageBusKeys.session_lock(session_id)):
                started.set()
                await release.wait()
        else:
            started.set()
            await release.wait()

    task = app.state.chat_run_registry.spawn(hold(), session_id=session_id)
    await started.wait()
    return task, release


async def _finish(task, release=None):
    if release is not None:
        release.set()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1)


@pytest.mark.parametrize('phase', ['preparing', 'running', 'idle'])
def test_permission_change_uses_native_state_and_rejects_active_runs(runtime, phase):
    client, app, service, _, wid = runtime
    sid = _session(client, wid)
    task = None
    if phase != 'idle':
        task, release = client.portal.call(_register, app, sid, phase == 'running')
    try:
        response = client.patch(f'/product/sessions/{sid}/permission', params={'agent_id': AGENT_ID},
                                json={'permission_mode': 'bypass'})
        assert response.status_code == (200 if phase == 'idle' else 409), response.text
        saved = client.portal.call(service.storage.get_session, 'owner', AGENT_ID, sid)
        assert saved.state.permission_context.mode.value == ('bypass' if phase == 'idle' else 'default')
        if phase == 'idle':
            assert response.json()['state']['permission_context']['mode'] == 'bypass'
            response = client.patch(f'/product/sessions/{sid}/permission', params={'agent_id': AGENT_ID},
                                    json={'permission_mode': 'default'})
            assert response.status_code == 200
            assert response.json()['state']['permission_context']['mode'] == 'default'
    finally:
        if task is not None:
            client.portal.call(_finish, task, release)


def test_permission_change_rejects_foreign_sessions_and_invalid_modes(runtime):
    client, _, service, _, wid = runtime
    sid = _session(client, wid)
    url = f'/product/sessions/{sid}/permission'
    assert client.patch(url, params={'agent_id': AGENT_ID}, json={'permission_mode': 'bypass'},
                        headers={'X-User-ID': 'another'}).status_code == 404
    assert client.patch(url, params={'agent_id': AGENT_ID}, json={'permission_mode': 'dont_ask'}).status_code == 422
    saved = client.portal.call(service.storage.get_session, 'owner', AGENT_ID, sid)
    assert saved.state.permission_context.mode.value == 'default'


def test_runtime_projects_only_active_main_sessions_in_the_owned_workspace(runtime, monkeypatch):
    client, app, service, manager, wid = runtime
    preparing, running, idle = [_session(client, wid) for _ in range(3)]
    second_wid = client.post('/product/workspaces', json={'name': '另一个工作区'}).json()['workspace_id']
    other = _session(client, second_wid)
    child_agent = analyst_record('owner', 4).model_copy(update={'id': 'fixture-child', 'source': 'team'})
    client.portal.call(service.storage.upsert_agent, 'owner', child_agent)
    child = client.portal.call(service.storage.upsert_session, 'owner', child_agent.id, SessionConfig(workspace_id=wid))
    prepare_task, prepare_release = client.portal.call(_register, app, preparing)
    client.portal.call(_register, app, running, True)
    client.portal.call(_register, app, other)
    client.portal.call(_register, app, child.id)
    integrations = [{'name': 'idapro', 'optional': True, 'status': 'unavailable',
                     'message': 'IDA MCP 暂不可用', 'checked_at': None}]
    checked = []

    def integration_status(workspace_id):
        checked.append(workspace_id)
        return integrations

    monkeypatch.setattr(manager, 'integration_status', integration_status, raising=False)
    # The native status only knows the run lock and misses registered setup.
    native = client.get(f'/sessions/{preparing}/status', params={'agent_id': AGENT_ID}).json()
    assert native['status'] == 'idle'
    response = client.get('/product/runtime', params={'workspace_id': wid})
    assert response.status_code == 200, response.text
    assert response.json() == {'sessions': {preparing: 'preparing', running: 'running'},
                               'members': {}, 'integrations': integrations}
    assert checked == [wid]
    assert idle not in response.json()['sessions']
    client.portal.call(_finish, prepare_task, prepare_release)
    assert client.get('/product/runtime', params={'workspace_id': wid}).json()['sessions'] == {running: 'running'}


def test_runtime_rejects_other_owners_and_unknown_workspaces_before_reading_integrations(runtime, monkeypatch):
    client, _, _, manager, wid = runtime

    def forbidden_status(workspace_id):
        raise AssertionError('Do not expose integration status before ownership is checked')

    monkeypatch.setattr(manager, 'integration_status', forbidden_status, raising=False)
    assert client.get('/product/runtime', params={'workspace_id': 'missing'}).status_code == 404
    assert client.get('/product/runtime', params={'workspace_id': wid},
                      headers={'X-User-ID': 'another'}).status_code == 404
    assert client.get('/product/runtime').status_code == 422


@pytest.mark.parametrize('phase', ['preparing', 'running', 'idle'])
def test_interrupt_uses_native_cancellation_for_setup_and_native_service_otherwise(runtime, monkeypatch, phase):
    client, app, _, _, wid = runtime
    sid = _session(client, wid)
    task = None
    if phase != 'idle':
        task, _ = client.portal.call(_register, app, sid, phase == 'running')
    original = app.state.chat_service.interrupt
    calls = []

    async def interrupt(user_id, session_id, agent_id):
        calls.append((user_id, session_id, agent_id))
        if phase != 'idle':
            await original(user_id, session_id, agent_id)

    monkeypatch.setattr(app.state.chat_service, 'interrupt', interrupt)
    response = client.post(f'/product/sessions/{sid}/interrupt', params={'agent_id': AGENT_ID})
    assert response.status_code == 202, response.text
    assert response.json() == {'session_id': sid}
    assert calls == ([] if phase == 'preparing' else [('owner', sid, AGENT_ID)])
    if task is not None:
        client.portal.call(_finish, task)
        assert task.cancelled()


def test_interrupt_rejects_inaccessible_agents_and_sessions_without_cancelling(runtime):
    client, app, _, _, wid = runtime
    sid = _session(client, wid)
    task, _ = client.portal.call(_register, app, sid)
    for target, agent, user in [(sid, AGENT_ID, 'another'), (sid, 'missing-agent', 'owner'),
                                ('missing-session', AGENT_ID, 'owner')]:
        response = client.post(f'/product/sessions/{target}/interrupt', params={'agent_id': agent},
                               headers={'X-User-ID': user})
        assert response.status_code == 404, response.text
        assert not task.done()
    response = client.post(f'/product/sessions/{sid}/interrupt', params={'agent_id': AGENT_ID})
    assert response.status_code == 202, response.text
    client.portal.call(_finish, task)
    assert task.cancelled()


def test_real_native_chat_setup_is_visible_and_can_be_interrupted(runtime, monkeypatch):
    client, app, _, manager, wid = runtime
    sid = _session(client, wid)
    started = asyncio.Event()

    async def prepare_workspace(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, 'get_workspace', prepare_workspace)
    monkeypatch.setattr(manager, 'integration_status', lambda workspace_id: [], raising=False)
    response = client.post('/chat/', json={
        'agent_id': AGENT_ID, 'session_id': sid,
        'input': {'name': 'user', 'role': 'user', 'content': [{'type': 'text', 'text': 'fixture'}]},
    })
    assert response.status_code == 200, response.text
    client.portal.call(asyncio.wait_for, started.wait(), 1)
    task = client.portal.call(lambda: (app.state.chat_run_registry.get(sid),))[0]
    assert client.get('/product/runtime', params={'workspace_id': wid}).json() == {
        'sessions': {sid: 'preparing'}, 'members': {}, 'integrations': [],
    }
    response = client.post(f'/product/sessions/{sid}/interrupt', params={'agent_id': AGENT_ID})
    assert response.status_code == 202, response.text
    client.portal.call(_finish, task)
    assert task.cancelled()
    assert client.get('/product/runtime', params={'workspace_id': wid}).json()['sessions'] == {}


async def _create_team(service, owner, leader, workspace_id, states):
    team = TeamRecord(user_id=owner, session_id=leader, data=TeamData(name='状态验证团队'))
    await service.storage.upsert_team(owner, team)
    await service.storage.set_session_team_id(owner, leader, team.id)
    workers = {}
    for name, tool_state in states.items():
        agent = analyst_record(owner, 4).model_copy(update={'id': uuid4().hex, 'source': 'team'})
        agent.data.name = name
        await service.storage.upsert_agent(owner, agent)
        worker = await service.storage.upsert_session(owner, agent.id, SessionConfig(workspace_id=workspace_id))
        if tool_state is not None:
            worker.state.context = [Msg(name=name, role='assistant', content=[
                ToolCallBlock(id=f'call-{name}', name='save_analysis_artifact', input='{}', state=tool_state),
            ])]
            await service.storage.update_session_state(owner, agent.id, worker.id, worker.state)
        await service.storage.set_session_team_id(owner, worker.id, team.id)
        team.data.members.append(TeamMember(owner_id=owner, agent_id=agent.id, session_id=worker.id, role='created'))
        workers[name] = worker
    await service.storage.upsert_team(owner, team)
    return team, workers


def test_runtime_reports_child_permission_and_native_states_even_when_leader_is_idle(runtime):
    client, app, service, _, wid = runtime
    leader = _session(client, wid)
    _, workers = client.portal.call(_create_team, service, 'owner', leader, wid, {
        'permission': ToolCallState.ASKING, 'external': ToolCallState.SUBMITTED,
        'preparing': None, 'running': None, 'idle': None,
    })
    client.portal.call(_register, app, workers['preparing'].id)
    client.portal.call(_register, app, workers['running'].id, True)
    response = client.get('/product/runtime', params={'workspace_id': wid})
    assert response.status_code == 200, response.text
    assert response.json()['sessions'] == {}
    assert response.json()['members'] == {
        workers['permission'].id: 'awaiting_permission', workers['external'].id: 'awaiting_external_result',
        workers['preparing'].id: 'preparing', workers['running'].id: 'running', workers['idle'].id: 'idle',
    }
    for name in ('permission', 'external', 'running', 'idle'):
        worker = workers[name]
        native = client.get(f'/sessions/{worker.id}/status', params={'agent_id': worker.agent_id})
        assert response.json()['members'][worker.id] == native.json()['status']


def test_pause_team_cancels_running_and_preparing_children_without_touching_other_teams(runtime):
    client, app, service, _, wid = runtime
    leader = _session(client, wid)
    _, workers = client.portal.call(_create_team, service, 'owner', leader, wid, {'running': None, 'preparing': None})
    other = _session(client, wid)
    other_task, other_release = client.portal.call(_register, app, other, True)
    tasks = [client.portal.call(_register, app, leader, True)[0]]
    tasks += [client.portal.call(_register, app, w.id, name == 'running')[0] for name, w in workers.items()]
    response = client.post(f'/product/sessions/{leader}/interrupt', params={'agent_id': AGENT_ID, 'scope': 'team'})
    assert response.status_code == 202, response.text
    for task in tasks:
        client.portal.call(_finish, task)
        assert task.cancelled()
    assert not other_task.done()
    client.portal.call(_finish, other_task, other_release)


def test_runtime_member_projection_requires_owned_team_and_session_back_references(runtime, monkeypatch):
    client, app, service, _, wid = runtime
    leader = _session(client, wid)
    team, workers = client.portal.call(_create_team, service, 'owner', leader, wid, {'visible': None})
    other_wid = client.post('/product/workspaces', json={'name': '另一个团队'}).json()['workspace_id']
    other_leader = _session(client, other_wid)
    _, outside = client.portal.call(_create_team, service, 'owner', other_leader, other_wid, {'unrelated': None})
    foreign_team, foreign = client.portal.call(_create_team, service, 'another', 'foreign-leader', 'foreign-workspace', {'private': None})
    private = foreign['private']
    client.portal.call(_register, app, private.id, True)
    # Stale/corrupt roster references must not expose another user's running
    # lock, nor a same-owner session that belongs to a different native team.
    team.data.members.extend([
        foreign_team.data.members[0],
        foreign_team.data.members[0].model_copy(update={'owner_id': 'owner'}),
        TeamMember(owner_id='owner', agent_id=outside['unrelated'].agent_id,
                   session_id=outside['unrelated'].id, role='created'),
        TeamMember(owner_id='owner', agent_id=AGENT_ID, session_id=leader, role='created'),
        TeamMember(owner_id='owner', agent_id='missing-agent', session_id='missing-session', role='created'),
    ])
    client.portal.call(service.storage.upsert_team, 'owner', team)
    original = app.state.session_service.get_session_status
    queried = []

    async def status(user_id, agent_id, session_id):
        queried.append((user_id, session_id))
        return await original(user_id, agent_id, session_id)

    monkeypatch.setattr(app.state.session_service, 'get_session_status', status)
    response = client.get('/product/runtime', params={'workspace_id': wid})
    assert response.status_code == 200, response.text
    assert response.json()['members'] == {workers['visible'].id: 'idle'}
    assert queried == [('owner', workers['visible'].id)]
    assert client.get('/product/runtime', params={'workspace_id': wid},
                      headers={'X-User-ID': 'another'}).status_code == 404
