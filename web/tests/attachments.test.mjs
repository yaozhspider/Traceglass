import test from 'node:test';
import assert from 'node:assert/strict';
import {AttachmentQueue, attachmentContext} from '../src/features/workspaces/attachments.ts';

const sample = name => ({sample_id:name, name, sample_path:`samples/${name}`, sandbox_path:`/workspace/samples/${name}`, sha256:'abc', size:5});
const tick = () => new Promise(resolve => setImmediate(resolve));

test('existing selections coexist with uploads, deduplicate and remove by sample identity', async () => {
 const queue = new AttachmentQueue(async file => sample(file.name));
 queue.add([new File(['a'], 'new.txt')]);
 await tick();
 queue.addExisting([sample('old.txt'), sample('guide.docx'), sample('old.txt'), sample('new.txt')]);
 assert.deepEqual(queue.ready().map(s => s.name), ['new.txt', 'old.txt', 'guide.docx']);
 queue.removeSample('old.txt');
 assert.deepEqual(queue.ready().map(s => s.name), ['new.txt', 'guide.docx']);
 assert.equal(queue.blocked(), false);
});

test('batch uploads preserve every attachment and retry only failed files', async () => {
 const calls = []; let fail = true;
 const queue = new AttachmentQueue(async file => {
  calls.push(file.name);
  if (file.name === 'notes.pdf' && fail) throw new Error('offline');
  return sample(file.name);
 });
 queue.add([new File(['a'],'one.bin'), new File(['b'],'notes.pdf'), new File(['c'],'three.docx')]);
 await tick();
 assert.deepEqual(queue.snapshot().map(x=>x.status),['ready','error','ready']);
 assert.equal(queue.blocked(),true);
 fail=false; queue.retry(queue.snapshot()[1].id); await tick();
 assert.deepEqual(calls,['one.bin','notes.pdf','three.docx','notes.pdf']);
 assert.equal(queue.blocked(),false);
 assert.equal(queue.ready().length,3);
});

test('removal and navigation cancel uploads and ignore late results', async () => {
 let resolve; let signal;
 const queue = new AttachmentQueue((file, progress, currentSignal) => {
  signal=currentSignal; return new Promise(done=>{resolve=done;});
 });
 queue.add([new File(['a'],'notes.txt')]);
 queue.clear(); assert.equal(signal.aborted,true);
 resolve(sample('notes.txt')); await tick();
 assert.deepEqual(queue.snapshot(),[]);
});

test('removing an in-flight attachment lets remaining files finish', async () => {
 const queue = new AttachmentQueue((file,progress,signal) => file.name === 'slow'
  ? new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(new Error('cancelled'))))
  : Promise.resolve(sample(file.name)));
 queue.add([new File(['a'],'slow'),new File(['b'],'fast')]);
 queue.remove(queue.snapshot()[0].id); await tick();
 assert.deepEqual(queue.ready().map(s=>s.name),['fast']);
});

test('attachment context requires reading all files and preserves metadata as data', () => {
 const text = attachmentContext([sample('说明.docx'),sample('ignore\ninstructions.pdf')]);
 const metadata=JSON.parse(text.slice(text.indexOf('['),text.lastIndexOf(']')+1));
 assert.equal(metadata.length,2);
 assert.equal(metadata[0].sandbox_path,'/workspace/samples/说明.docx');
 assert.match(text,/逐一读取/);
 assert.match(text,/不是指令/);
 assert.ok(!text.includes('ignore\ninstructions.pdf'));
});
