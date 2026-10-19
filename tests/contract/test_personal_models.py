from test_accounts import secured, register

MODEL={'base_url':'https://api.deepseek.com/v1','model':'deepseek-chat','api_key':'sk-test-private-alice'}


def test_asiainfo_preset_and_full_endpoint_use_canonical_private_credential(secured):
    c,app,root=secured
    alice=register(c,'asiainfo')
    preset=next(p for p in c.get('/product/model').json()['presets'] if p['id']=='asiainfo')
    assert preset['base_url']=='https://llm.asiainfo-sec.com/v1'
    assert preset['model']=='deepseek-v4-pro'
    body={'base_url':preset['base_url']+'/chat/completions',
          'model':preset['model'],'api_key':'asiainfo-fixture-private'}
    response=c.put('/product/model',json=body)
    assert response.status_code==200,response.text
    assert response.json()['base_url']==preset['base_url']
    assert c.put('/product/model',json={**body,'base_url':preset['base_url'],'api_key':''}).status_code==200
    profile=c.get('/product/model').json()
    assert profile['base_url']==preset['base_url']
    assert body['api_key'] not in str(profile)
    config=c.get('/product/config').json()['model_config']
    credential=c.portal.call(app.state.storage.get_credential,alice['user']['id'],config['credential_id'])
    assert credential.data['base_url']==preset['base_url']
    assert b'asiainfo-fixture-private' not in (root/'agentscope.sqlite3').read_bytes()


def test_models_are_private_encrypted_and_drive_config(secured):
    c,app,root=secured
    alice=register(c,'alice')
    response=c.put('/product/model',json=MODEL)
    assert response.status_code==200,response.text
    assert 'sk-test-private-alice' not in response.text
    assert response.json()['has_key'] is True
    config=c.get('/product/config').json()
    assert config['model_config']['model']=='deepseek-chat'
    credential=config['model_config']['credential_id']
    record=c.portal.call(app.state.storage.get_credential,alice['user']['id'],credential)
    assert record.data['api_key']=='sk-test-private-alice'
    assert b'sk-test-private-alice' not in (root/'agentscope.sqlite3').read_bytes()
    assert c.put('/product/model',json={'base_url':MODEL['base_url'],'model':'deepseek-reasoner','api_key':''}).status_code==200
    assert c.get('/product/config').json()['model_config']['model']=='deepseek-reasoner'
    assert c.put('/product/model',json={'base_url':'https://api.openai.com/v1','model':'other','api_key':''}).status_code==422
    assert c.post('/auth/logout').status_code==204
    register(c,'bob')
    assert c.get('/product/model').json()['has_key'] is False
    assert c.get('/product/config').json()['model_config'] is None
    assert c.post('/product/model/test',json={'base_url':MODEL['base_url'],'model':'deepseek-chat','api_key':''}).status_code==422


def test_model_rejects_unsafe_urls_and_never_echoes_keys(secured):
    c,app,root=secured
    register(c,'alice')
    for url in ['http://api.deepseek.com/v1','https://127.0.0.1/v1','https://api.deepseek.com.evil.example/v1',
                'https://api.deepseek.com:8000/v1','https://user:pass@api.deepseek.com/v1',
                'https://api.deepseek.com/v1?key=secret','https://unapproved.example/v1']:
        response=c.put('/product/model',json={**MODEL,'base_url':url})
        assert response.status_code==422,response.text
        assert MODEL['api_key'] not in response.text
    response=c.put('/product/model',json={**MODEL,'model':'','api_key':'sk-do-not-echo'})
    assert response.status_code==422
    assert 'sk-do-not-echo' not in response.text


def test_self_hosted_origin_saves_and_reports_tls_errors_without_exposing_keys(tmp_path, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from traceglass.api.app import create_app
    from traceglass.config import Settings
    import traceglass.accounts.models as models
    app = create_app(settings=Settings(data_dir=tmp_path, workspace_backend='docker', auth_enabled=True,
        allowed_origins=('http://testserver',), model_allowed_origins=('https://182.44.7.227:8001',)))
    async def destination(url):
        assert url == 'https://182.44.7.227:8001/v1'
    async def tls_error(*args, **kwargs):
        raise httpx.ConnectError('[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate')
    monkeypatch.setattr(models, 'public_destination', destination)
    monkeypatch.setattr(models, 'probe_stream', tls_error)
    with TestClient(app) as client:
        register(client, 'selfhosted')
        body = {**MODEL, 'base_url': 'https://182.44.7.227:8001/v1'}
        assert client.put('/product/model', json=body).status_code == 200
        assert client.get('/product/model').json()['allowed_origins'] == ['https://182.44.7.227:8001']
        result = client.post('/product/model/test', json={**body, 'api_key': ''})
        assert result.status_code == 400
        assert '证书校验失败' in result.json()['detail']
        assert MODEL['api_key'] not in result.text


def test_credentials_and_login_survive_restart_without_exposing_secrets(secured):
    from fastapi.testclient import TestClient
    from traceglass.api.app import create_app
    from traceglass.config import Settings
    c,app,root=secured
    alice=register(c,'alice')
    assert c.put('/product/model',json=MODEL).status_code==200
    cookie=c.cookies.get('traceglass_session')
    restarted=create_app(settings=Settings(data_dir=root,workspace_backend='docker',auth_enabled=True,
                                          allowed_origins=('http://testserver',)))
    with TestClient(restarted) as again:
        again.cookies.set('traceglass_session',cookie)
        session=again.get('/auth/session').json()
        assert session['user']['id']==alice['user']['id']
        config=again.get('/product/config').json()
        assert config['model_config']['model']=='deepseek-chat'
        saved=again.portal.call(restarted.state.storage.get_credential,alice['user']['id'],config['model_config']['credential_id'])
        assert saved.data['api_key']==MODEL['api_key']
        assert MODEL['api_key'] not in again.get('/product/model').text


def test_new_sessions_use_personal_defaults_and_keep_previous_credentials(secured):
    c,app,_=secured
    alice=register(c,'alice')
    ws=c.post('/product/workspaces',json={'name':'model defaults'}).json()['workspace_id']
    assert c.put('/product/model',json=MODEL).status_code==200
    first=c.post(f'/product/workspaces/{ws}/sessions',json={'name':'first'})
    assert first.status_code==201,first.text
    first=first.json()
    old=c.portal.call(app.state.storage.get_session,alice['user']['id'],first['agent_id'],first['session_id'])
    assert old.config.chat_model_config.model=='deepseek-chat'
    assert c.put('/product/model',json={**MODEL,'model':'deepseek-reasoner','api_key':'new-private-key'}).status_code==200
    second=c.post(f'/product/workspaces/{ws}/sessions',json={'name':'second'}).json()
    new=c.portal.call(app.state.storage.get_session,alice['user']['id'],second['agent_id'],second['session_id'])
    assert new.config.chat_model_config.model=='deepseek-reasoner'
    assert old.config.chat_model_config.credential_id != new.config.chat_model_config.credential_id
    old_key=c.portal.call(app.state.storage.get_credential,alice['user']['id'],old.config.chat_model_config.credential_id)
    assert old_key.data['api_key']==MODEL['api_key']
    c.post('/auth/logout')
    register(c,'bob')
    bob_ws=c.post('/product/workspaces',json={'name':'Bob'}).json()['workspace_id']
    assert c.post(f'/product/workspaces/{bob_ws}/sessions',json={
        'chat_model_config':old.config.chat_model_config.model_dump()}).status_code==404
