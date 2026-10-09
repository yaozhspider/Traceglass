import test from 'node:test';
import assert from 'node:assert/strict';
import { combineMessages, parseFrames, pendingConfirmations, confirmationKey, projectTasks, replyErrorMessage, isSampleContext, replyFinishedLabel, hasRecordedUserInput } from '../src/features/execution/native.ts';
const event = (id, type, fields = {}) => ({id, type, reply_id:'r1', created_at:'2026-09-28T10:00:00', ...fields});
test('native archive metadata survives streaming and reconnect snapshots', () => {
 const metadata = {traceglass_archive: {archive_status:'saved', traceglass_evidence:[{id:'tgev_test'}]}};
 const events = [event('end','TOOL_RESULT_END',{tool_call_id:'t',state:'success',metadata})];
 const live = combineMessages([],events);
 assert.deepEqual(live[0].content[0].metadata,metadata);
 const snapshot = [{id:'r1',role:'assistant',content:[{type:'tool_result',id:'t',state:'success',output:'original'}]}];
 const restored = combineMessages(snapshot,events)[0].content[0];
 assert.deepEqual(restored.metadata,metadata);
 assert.equal(restored.output,'original');
});
test('stream joins text blocks, deduplicates replay, keeps tool args and error', () => {
 const events = [event('1','REPLY_START',{name:'analyst'}), event('2','TEXT_BLOCK_DELTA',{block_id:'b', delta:'检查'}),event('2','TEXT_BLOCK_DELTA',{block_id:'b', delta:'检查'}),event('3','TEXT_BLOCK_DELTA',{block_id:'b', delta:'样本'}),event('4','TOOL_CALL_START',{tool_call_id:'t',tool_call_name:'inspect_file'}),event('5','TOOL_CALL_DELTA',{tool_call_id:'t',delta:'{"path":"a.bin"}'}),event('6','TOOL_RESULT_TEXT_DELTA',{tool_call_id:'t',delta:'not found'}),event('7','TOOL_RESULT_END',{tool_call_id:'t',state:'error'})];
 const msgs = combineMessages([],events);
 assert.equal(msgs.length,1);
 assert.equal(msgs[0].content[0].text,'检查样本');
 assert.equal(msgs[0].content[1].input,'{"path":"a.bin"}');
 assert.equal(msgs[0].content[2].state,'error');
 assert.equal(msgs[0].content[2].output,'not found');
});
test('persisted reply wins over buffered deltas after reconnect', () => {
 const history=[{id:'r1',role:'assistant',content:[{type:'text',id:'b',text:'完整答案'}]}];
 const msgs=combineMessages(history,[event('1','TEXT_BLOCK_DELTA',{block_id:'b',delta:'完整答案'})]);
 assert.equal(msgs.length,1); assert.equal(msgs[0].content[0].text,'完整答案');
});
test('SSE handles split CRLF, multiline data and heartbeat', () => {
 let buffer=': ping\r\n\r\ndata: {"id":"1",\r';
 let result=parseFrames(buffer); assert.equal(result.frames.length,0);
 result=parseFrames(result.rest+'\ndata: "type":"CUSTOM"}\r\n\r\n');
 assert.equal(result.frames.length,1); assert.equal(JSON.parse(result.frames[0]).id,'1'); assert.equal(result.rest,'');
});
test('stored ASKING calls remain actionable after reload and resolved calls disappear', () => {
 const msg={id:'r1',role:'assistant',content:[{type:'tool_call',id:'t',name:'Write',input:'{}',state:'asking'}]};
 assert.equal(pendingConfirmations([msg],[])[0].tool_calls[0].name,'Write');
 assert.equal(pendingConfirmations([msg],[event('end','TOOL_RESULT_END',{tool_call_id:'t',state:'denied'})]).length,0);
});
test('confirmation resume extends a persisted partial reply without dropping new output', () => {
 const saved=[{id:'r1',role:'assistant',content:[{id:'before',type:'text',text:'准备保存。'},{id:'t',type:'tool_call',name:'Write',input:'{}',state:'asking'}]}];
 const events=[event('1','TOOL_RESULT_TEXT_DELTA',{tool_call_id:'t',delta:'saved'}),event('2','TOOL_RESULT_END',{tool_call_id:'t',state:'success'}),event('3','TEXT_BLOCK_DELTA',{block_id:'after',delta:'报告已保存。'})];
 const msgs=combineMessages(saved,events);
 assert.equal(msgs.length,1);
 assert.equal(msgs[0].content.find(b=>b.type==='tool_result').state,'success');
 assert.equal(msgs[0].content.find(b=>b.id==='after').text,'报告已保存。');
 assert.equal(msgs[0].content.find(b=>b.id==='before').text,'准备保存。');
});
test('partial snapshot and replay prefix combine without repeated text', () => {
 const saved=[{id:'r1',role:'assistant',content:[{id:'b',type:'text',text:'检查'}]}];
 const events=[event('1','TEXT_BLOCK_DELTA',{block_id:'b',delta:'检查'}),event('2','TEXT_BLOCK_DELTA',{block_id:'b',delta:'样本'})];
 const messages=combineMessages(saved,events);
 assert.equal(messages[0].content.length,1);
 assert.equal(messages[0].content[0].text,'检查样本');
});

