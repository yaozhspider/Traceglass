import test from 'node:test';
import assert from 'node:assert/strict';
import {authFetch,csrfHeaders,setCsrf} from '../src/features/account/transport.ts';

test('authenticated transport sends current CSRF cookie mode and clears tokens on logout', async () => {
  const previous=globalThis.fetch;
  const events=[];
  globalThis.window={dispatchEvent:event=>events.push(event.type)};
  const requests=[];
  globalThis.fetch=async(url,init)=>{requests.push({url,...init});return new Response('',{status:url.endsWith('/denied')?401:200});};
  try {
    setCsrf('alice-session');
    await authFetch('/api/product/workspaces',{method:'POST',headers:{'Content-Type':'application/json'}});
    assert.equal(requests[0].credentials,'same-origin');
    assert.equal(requests[0].headers['X-Traceglass-CSRF'],'alice-session');
    assert.equal(requests[0].headers['Content-Type'],'application/json');
    setCsrf(null);
    assert.deepEqual(csrfHeaders(),{});
    await authFetch('/api/product/denied');
    assert.deepEqual(events,['traceglass-auth-expired']);
    await authFetch('/api/auth/denied');
    assert.equal(events.length,1);
  } finally {globalThis.fetch=previous;delete globalThis.window;setCsrf(null);}
});
