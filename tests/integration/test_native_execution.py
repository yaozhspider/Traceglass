"""Verify our native composition with scripted provider responses.

Only the external model transport is replaced. AgentScope still assembles and
executes real Task/Team/domain tools, persists their state, and serves history.
These tests make no assertion about live-provider quality or availability.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse, OpenAIChatModel

from traceglass.api.app import create_app
from traceglass.config import Settings


def _tool(name: str, arguments: dict, number: int) -> ChatResponse:
    return ChatResponse(content=[ToolCallBlock(
        id=f"fixture-call-{number}", name=name, input=json.dumps(arguments),
    )], is_last=True)


def _results(messages) -> list[ToolResultBlock]:
    return [
        block for message in messages for block in message.content
        if isinstance(block, ToolResultBlock)
    ]


async def _session(client: httpx.AsyncClient) -> tuple[dict, str]:
    config = (await client.get("/product/config")).json()
    response = await client.post("/sessions/", json={
        "agent_id": config["agent_id"], "workspace_id": "execution-fixture",
        "chat_model_config": config["model_config"], "name": "原生执行测试",
    })
    assert response.status_code == 201, response.text
    return config, response.json()["session_id"]


def test_todowrite_runs_domain_tools_and_restores_native_tasks(tmp_path: Path, monkeypatch):
    """Losing extra tools or native task state makes this workflow fail."""
    # Do not make offline fixtures depend on a developer's proxy installation.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)

    async def provider(self, model_name, messages, tools=None, **kwargs):
        results = _results(messages)
        script = [
            ("TodoWrite", {"todos": [{"subject": "核查样本", "description": "读取文件元数据并核验摘要", "status": "in_progress"}]}),
            ("inspect_file", {"path": "sample.bin"}),
            ("TodoWrite", {"todos": [{"task_id": "1", "subject": "核查样本", "status": "completed"}]}),
        ]
        if len(results) < len(script):
            name, arguments = script[len(results)]
            return _tool(name, arguments, len(results))
        return ChatResponse(content=[TextBlock(text="样本元数据已核查。")], is_last=True)

    monkeypatch.setattr(OpenAIChatModel, "_call_api", provider)
    settings = Settings(data_dir=tmp_path, model_name="fixture", api_key="fixture-secret")
    sample = b"MZ\x00fixture static sample"

    async def run():
        app = create_app(settings=settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
                headers={"X-User-ID": "local"},
            ) as client:
                config, session_id = await _session(client)
                workspace = await app.state.workspace_manager.get_workspace(
                    user_id="local", agent_id=config["agent_id"],
                    session_id=session_id, workspace_id="execution-fixture",
                )
                (Path(workspace.workdir) / "sample.bin").write_bytes(sample)
                await app.state.chat_service.run(
                    user_id="local", agent_id=config["agent_id"], session_id=session_id,
                    input_msg=Msg(name="user", role="user", content=[TextBlock(text="核查样本")]),
                )
                return config["agent_id"], session_id

    agent_id, session_id = asyncio.run(run())

    async def restore():
        app = create_app(settings=settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
                headers={"X-User-ID": "local"},
            ) as client:
                session = (await client.get("/sessions/", params={"agent_id": agent_id})).json()["sessions"][0]
                tasks = session["session"]["state"]["tasks_context"]["tasks"]
                assert [(task["subject"], task["state"]) for task in tasks] == [("核查样本", "completed")]
                response = await client.get(f"/sessions/{session_id}/messages", params={"agent_id": agent_id})
                assert response.status_code == 200, response.text
                blocks = [block for message in response.json()["messages"] for block in message["content"]]
                todos = [block for block in blocks if block["type"] == "tool_result" and block["name"] == "TodoWrite"]
                assert len(todos) == 2 and all(block["state"] == "success" for block in todos), todos
                inspected = next(block for block in blocks if block["type"] == "tool_result" and block["name"] == "inspect_file")
                assert inspected["state"] == "success", inspected
                assert hashlib.sha256(sample).hexdigest() in json.dumps(inspected["output"])
                assert any(block.get("text") == "样本元数据已核查。" for block in blocks)

    asyncio.run(restore())


@pytest.mark.parametrize('permission_mode', ['default', 'bypass'])
def test_todo_delegation_runs_dynamic_default_worker_and_exposes_its_history(tmp_path: Path, monkeypatch, permission_mode):
    """A broken template or lost workspace/tool binding must be observable."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)

    async def provider(self, model_name, messages, tools=None, **kwargs):
        results = _results(messages)
        is_worker = "你是团队" in messages[0].get_text_content()
        if is_worker:
            script = [
                ("inspect_file", {"path": "sample.bin"}),
                ("TeamSay", {"content": "WORKER_VERIFIED"}),
            ]
            final_text = "WORKER_COMPLETE"
        else:
            script = [
                ("TodoWrite", {"todos": [{"subject": "核查样本摘要", "description": "读取 sample.bin 并返回 SHA-256"}]}),
                ("TeamCreate", {"name": "样本复核", "description": "独立复核样本摘要"}),
                ("AgentCreate", {"name": "string-decoder", "description": "本次任务动态生成的字符串解密分析员，先核查样本摘要",
                    "prompt": "task_id=1\n独立读取 sample.bin 并报告摘要。", "subagent_type": "default"}),
                ("TaskUpdate", {"task_id": "1", "status": "completed"}),
                ("TodoWrite", {"todos": [{"subject": "后续核查", "description": "复用成员核对摘要"}]}),
                ("TeamSay", {"to": "string-decoder", "content": "task_id=2\n复核后续问题。"}),
            ]
            final_text = "已完成委派。"
        if len(results) < len(script):
            name, arguments = script[len(results)]
            return _tool(name, arguments, len(results))
        return ChatResponse(content=[TextBlock(text=final_text)], is_last=True)

    monkeypatch.setattr(OpenAIChatModel, "_call_api", provider)
    settings = Settings(data_dir=tmp_path, model_name="fixture", api_key="fixture-secret")
    sample = b"MZ\x00shared sample evidence"

    async def run():
        app = create_app(settings=settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
                headers={"X-User-ID": "local"},
            ) as client:
                config, session_id = await _session(client)
                changed = await client.patch(f'/product/sessions/{session_id}/permission',
                    params={'agent_id': config['agent_id']}, json={'permission_mode': permission_mode})
                assert changed.status_code == 200, changed.text
                workspace = await app.state.workspace_manager.get_workspace(
                    user_id="local", agent_id=config["agent_id"],
                    session_id=session_id, workspace_id="execution-fixture",
                )
                (Path(workspace.workdir) / "sample.bin").write_bytes(sample)
                await app.state.chat_service.run(
                    user_id="local", agent_id=config["agent_id"], session_id=session_id,
                    input_msg=Msg(name="user", role="user", content=[TextBlock(text="委派独立复核")]),
                )
                sessions = (await client.get("/sessions/", params={"agent_id": config["agent_id"]})).json()["sessions"]
                team = sessions[0]["team"]
                assert team is not None, sessions
                assert len(team["members"]) == 1, team
                member = team["members"][0]
                assert member["agent"]["source"] == "team"
                assert member["agent"]["data"]["name"] == "string-decoder"
                assert "本次任务动态生成的字符串解密分析员" in member["agent"]["data"]["system_prompt"]
                tasks = sessions[0]["session"]["state"]["tasks_context"]["tasks"]
                assert [(task["subject"], task["owner"]) for task in tasks] == [
                    ("核查样本摘要", "string-decoder"), ("后续核查", "string-decoder")]
                leader_messages, _ = await app.state.storage.list_messages('local', session_id, limit=30)
                leader_results = _results(leader_messages)
                assert all(b.state == 'success' for b in leader_results), leader_results
                assert member["session_id"] != session_id

                async def completed_worker_history():
                    while True:
                        response = await client.get(
                            f"/sessions/{member['session_id']}/messages",
                            params={"agent_id": member["agent"]["id"]},
                        )
                        assert response.status_code == 200, response.text
                        messages = response.json()["messages"]
                        blocks = [block for message in messages for block in message["content"]]
                        if any(block.get("text") == "WORKER_COMPLETE" for block in blocks):
                            return blocks
                        assert not any(message.get("error") for message in messages), messages
                        await asyncio.sleep(0.02)

                blocks = await asyncio.wait_for(completed_worker_history(), timeout=5)
                child = await app.state.storage.get_session('local', member['agent']['id'], member['session_id'])
                assert child.state.permission_context.mode.value == permission_mode
                results = {block["name"]: block for block in blocks if block["type"] == "tool_result"}
                assert results["inspect_file"]["state"] == "success", results
                assert hashlib.sha256(sample).hexdigest() in json.dumps(results["inspect_file"]["output"])
                assert results["TeamSay"]["state"] == "success", results

    asyncio.run(run())


