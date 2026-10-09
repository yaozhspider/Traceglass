"""Run: python -m traceglass.evaluation --help. Never executes an input sample."""
from __future__ import annotations

import argparse
import httpx
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
from uuid import uuid4

from agentscope.agent import Agent, ReActConfig
from agentscope.credential import OpenAICredential
from agentscope.message import Msg, TextBlock
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionMode
from agentscope.tool import Toolkit

from traceglass.agents import ANALYST_PROMPT, INTEGRATED_PROMPT_V3
from traceglass.analysis_policy import AnalysisQualityMiddleware, POLICY_VERSION
from traceglass.config import Settings
from traceglass.evaluation.scoring import Assessment, replay_schema, score
from traceglass.integrations.rea import ReaSessions
from traceglass.tools.review import AnalysisReview, REVIEW_KEY
import traceglass.tools.review as review_module

SKILL = Path(__file__).parents[1]/'resources/skills/behavior-chain-review'
STATIC_REA = frozenset({
    'binary_session','open_binary','close_binary','binary_overview','search_procedures',
    'search_strings','procedure_pseudo_code','procedure_info','procedure_callers',
    'procedure_callees','xrefs','read_bytes','procedure_assembly','read_function_instructions',
    'inspect_native_instruction','inspect_native_data_type','address_to_file_offset',
    'procedure_references','resolve_containing_procedure','batch_decompile','get_call_graph',
    'find_xrefs_to_name','trace_native_values','list_imports','list_names','list_procedures',
})


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def public_transcript(agent):
    # Exclude private reasoning and provider credentials; keep observable text,
    # calls and returned evidence for human scoring. Store locally by default.
    return [{'role':m.role, 'name':m.name,
             'content':[b.model_dump(mode='json') for b in m.content if b.type in {'text','tool_call','tool_result'}]}
            for m in agent.state.context if isinstance(m,Msg)]


