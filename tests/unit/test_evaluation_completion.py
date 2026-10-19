"""Evaluation consumer boundaries: grading, answer isolation, safety and cost."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from traceglass.evaluation.scoring import score_binary


def assessment(**changes):
    return dict(verdict='vulnerable', evidence_refs=['captured'], dataflow='packet to copy',
                authorization='synthetic fixture', privilege='unknown runtime', gaps=['dynamic untested'],
                next_steps=[], explanation='unchecked copy', vuln_classes=['stack_overflow'],
                exploitability='unknown', **changes)


def test_binary_rubric_rejects_missing_class_and_empty_scope_fields():
    case = {'rubric': {'allowed_verdicts': ['vulnerable'],
                      'required_vuln_classes': ['stack_overflow'], 'requires_gaps': True}}
    output = assessment()
    output.update(vuln_classes=[], dataflow='', gaps=[])
    result = score_binary(case, output, {'captured'})
    assert {'missing_vulnerability_class', 'missing_dataflow', 'missing_coverage_gaps'} <= set(result['failures'])


def test_manifest_checks_binary_hash_and_keeps_answer_out_of_request(tmp_path):
    from traceglass.evaluation.manifest import load_manifest, static_request
    binary = tmp_path / 'opaque.o'
    binary.write_bytes(b'ELF fixture')
    secret = 'PRIVATE_RUBRIC_CANARY'
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'id': 'sample', 'binary': 'opaque.o',
        'sample_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'analysis_goal': 'vulnerability', 'task': 'Assess one packet handler.',
        'rubric': {'allowed_verdicts': ['vulnerable'], 'private_note': secret}}))
    case, path = load_manifest(manifest)
    request = static_request(case, '/workspace/sample.o')
    assert path == binary
    assert 'Assess one packet handler.' in request and 'vulnerability' in request
    assert secret not in request and 'allowed_verdicts' not in request and str(tmp_path) not in request
    binary.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='hash'):
        load_manifest(manifest)


def test_archive_grading_rejects_wrong_sample_and_requires_successful_strict_review(tmp_path):
    from traceglass.evaluation.manifest import grade_static
    from traceglass.evidence.store import EvidenceStore
    store = EvidenceStore(tmp_path / 'evidence.sqlite3')
    sha = 'a' * 64
    receipt = store.capture_call(user_id='eval', workspace_id='w', session_id='s', agent_id='a',
        connection_id='c', call_id='call', tool_name='inspect_file', arguments={}, raw='unchecked copy',
        state='success', sample_sha256=sha)
    output = assessment()
    output['evidence_refs'] = [receipt['id']]
    case = {'sample_sha256': sha, 'rubric': {'allowed_verdicts': ['vulnerable']}}
    review = {sha: {'reference_mode': 'strict', 'review_passed': True, 'references_passed': True,
                   'proposed_verdict': 'vulnerable', 'recommended_verdict': 'vulnerable'}}
    assert grade_static(case, output, store, 'eval', 'w', review)['passed']
    case['sample_sha256'] = 'b' * 64
    bad = grade_static(case, output, store, 'eval', 'w', review)
    assert not bad['passed'] and bad['unsupported_citation_count'] == 1
    case['sample_sha256'] = sha
    assert 'strict_review_not_passed' in grade_static(case, output, store, 'eval', 'w', {})['failures']
    review[sha].update(proposed_verdict='unknown', recommended_verdict='unknown')
    mismatch = grade_static(case, output, store, 'eval', 'w', review)
    assert not mismatch['passed'] and 'assessment_review_verdict_mismatch' in mismatch['failures']


def test_product_grades_only_final_assistant_answer_and_honors_retraction():
    from traceglass.evaluation.__main__ import _assessment_from_product
    output = json.dumps(assessment())
    earlier = {'role': 'assistant', 'content': [{'type': 'text', 'text': output}]}
    final = {'role': 'assistant', 'content': [{'type': 'text', 'text': 'I retract that assessment; evidence is incomplete.'}]}
    assert _assessment_from_product([earlier, final]) is None
    final['content'] = [{'type': 'text', 'text': '```json\n' + output + '\n```'}]
    assert _assessment_from_product([earlier, final])['verdict'] == 'vulnerable'
    native_reply = {'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'I will inspect the fixture.'},
        {'type': 'tool_call', 'name': 'inspect_file'}, {'type': 'tool_result', 'name': 'inspect_file'},
        {'type': 'text', 'text': output}]}
    assert _assessment_from_product([native_reply])['verdict'] == 'vulnerable'


def test_tool_costs_count_error_states_and_retries_from_actual_results():
    from traceglass.evaluation.telemetry import tool_metrics, token_metrics, skill_fingerprint
    messages = [{'content': [{'type': 'tool_call', 'id': '1', 'name': 'inspect_file', 'input': '{"path":"x"}'},
        {'type': 'tool_result', 'id': '1', 'name': 'inspect_file', 'state': 'error'}]},
        {'content': [{'type': 'tool_call', 'id': '2', 'name': 'inspect_file', 'input': '{"path":"x"}'},
        {'type': 'tool_result', 'id': '2', 'name': 'inspect_file', 'state': 'success'}]}]
    metrics = tool_metrics(messages)
    assert metrics['calls'] == 2 and metrics['failures'] == 1 and metrics['repeated_calls'] == 1
    assert metrics['failure_rate'] == 0.5
    assert token_metrics([{'input_tokens': 10, 'output_tokens': 4}])['total_tokens'] == 14


def test_skill_references_change_package_fingerprint(tmp_path):
    from traceglass.evaluation.telemetry import skill_fingerprint
    (tmp_path / 'SKILL.md').write_text('Read references/rules.md')
    (tmp_path / 'references').mkdir()
    rules = tmp_path / 'references/rules.md'
    rules.write_text('before')
    before = skill_fingerprint(tmp_path)
    rules.write_text('after')
    assert before != skill_fingerprint(tmp_path)


def test_comparison_keeps_timeout_separate_and_flags_parameters(tmp_path):
    from traceglass.evaluation.compare import summarize, compare
    before, after = tmp_path / 'before', tmp_path / 'after'
    before.mkdir(); after.mkdir()
    common = dict(case='x', model='m', max_iters=5, timeout=20, sample_sha256='sha',
                  case_sha256='case', elapsed_seconds=2, evaluation_type='binary-static',
                  tool_metrics={'calls': 4, 'failures': 1}, model_usage=[{'input_tokens': 10, 'output_tokens': 5}])
    (before / 'run.json').write_text(json.dumps({**common, 'status':'timeout', 'model_parameters': {'temperature': 0}}))
    (after / 'run.json').write_text(json.dumps({**common, 'status':'completed', 'score': {'passed':None}, 'model_parameters': {'temperature': 1}}))
    summary = summarize(before)['cases']['x']
    assert summary['timeout'] == 1 and summary['invalid'] == 0 and summary['median_total_tokens'] == 15
    result = compare(before, after)
    assert any('model_parameters' in w for w in result['warnings'])


def test_fixture_builder_compiles_four_matched_static_objects(tmp_path):
    import shutil
    import subprocess
    compiler = shutil.which('clang')
    if not compiler:
        pytest.skip('clang unavailable')
    completed = subprocess.run(['.venv/bin/python', 'scripts/build_static_fixtures.py', '--output', str(tmp_path)],
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    manifests = sorted(tmp_path.glob('*.json'))
    assert len(manifests) == 4
    for manifest in manifests:
        data = json.loads(manifest.read_text())
        binary = tmp_path / data['binary']
        assert binary.read_bytes()[:4] == b'\x7fELF'
        assert data['sample_sha256'] == hashlib.sha256(binary.read_bytes()).hexdigest()
        assert '-c' in data['build']['command'] and data['build']['target'] == 'x86_64-linux-gnu'
        assert data['source_sha256'] and data['rubric']['required_facts']


def test_product_static_guard_blocks_execution_before_handler_and_permits_native_task():
    from agentscope.message import ToolCallBlock
    from agentscope.tool import ToolResponse
    from traceglass.evaluation.product import StaticEvaluationGuard
    called = []
    async def handler(**kwargs):
        called.append(kwargs['tool_call'].name)
        yield ToolResponse(content=[], state='success')
    async def run():
        guard = StaticEvaluationGuard()
        for name in ['Bash', 'mcp__rea__run_binary', 'TodoWrite']:
            responses = [r async for r in guard.on_acting(None, {'tool_call': ToolCallBlock(id=name, name=name, input='{}')}, handler)]
            assert responses[0].state == ('success' if name == 'TodoWrite' else 'error')
    asyncio.run(run())
    assert called == ['TodoWrite']


def test_binary_runner_grades_captured_static_evidence_and_hides_independent_rubric(tmp_path, monkeypatch):
    from agentscope.model import ChatResponse, OpenAIChatModel
    from agentscope.message import ToolCallBlock, ToolResultBlock, TextBlock
    from agentscope.tool import FunctionTool, ToolChunk
    from traceglass.evaluation.__main__ import run_one
    from traceglass.config import Settings
    from traceglass.integrations.rea import ReaSessions
    source = tmp_path / 'fixture.o'
    source.write_bytes(b'ELF fixture')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    async def overview():
        return ToolChunk(content=[TextBlock(text=json.dumps({'evidence_id': 'ev_' + 'c' * 64,
            'normalized_result': {'entry':'handle_request'}, 'provider': {'name':'synthetic-static'},
            'operation':'binary_overview', 'subject': {'digest': {'sha256':sha}}}))])
    async def tools(self, *args):
        return [FunctionTool(overview, name='mcp__rea__binary_overview', is_read_only=True)]
    monkeypatch.setattr(ReaSessions, 'tools', tools)
    async def provider(self, model_name, messages, tools=None, **kwargs):
        combined = '\n'.join(m.get_text_content() or '' for m in messages)
        assert 'PRIVATE_RUBRIC_CANARY' not in combined
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        if not results:
            return ChatResponse(content=[ToolCallBlock(id='static', name='mcp__rea__binary_overview', input='{}')], is_last=True)
        receipt = json.loads(results[0].output[-1].text)['traceglass_evidence'][0]['id']
        if len(results) == 1:
            params = dict(sample_sha256=sha, verdict='unknown', scope='object only', paths=[dict(
                id='entry', source='handle_request', dispatch='unknown', sink='unknown',
                authorization='fixture', privilege='unknown', status='blocked',
                evidence=[dict(reference='handle_request', evidence_id=receipt, method='decompilation')])])
            return ChatResponse(content=[ToolCallBlock(id='review', name='AnalysisReview', input=json.dumps(params))], is_last=True)
        output = assessment()
        output.update(verdict='unknown', evidence_refs=[receipt], facts=[dict(category='entry',
            statement='handle_request entry', evidence_refs=[receipt])])
        return ChatResponse(content=[ToolCallBlock(id='answer', name='GenerateStructuredOutput', input=json.dumps(output))], is_last=True)
    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)
    case = dict(id='synthetic', analysis_goal='vulnerability', task='Assess object', sample_sha256=sha,
        rubric={'allowed_verdicts':['unknown'], 'private_note':'PRIVATE_RUBRIC_CANARY',
                'required_facts':[{'category':'entry', 'pattern':'handle_request'}]})
    args = SimpleNamespace(variant='candidate', binary=source, product=False, max_iters=5, timeout=20,
                           analysis_goal='behavior', cache_condition='cold', concurrency=1)
    output = tmp_path / 'run'; output.mkdir()
    record = asyncio.run(run_one(args, Settings(model_name='fixture', api_key='fixture', rea_kit=tmp_path), case, 1, output))
    assert record['status'] == 'completed', record
    assert record['score']['passed'] and record['score']['key_fact_recall'] == 1
    assert record['tool_metrics']['calls'] == 3 and record['container_metrics']['starts'] == 0


def test_actual_product_evaluation_preserves_native_team_and_blocks_shell(tmp_path, monkeypatch):
    from agentscope.model import ChatResponse, OpenAIChatModel
    from agentscope.message import TextBlock, ToolCallBlock, ToolResultBlock
    from traceglass.config import Settings
    from traceglass.evaluation.product import run_product
    for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
        monkeypatch.delenv(key, raising=False)
    async def provider(self, model_name, messages, tools=None, **kwargs):
        offered = {t['function']['name'] for t in tools or []}
        assert 'Bash' not in offered
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        worker = '你是团队' in messages[0].get_text_content()
        script = [('inspect_file', {'path':'sample.o'}), ('TeamSay', {'content':'static metadata verified'})] if worker else [
            ('TodoWrite', {'todos':[{'subject':'Verify metadata','description':'Read sample.o and return hash'}]}),
            ('TeamCreate', {'name':'Static verification','description':'Independent hash review'}),
            ('AgentCreate', {'name':'verifier','description':'independent metadata reader',
                             'prompt':'task_id=1\nRead sample.o metadata and report to leader.', 'subagent_type':'default'})]
        if len(results) < len(script):
            name, arguments = script[len(results)]
            return ChatResponse(content=[ToolCallBlock(id=f'call-{len(results)}', name=name, input=json.dumps(arguments))], is_last=True)
        return ChatResponse(content=[TextBlock(text='WORKER_DONE' if worker else 'LEADER_DONE')], is_last=True)
    monkeypatch.setattr(OpenAIChatModel, '_call_api', provider)
    source = tmp_path / 'sample.o'; source.write_bytes(b'synthetic object bytes')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    case = dict(id='fixture', task='Evaluate metadata', analysis_goal='behavior', sample_sha256=sha,
                rubric={'allowed_verdicts':['unknown']})
    payload, store = asyncio.run(run_product(Settings(model_name='fixture', api_key='fixture'), case, source,
                                           tmp_path / 'product', 20))
    assert payload['status'] == 'completed'
    assert payload['permission_mode'] == 'default'
    assert payload['native_team_created'] and payload['native_member_count'] == 1
    worker_results = [b for m in payload['members'][0]['transcript'] for b in m['content'] if b['type']=='tool_result']
    assert any(b['name'] == 'inspect_file' and b['state'] == 'success' for b in worker_results)
    records = store.list('local', payload['workspace_id'])['items']
    assert any(r['tool_name'] == 'inspect_file' and r['sample_sha256'] == sha for r in records)


def test_runtime_cost_collector_keeps_calls_outside_compressed_history():
    from agentscope.message import ToolCallBlock
    from agentscope.tool import ToolResponse
    from traceglass.evaluation.telemetry import EvaluationTelemetry
    async def run():
        collector = EvaluationTelemetry()
        async def model(**kwargs):
            raise RuntimeError('synthetic model unavailable')
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await collector.on_model_call(None, {}, model)
        async def tool(**kwargs):
            yield ToolResponse(state='error', content=[])
        for identity in ['first', 'retry']:
            async for _ in collector.on_acting(None, {'tool_call': ToolCallBlock(
                id=identity, name='static', input='{}')}, tool):
                pass
        metrics = collector.snapshot()
        assert metrics['tool_metrics']['calls'] == 2 and metrics['tool_metrics']['failures'] == 2
        assert metrics['tool_metrics']['repeated_calls'] == 1
        assert metrics['model_metrics']['calls'] == 2 and metrics['model_metrics']['failures'] == 2
        assert metrics['model_metrics']['provider_internal_retries'] is None
    asyncio.run(run())


def test_token_telemetry_survives_context_replacement_and_observes_direct_summary_calls():
    from agentscope.model import ChatResponse, ChatUsage
    from agentscope.message import TextBlock
    from traceglass.evaluation.telemetry import EvaluationTelemetry
    class Model:
        async def _call_api(self, *args, **kwargs):
            return ChatResponse(content=[TextBlock(text='synthetic')], is_last=True,
                                usage=ChatUsage(input_tokens=10, output_tokens=4, time=0))
    async def run():
        collector = EvaluationTelemetry()
        model = Model()
        original = model._call_api
        agent = SimpleNamespace(model=model)
        async def reply(**kwargs):
            # Direct calls mirror native compression's route around on_model_call.
            await model._call_api('model', messages=[])
            await model._call_api('model', messages=[])
            yield None
        async for _ in collector.on_reply(agent, {}, reply):
            pass
        assert model._call_api == original
        metrics = collector.snapshot()
        assert metrics['token_metrics']['total_tokens'] == 28
        assert metrics['model_metrics']['provider_api_attempts'] == 2
        assert len(metrics['model_usage']) == 2
    asyncio.run(run())


def test_native_interrupted_reply_after_deadline_is_timeout_not_invalid(tmp_path, monkeypatch):
    from agentscope.model import OpenAIChatModel
    from traceglass.evaluation.__main__ import run_one
    from traceglass.config import Settings
    async def never_returns(self, *args, **kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(OpenAIChatModel, '_call_api', never_returns)
    args = SimpleNamespace(variant='candidate', binary=None, product=False, max_iters=3, timeout=0.03,
                           analysis_goal='behavior', cache_condition='unspecified', concurrency=1)
    case = dict(id='deadline', task='Assess supplied bytes only', sample_sha256='a'*64,
                evidence=[{'id':'E1', 'text':'No behavior known'}], rubric={'allowed_verdicts':['unknown']})
    record = asyncio.run(run_one(args, Settings(model_name='fixture', api_key='fixture'), case, 1, tmp_path))
    assert record['status'] == 'timeout' and record['error_type'] == 'TimeoutError'
    assert 'vuln-discovery' in record['skill_hashes']


def test_static_guard_keeps_native_skill_viewer_available():
    from traceglass.evaluation.product import StaticEvaluationGuard
    assert StaticEvaluationGuard.allowed('Skill')
    assert StaticEvaluationGuard.allowed('ReadSkillResource')


def test_static_authorization_uses_native_rules_without_bypass_or_execution_allowance():
    from agentscope.permission import PermissionDecision, PermissionBehavior, PermissionMode
    from agentscope.permission._engine import PermissionEngine
    from agentscope.state import AgentState
    from agentscope.tool import FunctionTool
    from traceglass.evaluation.product import authorize_static_tools
    async def operation():
        pass
    async def requires_confirmation(tool_input, context):
        return PermissionDecision(PermissionBehavior.ASK, 'static MCP confirmation')
    tool = FunctionTool(operation, name='mcp__rea__open_binary', is_read_only=False)
    tool.check_permissions = requires_confirmation
    state = AgentState()
    async def run():
        engine = PermissionEngine(state.permission_context)
        assert (await engine.check_permission(tool, {})).behavior == PermissionBehavior.ASK
        authorize_static_tools(state)
        assert state.permission_context.mode == PermissionMode.DEFAULT
        assert 'Bash' not in state.permission_context.allow_rules
        assert 'mcp__rea__run_binary' not in state.permission_context.allow_rules
        assert (await engine.check_permission(tool, {})).behavior == PermissionBehavior.ALLOW
    asyncio.run(run())


def test_product_normalized_cancellation_still_reports_timeout(tmp_path, monkeypatch):
    from agentscope.model import OpenAIChatModel
    from traceglass.config import Settings
    from traceglass.evaluation.product import run_product
    for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
        monkeypatch.delenv(key, raising=False)
    async def wait_forever(self, *args, **kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(OpenAIChatModel, '_call_api', wait_forever)
    source = tmp_path / 'sample.o'; source.write_bytes(b'synthetic')
    case = dict(id='timeout', task='Static only', analysis_goal='behavior',
                sample_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    payload, _ = asyncio.run(run_product(Settings(model_name='fixture', api_key='fixture'), case, source,
                                        tmp_path / 'product', 0.05))
    assert payload['status'] == 'timeout'
