"""Exercise product delegation limits around the real native team tools."""
from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._tool import AgentCreate, AgentInvite, TeamCreate, TeamDelete, TeamSay
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import AgentData, AgentRecord, AsyncSQLAlchemyStorage, SessionConfig
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultState
from agentscope.state import AgentState, Task
from agentscope.tool import ToolResponse


@asynccontextmanager
async def _team(tmp_path: Path):
    from traceglass.delegation import DelegationPolicy

    storage = AsyncSQLAlchemyStorage(url=f"sqlite+aiosqlite:///{tmp_path / 'teams.sqlite3'}")
    await storage.__aenter__()
    bus = InMemoryMessageBus()
    workspaces = LocalWorkspaceManager(basedir=str(tmp_path / 'workspaces'))
    leader = AgentRecord(user_id='local', data=AgentData(
        name='leader', context_config=ContextConfig(), react_config=ReActConfig(),
    ))
    await storage.upsert_agent('local', leader)
    session = await storage.upsert_session('local', leader.id, SessionConfig(workspace_id='analysis'))
    agent = SimpleNamespace(state=AgentState())
    agent.state.tasks_context.tasks.append(Task(
        id='1', subject='核查样本', description='独立核查证据', metadata={},
    ))
    tool_args = dict(storage=storage, message_bus=bus, workspace_manager=workspaces,
                     user_id='local', session_id=session.id, agent_id=leader.id)
    policy = DelegationPolicy(storage)
    middleware = policy.for_session('local', leader.id, session.id)
    fixture = SimpleNamespace(storage=storage, bus=bus, workspaces=workspaces,
                              leader=leader, session=session, agent=agent,
                              tool_args=tool_args, middleware=middleware, policy=policy)
    try:
        result = await _call(fixture, TeamCreate(**tool_args), name='analysis', description='核查证据')
        assert result.state == ToolResultState.SUCCESS, result
        yield fixture
    finally:
        await bus.aclose()
        await storage.aclose()


async def _call(fixture, tool, **arguments) -> ToolResponse:
    call = ToolCallBlock(id='fixture-call', name=tool.name, input=json.dumps(arguments))

    async def execute(**kwargs):
        # Yield to competitors before the native read-modify-write operation.
        await asyncio.sleep(0)
        supplied = dict(arguments)
        if tool.is_state_injected:
            supplied['_agent_state'] = fixture.agent.state
        chunk = await tool(**supplied)
        yield ToolResponse().append_chunk(chunk)

    chunks = [chunk async for chunk in fixture.middleware.on_acting(
        fixture.agent, {'tool_call': call}, execute,
    )]
    return chunks[-1]


async def _create(fixture, number: int) -> ToolResponse:
    # Runtime assembly can create different middleware instances for one leader.
    fixture = copy.copy(fixture)
    fixture.middleware = fixture.policy.for_session('local', fixture.leader.id, fixture.session.id)
    return await _call(fixture, AgentCreate(**fixture.tool_args),
                       name=f'worker-{number}', description='核查样本证据', prompt='报告样本哈希')


async def _roster(fixture):
    session = await fixture.storage.get_session('local', fixture.leader.id, fixture.session.id)
    return await fixture.storage.get_team('local', session.team_id)


@pytest.mark.parametrize('concurrent', [False, True])
def test_ninth_native_worker_is_rejected_without_losing_first_eight(tmp_path: Path, concurrent: bool):
    """Missing serialization or a capacity check can overbook or lose members."""
    async def run():
        async with _team(tmp_path) as fixture:
            if concurrent:
                results = await asyncio.gather(*(_create(fixture, n) for n in range(9)))
            else:
                results = [await _create(fixture, n) for n in range(9)]
            assert [result.state for result in results].count(ToolResultState.SUCCESS) == 8
            assert [result.state for result in results].count(ToolResultState.ERROR) == 1
            team = await _roster(fixture)
            assert len(team.data.members) == 8
            assert len({member.agent_id for member in team.data.members}) == 8
            assert fixture.leader.id not in [member.agent_id for member in team.data.members]

    asyncio.run(run())


