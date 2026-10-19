"""Static-only evaluation through Traceglass's actual native product composition."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

import httpx
from agentscope.message import Msg, TextBlock
from agentscope.middleware import MiddlewareBase
from agentscope.permission import PermissionBehavior, PermissionDecision, PermissionRule
from agentscope.tool import ToolResponse

from .manifest import static_request
from .telemetry import EvaluationTelemetry, tool_metrics

# Native orchestration stays available. Generic shell/file execution is excluded.
NATIVE_STATIC = frozenset({'TodoWrite', 'TaskCreate', 'TaskUpdate', 'TaskGet', 'TaskList',
    'TeamCreate', 'TeamDelete', 'TeamSay', 'TeamGet', 'TeamList', 'AgentCreate', 'AgentInvite',
    'AgentList', 'AnalysisReview', 'QueryAnalysisEvidence', 'ReadAnalysisEvidence',
    'QueryReaEvidence', 'ReadReaEvidence', 'SearchAnalysisTools', 'SearchReaTools', 'reset_tools',
    'read_skill', 'read_skill_resource', 'list_skills', 'inspect_file', 'extract_strings',
    'Skill', 'ReadSkill', 'ReadSkillResource', 'ListSkills', 'ConnectRea',
    'ListReaWorkflows', 'GetReaWorkflow', 'GetReaStatus', 'ReleaseReaSession', 'GenerateStructuredOutput'})
STATIC_REA = frozenset({
    'binary_session', 'open_binary', 'close_binary', 'binary_overview', 'search_procedures',
    'search_strings', 'procedure_pseudo_code', 'procedure_info', 'procedure_callers',
    'procedure_callees', 'xrefs', 'read_bytes', 'procedure_assembly', 'read_function_instructions',
    'inspect_native_instruction', 'inspect_native_data_type', 'address_to_file_offset',
    'procedure_references', 'resolve_containing_procedure', 'batch_decompile', 'get_call_graph',
    'find_xrefs_to_name', 'trace_native_values', 'list_imports', 'list_names', 'list_procedures',
})


def authorize_static_tools(state):
    """Express the requested static analysis through native scoped allow rules.

    No permission mode change, execution allowance or check_permissions bypass.
    Existing deny/ask rules and bypass-immune checks keep native precedence.
    """
    for name in STATIC_REA:
        tool_name = 'mcp__rea__' + name
        rules = state.permission_context.allow_rules.setdefault(tool_name, [])
        if not any(r.source == 'staticEvaluation' for r in rules):
            rules.append(PermissionRule(tool_name=tool_name, rule_content=None,
                                        behavior=PermissionBehavior.ALLOW, source='staticEvaluation'))


class StaticEvaluationGuard(MiddlewareBase):
    @staticmethod
    def allowed(name):
        return name in NATIVE_STATIC or (name.startswith('mcp__rea__') and name[10:] in STATIC_REA)

    async def on_reply(self, agent, input_kwargs, next_handler):
        authorize_static_tools(agent.state)
        async for item in next_handler(**input_kwargs):
            yield item

    async def on_model_call(self, agent, input_kwargs, next_handler):
        tools = [t for t in input_kwargs.get('tools') or [] if self.allowed(t.get('function', {}).get('name', t.get('name', '')))]
        return await next_handler(**{**input_kwargs, 'tools': tools})

    async def on_check_permission(self, agent, input_kwargs, next_handler):
        if not self.allowed(input_kwargs['tool_call'].name):
            return PermissionDecision(behavior=PermissionBehavior.DENY,
                message='Static evaluation excludes this tool. No execution authorization.')
        return await next_handler(**input_kwargs)

    async def on_acting(self, agent, input_kwargs, next_handler):
        if not self.allowed(input_kwargs['tool_call'].name):
            yield ToolResponse(state='error', content=[TextBlock(text='Static evaluation: tool excluded.')])
            return
        async for item in next_handler(**input_kwargs):
            yield item


def create_static_product_app(settings):
    from traceglass.api.app import create_app
    app = create_app(settings=settings)
    original = app.state.extra_agent_middlewares
    app.state.evaluation_telemetry = []
    async def guarded(user_id, agent_id, session_id, workspace):
        collector = EvaluationTelemetry()
        app.state.evaluation_telemetry.append(collector)
        return [StaticEvaluationGuard(), collector, *await original(user_id, agent_id, session_id, workspace)]
    # Native lifespan installs this factory in ChatService, including team workers.
    app.state.extra_agent_middlewares = guarded
    return app


def observable_messages(context):
    return [{'role': m.role, 'name': m.name, 'content': [b.model_dump(mode='json') for b in m.content
             if b.type in {'text', 'tool_call', 'tool_result'}]}
            for m in context if isinstance(m, Msg)]


async def run_product(settings, case, source, data_dir, timeout):
    """Fresh local evaluation app; retain native permission decisions and workers.

    Only a copied opaque object enters the workspace. No independent source or
    rubric is mounted. Native default permissions remain active; no bypass.
    Product conclusions remain ungraded unless the final text is valid Assessment
    JSON; orchestration state and member transcripts are separately recorded.
    """
    settings = replace(settings, data_dir=Path(data_dir), workspace_backend='local',
        legacy_project=None, mcp_config_path=None, ida_mcp_url=None, auth_enabled=False,
        skill_roots=(), strict_evidence_references=True)
    app = create_static_product_app(settings)
    payload = {'coverage': {}, 'transcript': [], 'model_usage': [], 'members': [],
               'permission_mode': 'default', 'target_executed': False}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            config = (await client.get('/product/config')).json()
            wid = (await client.post('/product/workspaces', json={'name': 'Static product evaluation'})).json()['workspace_id']
            response = await client.post(f'/product/workspaces/{wid}/sessions', json={
                'name': 'Static evaluation', 'chat_model_config': config['model_config'], 'permission_mode': 'default'})
            response.raise_for_status()
            sid, aid = response.json()['session_id'], response.json()['agent_id']
            workspace = await app.state.workspace_manager.get_workspace(settings.user_id, aid, sid, wid)
            target = Path(workspace.workdir) / ('sample' + source.suffix)
            shutil.copyfile(source, target)
            target.chmod(0o444)
            sha = hashlib.sha256(target.read_bytes()).hexdigest()
            if sha != case['sample_sha256']:
                raise ValueError('Sample hash changed before product analysis')
            request = static_request(case, '/workspace/' + target.name) + (
                f' Native workspace-relative input path: {target.name}. Native tasks/team are available '
                'when independent work is justified. Return the final assessment as one JSON object '
                'with verdict, evidence_refs, dataflow, authorization, privilege, gaps, next_steps, explanation, '
                'vuln_classes, exploitability and facts.')
            from traceglass.tools.review import REVIEW_KEY
            try:
                async with asyncio.timeout(timeout) as budget:
                    await app.state.chat_service.run(user_id=settings.user_id, agent_id=aid, session_id=sid,
                        input_msg=Msg(name='user', role='user', content=[TextBlock(text=request)]))
                    # Workers run via native message bus. Do not discard their cost/history.
                    while True:
                        runtime = (await client.get('/product/runtime', params={'workspace_id': wid})).json()
                        if not runtime.get('sessions') and not runtime.get('members'):
                            break
                        # Runtime member shape is status records; completed members can remain visible.
                        active = {'running', 'preparing', 'busy'}
                        member_states = [v.get('status') if isinstance(v, dict) else v for v in runtime.get('members', {}).values()]
                        if not runtime.get('sessions') and not any(v in active for v in member_states):
                            break
                        await asyncio.sleep(0.1)
                if budget.expired():
                    raise TimeoutError
                payload['status'] = 'completed'
            except TimeoutError:
                payload['status'] = 'timeout'
            session = await app.state.storage.get_session(settings.user_id, aid, sid)
            payload['coverage'] = session.state.middle_context.get(REVIEW_KEY, {})
            payload['transcript'] = observable_messages(session.state.context)
            payload['tasks'] = session.state.tasks_context.model_dump(mode='json')
            if session.team_id:
                team = await app.state.storage.get_team(settings.user_id, session.team_id)
                for member in team.data.members:
                    child = await app.state.storage.get_session(settings.user_id, member.agent_id, member.session_id)
                    if child:
                        payload['members'].append(dict(agent_id=member.agent_id, session_id=member.session_id,
                            transcript=observable_messages(child.state.context)))
                        payload['model_usage'].extend(m.usage.model_dump(mode='json') for m in child.state.context if isinstance(m, Msg) and m.usage)
            payload['model_usage'].extend(m.usage.model_dump(mode='json') for m in session.state.context if isinstance(m, Msg) and m.usage)
            payload.update(workspace_id=wid, session_id=sid, agent_id=aid,
                           native_team_created=bool(session.team_id), native_member_count=len(payload['members']))
    payload['container_metrics'] = dict(starts=app.state.rea_sessions.starts,
        connection_seconds=app.state.rea_sessions.connection_seconds, scope='REA connection occupancy only')
    events = []
    for index, collector in enumerate(app.state.evaluation_telemetry):
        for event in collector.events:
            events.append({'content':[{**block, 'id':f"{index}:{block['id']}"} for block in event['content']]})
    payload['tool_metrics'] = tool_metrics(events)
    payload['model_metrics'] = dict(calls=sum(c.model_calls for c in app.state.evaluation_telemetry),
        failures=sum(c.model_failures for c in app.state.evaluation_telemetry), provider_internal_retries=None,
        cancelled=sum(c.model_cancelled for c in app.state.evaluation_telemetry),
        provider_api_attempts=sum(c.api_attempts for c in app.state.evaluation_telemetry),
        scope='all native main/worker middleware model invocations; provider-internal retries unavailable')
    payload['model_usage'] = [usage for c in app.state.evaluation_telemetry for usage in c.model_usage]
    payload['assembled_prompt_hashes'] = sorted({h for c in app.state.evaluation_telemetry for h in c.prompt_hashes})
    payload['exposed_tool_schema_hashes'] = sorted({h for c in app.state.evaluation_telemetry for h in c.schema_hashes})
    return payload, app.state.rea_evidence
