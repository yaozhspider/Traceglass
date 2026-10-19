"""Offline account-to-native-model smoke; only workspace and model I/O are replaced."""
import asyncio
import httpx
from agentscope.message import Msg, TextBlock
from agentscope.model import ChatResponse, OpenAIChatModel
from agentscope.workspace import LocalWorkspace
from traceglass.api.app import create_app
from traceglass.config import Settings
from traceglass.workspaces.manager import ProductDockerWorkspaceManager


def test_personal_credentials_reach_native_model_without_cross_account_fallback(tmp_path, monkeypatch):
    for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        monkeypatch.delenv(name,raising=False)
    seen=[]
    workspaces=[]

    async def offline_workspace(self,user_id,agent_id,session_id,workspace_id=None):
        root=self.host_root(user_id,agent_id,workspace_id)
        root.mkdir(parents=True,exist_ok=True)
        ws=LocalWorkspace(workdir=str(root),instructions='Offline account test.')
        await ws.initialize()
        workspaces.append(ws)
        return ws

    async def provider(self,model_name,messages,tools=None,**kwargs):
        seen.append((self.client.api_key,str(self.client.base_url),model_name))
        return ChatResponse(content=[TextBlock(text='ACCOUNT_MODEL_OK')],is_last=True)

    monkeypatch.setattr(ProductDockerWorkspaceManager,'get_workspace',offline_workspace)
    monkeypatch.setattr(OpenAIChatModel,'_call_api',provider)

    async def run():
        app=create_app(settings=Settings(data_dir=tmp_path,workspace_backend='docker',auth_enabled=True,
            allowed_origins=('http://testserver',),model_name='must-not-inherit',api_key='deployment-secret'))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',
                                         headers={'Origin':'http://testserver'}) as client:
                try:
                    for username,model,key,url in [('alice','alice-model','alice-private','https://api.deepseek.com/v1'),
                                                    ('bob','bob-model','bob-private','https://api.openai.com/v1')]:
                        registered=await client.post('/auth/register',json={'username':username,'password':'Test-password-2026!'})
                        assert registered.status_code==201,registered.text
                        uid=registered.json()['user']['id']
                        client.headers['X-Traceglass-CSRF']=registered.json()['csrf_token']
                        assert not (await client.get('/product/config')).json()['model_configured']
                        saved=await client.put('/product/model',json={'base_url':url,'model':model,'api_key':key})
                        assert saved.status_code==200,saved.text
                        wid=(await client.post('/product/workspaces',json={'name':username})).json()['workspace_id']
                        session=(await client.post(f'/product/workspaces/{wid}/sessions',json={})).json()
                        await app.state.chat_service.run(user_id=uid,agent_id=session['agent_id'],session_id=session['session_id'],
                            input_msg=Msg(name='user',role='user',content=[TextBlock(text='Return a short test response.')]))
                        response=await client.get(f"/sessions/{session['session_id']}/messages",params={'agent_id':session['agent_id']})
                        assert 'ACCOUNT_MODEL_OK' in response.text,response.text
                        assert (await client.post('/auth/logout')).status_code==204
                finally:
                    for ws in workspaces: await ws.close()
    asyncio.run(run())
    assert seen==[('alice-private','https://api.deepseek.com/v1/','alice-model'),
                  ('bob-private','https://api.openai.com/v1/','bob-model')]
