import test from 'node:test';
import assert from 'node:assert/strict';
import {
  timelineBlocks,
  delegationAnchor,
  toolStatus,
  toolArguments,
  evidenceTools,
} from '../src/features/execution/timeline.ts';
const call = (id, name = 'Read') => ({
  type: 'tool_call',
  id,
  name,
  input: '{"path":"report.md"}',
});
const result = (id, state = 'success') => ({
  type: 'tool_result',
  id,
  state,
  output: 'source data',
});
const message = (id, content) => ({ id, role: 'assistant', content });
test('parallel tools match results by id while preserving explanations and orphan outputs', () => {
  const blocks = [
    call('a'),
    call('b'),
    { type: 'text', text: '解释' },
    result('b', 'error'),
    result('a'),
    result('orphan'),
  ];
  const rows = timelineBlocks(blocks);
  assert.equal(rows.length, 4);
  assert.equal(rows[0].result.state, 'success');
  assert.equal(rows[1].result.state, 'error');
  assert.equal(rows[2].block.text, '解释');
  assert.equal(rows[3].block.id, 'orphan');
  assert.equal(blocks.length, 6);
});
test('missing ids never merge unrelated tools', () => {
  assert.equal(
    timelineBlocks([
      { type: 'tool_call', name: 'Read' },
      { type: 'tool_result', output: 'x' },
    ]).length,
    2,
  );
});
test('tool status distinguishes tool errors, permission, completed calls without results, and interrupted calls', () => {
  assert.equal(toolStatus(call('a'), result('a', 'error'), false), '失败');
  assert.equal(
    toolStatus({ ...call('a'), state: 'asking' }, undefined, true),
    '等待授权',
  );
  assert.equal(
    toolStatus({ ...call('a'), state: 'finished' }, undefined, false),
    '结果未记录',
  );
  assert.equal(toolStatus(call('a'), undefined, false), '未完成');
  assert.equal(toolStatus(call('a'), undefined, true), '调用中');
});
test('team anchors at delegation, never at plan or team creation; incomplete history uses fallback', () => {
  const messages = [
    message('m1', [call('p', 'TodoWrite'), call('t', 'TeamCreate')]),
    message('m2', [call('a', 'AgentCreate'), call('b', 'AgentCreate')]),
  ];
  assert.deepEqual(delegationAnchor(messages), {
    messageId: 'm2',
    blockIndex: 0,
  });
  assert.equal(delegationAnchor([messages[0]]), null);
});
test('streamed partial JSON never breaks rendering or invents delegation metadata', () => {
  assert.deepEqual(toolArguments({ ...call('a'), input: '{"name":' }), {});
  assert.deepEqual(toolArguments({ ...call('a'), input: 'null' }), {});
  assert.equal(toolArguments(call('a')).path, 'report.md');
});
test('evidence index includes only successful evidence registrations, not attempted or failed ones', () => {
  const messages = [
    message('m', [
      call('a', 'record_evidence'),
      result('a'),
      call('b', 'record_evidence'),
      result('b', 'error'),
      call('c', 'record_evidence'),
    ]),
  ];
  assert.equal(evidenceTools(messages).length, 1);
  assert.equal(evidenceTools(messages)[0].call.id, 'a');
});
test('artifact save counts as evidence only when the result contains a native evidence record', () => {
  const saved = {
    ...result('a'),
    output: [
      {
        type: 'text',
        text: JSON.stringify({
          id: 'E-123',
          type: 'artifact',
          location: 'artifacts/report.md',
          summary: '报告',
        }),
      },
    ],
  };
  const messages = [
    message('m', [
      call('a', 'mcp__reverse__save_analysis_artifact'),
      saved,
      call('b', 'save_analysis_artifact'),
      result('b'),
    ]),
  ];
  assert.equal(evidenceTools(messages).length, 1);
});

test('REA index recognizes only successful archive receipts and retains source call identity', () => {
  const receipt = {traceglass_archive: {archive_status: 'saved',
    traceglass_evidence: [{id: 'tgev_fixture', upstream_id: 'ev_fixture', kind: 'evidence'}]}};
  const blocks = [call('a', 'mcp__rea__procedure_pseudo_code'),
    {...result('a'), metadata: receipt},
    call('b', 'mcp__rea__procedure_pseudo_code'),
    {...result('b', 'error'), metadata: receipt},
    call('c', 'mcp__rea__procedure_pseudo_code'),
    {...result('c'), output: [{type:'text', text:JSON.stringify(receipt.traceglass_archive)}]}];
  const records = evidenceTools([message('rea-message', blocks)]);
  assert.equal(records.length, 1);
  assert.equal(records[0].messageId, 'rea-message');
  assert.equal(records[0].call.id, 'a');
});
