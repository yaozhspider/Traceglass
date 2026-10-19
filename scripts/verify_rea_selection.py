#!/usr/bin/env python3
"""Live-model selection smoke test with REA, reverse MCP and native workspace tools.

Uses a dedicated Docker workspace and a caller-supplied benign fixture (never
executes it). No routing hint is added to the user request. Test-only permissions
do not change any product session. Results exclude private reasoning and secrets.
"""
import argparse
import asyncio
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from agentscope.app.storage import SessionConfig
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.permission import PermissionMode

from traceglass.agents import AGENT_ID
from traceglass.api.app import create_app
from traceglass.config import Settings


async def verify(args):
    settings = Settings.from_env()
    if not settings.model_configured or not settings.rea_kit or settings.workspace_backend != 'docker':
        raise ValueError('Configure the model, Docker workspace and REA first')
    sample = args.sample.resolve(strict=True).read_bytes()
    args.output.mkdir(parents=True, exist_ok=False)
    # Disable shared IDA: this checks REA selection against reverse + Bash,
    # without taking over any user's host IDA state.
    settings = replace(settings, data_dir=args.output.resolve() / 'runtime', auth_enabled=False,
                       ida_mcp_url=None, mcp_config_path=None, max_iters=args.max_iters)
    app = create_app(settings=settings)
    report = {'model': settings.model_name, 'sample_sha256': hashlib.sha256(sample).hexdigest(),
              'target_executed_by_harness': False, 'status': 'incomplete',
              'scope': 'Single benign fixture; real REA, reverse MCP and native tools; shared IDA excluded.'}
    async with app.router.lifespan_context(app):
        storage = app.state.storage
        # Use the same configured native model adapter as product sessions.
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'X-User-ID': settings.user_id}) as client:
            config = (await client.get('/product/config')).json()['model_config']
            created = await client.post('/product/workspaces', json={'name': 'REA selection validation'})
            created.raise_for_status()
            wid = created.json()['workspace_id']
        session = await storage.upsert_session(settings.user_id, AGENT_ID,
            SessionConfig(workspace_id=wid, chat_model_config=config))
        session.state.permission_context.mode = PermissionMode.BYPASS
        await storage.upsert_session(settings.user_id, AGENT_ID, session.config,
                                     session_id=session.id, state=session.state)
        root = app.state.workspace_manager.host_root(settings.user_id, AGENT_ID, wid)
        (root / 'samples').mkdir(parents=True, exist_ok=True)
        target = root / 'samples' / ('fixture' + args.sample.suffix)
        target.write_bytes(sample)
        request = (f'只做静态分析 /workspace/samples/{target.name}：解释 toy_score 的计算公式和条件分支，'
                   '确认 main 到 toy_score 的调用关系，给出调用图、函数地址和原始证据。'
                   '这是一个小型独立任务，不创建团队，不执行样本，不安装工具，不需要判断恶意性。')
        report['request'] = request
        print('Live selection run started in an isolated workspace.', flush=True)
        try:
            async with asyncio.timeout(args.timeout):
                await app.state.chat_service.run(settings.user_id, session.id, AGENT_ID,
                    Msg(name='user', role='user', content=[TextBlock(text=request)]))
            report['status'] = 'returned'
        except Exception as exc:
            report.update(status='invalid', error_type=type(exc).__name__)
        messages, _ = await storage.list_messages(settings.user_id, session.id, limit=500)
        calls = [b for m in messages for b in m.content if isinstance(b, ToolCallBlock)]
        results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
        report['tool_calls'] = dict(Counter(b.name for b in calls))
        report['result_states'] = dict(Counter(b.state for b in results))
        report['successful_rea_tools'] = sorted({b.name for b in results
            if b.name.startswith('mcp__rea__') and b.state == 'success'})
        report['transcript'] = [{'role': m.role, 'content': [b.model_dump(mode='json') for b in m.content
            if b.type in {'text', 'tool_call', 'tool_result'}]} for m in messages]
        report['rea_status'] = app.state.rea_sessions.status(wid)
    (args.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'transcript'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', type=Path, required=True, help='Benign compiled toy_score/main fixture')
    parser.add_argument('--output', type=Path, required=True, help='New directory; refuses overwrite')
    parser.add_argument('--timeout', type=int, default=420)
    parser.add_argument('--max-iters', type=int, default=20)
    asyncio.run(verify(parser.parse_args()))
