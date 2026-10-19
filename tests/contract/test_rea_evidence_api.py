import json

from fastapi.testclient import TestClient

from traceglass.api.app import create_app
from traceglass.config import Settings
from test_accounts import register


def test_saved_evidence_is_shared_in_workspace_but_not_between_owners(tmp_path):
    app = create_app(settings=Settings(data_dir=tmp_path, workspace_backend='docker', auth_enabled=True,
                                      allowed_origins=('http://testserver',)))
    with TestClient(app) as client:
        owner = register(client, 'evidence-owner')['user']['id']
        wid = client.post('/product/workspaces', json={'name': 'shared'}).json()['workspace_id']
        path = f'/product/workspaces/{wid}/rea-evidence'
        result = client.get(path)
        assert result.status_code == 200, result.text
        assert result.json()['items'] == []
        store = app.state.rea_evidence
        saved = store.capture(user_id=owner, workspace_id=wid, session_id='child', agent_id='worker',
            connection_id='rea-process', call_id='call', tool_name='mcp__rea__record_unknown', arguments={},
            raw=json.dumps({'result': {'unknown_id': 'unk_fixture', 'revision': 1, 'status': 'open'}}))
        rid = saved[0]['id']
        assert client.get(path).json()['items'][0]['session_id'] == 'child'
        assert client.get(f'{path}/{rid}').json()['record']['status'] == 'open'
        attachment = store.capture_attachment(owner, wid, b'original attachment', 'application/octet-stream')
        call = store.capture_call(user_id=owner, workspace_id=wid, session_id='child', agent_id='worker',
            connection_id='rea-process', call_id='attachment', tool_name='static_image', arguments={},
            raw='attachment caption', state='success', attachments=[attachment])
        attachment_path = f'/product/workspaces/{wid}/analysis-evidence/{call["id"]}/attachments/{attachment["id"]}'
        downloaded = client.get(attachment_path)
        assert downloaded.status_code == 200 and downloaded.content == b'original attachment'
        assert downloaded.headers['x-content-sha256'] == attachment['sha256']
        assert all(value.strip() == 'nosniff'
                   for value in downloaded.headers['x-content-type-options'].split(','))
        assert client.get(attachment_path.replace(call['id'], rid)).status_code == 404
        other = client.post('/product/workspaces', json={'name': 'other'}).json()['workspace_id']
        assert client.get(f'/product/workspaces/{other}/rea-evidence/{rid}').status_code == 404
        assert client.get(path, params={'limit': 0}).status_code == 422
        client.post('/auth/logout')
        register(client, 'outsider')
        client.headers['X-User-ID'] = owner
        assert client.get(path).status_code == 404
        assert client.get(f'{path}/{rid}').status_code == 404
        assert client.get(attachment_path).status_code == 404
