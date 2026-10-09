"""Native inbox results must not strand during the final model call."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from agentscope.agent import ContextConfig, ReActConfig
import agentscope.app._service._chat as native_chat
from agentscope.app._bus_ops import enqueue_run_trigger
from agentscope.app._tool import TeamSay
from agentscope.app.message_bus import MessageBusKeys
from agentscope.app.middleware import ToolOffloadMiddleware
from agentscope.app.storage import AgentData, AgentRecord, SessionConfig, TeamData, TeamMember, TeamRecord
from agentscope.message import HintBlock, TextBlock, ToolCallBlock, ToolResultBlock, ToolResultState
from agentscope.model import ChatResponse, OpenAIChatModel
from agentscope.tool import FunctionTool

import traceglass.tools
from traceglass.api.app import create_app
from traceglass.config import Settings


async def _wait_for(predicate, timeout=2):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), timeout)


def _reply(text):
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


@asynccontextmanager
async def _app(tmp_path, monkeypatch, provider, tools=()):
    # The provider is scripted in-process; host proxy settings must not affect
    # OpenAI/httpx client construction before that model boundary is reached.
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)
    monkeypatch.setattr(traceglass.tools, 'build_analysis_tools', lambda *args, **kwargs: list(tools))
    original = ToolOffloadMiddleware.__init__

    def init(self, *args, **kwargs):
        kwargs['timeout_secs'] = 0.02
        original(self, *args, **kwargs)

    monkeypatch.setattr(ToolOffloadMiddleware, '__init__', init)
    app = create_app(settings=Settings(data_dir=tmp_path, model_name='fixture', api_key='fixture'))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'X-User-ID': 'local'}) as client:
            cfg = (await client.get('/product/config')).json()
            created = await client.post('/sessions/', json={
                'agent_id': cfg['agent_id'], 'name': 'wakeup fixture', 'chat_model_config': cfg['model_config'],
            })
            sid = created.json()['session_id']
            yield app, client, cfg['agent_id'], sid


async def _start(client, agent_id, sid):
    response = await client.post('/chat/', json={
        'agent_id': agent_id, 'session_id': sid,
        'input': {'name': 'user', 'role': 'user', 'content': [{'type': 'text', 'text': '核查证据'}]},
    })
    assert response.status_code == 200, response.text


async def _messages(client, agent_id, sid):
    response = await client.get(f'/sessions/{sid}/messages', params={'agent_id': agent_id})
    return response.json()['messages']


async def _worker(app, agent_id, sid):
    storage = app.state.storage
    record = AgentRecord(user_id='local', source='team', data=AgentData(
        name='reporter', context_config=ContextConfig(), react_config=ReActConfig(),
    ))
    await storage.upsert_agent('local', record)
    session = await storage.upsert_session('local', record.id, SessionConfig(workspace_id='fixture'))
    team = TeamRecord(user_id='local', session_id=sid, data=TeamData(
        name='wakeup fixture', member_ids=[record.id], members=[TeamMember(
            owner_id='local', agent_id=record.id, session_id=session.id, role='created',
        )],
    ))
    await storage.upsert_team('local', team)
    await storage.set_session_team_id('local', sid, team.id)
    await storage.set_session_team_id('local', session.id, team.id)
    return TeamSay(storage=storage, message_bus=app.state.message_bus,
                   workspace_manager=app.state.workspace_manager, user_id='local',
                   agent_id=record.id, session_id=session.id, role='worker')


@pytest.mark.parametrize('source,count', [('background', 1), ('team', 1), ('background', 2), ('team', 2)])
def test_results_during_final_model_call_wake_once_and_keep_native_history(tmp_path: Path, monkeypatch, source, count):
    async def run():
        model_waiting, release_model, release_tool = asyncio.Event(), asyncio.Event(), asyncio.Event()
        consumed = asyncio.Event()
        calls = []

        async def delayed_probe(number: int) -> str:
            """Return harmless evidence after the test releases it."""
            await release_tool.wait()
            return f'EVIDENCE_{number}'

        async def provider(self, model_name, messages, tools=None, **kwargs):
            blocks = [block for message in messages for block in message.content]
            hints = [str(block.hint) for block in blocks if isinstance(block, HintBlock)]
            calls.append(hints)
            if any('EVIDENCE_' in hint for hint in hints):
                assert all(any(f'EVIDENCE_{n}' in hint for hint in hints) for n in range(count))
                consumed.set()
                return _reply('ALL_EVIDENCE_RECEIVED')
            if source == 'background' and not any(isinstance(b, ToolResultBlock) for b in blocks):
                return ChatResponse(content=[ToolCallBlock(id=f'probe-{n}', name='delayed_probe',
                                     input='{"number": ' + str(n) + '}') for n in range(count)], is_last=True)
            model_waiting.set()
            await release_model.wait()
            return _reply('WAITING_FOR_EVIDENCE')

        async with _app(tmp_path, monkeypatch, provider, [FunctionTool(func=delayed_probe, is_read_only=True,
                                                                      is_concurrency_safe=True)]) as (app, client, agent_id, sid):
            worker = await _worker(app, agent_id, sid) if source == 'team' else None
            await _start(client, agent_id, sid)
            await asyncio.wait_for(model_waiting.wait(), 2)
            original_run = app.state.chat_run_registry.get(sid)
            if worker:
                for n in range(count):
                    result = await worker(content=f'EVIDENCE_{n}')
                    assert result.state != ToolResultState.ERROR
            else:
                release_tool.set()
            bus = app.state.message_bus
            # The native dispatcher has consumed the wake while the model is
            # still running; inbox payloads remain available for its next step.
            await _wait_for(lambda: len(bus._queues.get(MessageBusKeys.inbox(sid), [])) == count
                            and not bus._queues.get(MessageBusKeys.wakeup_queue()))
            release_model.set()
            await asyncio.wait_for(original_run, 2)
            try:
                await asyncio.wait_for(consumed.wait(), 1)
            except TimeoutError:
                pytest.fail('Native result stranded in inbox after the last model call')
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            history = await _messages(client, agent_id, sid)
            final = [m for m in history if any(b.get('text') == 'ALL_EVIDENCE_RECEIVED' for b in m['content'])]
            assert len(final) == 1
            assert len(calls) == (3 if source == 'background' else 2)
            assert not bus._queues.get(MessageBusKeys.inbox(sid))
            assert all(any(f'EVIDENCE_{n}' in str(b.get('hint', '')) for m in history for b in m['content'])
                       for n in range(count))

    asyncio.run(run())


def test_setup_failure_with_pending_inbox_does_not_repeat_an_older_success(tmp_path: Path, monkeypatch):
    async def run():
        setup_waiting, release_setup = asyncio.Event(), asyncio.Event()
        calls, setups = [], []

        async def provider(self, model_name, messages, tools=None, **kwargs):
            calls.append(1)
            return _reply('PREVIOUS_SUCCESS')

        async def fail_model_setup(*args, **kwargs):
            setups.append(1)
            setup_waiting.set()
            await release_setup.wait()
            raise ValueError('fixture credential no longer available')

        async with _app(tmp_path, monkeypatch, provider) as (app, client, agent_id, sid):
            await _start(client, agent_id, sid)
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            monkeypatch.setattr(native_chat, 'get_model', fail_model_setup)
            await _start(client, agent_id, sid)
            await asyncio.wait_for(setup_waiting.wait(), 2)
            bus = app.state.message_bus
            await bus.queue_push(MessageBusKeys.inbox(sid), HintBlock(
                hint='EVIDENCE_DURING_FAILED_SETUP',
            ).model_dump(mode='json'))
            release_setup.set()
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            await asyncio.sleep(0.05)
            history = await _messages(client, agent_id, sid)
            assert calls == [1]
            assert setups == [1]
            assert [m['finished_reason'] for m in history if m['role'] == 'assistant'] == ['completed', 'error']
            assert bus.has_pending_inbox(sid)

    asyncio.run(run())


def test_idle_completion_uses_native_wakeup_without_duplicate_model_calls(tmp_path: Path, monkeypatch):
    async def run():
        release_tool, consumed = asyncio.Event(), asyncio.Event()
        calls = []

        async def delayed_probe() -> str:
            """Wait for a harmless fixture result."""
            await release_tool.wait()
            return 'IDLE_EVIDENCE'

        async def provider(self, model_name, messages, tools=None, **kwargs):
            blocks = [b for m in messages for b in m.content]
            calls.append(1)
            if any(isinstance(b, HintBlock) and 'IDLE_EVIDENCE' in str(b.hint) for b in blocks):
                consumed.set()
                return _reply('IDLE_RESULT_RECEIVED')
            if any(isinstance(b, ToolResultBlock) for b in blocks):
                return _reply('WAITING')
            return ChatResponse(content=[ToolCallBlock(id='idle-probe', name='delayed_probe', input='{}')], is_last=True)

        async with _app(tmp_path, monkeypatch, provider, [FunctionTool(func=delayed_probe, is_read_only=True)]) as (app, client, agent_id, sid):
            await _start(client, agent_id, sid)
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            release_tool.set()
            await asyncio.wait_for(consumed.wait(), 2)
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            assert len(calls) == 3
            assert not app.state.message_bus._queues.get(MessageBusKeys.inbox(sid))

    asyncio.run(run())


def test_completed_run_without_inbox_does_not_call_model_again(tmp_path: Path, monkeypatch):
    async def run():
        calls = []

        async def provider(self, model_name, messages, tools=None, **kwargs):
            calls.append(1)
            return _reply('DONE')

        async with _app(tmp_path, monkeypatch, provider) as (app, client, agent_id, sid):
            await _start(client, agent_id, sid)
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            # Even a duplicated native wake with an empty inbox has no work.
            await enqueue_run_trigger(app.state.message_bus, user_id='local',
                                      session_id=sid, agent_id=agent_id)
            await _wait_for(lambda: not app.state.message_bus._queues.get(MessageBusKeys.wakeup_queue())
                            and app.state.chat_run_registry.get(sid) is None)
            await asyncio.sleep(0.05)
            assert len(calls) == 1
            assert len([m for m in await _messages(client, agent_id, sid) if m['role'] == 'assistant']) == 1

    asyncio.run(run())


def test_explicit_native_continue_without_inbox_still_runs(tmp_path: Path, monkeypatch):
    async def run():
        calls = []

        async def provider(self, model_name, messages, tools=None, **kwargs):
            calls.append(1)
            return _reply(f'REPLY_{len(calls)}')

        async with _app(tmp_path, monkeypatch, provider) as (app, client, agent_id, sid):
            await _start(client, agent_id, sid)
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            response = await client.post('/chat/', json={
                'agent_id': agent_id, 'session_id': sid, 'input': None,
            })
            assert response.status_code == 200, response.text
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            assert len(calls) == 2
            history = await _messages(client, agent_id, sid)
            assert any(b.get('text') == 'REPLY_2' for m in history for b in m['content'])

    asyncio.run(run())


@pytest.mark.parametrize('ending', ['permission', 'model_error', 'interrupted'])
def test_pending_inbox_does_not_restart_a_parked_failed_or_interrupted_run(tmp_path: Path, monkeypatch, ending):
    async def run():
        model_waiting, release_model = asyncio.Event(), asyncio.Event()
        calls, executions = [], []

        async def needs_confirmation() -> str:
            """A harmless fixture deliberately requiring explicit confirmation."""
            executions.append(1)
            return 'CONFIRMED'

        async def provider(self, model_name, messages, tools=None, **kwargs):
            calls.append(1)
            model_waiting.set()
            await release_model.wait()
            if ending == 'model_error':
                raise ValueError('fixture model failure')
            return ChatResponse(content=[ToolCallBlock(
                id='permission-probe', name='needs_confirmation', input='{}',
            )], is_last=True)

        async with _app(tmp_path, monkeypatch, provider, [FunctionTool(
                func=needs_confirmation, is_read_only=False)]) as (app, client, agent_id, sid):
            worker = await _worker(app, agent_id, sid)
            await _start(client, agent_id, sid)
            await asyncio.wait_for(model_waiting.wait(), 2)
            await worker(content='EVIDENCE_WHILE_RUNNING')
            bus = app.state.message_bus
            await _wait_for(lambda: bus.has_pending_inbox(sid)
                            and not bus._queues.get(MessageBusKeys.wakeup_queue()))
            if ending == 'interrupted':
                response = await client.post(f'/sessions/{sid}/interrupt', params={'agent_id': agent_id})
                assert response.status_code == 202, response.text
            else:
                release_model.set()
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            history = await _messages(client, agent_id, sid)
            assistant = [m for m in history if m['role'] == 'assistant']
            assert len(assistant) == 1
            assert assistant[0]['finished_reason'] == {
                'permission': None, 'model_error': 'error', 'interrupted': 'interrupted',
            }[ending]
            if ending == 'permission':
                assert any(b['type'] == 'tool_call' and b['state'] == 'asking'
                           for b in assistant[0]['content'])
            # A later native TeamSay wake must respect the same parked/error/
            # stop boundary rather than restarting inference behind the user.
            await worker(content='EVIDENCE_AFTER_STOP')
            await _wait_for(lambda: not bus._queues.get(MessageBusKeys.wakeup_queue())
                            and app.state.chat_run_registry.get(sid) is None)
            await asyncio.sleep(0.05)
            assert calls == [1]
            assert executions == []
            assert len(bus._queues[MessageBusKeys.inbox(sid)]) == 2
            assert await _messages(client, agent_id, sid) == history

    asyncio.run(run())


def test_explicit_leader_assignment_resumes_the_same_interrupted_worker(tmp_path, monkeypatch):
    async def run():
        started, resumed = asyncio.Event(), asyncio.Event()

        async def provider(self, model_name, messages, tools=None, **kwargs):
            combined = '\n'.join(str(m.model_dump(mode='json')) for m in messages)
            if 'WORKER_FOR_RESUME' in combined:
                if 'NEW_ASSIGNMENT' in combined:
                    resumed.set()
                    return _reply('WORKER_RESUMED_WITH_HISTORY')
                started.set()
                await asyncio.Event().wait()
            results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock) and b.name == 'TeamSay']
            if not results:
                assert 'traceglass.team_resume' in combined and 'interrupted' in combined
                return ChatResponse(content=[ToolCallBlock(id='resume-worker', name='TeamSay',
                    input='{"to":"reporter","content":"NEW_ASSIGNMENT: 继续你的原有分析"}')], is_last=True)
            return _reply('ASSIGNMENT_SENT')

        async with _app(tmp_path, monkeypatch, provider) as (app, client, aid, sid):
            worker_tool = await _worker(app, aid, sid)
            child_sid, child_aid = worker_tool._session_id, worker_tool._agent_id
            child_agent = await app.state.storage.get_agent('local', child_aid)
            child_agent.data.system_prompt = 'WORKER_FOR_RESUME'
            await app.state.storage.upsert_agent('local', child_agent)
            parent = await app.state.storage.get_session('local', aid, sid)
            child = await app.state.storage.get_session('local', child_aid, child_sid)
            child.config.chat_model_config = parent.config.chat_model_config
            await app.state.storage.upsert_session('local', child_aid, child.config, session_id=child_sid)
            await _start(client, child_aid, child_sid)
            await asyncio.wait_for(started.wait(), 3)
            response = await client.post(f'/product/sessions/{child_sid}/interrupt', params={'agent_id': child_aid})
            assert response.status_code == 202, response.text
            await _wait_for(lambda: app.state.chat_run_registry.get(child_sid) is None)
            assert any(m['finished_reason'] == 'interrupted' for m in await _messages(client, child_aid, child_sid))
            # A normal late inbox delivery does not authorize resuming it.
            leader_tool = TeamSay(storage=app.state.storage, message_bus=app.state.message_bus,
                workspace_manager=app.state.workspace_manager, user_id='local', agent_id=aid, session_id=sid)
            await leader_tool(content='LATE_BROADCAST')
            await _wait_for(lambda: not app.state.message_bus._queues.get(MessageBusKeys.wakeup_queue())
                            and app.state.chat_run_registry.get(child_sid) is None)
            assert not resumed.is_set()
            await _start(client, aid, sid)
            await asyncio.wait_for(resumed.wait(), 3)
            await _wait_for(lambda: app.state.chat_run_registry.get(child_sid) is None)
            history = await _messages(client, child_aid, child_sid)
            assert any(m['finished_reason'] == 'interrupted' for m in history)
            assert any(m['finished_reason'] == 'completed' and any(b.get('text') == 'WORKER_RESUMED_WITH_HISTORY'
                       for b in m['content']) for m in history)
            team = await app.state.storage.get_team('local', child.team_id)
            assert [m.session_id for m in team.data.members] == [child_sid]

    asyncio.run(run())


def test_pause_idle_leader_blocks_late_reports_until_explicit_continuation(tmp_path, monkeypatch):
    async def run():
        calls = []

        async def provider(self, model_name, messages, tools=None, **kwargs):
            calls.append(1)
            return _reply('DONE')

        async with _app(tmp_path, monkeypatch, provider) as (app, client, aid, sid):
            worker = await _worker(app, aid, sid)
            await _start(client, aid, sid)
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            response = await client.post(f'/product/sessions/{sid}/interrupt', params={'agent_id': aid, 'scope': 'team'})
            assert response.status_code == 202
            await worker(content='LATE_REPORT_AFTER_PAUSE')
            await _wait_for(lambda: not app.state.message_bus._queues.get(MessageBusKeys.wakeup_queue())
                            and app.state.chat_run_registry.get(sid) is None)
            assert calls == [1]
            await _start(client, aid, sid)
            await _wait_for(lambda: app.state.chat_run_registry.get(sid) is None)
            assert calls == [1, 1]
    asyncio.run(run())


def test_worker_budget_summary_reaches_leader_through_native_team_inbox(tmp_path, monkeypatch):
    async def run():
        reported = asyncio.Event()

        async def provider(self, model_name, messages, tools=None, **kwargs):
            combined = '\n'.join(str(m.model_dump(mode='json')) for m in messages)
            if 'BUDGET_WORKER' in combined:
                return _reply('PARTIAL_EVIDENCE_WITH_UNFINISHED_WORK')
            if 'PARTIAL_EVIDENCE_WITH_UNFINISHED_WORK' in combined:
                reported.set()
            return _reply('LEADER_RECEIVED')

        async with _app(tmp_path, monkeypatch, provider) as (app, client, aid, sid):
            worker = await _worker(app, aid, sid)
            child_aid, child_sid = worker._agent_id, worker._session_id
            record = await app.state.storage.get_agent('local', child_aid)
            record.data.system_prompt = 'BUDGET_WORKER'
            record.data.react_config.max_iters = 1
            await app.state.storage.upsert_agent('local', record)
            parent = await app.state.storage.get_session('local', aid, sid)
            child = await app.state.storage.get_session('local', child_aid, child_sid)
            child.config.chat_model_config = parent.config.chat_model_config
            await app.state.storage.upsert_session('local', child_aid, child.config, session_id=child_sid)
            await _start(client, child_aid, child_sid)
            await asyncio.wait_for(reported.wait(), 3)
            await _wait_for(lambda: app.state.chat_run_registry.get(child_sid) is None)
            history = await _messages(client, child_aid, child_sid)
            assert any(b.get('source') == 'traceglass.iteration_closeout' for m in history for b in m['content'])
    asyncio.run(run())
