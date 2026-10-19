#!/usr/bin/env python3
"""Exercise a real REA engine and archive with a user-supplied benign fixture.

No model is called and the sample is not executed. Use a dedicated test workspace.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from agentscope.message import ToolCallBlock
from agentscope.tool import ToolResponse

from traceglass.evidence.store import EvidenceStore
from traceglass.integrations.rea import ReaSessions
from traceglass.integrations.rea_evidence import ReaEvidenceMiddleware


async def verify(args):
    root = args.workspace.resolve(strict=True)
    sample = (root / args.sample).resolve(strict=True)
    relative = sample.relative_to(root)
    pool = ReaSessions(args.kit)
    session = 'validation-' + uuid4().hex
    store = EvidenceStore(args.archive)
    report = {'session_id': session, 'provider': args.provider, 'operations': {}}
    try:
        tools = {t.name.removeprefix('mcp__rea__'): t for t in
                 await pool.tools(root, args.user, session, args.workspace_id)}
        if not tools:
            raise RuntimeError('REA did not connect')
        report['tools'] = sorted(tools)
        workflows = {t.name: t for t in await pool.workflow_tools(root, args.user, session, args.workspace_id)}
        catalog = json.loads((await workflows['ListReaWorkflows']()).content[0].text)
        assert catalog['status'] == 'available', catalog
        report['workflows'] = catalog['workflows']
        rendered = await workflows['GetReaWorkflow'](name='investigate_feature', arguments={'feature': 'fixture main function'})
        report['rendered_workflow'] = json.loads(rendered.content[0].text)
        assert report['rendered_workflow']['status'] == 'available'
        archive = ReaEvidenceMiddleware(store, args.user, args.workspace_id, session,
                                        'integration-validation', pool.connection_id(root, args.user, session))

        async def call(name, **arguments):
            result = await tools[name](**arguments)
            assert result.state not in ('error', 'denied', 'interrupted'), result
            payload = json.loads(result.content[0].text)
            assert not (isinstance(payload, dict) and 'error' in payload), payload
            response = ToolResponse(content=result.content, state='success', metadata=result.metadata)
            async def handler(**kwargs):
                yield response
            tool_call = ToolCallBlock(id=uuid4().hex, name=tools[name].name, input=json.dumps(arguments))
            completed = [r async for r in archive.on_acting(None, {'tool_call': tool_call}, handler)][-1]
            report['operations'][name] = {'result': payload, 'metadata': completed.metadata}
            return completed

        await call('open_binary', path='/workspace/' + relative.as_posix(), provider_id=args.provider)
        await call('binary_overview')
        await call('search_procedures', pattern='main')
        result = await call('procedure_pseudo_code', procedure='main')
        assert result.metadata['traceglass_archive']['archive_status'] == 'saved'
        await call('close_binary')
        if args.check_layout:
            await call('inspect_binary_layout', path='/workspace/' + relative.as_posix())
            layout = report['operations']['inspect_binary_layout']['result']
            assert layout['normalized_result']['artifact']['sha256'] == hashlib.sha256(sample.read_bytes()).hexdigest()
            sections = layout['normalized_result']['sections']
            assert any(section['name']['display'] == '.text' for section in sections)
            await call('inspect_analysis_view',
                       source={'kind': 'retained-evidence', 'evidence_id': layout['evidence_id']},
                       view={'kind': 'page', 'collection': 'sections', 'offset': 0, 'limit': 2})
            view = report['operations']['inspect_analysis_view']['result']['normalized_result']
            assert view['parent_evidence_id'] == layout['evidence_id']
            assert view['items'] == sections[:2]
            assert view['coverage']['total'] == len(sections)
        # Read back through a new store connection to verify durable writes.
        records = EvidenceStore(args.archive).list(args.user, args.workspace_id)['items']
        report['archive_records'] = [r['id'] for r in records if r['session_id'] == session]
        assert report['archive_records']
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'tools': len(tools), 'workflows': len(catalog['workflows']),
                          'archived': len(report['archive_records']), 'provider': args.provider,
                          'report': str(args.output)}))
    finally:
        await pool.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--sample', required=True, help='Benign fixture path relative to workspace')
    parser.add_argument('--workspace-id', required=True)
    parser.add_argument('--user', default='local')
    parser.add_argument('--provider', default='ghidra', choices=['ghidra', 'hopper'])
    parser.add_argument('--check-layout', action='store_true', help='Also verify the optional pwntools ELF provider and retained evidence pagination')
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(verify(parser.parse_args()))
