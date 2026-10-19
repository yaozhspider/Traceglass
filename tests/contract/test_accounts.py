from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from traceglass.api.app import create_app
from traceglass.config import Settings

@pytest.fixture
def secured(tmp_path):
    app = create_app(settings=Settings(data_dir=tmp_path, workspace_backend='docker', auth_enabled=True,
                                      allowed_origins=('http://testserver',)))
    with TestClient(app) as client:
        yield client, app, tmp_path

def register(client, name):
    response = client.post('/auth/register', json={'username':name,'password':'Correct horse 2026!'},
                           headers={'Origin':'http://testserver'})
    assert response.status_code == 201, response.text
    client.headers['X-Traceglass-CSRF'] = response.json()['csrf_token']
    return response.json()

def test_registration_requires_login_and_ignores_forged_identity(secured):
    c, app, root = secured
    assert c.get('/product/config', headers={'X-User-ID':'local'}).status_code == 401
    one = register(c, 'alice')
    assert 'HttpOnly' in c.get('/auth/session').headers.get('set-cookie','') or c.cookies.get('traceglass_session')
    config = c.get('/product/config', headers={'X-User-ID':'victim'}).json()
    assert config['user_id'] == one['user']['id']
    assert not config['model_configured']
    workspace = c.post('/product/workspaces', json={'name':'private'}).json()
    blob = c.post(f"/product/workspaces/{workspace['workspace_id']}/samples", files={'file':('private.txt',b'alice only')}).json()
    alice_cookie = c.cookies.get('traceglass_session')
    assert c.post('/auth/logout').status_code == 204
    assert c.get('/product/config').status_code == 401
    c.cookies.set('traceglass_session', alice_cookie)
    assert c.get('/product/config').status_code == 401
    c.cookies.clear()
    two = register(c, 'bob')
    assert two['user']['id'] != one['user']['id']
    assert c.get('/product/workspaces').json()['workspaces'] == []
    assert c.get(f"/product/workspaces/{workspace['workspace_id']}").status_code == 404
    assert c.get(f"/product/workspaces/{workspace['workspace_id']}/download",params={'path':blob['sample_path']}).status_code == 404
    assert c.get('/product/config').json()['agent_id'] != config['agent_id']
    for path in ['/credential/', '/agent/', '/workspace/', '/docs', '/openapi.json']:
        assert c.get(path).status_code in (403,404)

def test_auth_csrf_duplicate_and_password_storage(secured):
    c, app, root = secured
    result = register(c, 'alice')
    assert c.post('/product/workspaces',json={'name':'blocked'},headers={'X-Traceglass-CSRF':'bad'}).status_code == 403
    assert c.post('/auth/login',json={'username':'alice','password':'Correct horse 2026!'},headers={'Origin':'https://attacker.example'}).status_code == 403
    assert c.post('/auth/register',json={'username':'ALICE','password':'Correct horse 2026!'},headers={'Origin':'http://testserver'}).status_code == 409
    assert c.post('/auth/login',json={'username':'alice','password':'wrong password'},headers={'Origin':'http://testserver'}).status_code == 401
    assert b'Correct horse 2026!' not in (root/'accounts.sqlite3').read_bytes()
    assert c.post('/auth/logout').status_code == 204
    response=c.post('/auth/login',json={'username':'alice','password':'Correct horse 2026!'},headers={'Origin':'http://testserver'})
    assert response.status_code == 200
    assert response.json()['user']['id'] == result['user']['id']


def test_foreign_native_sessions_chat_and_workspace_cache_are_rejected(secured):
    c,app,_=secured
    alice=register(c,'alice')
    config=c.get('/product/config').json()
    ws=c.post('/product/workspaces',json={'name':'Alice'}).json()['workspace_id']
    session=c.post(f'/product/workspaces/{ws}/sessions',json={'name':'secret session'}).json()
    sid,aid=session['session_id'],session['agent_id']
    assert c.get('/sessions/',params={'agent_id':aid}).status_code==200
    assert c.patch(f'/sessions/{sid}',params={'agent_id':aid},json={'workspace_id':'arbitrary-host-path'}).status_code==403
    assert c.post('/sessions/',json={'agent_id':aid,'workspace_id':'arbitrary'}).status_code==404
    c.post('/auth/logout')
    bob=register(c,'bob')
    for suffix in ('messages','status','stream'):
        assert c.get(f'/sessions/{sid}/{suffix}',params={'agent_id':aid},headers={'X-User-ID':alice['user']['id']}).status_code==404
    assert c.post('/chat/',json={'agent_id':aid,'session_id':sid,'input':None}).status_code==404
    assert c.patch(f'/sessions/{sid}',params={'agent_id':aid},json={'name':'hijack'}).status_code==404
    assert c.post(f'/product/sessions/{sid}/interrupt',params={'agent_id':aid}).status_code==404
    manager=app.state.workspace_manager
    for wid in (ws,'arbitrary'):
        with pytest.raises(LookupError):
            manager.host_root(bob['user']['id'],aid,wid)


def test_expiry_and_login_rate_limit(secured):
    c,app,_=secured
    register(c,'alice')
    with app.state.accounts.connect() as db:
        db.execute('UPDATE logins SET expires=0')
    assert c.get('/product/config').status_code==401
    for _ in range(14):
        response=c.post('/auth/login',json={'username':'alice','password':'bad'},headers={'Origin':'http://testserver'})
        assert response.status_code==401
    response=c.post('/auth/login',json={'username':'alice','password':'bad'},headers={'Origin':'http://testserver'})
    assert response.status_code==429
    assert 'Retry-After' in response.headers


def test_auth_json_body_is_bounded_and_does_not_echo_invalid_passwords(secured):
    c,app,_=secured
    response=c.post('/auth/register',json={'username':'x','password':'private-value'},headers={'Origin':'http://testserver'})
    assert response.status_code==422
    assert 'private-value' not in response.text
    response=c.post('/auth/register',content=b'{' + b' '*(70*1024),headers={'Origin':'http://testserver','Content-Type':'application/json'})
    assert response.status_code==413
