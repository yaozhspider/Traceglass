"""Compose AgentScope's service with Traceglass's analysis capabilities."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from agentscope.app import create_app as create_agentscope_app
from agentscope.app.storage import AsyncSQLAlchemyStorage
from agentscope.app.workspace_manager import IsolationPolicy
from agentscope.credential import OpenAICredential
from fastapi import FastAPI

from traceglass.agents import (
    AGENT_ID, CREDENTIAL_ID, PRE_INTEGRATION_PROMPT, INTEGRATED_PROMPT_V1, INTEGRATED_PROMPT_V2, INTEGRATED_PROMPT_V3,
    analyst_record, subagent_templates, upgrade_shipped_iteration_limits,
)
from traceglass.config import Settings, materialize_skill_paths, mcp_clients
from traceglass.closeout import BudgetCloseoutMiddleware, worker_closeout_reporter
from traceglass.delegation import MAX_SUBAGENTS, DelegationPolicy
from traceglass.tools.todo import TodoWrite
from traceglass.api.workspaces import create_workspace_router
from traceglass.api.runtime import create_runtime_router
from traceglass.workspaces import ProductDockerWorkspaceManager, ProductLocalWorkspaceManager, ProductWorkspaceService
from traceglass.integrations.agentscope_compat import (
    PendingInboxMessageBus, WorkspaceFileCacheMiddleware, DirectedWorkerResumeMiddleware, preserve_inbox_wakeups,
)
from traceglass.analysis_policy import AnalysisQualityMiddleware
from traceglass.tools.review import AnalysisReview
from traceglass.integrations.analysis_input import AnalysisInput
from traceglass.integrations.rea import ReaSessions
from traceglass.integrations.rea_groups import ReaToolsMiddleware
from traceglass.integrations.rea_evidence import ReaEvidenceMiddleware
from traceglass.evidence.store import EvidenceStore
from traceglass.tools.rea_evidence import evidence_tools
from traceglass.api.evidence import create_evidence_router


def create_app(*, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    data_dir = settings.data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    if settings.legacy_project and settings.workspace_backend == 'docker':
        from traceglass.assets import import_legacy_assets
        import_legacy_assets(settings)
    storage = AsyncSQLAlchemyStorage(url=f"sqlite+aiosqlite:///{data_dir / 'agentscope.sqlite3'}")
    message_bus = PendingInboxMessageBus()
    delegation = DelegationPolicy(storage)
    paths = materialize_skill_paths(settings, data_dir / 'skill-cache')
    clients = mcp_clients(settings)
    workspace_options = dict(
        basedir=str(data_dir / ('docker-workspaces' if settings.workspace_backend == 'docker' else 'workspaces')),
        isolation=IsolationPolicy.PER_SESSION, skill_paths=paths, default_mcps=clients,
    )
    if settings.workspace_backend == 'docker':
        workspaces = ProductDockerWorkspaceManager(**workspace_options, base_image=settings.sandbox_image,
                                           extra_pip=['agentscope==2.0.6'])
    else:
        workspaces = ProductLocalWorkspaceManager(**workspace_options)
    product_workspaces = ProductWorkspaceService(data_dir, storage, workspaces, AGENT_ID)
    rea = ReaSessions(settings.rea_kit)
    rea_evidence = EvidenceStore(data_dir / 'rea-evidence.sqlite3')

    async def analysis_tools(user_id: str, agent_id: str, session_id: str):
        from traceglass.tools import build_analysis_tools

        session = await storage.get_session(user_id, agent_id, session_id)
        if session is None:
            raise LookupError("Session not found for analysis tools")
        team = await storage.get_team(user_id, session.team_id) if session.team_id else None
        tools = [TodoWrite()] if team is None or team.session_id == session_id else []
        root = workspaces.host_root(user_id, agent_id, session.config.workspace_id)
        tools += [AnalysisReview(), AnalysisInput(root, data_dir / 'analyzer-inputs',
            docker=settings.workspace_backend == 'docker', ida_local_files=settings.ida_local_files).tool()]
        # Docker analysis runs inside its native workspace, never on the API host.
        if settings.workspace_backend == 'docker':
            return tools
        workspace = await workspaces.get_workspace(
            user_id=user_id, agent_id=agent_id, session_id=session_id,
            workspace_id=session.config.workspace_id,
        )
        return tools + build_analysis_tools(Path(workspace.workdir))

    async def agent_middlewares(user_id: str, agent_id: str, session_id: str, workspace):
        rea_middlewares = []
        if settings.rea_kit:
            session = await storage.get_session(user_id, agent_id, session_id)
            root = workspaces.host_root(user_id, agent_id, session.config.workspace_id)
            wid = session.config.workspace_id
            rea_middlewares.append(ReaToolsMiddleware(
                await rea.tools(root, user_id, session_id, wid),
                await rea.workflow_tools(root, user_id, session_id, wid) + evidence_tools(rea_evidence, user_id, wid)))
            rea_middlewares.append(ReaEvidenceMiddleware(rea_evidence, user_id, wid, session_id,
                                                        agent_id, rea.connection_id(root, user_id, session_id)))
        if settings.rea_kit and 'REA integration:' not in workspace.instructions:
            workspace.instructions += ('\n\nREA integration: If mcp__rea__ tools are present, read the rea-analysis Skill. '
                'REA runs in a separate, network-disabled container with this workspace mounted read-only at /workspace. '
                'Its current binary is private to your session; do not assume another member opened it. '
                'After a service restart use binary_session and reopen the binary if needed. '
                'Only common REA tools are initially equipped. Use native reset_tools to enable the relevant rea_* '
                'groups on demand, and disable unused groups to conserve context. Never enable all groups at once. '
                'Save returned evidence with native workspace Write tools. Do not assume optional tools are connected.')
        return rea_middlewares + [AnalysisQualityMiddleware(), WorkspaceFileCacheMiddleware(workspace), BudgetCloseoutMiddleware(
                    worker_closeout_reporter(storage, message_bus, workspaces, user_id, agent_id, session_id)),
                DirectedWorkerResumeMiddleware(storage, message_bus, user_id, agent_id, session_id),
                delegation.for_session(user_id, agent_id, session_id)]

    app = create_agentscope_app(
        storage=storage, message_bus=message_bus, workspace_manager=workspaces,
        extra_agent_tools=analysis_tools,
        extra_agent_middlewares=agent_middlewares,
        custom_subagent_templates=subagent_templates(settings.max_iters),
        title="Traceglass", version="0.2.0",
    )
    native_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with native_lifespan(application):
            # Seed defaults only; edits through the native API survive restart.
            existing = await storage.get_agent(settings.user_id, AGENT_ID)
            if existing is None:
                await storage.upsert_agent(settings.user_id, analyst_record(settings.user_id, settings.max_iters))
            elif existing.data.system_prompt in {PRE_INTEGRATION_PROMPT, INTEGRATED_PROMPT_V1, INTEGRATED_PROMPT_V2, INTEGRATED_PROMPT_V3}:
                # Upgrade only our unedited default prompt, preserving user edits.
                existing.data.system_prompt = analyst_record(settings.user_id, settings.max_iters).data.system_prompt
                await storage.upsert_agent(settings.user_id, existing)
            if settings.model_configured:
                await storage.upsert_credential(settings.user_id, OpenAICredential(
                    id=CREDENTIAL_ID, name="Traceglass 模型", api_key=settings.api_key,
                    base_url=settings.base_url,
                ))
            await upgrade_shipped_iteration_limits(storage, settings.user_id, settings.max_iters)
            async with preserve_inbox_wakeups(application.state.chat_service, application.state.chat_run_registry,
                                             storage, message_bus):
                yield

    @asynccontextmanager
    async def managed_lifespan(application):
        try:
            async with lifespan(application):
                yield
        finally:
            await rea.close()

    app.state.rea_sessions = rea
    app.state.rea_evidence = rea_evidence
    app.include_router(create_evidence_router(product_workspaces, rea_evidence))
    app.router.lifespan_context = managed_lifespan
    app.include_router(create_workspace_router(product_workspaces))
    app.include_router(create_runtime_router(product_workspaces, rea.status))

    @app.get("/product/config")
    async def product_config():
        return {
            "user_id": settings.user_id,
            "agent_id": AGENT_ID,
            "model_configured": settings.model_configured,
            "model_config": {
                "type": "openai_chat", "credential_id": CREDENTIAL_ID,
                "model": settings.model_name, "parameters": settings.model_parameters,
            } if settings.model_configured else None,
            "orchestration": {"todo_tool": "TodoWrite", "max_subagents": MAX_SUBAGENTS,
                              "dynamic_roles": True, "max_iters": settings.max_iters},
            "integrations": {
                "model_source": 'legacy_project' if settings.legacy_project else 'environment',
                "workspace_backend": settings.workspace_backend,
                "sandbox_image": settings.sandbox_image if settings.workspace_backend == 'docker' else None,
                "skills": [Path(path).name for path in paths],
                "mcp_servers": [client.name for client in clients] + (['rea'] if settings.rea_kit else []),
                "legacy_tools": bool(settings.legacy_project),
            },
        }

    return app


def main() -> None:
    import uvicorn

    uvicorn.run("traceglass.api.app:create_app", factory=True, host="127.0.0.1", port=8000)
