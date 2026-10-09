"""Our service composition must expose working native sessions, not trace CRUD."""
from pathlib import Path
import asyncio

import pytest

from fastapi.testclient import TestClient

from traceglass.api.app import create_app
from traceglass.agents import AGENT_ID, PRE_INTEGRATION_PROMPT, INTEGRATED_PROMPT_V1, ANALYST_PROMPT, analyst_record
from traceglass.config import Settings
from agentscope.app.storage import SessionConfig, TeamData, TeamMember, TeamRecord


def test_native_service_creates_and_restores_session(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRACEGLASS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with TestClient(create_app()) as client:
        config = client.get("/product/config")
        assert config.status_code == 200
        info = config.json()
        assert info["model_configured"] is False
        headers = {"X-User-ID": info["user_id"]}
        created = client.post("/sessions/", headers=headers, json={
            "agent_id": info["agent_id"], "workspace_id": "test-workspace", "name": "分析样本",
        })
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]
    with TestClient(create_app()) as client:
        sessions = client.get("/sessions/", headers=headers, params={"agent_id": info["agent_id"]})
        assert sessions.status_code == 200, sessions.text
        assert [s["session"]["id"] for s in sessions.json()["sessions"]] == [session_id]
        assert sessions.json()["sessions"][0]["session"]["config"]["name"] == "分析样本"


def test_restart_upgrades_old_default_limits_for_leader_and_owned_workers(tmp_path):
    async def run():
        app = create_app(settings=Settings(data_dir=tmp_path))
        async with app.router.lifespan_context(app):
            storage = app.state.storage
            leader = analyst_record('local', 24)
            leader.data.name = '保留名称'
            await storage.upsert_agent('local', leader)
            root = await storage.upsert_session('local', AGENT_ID, SessionConfig(workspace_id='fixture'))
            worker = analyst_record('local', 24).model_copy(update={'id': 'old-worker', 'source': 'team'})
            worker.data.system_prompt = '保留动态角色'
            await storage.upsert_agent('local', worker)
            child = await storage.upsert_session('local', worker.id, root.config)
            team = TeamRecord(user_id='local', session_id=root.id, data=TeamData(name='fixture', members=[
                TeamMember(owner_id='local', agent_id=worker.id, session_id=child.id, role='created')]))
            await storage.upsert_team('local', team)
            await storage.set_session_team_id('local', root.id, team.id)
            await storage.set_session_team_id('local', child.id, team.id)
        app = create_app(settings=Settings(data_dir=tmp_path))
        async with app.router.lifespan_context(app):
            root_agent = await app.state.storage.get_agent('local', AGENT_ID)
            child_agent = await app.state.storage.get_agent('local', 'old-worker')
            assert root_agent.data.react_config.max_iters == 2000
            assert child_agent.data.react_config.max_iters == 2000
            assert root_agent.data.name == '保留名称'
            assert child_agent.data.system_prompt == '保留动态角色'
    asyncio.run(run())


def test_bootstrap_exposes_model_reference_without_secret(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRACEGLASS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "fixture-secret-never-expose")
    monkeypatch.setenv("LLM_MODEL", "fixture-model")
    with TestClient(create_app()) as client:
        response = client.get("/product/config")
        assert response.status_code == 200
        assert "fixture-secret-never-expose" not in response.text
        info = response.json()
        assert info["model_configured"] is True
        assert info["model_config"]["model"] == "fixture-model"
        created = client.post("/sessions/", headers={"X-User-ID": info["user_id"]}, json={
            "agent_id": info["agent_id"], "workspace_id": "with-model",
            "chat_model_config": info["model_config"],
        })
        assert created.status_code == 201, created.text


@pytest.mark.parametrize("saved_prompt", [PRE_INTEGRATION_PROMPT, INTEGRATED_PROMPT_V1, (Path(__file__).parents[1]/"fixtures/analyst_prompt_v3.txt").read_text(), "用户定制的分析提示词"])
def test_restart_upgrades_shipped_prompts_but_preserves_user_edits(tmp_path: Path, saved_prompt: str):
    async def run():
        settings = Settings(data_dir=tmp_path)
        app = create_app(settings=settings)
        async with app.router.lifespan_context(app):
            agent = analyst_record("local", 24)
            agent.data.system_prompt = saved_prompt
            await app.state.storage.upsert_agent("local", agent)
        app = create_app(settings=settings)
        async with app.router.lifespan_context(app):
            restored = await app.state.storage.get_agent("local", AGENT_ID)
            expected = saved_prompt if saved_prompt == "用户定制的分析提示词" else ANALYST_PROMPT
            assert restored.data.system_prompt == expected

    asyncio.run(run())


@pytest.mark.parametrize("custom_suffix", ["", "\n用户要求：保留我的分析方法。", "\n"])
def test_restart_migrates_v2_default_only_without_resetting_agent_config(tmp_path: Path, custom_suffix: str):
    # Frozen shipped text catches omitted migrations and overly broad matching.
    shipped_prompt = (Path(__file__).parents[1] / "fixtures" / "analyst_prompt_v2.txt").read_text()
    saved_prompt = shipped_prompt + custom_suffix

    async def run():
        settings = Settings(data_dir=tmp_path)
        app = create_app(settings=settings)
        async with app.router.lifespan_context(app):
            agent = analyst_record("local", 57)
            agent.data.name = "用户命名的分析助手"
            agent.data.system_prompt = saved_prompt
            await app.state.storage.upsert_agent("local", agent)

        app = create_app(settings=settings)
        async with app.router.lifespan_context(app):
            restored = await app.state.storage.get_agent("local", AGENT_ID)
            assert restored.data.name == "用户命名的分析助手"
            assert restored.data.react_config.max_iters == 57
            if custom_suffix:
                assert restored.data.system_prompt == saved_prompt
            else:
                assert restored.data.system_prompt != shipped_prompt
                assert restored.data.system_prompt == ANALYST_PROMPT

    asyncio.run(run())