async def run_one(args, settings, case, repetition, out):
    started = time.monotonic()
    prompt = INTEGRATED_PROMPT_V3 if args.variant=='baseline' else ANALYST_PROMPT
    # Same evaluation framing on both sides; the answer rubric is never passed.
    prompt += '\n本次是独立静态复核评测，不创建团队。只使用实际提供的证据和工具；不执行样本，不虚构调用。'
    model = OpenAIChatModel(credential=OpenAICredential(api_key=settings.api_key,base_url=settings.base_url),
        model=settings.model_name,parameters=OpenAIChatModel.Parameters.model_validate(settings.model_parameters),
        stream=False,max_retries=1,client_kwargs={'http_client':httpx.AsyncClient(trust_env=False)})
    toolkit = Toolkit(tools=[AnalysisReview()] if args.variant=='candidate' else [],
                      skills_or_loaders=[str(SKILL)] if args.variant=='candidate' else [])
    agent = Agent(name='Traceglass evaluation',system_prompt=prompt,model=model,toolkit=toolkit,
        middlewares=[AnalysisQualityMiddleware()] if args.variant=='candidate' else [],
        react_config=ReActConfig(max_iters=args.max_iters))
    agent.state.permission_context.mode = PermissionMode.BYPASS
    record = {'case':case['id'],'variant':args.variant,'repetition':repetition,
              'policy_version':POLICY_VERSION if args.variant=='candidate' else 'shipped-v3',
              'prompt_sha256':digest(prompt),'skill_sha256':digest((SKILL/'SKILL.md').read_text()) if args.variant=='candidate' else None,
              'case_sha256':digest(json.dumps(case,sort_keys=True,ensure_ascii=False)),
              'review_implementation_sha256':digest(Path(review_module.__file__).read_text()),
              'assessment_schema_sha256':digest(json.dumps(
                  (Assessment if args.binary else replay_schema(case)).model_json_schema(),sort_keys=True)),
              'model':settings.model_name,'model_parameters':settings.model_parameters,
              'max_iters':args.max_iters,'target_executed':False,
              'evaluation_type':'binary-static' if args.binary else 'evidence-replay'}
    pool = ReaSessions(settings.rea_kit)
    try:
        with tempfile.TemporaryDirectory(prefix='traceglass-eval-') as temp:
            if args.binary:
                source = args.binary.resolve(strict=True)
                target = Path(temp)/('sample'+source.suffix)
                shutil.copyfile(source,target)
                target.chmod(0o444)
                sha=hashlib.sha256(target.read_bytes()).hexdigest()
                record['sample_sha256']=sha
                tools = await pool.tools(Path(temp),'eval',uuid4().hex,'eval')
                tools = [t for t in tools if t.name.removeprefix('mcp__rea__') in STATIC_REA]
                if not tools:
                    raise RuntimeError('REA static tools unavailable')
                await toolkit.add_tool(tools)
                request = (f'独立静态分析 /workspace/{target.name}，SHA256={sha}。分析业务行为及是否存在远程代码执行，'
                    '给出可复核地址和调用/数据链、触发条件、授权和权限边界。禁止执行样本。'
                    '工具只暴露静态分析能力。必要时读取 behavior-chain-review；未知要保留。')
            else:
                request = json.dumps({k:case[k] for k in ('task','sample_sha256','evidence')},ensure_ascii=False)
            async with asyncio.timeout(args.timeout):
                reply = await agent.reply(Msg(name='user',role='user',content=[TextBlock(text=request)]),
                                          structured_schema=Assessment if args.binary else replay_schema(case))
            assessment = reply.structured_output
            record['finished_reason'] = reply.finished_reason
            if hasattr(assessment,'model_dump'):
                assessment=assessment.model_dump(mode='json')
            if not isinstance(assessment,dict):
                raise ValueError('Model did not return the required assessment schema')
            assessment = Assessment.model_validate(assessment).model_dump()
            record.update(status='completed',assessment=assessment,
                          score={'passed':None,'scoring_scope':'binary requires independent address/dataflow review'}
                          if args.binary else score(case,assessment))
    except Exception as exc:
        # Raw provider exceptions can contain request URLs or headers.
        record.update(status='invalid',error_type=type(exc).__name__)
    finally:
        await pool.close()
        await model.client.close()
    record['elapsed_seconds']=round(time.monotonic()-started,2)
    record['coverage']=agent.state.middle_context.get(REVIEW_KEY,{})
    record['model_usage']=[m.usage.model_dump(mode='json') if hasattr(m.usage,'model_dump') else m.usage
                           for m in agent.state.context if isinstance(m,Msg) and m.usage]
    record['transcript']=public_transcript(agent)
    if args.variant=='candidate' and record.get('status')=='completed' and not record['coverage']:
        record['score']={'passed':False,'failures':['analysis_review_not_called'],
                         'scoring_scope':'required review workflow missing'}
    name=f"{case['id']}-{args.variant}-{repetition}.json"
    (out/name).write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps({k:record.get(k) for k in ('case','variant','repetition','status','score','elapsed_seconds')},ensure_ascii=False),flush=True)
    return record


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,default=Path('evals/cases'))
    parser.add_argument('--case',action='append',help='Case stem; repeat to select several')
    parser.add_argument('--variant',choices=['baseline','candidate'],default='candidate')
    parser.add_argument('--repetitions',type=int,default=1)
    parser.add_argument('--concurrency',type=int,default=2)
    parser.add_argument('--max-iters',type=int,default=12)
    parser.add_argument('--timeout',type=int,default=240)
    parser.add_argument('--output',type=Path,required=True,help='New run directory; refuses overwrite')
    parser.add_argument('--binary',type=Path,help='Optional real binary, REA static tools only; separate from replay scores')
    args=parser.parse_args()
    if min(args.repetitions,args.concurrency,args.max_iters,args.timeout)<1:
        parser.error('Budgets must be positive')
    settings=Settings.from_env()
    if not settings.model_configured:
        parser.error('Configure model credentials before live evaluation')
    if args.binary and not settings.rea_kit:
        parser.error('Binary static evaluation requires TRACEGLASS_REA_KIT')
    cases=[json.loads(p.read_text()) for p in sorted(args.suite.glob('*.json')) if not args.case or p.stem in args.case]
    if args.binary:
        cases=[{'id':'actual-binary-static'}]
    if not cases:
        parser.error('No selected cases')
    args.output.mkdir(parents=True,exist_ok=False)
    semaphore=asyncio.Semaphore(args.concurrency)
    async def limited(case,rep):
        async with semaphore:
            return await run_one(args,settings,case,rep,args.output)
    results=await asyncio.gather(*(limited(case,rep) for case in cases for rep in range(1,args.repetitions+1)))
    summary={'runs':len(results),'completed':sum(r['status']=='completed' for r in results),
        'invalid':sum(r['status']=='invalid' for r in results),
        'rubric_passes':sum(r.get('score',{}).get('passed') is True for r in results),
        'rubric_failures':sum(r.get('score',{}).get('passed') is False for r in results),
        'human_review_required':True}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))
    if summary['invalid'] or summary['rubric_failures']:
        raise SystemExit(1)


if __name__=='__main__':
    asyncio.run(main())