@pytest.mark.parametrize("with_todo,expected_count", [(True, 8), (False, 0)])
def test_app_enforces_todo_before_delegation_and_eight_member_limit(tmp_path: Path, monkeypatch, with_todo, expected_count):
    """Removing the app's policy factory must allow too many native workers."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)

    async def provider(self, model_name, messages, tools=None, **kwargs):
        if "你是团队" in messages[0].get_text_content():
            return ChatResponse(content=[TextBlock(text="WORKER_COMPLETE")], is_last=True)
        results = _results(messages)
        script = [("TodoWrite", {"todos": [{"subject": f"独立核查证据 {i}"} for i in range(9)]})] if with_todo else []
        script += [("TeamCreate", {"name": "边界验证团队", "description": "测试成员数限制"})]
        script += [("AgentCreate", {"name": f"worker-{i}", "description": f"独立证据核查 {i}",
                                   "prompt": f"task_id={i+1}\n检查给定任务范围后结束。", "subagent_type": "default"}) for i in range(9)]
        if len(results) < len(script):
            name, arguments = script[len(results)]
            return _tool(name, arguments, len(results))
        return ChatResponse(content=[TextBlock(text="LIMIT_CHECK_COMPLETE")], is_last=True)

    monkeypatch.setattr(OpenAIChatModel, "_call_api", provider)

    async def run():
        app = create_app(settings=Settings(data_dir=tmp_path, model_name="fixture", api_key="fixture-secret"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                         headers={"X-User-ID": "local"}) as client:
                config, sid = await _session(client)
                await app.state.chat_service.run(
                    user_id="local", agent_id=config["agent_id"], session_id=sid,
                    input_msg=Msg(name="user", role="user", content=[TextBlock(text="验证委派边界")]),
                )
                sessions = (await client.get("/sessions/", params={"agent_id": config["agent_id"]})).json()["sessions"]
                assert len(sessions[0]["team"]["members"]) == expected_count
                messages = (await client.get(f"/sessions/{sid}/messages", params={"agent_id": config["agent_id"]})).json()["messages"]
                results = [b for m in messages for b in m["content"] if b["type"] == "tool_result" and b["name"] == "AgentCreate"]
                assert len(results) == 9
                assert sum(b["state"] == "success" for b in results) == expected_count
                assert results[-1]["state"] == "error"
                assert ("8" if with_todo else "TodoWrite") in json.dumps(results[-1]["output"])

    asyncio.run(run())
