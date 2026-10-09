import test from 'node:test';
import assert from 'node:assert/strict';
import {canPauseExecution, hasBudgetCloseout, canResumeReply} from '../src/features/execution/controls.ts';
import {combineMessages} from '../src/features/execution/native.ts';

test('pause remains available for approval waits and child-only execution', () => {
  for (const status of ['preparing', 'running', 'awaiting_permission', 'awaiting_external_result']) {
    assert.equal(canPauseExecution(status, []), true);
    assert.equal(canPauseExecution('idle', [status]), true);
  }
  assert.equal(canPauseExecution('idle', ['idle', undefined]), false);
  assert.equal(canPauseExecution(undefined, []), false);
});

test('budget closeout stays resumable in live events and persisted history', () => {
  const events = [
    {id:'1', type:'HINT_BLOCK', reply_id:'reply', block_id:'hint', source:'traceglass.iteration_closeout', hint:'budget'},
    {id:'2', type:'REPLY_END', reply_id:'reply', finished_reason:'completed'},
  ];
  const [reply] = combineMessages([], events);
  assert.equal(hasBudgetCloseout(reply), true);
  assert.equal(canResumeReply(reply), true);
  assert.equal(canResumeReply(combineMessages([reply], [])[0]), true);
  assert.equal(canResumeReply({...reply, finished_reason:undefined}), false);
  assert.equal(canResumeReply({...reply, content:[]}), false);
  assert.equal(canResumeReply({...reply, finished_reason:'interrupted'}), true);
  assert.equal(canResumeReply({...reply, finished_reason:'error'}), true);
  assert.equal(canResumeReply({...reply, role:'user'}), false);
});
