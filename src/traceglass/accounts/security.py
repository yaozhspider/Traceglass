"""Authenticate the entire HTTP surface before invoking any native router."""
import asyncio
import hmac
import json
import re
from starlette.requests import Request
from starlette.responses import JSONResponse

COOKIE = 'traceglass_session'
CSRF_HEADER = 'x-traceglass-csrf'
PUBLIC = {('/auth/session','GET'),('/auth/login','POST'),('/auth/register','POST')}


def permitted(path: str, method: str) -> bool:
    if (path,method) in PUBLIC or (path,method) == ('/auth/logout','POST'):
        return True
    if path.startswith('/product/'):
        return True
    if path == '/chat/' and method == 'POST':
        return True
    if path == '/sessions/' and method == 'GET':
        return True
    return bool(re.fullmatch(r'/sessions/[^/]+(?:/(?:messages|status|stream))?',path)) and method in {'GET','PATCH'}


class AuthenticationMiddleware:
    def __init__(self, app, accounts, storage, settings):
        self.app, self.accounts, self.storage, self.settings = app, accounts, storage, settings

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http','websocket'}:
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            return await send({'type':'websocket.close','code':1008})
        request = Request(scope, receive)
        path, method = scope['path'], scope['method']
        async def reject(status, message):
            await JSONResponse({'detail':message},status_code=status)(scope,receive,send)
        if not permitted(path,method):
            return await reject(404,'接口不可用')
        state = scope.setdefault('state',{})
        session = await asyncio.to_thread(self.accounts.session, request.cookies.get(COOKIE,''))
        state['account'] = session
        public = (path,method) in PUBLIC
        if not session and not public:
            return await reject(401,'请先登录')
        if method not in {'GET','HEAD','OPTIONS'}:
            origin = request.headers.get('origin')
            if origin and origin not in self.settings.allowed_origins:
                return await reject(403,'请求来源不受信任')
            if public and not origin:
                return await reject(403,'注册和登录需要可信的 Origin')
            if not public and (not session or not hmac.compare_digest(request.headers.get(CSRF_HEADER,''),session['csrf'])):
                return await reject(403,'登录校验已失效，请刷新页面')
        if session:
            # Ignore every caller-supplied identity, including native routes.
            scope['headers'] = [(k,v) for k,v in scope['headers'] if k.lower() != b'x-user-id'] + [(b'x-user-id',session['id'].encode())]
        json_write = method in {'POST','PUT','PATCH'} and not (path.startswith('/product/workspaces/') and path.endswith('/samples'))
        if json_write:
            chunks, size = [], 0
            maximum = 1024*1024 if path == '/chat/' else 64*1024
            while True:
                event = await receive()
                if event['type'] == 'http.disconnect':
                    return
                chunk=event.get('body',b''); size+=len(chunk)
                if size > maximum:
                    return await reject(413,'请求过大')
                chunks.append(chunk)
                if not event.get('more_body'): break
            body=b''.join(chunks)
            try:
                data=json.loads(body) if body else {}
                if not isinstance(data,dict): raise ValueError()
            except (ValueError,TypeError):
                return await reject(400,'无效请求')
            if path.startswith('/sessions/') and method == 'PATCH' and set(data)-{'name','chat_model_config','permission_mode'}:
                return await reject(403,'此页面只能修改名称、模型和执行权限')
            if path == '/chat/' or path.startswith('/sessions/'):
                sid=data.get('session_id') if path == '/chat/' else path.split('/')[2]
                aid=data.get('agent_id') if path == '/chat/' else request.query_params.get('agent_id')
                if not isinstance(sid,str) or not isinstance(aid,str) or await self.storage.get_session(session['id'],aid,sid) is None:
                    return await reject(404,'会话不存在')
            sent=False
            original_receive=receive
            async def replay():
                nonlocal sent
                if not sent:
                    sent=True
                    return {'type':'http.request','body':body,'more_body':False}
                return await original_receive()
            receive=replay
        elif not public and path.startswith('/sessions/') and path != '/sessions/':
            sid=path.split('/')[2]; aid=request.query_params.get('agent_id','')
            if await self.storage.get_session(session['id'],aid,sid) is None:
                return await reject(404,'会话不存在')
        async def secured_send(message):
            if message['type']=='http.response.start':
                message['headers'] = list(message.get('headers',[]))+[(b'cache-control',b'no-store'),(b'x-content-type-options',b'nosniff')]
            await send(message)
        await self.app(scope,receive,secured_send)
