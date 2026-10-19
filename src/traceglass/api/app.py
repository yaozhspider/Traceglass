"""Compose AgentScope's service with Traceglass's analysis capabilities."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from agentscope.app import create_app as create_agentscope_app
from agentscope.app.workspace_manager import IsolationPolicy
from agentscope.credential import OpenAICredential
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import asyncio
from traceglass.accounts.models import create_model_router, chat_config
from agentscope.app.deps import get_current_user_id
from traceglass.accounts.store import Accounts
from traceglass.accounts.storage import EncryptedStorage
from traceglass.accounts.security import AuthenticationMiddleware
from traceglass.accounts.routes import create_auth_router
from traceglass.accounts.identity import personal_agent_id

from traceglass.agents import (
    AGENT_ID, CREDENTIAL_ID, PRE_INTEGRATION_PROMPT, INTEGRATED_PROMPT_V1, INTEGRATED_PROMPT_V2, INTEGRATED_PROMPT_V3,
    analyst_record, subagent_templates, upgrade_shipped_iteration_limits, upgrade_agent_prompt,
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
from traceglass.integrations.tool_catalog import AnalysisToolsMiddleware
from traceglass.integrations.rea_evidence import ReaEvidenceMiddleware
from traceglass.integrations.evidence_ledger import EvidenceLedgerMiddleware
from traceglass.evidence.store import EvidenceStore
from traceglass.tools.rea_evidence import evidence_tools
from traceglass.api.evidence import create_evidence_router
from traceglass.accounts.model_tls import ModelTLSPolicy, ModelTLSMiddleware


def create_app(*, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    model_tls = ModelTLSPolicy(settings.model_tls_config, settings.model_allowed_origins)
    data_dir = settings.data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    if settings.legacy_project and settings.workspace_backend == 'docker' and not settings.auth_enabled:
        from traceglass.assets import import_legacy_assets
        import_legacy_assets(settings)
    storage = EncryptedStorage(key_path=data_dir / 'credentials.key', url=f"sqlite+aiosqlite:///{data_dir / 'agentscope.sqlite3'}")
    accounts = Accounts(data_dir / 'accounts.sqlite3')
    message_bus = PendingInboxMessageBus()
    delegation = DelegationPolicy(storage)
    paths = materialize_skill_paths(settings, data_dir / 'skill-cache')
    # Build only tenant-safe clients, including the bridge's environment: merely
    # dropping the IDA client would still expose it through reverse's IDA tools.
    from dataclasses import replace
    client_settings = replace(settings, ida_mcp_url=None, mcp_config_path=None) if settings.auth_enabled else settings
    clients = mcp_clients(client_settings)
    workspace_options = dict(
        basedir=str(data_dir / ('docker-workspaces' if settings.workspace_backend == 'docker' else 'workspaces')),
        isolation=IsolationPolicy.PER_SESSION, skill_paths=paths, default_mcps=clients,
    )
    if settings.workspace_backend == 'docker':
        workspaces = ProductDockerWorkspaceManager(**workspace_options, base_image=settings.sandbox_image,
                                           extra_pip=['agentscope==2.0.6'])
    else:
        workspaces = ProductLocalWorkspaceManager(**workspace_options)
    workspaces.require_product_workspace = settings.auth_enabled
    product_workspaces = ProductWorkspaceService(data_dir, storage, workspaces, AGENT_ID,
        agent_resolver=personal_agent_id if settings.auth_enabled else None)
    rea = ReaSessions(settings.rea_kit, max_connections=settings.rea_max_connections,
                      idle_seconds=settings.rea_idle_seconds, quota_wait_seconds=settings.rea_queue_wait_seconds)
    rea_evidence = EvidenceStore(data_dir / 'rea-evidence.sqlite3')
    delegation.evidence_store = rea_evidence
    windows = None
    if settings.windows_config:
        from traceglass.windows.config import WindowsConfig
        from traceglass.windows.service import WindowsService
        windows = WindowsService(WindowsConfig.load(settings.windows_config), data_dir / 'windows-jobs')

    async def analysis_tools(user_id: str, agent_id: str, session_id: str):
        from traceglass.tools import build_analysis_tools

        session = await storage.get_session(user_id, agent_id, session_id)
        if session is None:
            raise LookupError("Session not found for analysis tools")
        team = await storage.get_team(user_id, session.team_id) if session.team_id else None
        tools = [TodoWrite()] if team is None or team.session_id == session_id else []
        root = workspaces.host_root(user_id, agent_id, session.config.workspace_id)
        from traceglass.analysis_contract import reference_mode
        mode = reference_mode(session.state, strict=settings.strict_evidence_references)
        tools += evidence_tools(rea_evidence, user_id, session.config.workspace_id, analysis=True)
        tools += [AnalysisReview(store=rea_evidence, user_id=user_id, workspace_id=session.config.workspace_id,
            strict_references=mode == 'strict'), AnalysisInput(root, data_dir / 'analyzer-inputs' / user_id,
            docker=settings.workspace_backend == 'docker', ida_local_files=settings.ida_local_files and not settings.auth_enabled).tool()]
        if windows is not None:
            from traceglass.windows.tools import windows_tools
            tools += windows_tools(windows, user_id, session.config.workspace_id, root)
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
        session = await storage.get_session(user_id, agent_id, session_id)
        root = workspaces.host_root(user_id, agent_id, session.config.workspace_id)
        wid = session.config.workspace_id
        if settings.rea_kit:
            from agentscope.tool import FunctionTool
            from traceglass.integrations.rea_workflows import response
            async def connect_rea():
                """Discover REA tool metadata when the deployed cache is missing. No sample is opened or executed."""
                try:
                    count = await rea.refresh_schema(root)
                    return response({'schema_tools': count, 'status': 'metadata_saved',
                        'next_step': 'Use SearchAnalysisTools and reset_tools; actual engines are probed on use.'})
                except Exception:
                    return response({'status': 'unavailable', 'next_step': 'Check kit and capacity; use available alternatives.'}, error=True)
            lazy_loader = lambda: rea.lazy_tools(root, user_id, session_id, wid)
            rea_middlewares.append(ReaToolsMiddleware(
                lazy_loader(),
                await rea.workflow_tools(root, user_id, session_id, wid) + evidence_tools(rea_evidence, user_id, wid)
                + [FunctionTool(connect_rea, name='ConnectRea', is_read_only=False)], loader=lazy_loader,
                idle_seconds=settings.rea_idle_seconds,
                release_on_complete=lambda: rea.release(root, user_id, session_id)))
        rea_middlewares.append(EvidenceLedgerMiddleware(rea_evidence, user_id, wid, session_id,
            agent_id, lambda: rea.connection_id(root, user_id, session_id), workspace_root=root,
            windows_service=windows))
        if settings.rea_kit and 'REA integration:' not in workspace.instructions:
            workspace.instructions += ('\n\nREA integration: If mcp__rea__ tools are present, read the rea-analysis Skill. '
                'REA runs in a separate, network-disabled container with this workspace mounted read-only at /workspace. '
                'Its current binary is private to your session; do not assume another member opened it. '
                'After a service restart use binary_session and reopen the binary if needed. '
                'Only common REA tools are initially equipped. Use native reset_tools to enable the relevant rea_* '
                'groups on demand, and disable unused groups to conserve context. Never enable all groups at once. '
                'Original calls are archived automatically; read receipt IDs with QueryAnalysisEvidence/ReadAnalysisEvidence. '
                'Write saves reports. Registered tools may be deferred; do not assume optional providers are connected.')
        return [ModelTLSMiddleware(model_tls)] + rea_middlewares + [AnalysisToolsMiddleware(), AnalysisQualityMiddleware(), WorkspaceFileCacheMiddleware(workspace), BudgetCloseoutMiddleware(
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
            if not settings.auth_enabled:
                # Seed defaults only; edits through the native API survive restart.
                existing = await storage.get_agent(settings.user_id, AGENT_ID)
                if existing is None:
                    await storage.upsert_agent(settings.user_id, analyst_record(settings.user_id, settings.max_iters))
                await upgrade_agent_prompt(storage, settings.user_id, AGENT_ID)
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

    app.state.accounts = accounts
    app.include_router(create_auth_router(accounts, storage, settings))
    app.include_router(create_model_router(accounts, storage, settings, model_tls))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default includes the rejected input, which may contain keys/passwords.
        return JSONResponse(status_code=422, content={'detail': [
            {'loc':list(error['loc']), 'msg':error['msg'], 'type':error['type']} for error in exc.errors()]})
    if settings.auth_enabled:
        app.add_middleware(AuthenticationMiddleware, accounts=accounts, storage=storage, settings=settings)
        async def authenticated_user(request: Request):
            account = getattr(request.state, 'account', None)
            if not account:
                raise HTTPException(401, '请先登录')
            return account['id']
        app.dependency_overrides[get_current_user_id] = authenticated_user
    else:
        # Product config is also readable before the legacy frontend sends its identity.
        async def local_user():
            return settings.user_id
        app.dependency_overrides[get_current_user_id] = local_user
    app.state.rea_sessions = rea
    app.state.rea_evidence = rea_evidence
    app.state.windows_service = windows
    async def release_completed(owner_id, member_agent_id, member_session_id):
        async def release_if_idle():
            try:
                active = app.state.chat_run_registry.get(member_session_id)
                if (active and not active.done()) or message_bus.has_pending_inbox(member_session_id) or message_bus.is_paused(member_session_id):
                    return
                member = await storage.get_session(owner_id, member_agent_id, member_session_id)
                if member:
                    root = workspaces.host_root(owner_id, member_agent_id, member.config.workspace_id)
                    await rea.release(root, owner_id, member_session_id)
            except Exception:
                # Shutdown and wake races do not change an accepted native task.
                import logging
                logging.getLogger(__name__).warning('Deferred REA release failed for session %s', member_session_id)
        active = app.state.chat_run_registry.get(member_session_id)
        if active and not active.done():
            active.add_done_callback(lambda _: asyncio.create_task(release_if_idle()))
        else:
            await release_if_idle()
    delegation.release_completed = release_completed
    app.include_router(create_evidence_router(product_workspaces, rea_evidence))
    app.router.lifespan_context = managed_lifespan
    app.include_router(create_workspace_router(product_workspaces, accounts))
    app.include_router(create_runtime_router(product_workspaces, rea.status))

    @app.get("/product/config")
    async def product_config(user_id: str = Depends(get_current_user_id)):
        if settings.auth_enabled:
            from traceglass.accounts.identity import ensure_analyst
            await ensure_analyst(storage, user_id, settings.max_iters)
        personal = await asyncio.to_thread(accounts.model, user_id)
        configured = chat_config(personal)
        if configured is None and not settings.auth_enabled and settings.model_configured:
            configured = {'type':'openai_chat', 'credential_id':CREDENTIAL_ID,
                          'model':settings.model_name, 'parameters':settings.model_parameters}
        return {
            "user_id": user_id,
            "agent_id": product_workspaces.agent_id_for(user_id),
            "model_configured": configured is not None,
            "model_config": configured,
            "orchestration": {"todo_tool": "TodoWrite", "max_subagents": MAX_SUBAGENTS,
                              "dynamic_roles": True, "max_iters": settings.max_iters},
            "integrations": {
                "model_source": 'personal' if personal else 'legacy_project' if settings.legacy_project else 'environment',
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