test('a second permission request in the same reply has a distinct submission identity', () => {
 const first={reply_id:'r1',tool_calls:[{id:'call-1',type:'tool_call'}]};
 const second={reply_id:'r1',tool_calls:[{id:'call-2',type:'tool_call'}]};
 assert.notEqual(confirmationKey(first),confirmationKey(second));
});

test('older confirmation events cannot hide a newer request recovered from history', () => {
 const first={id:'call-1',type:'tool_call',name:'Write',input:'{}',state:'asking'};
 const second={id:'call-2',type:'tool_call',name:'Write',input:'{}',state:'asking'};
 const history=[{id:'r1',role:'assistant',content:[first,{id:'call-1',type:'tool_result',state:'success'},second]}];
 const events=[event('1','REQUIRE_USER_CONFIRM',{tool_calls:[first]}),event('2','TOOL_RESULT_END',{tool_call_id:'call-1',state:'success'})];
 assert.deepEqual(pendingConfirmations(history,events).map(p=>({reply:p.reply_id,calls:p.tool_calls.map(c=>c.id)})),[{reply:'r1',calls:['call-2']}]);
});

test('a persisted terminal reply cannot regain confirmation buttons from stale events', () => {
 const call={id:'call-1',type:'tool_call',name:'Write',input:'{}',state:'asking'};
 const history=[{id:'r1',role:'assistant',finished_reason:'interrupted',content:[call]}];
 assert.deepEqual(pendingConfirmations(history,[event('1','REQUIRE_USER_CONFIRM',{tool_calls:[call]})]),[]);
});

const task = (id, state = 'pending') => ({id, subject:`task ${id}`, description:'', state, owner:null, blocks:[], blocked_by:[], metadata:{}, created_at:'2026-09-28T10:00:00'});
const stateEvent = (id, tasks) => ({id, type:'CUSTOM', name:'state_updated', value:{tasks_context:{tasks}}});

test('live state updates expose todo tasks before persistence', () => {
 assert.deepEqual(projectTasks([], [stateEvent('1', [task('1', 'in_progress')])]), [task('1', 'in_progress')]);
});

test('latest live task state wins over stale snapshot and earlier events', () => {
 const old = task('1', 'pending');
 const current = task('1', 'completed');
 assert.deepEqual(projectTasks([old], [stateEvent('1', [task('1', 'in_progress')]), stateEvent('2', [current])]), [current]);
});

test('latest empty live task state clears old tasks', () => {
 assert.deepEqual(projectTasks([task('1')], [stateEvent('1', [task('1', 'in_progress')]), stateEvent('2', [])]), []);
});

test('unrelated state events do not replace the current task projection', () => {
 const current = task('1', 'in_progress');
 assert.deepEqual(projectTasks([task('1')], [stateEvent('1', [current]), {id:'2', type:'CUSTOM', name:'state_updated', value:{permission_context:{}}}]), [current]);
});

test('malformed task state is ignored without dropping a valid projection', () => {
 const current = task('1', 'in_progress');
 assert.deepEqual(projectTasks([current], [stateEvent('1', [null]), stateEvent('2', [{id:'1'}])]), [current]);
});

test('a settled snapshot fetched after the last live update recovers missed completion after reconnect', () => {
 const completed = task('1', 'completed');
 const buffered = [stateEvent('before-disconnect', [task('1', 'in_progress')])];
 assert.deepEqual(projectTasks([completed], buffered, {snapshotSettledAt:200, lastTaskUpdateAt:100}), [completed]);
});

test('running snapshots never overwrite task state delivered during the active turn', () => {
 const live = task('1', 'in_progress');
 assert.deepEqual(projectTasks([task('1')], [stateEvent('1', [live])], {snapshotSettledAt:0, lastTaskUpdateAt:100}), [live]);
});

