"""Use a real local HTTPS server: verification must finish before credentials leave."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
import threading

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@pytest.fixture
def private_model(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'model.internal')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('model.internal')]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    ca = tmp_path / 'model.pem'
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile = tmp_path / 'key.pem'
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append((self.path, self.headers.get('Authorization')))
            body = json.dumps({'data': [{'id': 'fixture'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            received.append((self.path, self.headers.get('Authorization')))
            self.rfile.read(int(self.headers['Content-Length']))
            packets = [dict(id='fixture', object='chat.completion.chunk', created=1, model='fixture',
                choices=[dict(index=0, delta={'content': 'TLS_OK'}, finish_reason=None)]),
                dict(id='fixture', object='chat.completion.chunk', created=1, model='fixture',
                choices=[dict(index=0, delta={}, finish_reason='stop')])]
            body = ('\n\n'.join('data: ' + json.dumps(p) for p in packets) + '\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(ca, keyfile)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'https://127.0.0.1:{server.server_port}'
    config = tmp_path / 'tls.json'
    config.write_text(json.dumps([{'origin': origin, 'ca_file': 'model.pem', 'server_name': 'model.internal'}]))
    yield config, origin, received
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_scoped_tls_connects_but_keeps_default_and_hostname_verification(private_model):
    from traceglass.accounts.model_tls import ModelTLSPolicy
    config, origin, received = private_model
    policy = ModelTLSPolicy(config, (origin,))

    async def run():
        async with policy.client(origin + '/v1', headers={'Authorization': 'Bearer private-test'}) as client:
            response = await client.get(origin + '/v1/models')
            assert response.json()['data'][0]['id'] == 'fixture'
            with pytest.raises(httpx.ConnectError):
                await client.get('https://example.com/v1/models')
        assert received == [('/v1/models', 'Bearer private-test')]
        async with httpx.AsyncClient(trust_env=False) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(origin + '/v1/models')
        profiles = json.loads(config.read_text())
        profiles[0]['server_name'] = 'wrong.internal'
        config.write_text(json.dumps(profiles))
        wrong = ModelTLSPolicy(config, (origin,))
        async with wrong.client(origin) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(origin + '/v1/models')
        assert len(received) == 1  # No HTTP request sent after a failed handshake.
    asyncio.run(run())
    with pytest.raises(ValueError):
        ModelTLSPolicy(config, ())  # TLS trust cannot add an unapproved destination.


def test_native_agent_uses_same_scoped_tls_and_restores_client(private_model):
    from agentscope.agent import Agent, ReActConfig
    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel
    from agentscope.message import Msg, TextBlock
    from traceglass.accounts.model_tls import ModelTLSPolicy, ModelTLSMiddleware
    config, origin, received = private_model

    async def run():
        model = OpenAIChatModel(credential=OpenAICredential(api_key='native-private', base_url=origin+'/v1'),
                               model='fixture', max_retries=0)
        original = model.client
        agent = Agent(name='test', system_prompt='Return a short response.', model=model,
            react_config=ReActConfig(max_iters=2), middlewares=[ModelTLSMiddleware(ModelTLSPolicy(config, (origin,)))])
        try:
            reply = await agent.reply(Msg(name='user', role='user', content=[TextBlock(text='hello')]))
            assert any(b.type == 'text' and b.text == 'TLS_OK' for b in reply.content)
            assert model.client is original
            assert received == [('/v1/chat/completions', 'Bearer native-private')]
        finally:
            await original.close()
    asyncio.run(run())


def test_account_discovery_and_native_chat_share_tls_profile(private_model, tmp_path, monkeypatch):
    from agentscope.message import Msg, TextBlock
    from agentscope.workspace import LocalWorkspace
    from traceglass.api.app import create_app
    from traceglass.config import Settings
    from traceglass.workspaces.manager import ProductDockerWorkspaceManager
    import traceglass.accounts.models as routes
    config, origin, received = private_model
    workspaces = []

    async def local_workspace(self, user_id, agent_id, session_id, workspace_id=None):
        root = self.host_root(user_id, agent_id, workspace_id)
        root.mkdir(parents=True, exist_ok=True)
        ws = LocalWorkspace(workdir=str(root), instructions='TLS integration test.')
        await ws.initialize()
        workspaces.append(ws)
        return ws

    async def allow_test_server(url):
        assert url == origin + '/v1'

    monkeypatch.setattr(ProductDockerWorkspaceManager, 'get_workspace', local_workspace)
    monkeypatch.setattr(routes, 'public_destination', allow_test_server)

    async def run():
        app = create_app(settings=Settings(data_dir=tmp_path/'app', workspace_backend='docker', auth_enabled=True,
            allowed_origins=('http://testserver',), model_allowed_origins=(origin,), model_tls_config=config))
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                    headers={'Origin': 'http://testserver'}) as client:
                    registered = await client.post('/auth/register', json={'username': 'tlsuser', 'password': 'Test-password-2026!'})
                    assert registered.status_code == 201
                    uid = registered.json()['user']['id']
                    client.headers['X-Traceglass-CSRF'] = registered.json()['csrf_token']
                    body = {'base_url': origin+'/v1', 'model': 'fixture', 'api_key': 'account-private'}
                    assert (await client.put('/product/model', json=body)).status_code == 200
                    response = await client.post('/product/model/discover', json={**body, 'api_key': ''})
                    assert response.json() == {'models': ['fixture']}, response.text
                    wid = (await client.post('/product/workspaces', json={'name': 'TLS'})).json()['workspace_id']
                    session = (await client.post(f'/product/workspaces/{wid}/sessions', json={})).json()
                    await app.state.chat_service.run(uid, session['session_id'], session['agent_id'],
                        Msg(name='user', role='user', content=[TextBlock(text='Return a short response.')]))
                    response = await client.get(f"/sessions/{session['session_id']}/messages", params={'agent_id': session['agent_id']})
                    assert 'TLS_OK' in response.text
                    assert 'account-private' not in response.text
        finally:
            for ws in workspaces:
                await ws.close()
    asyncio.run(run())
    assert received == [('/v1/models', 'Bearer account-private'), ('/v1/chat/completions', 'Bearer account-private')]