def test_native_invited_members_share_the_eight_member_budget(tmp_path: Path):
    """AgentInvite must not bypass the created-member limit."""
    async def invite(fixture, name):
        record = AgentRecord(user_id='local', data=AgentData(
            name=name, context_config=ContextConfig(), react_config=ReActConfig(),
        ))
        record.data.invite_config.invitable = True
        record.data.invite_config.invite_description = 'Independent verifier'
        await fixture.storage.upsert_agent('local', record)
        await fixture.storage.upsert_session('local', record.id, SessionConfig(workspace_id='invited'))
        tool = AgentInvite(**fixture.tool_args, invitable_pool=[record])
        target = tool.input_schema['properties']['target']['enum'][0]
        return await _call(fixture, tool, target=target, prompt='核查证据')

    async def run():
        async with _team(tmp_path) as fixture:
            for n in range(7):
                assert (await _create(fixture, n)).state == ToolResultState.SUCCESS
            assert (await invite(fixture, 'first-invite')).state == ToolResultState.SUCCESS
            assert (await invite(fixture, 'second-invite')).state == ToolResultState.ERROR
            assert (await _create(fixture, 9)).state == ToolResultState.ERROR
            team = await _roster(fixture)
            assert len(team.data.members) == 8
            assert [member.role for member in team.data.members].count('invited') == 1

    asyncio.run(run())


@pytest.mark.parametrize('completed', [False, True])
def test_creation_requires_an_unfinished_todo(tmp_path: Path, completed: bool):
    """An empty or already completed plan must not permit new delegation."""
    async def run():
        async with _team(tmp_path) as fixture:
            if completed:
                fixture.agent.state.tasks_context.tasks[0].state = 'completed'
            else:
                fixture.agent.state.tasks_context.tasks.clear()
            result = await _create(fixture, 1)
            assert result.state == ToolResultState.ERROR
            assert 'TodoWrite' in ''.join(block.text for block in result.content)
            assert not (await _roster(fixture)).data.members

    asyncio.run(run())


def test_persisted_legacy_roster_enforces_limit_after_policy_restart(tmp_path: Path):
    """Counting only in-memory reservations would forget existing workers."""
    from traceglass.delegation import DelegationPolicy

    async def run():
        async with _team(tmp_path) as fixture:
            for n in range(8):
                assert (await _create(fixture, n)).state == ToolResultState.SUCCESS
            team = await _roster(fixture)
            team.data.members = []  # AgentScope's prior persistent roster format.
            await fixture.storage.upsert_team('local', team)
            await fixture.storage.aclose()
            fixture.storage = AsyncSQLAlchemyStorage(url=f"sqlite+aiosqlite:///{tmp_path / 'teams.sqlite3'}")
            await fixture.storage.__aenter__()
            fixture.tool_args['storage'] = fixture.storage
            fixture.policy = DelegationPolicy(fixture.storage)
            fixture.middleware = fixture.policy.for_session('local', fixture.leader.id, fixture.session.id)
            try:
                assert (await _create(fixture, 9)).state == ToolResultState.ERROR
                assert len((await _roster(fixture)).data.members) == 8
            finally:
                await fixture.storage.aclose()

    asyncio.run(run())


def test_full_team_can_still_receive_work_through_native_team_say(tmp_path: Path):
    """The cap must gate new members without blocking reuse or communication."""
    async def run():
        async with _team(tmp_path) as fixture:
            for n in range(8):
                assert (await _create(fixture, n)).state == ToolResultState.SUCCESS
            fixture.agent.state.tasks_context.tasks.clear()
            result = await _call(fixture, TeamSay(**fixture.tool_args),
                                 to='worker-0', content='继续复核下一个证据')
            assert result.state == ToolResultState.SUCCESS
            assert len((await _roster(fixture)).data.members) == 8

    asyncio.run(run())


def test_model_cannot_delete_team_history_or_reset_member_budget(tmp_path: Path):
    """Native TeamDelete must not erase the expandable execution record."""
    async def run():
        async with _team(tmp_path) as fixture:
            for n in range(8):
                assert (await _create(fixture, n)).state == ToolResultState.SUCCESS
            team = await _roster(fixture)
            member = team.data.members[0]
            evidence = Msg(name='worker-0', role='assistant', content=[TextBlock(text='Verified sample evidence')])
            await fixture.storage.upsert_message('local', member.session_id, evidence)

            result = await _call(fixture, TeamDelete(**fixture.tool_args))

            assert result.state == ToolResultState.ERROR
            assert 'TeamSay' in ''.join(block.text for block in result.content)
            assert (await _roster(fixture)).id == team.id
            assert len((await _roster(fixture)).data.members) == 8
            for existing in team.data.members:
                assert await fixture.storage.get_session('local', existing.agent_id, existing.session_id) is not None
            history, _ = await fixture.storage.list_messages('local', member.session_id)
            assert [(msg.id, msg.get_text_content()) for msg in history] == [(evidence.id, 'Verified sample evidence')]
            assert (await _create(fixture, 9)).state == ToolResultState.ERROR

    asyncio.run(run())