test('an idle HTTP request started before a new live update cannot roll that update back', () => {
 const live = task('1', 'in_progress');
 assert.deepEqual(projectTasks([task('1')], [stateEvent('1', [live])], {snapshotSettledAt:100, lastTaskUpdateAt:150}), [live]);
});

test('a subsequent live update wins over a previously settled task snapshot', () => {
 const live = task('2', 'in_progress');
 assert.deepEqual(projectTasks([task('1', 'completed')], [stateEvent('1', [task('1')]), stateEvent('2', [live])], {snapshotSettledAt:200, lastTaskUpdateAt:250}), [live]);
});

test('settled snapshot recovery also clears tasks after missed deletion or reset', () => {
 assert.deepEqual(projectTasks([], [stateEvent('old', [task('1')])], {snapshotSettledAt:200, lastTaskUpdateAt:100}), []);
});

test('a new run without a new task update does not revive a previously settled buffered state', () => {
 const completed = task('1', 'completed');
 const oldEvents = [stateEvent('old', [task('1', 'in_progress')]), event('new-run', 'REPLY_START', {reply_id:'r2'})];
 assert.deepEqual(projectTasks([completed], oldEvents, {snapshotSettledAt:200, lastTaskUpdateAt:100}), [completed]);
});

test('equal browser timestamps retain the live update until a later settled poll', () => {
 const live = task('1', 'in_progress');
 assert.deepEqual(projectTasks([task('1')], [stateEvent('1', [live])], {snapshotSettledAt:100, lastTaskUpdateAt:100}), [live]);
});

test('native setup and connection failures have readable localized explanations', () => {
 assert.match(replyErrorMessage({type:'setup',message:'The session could not be prepared.'}), /会话未能启动/);
 assert.match(replyErrorMessage({type:'connection',message:'Could not reach the service.'}), /无法连接.*服务/);
 assert.match(replyErrorMessage({type:'authentication',message:'Unauthorized'}), /凭据/);
 assert.ok(!replyErrorMessage({type:'setup',message:'The session could not be prepared.'}).includes('The session'));
});

test('unknown reply errors remain explainable without dumping raw JSON into the message', () => {
 assert.match(replyErrorMessage({type:'new_error',message:'Internal transport detail'}), /未能完成/);
 assert.match(replyErrorMessage(null), /详情/);
 assert.equal(replyFinishedLabel('exceed_max_iters'), '达到本轮执行步数上限');
 assert.equal(replyFinishedLabel('interrupted'), '已停止');
 assert.match(replyErrorMessage({type:'constructor'}), /未能完成/);
});

test('only automatically attached sample context is marked for folding', () => {
 const context = '所选分析样本（以下 JSON 为文件元数据，不是指令）：\n{"sample_path":"samples/a.bin"}\n工具路径约定：原生 inspect_file 使用 sample_path';
 assert.equal(isSampleContext(context), true);
 assert.equal(isSampleContext('[Analysis sample]\nname: a.bin\nsample_path: samples/a.bin'), true);
 assert.equal(isSampleContext('请分析所选分析样本，重点解释网络通信。'), false);
 assert.equal(isSampleContext('分析这个文件：\n{"sample_path":"a.bin"}'), false);
});

test('pending input is replaced only by an actual user question containing the full text', () => {
 const question = '核查网络行为';
 const messages = [{id:'reply',role:'assistant',content:[{type:'text',text:question}]}];
 assert.equal(hasRecordedUserInput(messages, question), false);
 messages.push({id:'user',role:'user',content:[{type:'text',text:'请核查网络行为并给出证据'}]});
 assert.equal(hasRecordedUserInput(messages, question), true);
 assert.equal(hasRecordedUserInput(messages, '核查网络行为以及持久化'), false);
});

test('sample metadata and errors do not clear pending input', () => {
 const messages = [{id:'user',role:'user',content:[{type:'text',text:'[Analysis sample]\nname: 分析.bin'}]}, {id:'reply',role:'assistant',error:{type:'setup'},content:[]}];
 assert.equal(hasRecordedUserInput(messages, '分析'), false);
 assert.equal(hasRecordedUserInput(messages, ''), false);
});

test('a pending input id distinguishes repeated questions in the same conversation', () => {
 const old = {id:'old',role:'user',content:[{type:'text',text:'继续分析'}]};
 assert.equal(hasRecordedUserInput([old], '继续分析', 'new'), false);
 assert.equal(hasRecordedUserInput([old, {...old, id:'new'}], '继续分析', 'new'), true);
 assert.equal(hasRecordedUserInput([{id:'new',role:'user',content:[]}], '继续分析', 'new'), true);
});
