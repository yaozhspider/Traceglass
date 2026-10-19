"""Static/replay/product evaluation: never executes an input sample."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
from uuid import uuid4

import httpx
from agentscope.agent import Agent, ReActConfig
from agentscope.credential import OpenAICredential
from agentscope.message import Msg, TextBlock
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit

from traceglass.agents import ANALYST_PROMPT, INTEGRATED_PROMPT_V3
from traceglass.analysis_policy import AnalysisQualityMiddleware, POLICY_VERSION
from traceglass.config import Settings
from traceglass.evidence.store import EvidenceStore
from traceglass.evaluation.manifest import grade_static, load_manifest, static_request
from traceglass.evaluation.product import STATIC_REA, StaticEvaluationGuard, observable_messages, run_product
from traceglass.evaluation.scoring import Assessment, replay_schema, score
from traceglass.evaluation.telemetry import EvaluationTelemetry, fingerprints, skill_fingerprint, token_metrics, tool_metrics
from traceglass.integrations.evidence_ledger import EvidenceLedgerMiddleware
from traceglass.integrations.rea import ReaSessions
from traceglass.tools.rea_evidence import evidence_tools
from traceglass.tools.review import AnalysisReview, REVIEW_KEY
import traceglass.tools.review as review_module

SKILL_ROOT = Path(__file__).parents[1] / 'resources/skills'
SKILL = SKILL_ROOT / 'behavior-chain-review'


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def public_transcript(agent):
    return observable_messages(agent.state.context)


def _assessment_from_product(transcript):
    message = next((m for m in reversed(transcript) if m['role'] == 'assistant'), None)
    if message is None:
        return None
    content = message['content']
    # One native AssistantMsg can contain every tool iteration in a reply.
    # The final answer is the text suffix after the last tool boundary.
    boundary = max((i for i, b in enumerate(content) if b.get('type') in {'tool_call', 'tool_result'}), default=-1)
    text = '\n'.join(b['text'] for b in content[boundary + 1:] if b.get('type') == 'text').strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    try:
        return Assessment.model_validate_json(text).model_dump(mode='json')
    except ValueError:
        return None


async def run_one(args, settings, case, repetition, out):
    started = time.monotonic()
    binary = args.binary
    product = getattr(args, 'product', False)
    prompt = INTEGRATED_PROMPT_V3 if args.variant == 'baseline' else ANALYST_PROMPT
    if not product:
        prompt += '\n本次是独立静态复核评测，不创建团队。只使用实际提供的证据和工具；不执行样本，不虚构调用。'
    goal = case.get('analysis_goal', getattr(args, 'analysis_goal', 'behavior'))
    skills = [SKILL, SKILL_ROOT / 'vuln-discovery', SKILL_ROOT / 'vulnerability-hunting',
              SKILL_ROOT / 'unpacking', SKILL_ROOT / 'sandbox-dynamic-debugging']
    if product:
        skills = sorted(p.parent for p in SKILL_ROOT.rglob('SKILL.md'))
    record = dict(case=case['id'], variant=args.variant, repetition=repetition,
        policy_version=POLICY_VERSION if args.variant == 'candidate' else 'shipped-v3',
        prompt_sha256=digest(prompt), skill_sha256=skill_fingerprint(SKILL) if args.variant == 'candidate' else None,
        skill_hashes={p.name: skill_fingerprint(p) for p in skills} if args.variant == 'candidate' else {},
        analysis_goal=goal, case_sha256=digest(json.dumps(case, sort_keys=True, ensure_ascii=False)),
        review_implementation_sha256=digest(Path(review_module.__file__).read_text()),
        assessment_schema_sha256=digest(json.dumps((Assessment if binary else replay_schema(case)).model_json_schema(), sort_keys=True)),
        model=settings.model_name, model_parameters=settings.model_parameters, max_iters=args.max_iters,
        timeout=args.timeout, concurrency=getattr(args, 'concurrency', 1),
        cache_condition=getattr(args, 'cache_condition', 'unspecified'), target_executed=False,
        evaluation_type='product-static' if product else 'binary-static' if binary else 'evidence-replay',
        model_retry_budget=3 if product else 1, agent_model_retry_budget=0, reference_mode='strict' if binary else 'replay-compatible',
        **fingerprints(settings.rea_kit))
    pool = ReaSessions(settings.rea_kit, max_connections=settings.rea_max_connections,
        idle_seconds=settings.rea_idle_seconds, quota_wait_seconds=settings.rea_queue_wait_seconds)
    agent, model = None, None
    session_id, workspace_id = uuid4().hex, uuid4().hex
    user_id, agent_id = 'eval', 'eval'
    coverage, transcript, usage = {}, [], []
    telemetry = EvaluationTelemetry()
    store = EvidenceStore(out / f"{case['id']}-{args.variant}-{repetition}-evidence.sqlite3")
    try:
        if product:
            from dataclasses import replace
            payload, store = await run_product(replace(settings, max_iters=args.max_iters), case, binary.resolve(strict=True),
                out / f"{case['id']}-{repetition}-product", args.timeout)
            record.update({k: v for k, v in payload.items() if k not in {'coverage', 'transcript', 'model_usage'}})
            coverage, transcript, usage = payload['coverage'], payload['transcript'], payload['model_usage']
            user_id, workspace_id = settings.user_id, payload['workspace_id']
            assessment = _assessment_from_product(transcript)
            if record['status'] == 'completed':
                if assessment is None:
                    record.update(status='ungraded', score={'passed': None, 'scoring_scope':'product final response is not Assessment JSON'})
                else:
                    record.update(assessment=assessment, score=grade_static(case, assessment, store, user_id, workspace_id, coverage))
        else:
            model = OpenAIChatModel(credential=OpenAICredential(api_key=settings.api_key, base_url=settings.base_url),
                model=settings.model_name, parameters=OpenAIChatModel.Parameters.model_validate(settings.model_parameters),
                stream=False, max_retries=1, client_kwargs={'http_client':httpx.AsyncClient(trust_env=False)})
            review = AnalysisReview(store=store if binary else None, user_id=user_id, workspace_id=workspace_id,
                                    strict_references=bool(binary))
            tools = [review] if args.variant == 'candidate' else []
            if binary:
                tools += evidence_tools(store, user_id, workspace_id, analysis=True)
            toolkit = Toolkit(tools=tools, skills_or_loaders=[str(p) for p in skills] if args.variant == 'candidate' else [])
            middlewares = [telemetry] + ([AnalysisQualityMiddleware()] if args.variant == 'candidate' else [])
            with tempfile.TemporaryDirectory(prefix='traceglass-eval-') as temp:
                if binary:
                    source = binary.resolve(strict=True)
                    target = Path(temp) / ('sample' + source.suffix)
                    shutil.copyfile(source, target)
                    target.chmod(0o444)
                    sha = hashlib.sha256(target.read_bytes()).hexdigest()
                    if case.get('sample_sha256') and sha != case['sample_sha256']:
                        raise ValueError('Sample hash changed before static analysis')
                    case = {**case, 'sample_sha256':sha, 'analysis_goal':goal}
                    record['sample_sha256'] = sha
                    middlewares = [StaticEvaluationGuard(), EvidenceLedgerMiddleware(store, user_id, workspace_id,
                        session_id, agent_id, lambda: pool.connection_id(Path(temp), user_id, session_id),
                        workspace_root=Path(temp)), *middlewares]
                    request = static_request(case, '/workspace/' + target.name)
                else:
                    request = json.dumps({k:case[k] for k in ('task', 'sample_sha256', 'evidence')}, ensure_ascii=False)
                agent = Agent(name='Traceglass evaluation', system_prompt=prompt, model=model, toolkit=toolkit,
                    middlewares=middlewares, react_config=ReActConfig(max_iters=args.max_iters))
                async with asyncio.timeout(args.timeout) as budget:
                    if binary:
                        static_tools = [t for t in await pool.tools(Path(temp), user_id, session_id, workspace_id)
                                        if t.name.removeprefix('mcp__rea__') in STATIC_REA]
                        if not static_tools:
                            raise RuntimeError('REA static tools unavailable')
                        await toolkit.add_tool(static_tools)
                    reply = await agent.reply(Msg(name='user', role='user', content=[TextBlock(text=request)]),
                                              structured_schema=Assessment if binary else replay_schema(case))
                if budget.expired():
                    # Native reply intentionally normalizes cancellation to an
                    # interrupted reply. Preserve the evaluator deadline status.
                    raise TimeoutError
                assessment = reply.structured_output
                record['finished_reason'] = reply.finished_reason
                if hasattr(assessment, 'model_dump'):
                    assessment = assessment.model_dump(mode='json')
                if not isinstance(assessment, dict):
                    raise ValueError('Model did not return the required assessment schema')
                assessment = Assessment.model_validate(assessment).model_dump(mode='json')
                coverage = agent.state.middle_context.get(REVIEW_KEY, {})
                if binary and 'rubric' not in case:
                    record.update(status='ungraded', assessment=assessment,
                        score={'passed':None, 'scoring_scope':'No independent rubric; static evidence retained for human review'})
                else:
                    result = grade_static(case, assessment, store, user_id, workspace_id, coverage,
                        require_review=args.variant == 'candidate') if binary else score(case, assessment)
                    record.update(status='completed', assessment=assessment, score=result)
                if args.variant == 'candidate' and not binary and not coverage:
                    record['score'].update(passed=False, failures=[*record['score']['failures'], 'analysis_review_not_called'])
    except TimeoutError:
        record.update(status='timeout', error_type='TimeoutError')
    except Exception as exc:
        # Provider exception bodies can contain request URLs/headers; retain type only.
        import traceback
        record.update(status='invalid', error_type=type(exc).__name__,
            error_frames=[dict(file=Path(f.filename).name, function=f.name, line=f.lineno)
                          for f in traceback.extract_tb(exc.__traceback__)][-8:])
    finally:
        await pool.close()
        if model:
            await model.client.close()
        if agent:
            coverage = agent.state.middle_context.get(REVIEW_KEY, {})
            transcript = public_transcript(agent)
            usage = [m.usage.model_dump(mode='json') if hasattr(m.usage, 'model_dump') else m.usage
                     for m in agent.state.context if isinstance(m, Msg) and m.usage]
    record.update(elapsed_seconds=round(time.monotonic() - started, 2), coverage=coverage,
                  transcript=transcript, model_usage=usage, token_metrics=token_metrics(usage))
    all_transcripts = transcript + [message for member in record.get('members', []) for message in member['transcript']]
    record['transcript_tool_metrics'] = tool_metrics(all_transcripts)
    if not product:
        record.update(telemetry.snapshot())
    else:
        record.setdefault('tool_metrics', tool_metrics(all_transcripts))
    record.setdefault('container_metrics', dict(starts=pool.starts, connection_seconds=round(pool.connection_seconds, 3),
                                               scope='REA connection occupancy only'))
    if binary:
        record.setdefault('sample_sha256', case.get('sample_sha256'))
    name = f"{case['id']}-{args.variant}-{repetition}.json"
    (out / name).write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({k:record.get(k) for k in ('case', 'variant', 'repetition', 'status', 'score', 'elapsed_seconds')}, ensure_ascii=False), flush=True)
    return record


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=Path('evals/cases'))
    parser.add_argument('--case', action='append', help='Case stem; repeat to select several')
    parser.add_argument('--variant', choices=['baseline', 'candidate'], default='candidate')
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--concurrency', type=int, default=2)
    parser.add_argument('--max-iters', type=int, default=12)
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--output', type=Path, required=True, help='New run directory; refuses overwrite')
    parser.add_argument('--analysis-goal', choices=['behavior', 'vulnerability'], default='behavior')
    parser.add_argument('--binary', type=Path, help='Unlabelled real binary; remains explicitly ungraded')
    parser.add_argument('--binary-manifest', type=Path, help='Evaluator-only manifest, sample hash and rubric; never mounted in agent workspace')
    parser.add_argument('--product', action='store_true', help='Use actual product/native task/team wiring with static guard and default permissions')
    parser.add_argument('--cache-condition', choices=['cold', 'warm', 'unspecified'], default='unspecified')
    args = parser.parse_args()
    if min(args.repetitions, args.concurrency, args.max_iters, args.timeout) < 1:
        parser.error('Budgets must be positive')
    if args.binary and args.binary_manifest:
        parser.error('Choose --binary or --binary-manifest')
    settings = Settings.from_env()
    if not settings.model_configured:
        parser.error('Configure model credentials before live evaluation')
    if args.binary_manifest:
        try:
            case, args.binary = load_manifest(args.binary_manifest)
        except (OSError, ValueError, KeyError) as exc:
            parser.error(f'Invalid binary manifest ({type(exc).__name__})')
        cases = [case]
    elif args.binary:
        cases = [dict(id='actual-binary-static', analysis_goal=args.analysis_goal,
                      task='Assess the scoped input-to-operation behavior. No independent ground truth is supplied.')]
    else:
        cases = [json.loads(p.read_text()) for p in sorted(args.suite.glob('*.json')) if not args.case or p.stem in args.case]
    if args.binary and not settings.rea_kit:
        parser.error('Binary static evaluation requires TRACEGLASS_REA_KIT')
    if args.product and (not args.binary_manifest or args.variant != 'candidate'):
        parser.error('Product evaluation requires a binary manifest and candidate variant')
    if not cases:
        parser.error('No selected cases')
    args.output.mkdir(parents=True, exist_ok=False)
    semaphore = asyncio.Semaphore(args.concurrency)
    async def limited(case, rep):
        async with semaphore:
            return await run_one(args, settings, case, rep, args.output)
    results = await asyncio.gather(*(limited(case, rep) for case in cases for rep in range(1, args.repetitions + 1)))
    summary = dict(runs=len(results), completed=sum(r['status'] == 'completed' for r in results),
        invalid=sum(r['status'] == 'invalid' for r in results), timeout=sum(r['status'] == 'timeout' for r in results),
        ungraded=sum(r['status'] == 'ungraded' for r in results),
        rubric_passes=sum(r.get('score', {}).get('passed') is True for r in results),
        rubric_failures=sum(r.get('score', {}).get('passed') is False for r in results), human_review_required=True)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))
    if summary['invalid'] or summary['timeout'] or summary['rubric_failures'] or summary['ungraded']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
