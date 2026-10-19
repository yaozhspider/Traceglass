"""Personal OpenAI-compatible model defaults, discovery and streaming probes."""
import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlsplit
from uuid import uuid4
import httpx
from agentscope.credential import OpenAICredential
from agentscope.app.deps import get_current_user_id
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

PRESETS = [
    {'id':'openai','name':'OpenAI','base_url':'https://api.openai.com/v1','model':''},
    {'id':'deepseek','name':'DeepSeek','base_url':'https://api.deepseek.com/v1','model':'deepseek-chat'},
    {'id':'asiainfo','name':'亚信安全 / DeepSeek','base_url':'https://llm.asiainfo-sec.com/v1','model':'deepseek-v4-pro'},
    {'id':'qwen','name':'阿里云百炼 / 通义千问','base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1','model':'qwen-plus'},
    {'id':'glm','name':'智谱 GLM','base_url':'https://open.bigmodel.cn/api/paas/v4','model':'glm-4.6'},
    {'id':'moonshot','name':'Moonshot / Kimi','base_url':'https://api.moonshot.cn/v1','model':'kimi-k2.5'},
    {'id':'siliconflow','name':'硅基流动','base_url':'https://api.siliconflow.cn/v1','model':''},
    {'id':'volcengine','name':'火山方舟 / 豆包','base_url':'https://ark.cn-beijing.volces.com/api/v3','model':''},
    {'id':'minimax','name':'MiniMax','base_url':'https://api.minimaxi.com/v1','model':''},
]


class ModelInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    base_url: str = Field(min_length=8,max_length=500)
    model: str = Field(default='',max_length=200)
    api_key: SecretStr = Field(default=SecretStr(''),max_length=4096)


def validate_url(raw: str, hosts: tuple[str,...], origins: tuple[str,...] = ()) -> str:
    url=raw.strip().rstrip('/')
    try:
        parsed=urlsplit(url)
        allowed = ((parsed.hostname in hosts and parsed.port in (None,443)) or
                   any((parsed.scheme, parsed.hostname, parsed.port or 443) ==
                       (entry.scheme, entry.hostname, entry.port or 443)
                       for entry in map(urlsplit, origins)))
        valid=(parsed.scheme=='https' and parsed.hostname and parsed.port != 0 and allowed
               and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
               and not any(ord(char)<33 for char in url) and '\\' not in url
               and '..' not in parsed.path.split('/'))
    except ValueError:
        valid=False
    if not valid:
        raise HTTPException(422,'API 地址须使用 HTTPS；此主机和端口须由管理员加入允许列表，且不能包含账号、查询参数或片段')
    # Users often paste the complete curl endpoint. The SDK and probes append
    # /chat/completions themselves, so persist the common API base instead.
    if parsed.path.endswith('/chat/completions'):
        url=url[:-len('/chat/completions')]
    return url


async def public_destination(url: str):
    parsed=urlsplit(url)
    host=parsed.hostname
    try:
        records=await asyncio.to_thread(socket.getaddrinfo,host,parsed.port or 443,type=socket.SOCK_STREAM)
        if not records or any(not ipaddress.ip_address(record[4][0]).is_global for record in records):
            raise ValueError()
    except (OSError,ValueError):
        raise HTTPException(422,'模型地址无法解析为公网服务') from None


def chat_config(profile: dict | None):
    return {'type':'openai_chat','credential_id':profile['credential_id'],'model':profile['model'],'parameters':{}} if profile else None


async def bounded_json(client, method, url, **kwargs):
    async with client.stream(method,url,**kwargs) as response:
        response.raise_for_status()
        data=bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data)>2*1024*1024: raise ValueError('response too large')
        return json.loads(data)


async def probe_stream(client, url, model):
    payload={'model':model,'messages':[{'role':'user','content':'Call connection_check with ok=true.'}],
             'tools':[{'type':'function','function':{'name':'connection_check','description':'Validate tool calling.',
                       'parameters':{'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok']}}}],
             'tool_choice':'auto','stream':True,'max_tokens':128}
    tool_name, args, seen, size='', '', False, 0
    async with client.stream('POST',url+'/chat/completions',json=payload) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            size+=len(line)
            if size>1024*1024: raise ValueError('response too large')
            if not line.startswith('data:'): continue
            raw=line[5:].strip()
            if raw=='[DONE]': break
            packet=json.loads(raw)
            if packet.get('error'): raise ValueError('provider error')
            for choice in packet.get('choices',[]):
                delta=choice.get('delta',{})
                seen=True
                for tool in delta.get('tool_calls',[]):
                    fn=tool.get('function',{})
                    tool_name+=fn.get('name',''); args+=fn.get('arguments','')
    try: tool_ok=tool_name=='connection_check' and json.loads(args).get('ok') is True
    except (ValueError,AttributeError): tool_ok=False
    if not seen: raise ValueError('no stream')
    return {'streaming':True,'tool_calling':tool_ok,
            'message':'连接、流式响应和工具调用验证通过。' if tool_ok else '流式连接成功，但未收到有效工具调用；请确认所选模型支持工具。'}


def create_model_router(accounts, storage, settings, tls_policy=None):
    from traceglass.accounts.model_tls import ModelTLSPolicy
    tls_policy = tls_policy or ModelTLSPolicy(settings.model_tls_config, settings.model_allowed_origins)
    router=APIRouter(prefix='/product/model',tags=['personal-model'])

    @router.get('')
    async def read(user_id: str=Depends(get_current_user_id)):
        profile=await asyncio.to_thread(accounts.model,user_id)
        return {'base_url':profile['base_url'] if profile else '', 'model':profile['model'] if profile else '',
                'has_key':bool(profile), 'presets':PRESETS, 'allowed_hosts':settings.model_allowed_hosts,
                'allowed_origins':settings.model_allowed_origins}

    async def resolve(body,user_id,require_model=True):
        url=validate_url(body.base_url,settings.model_allowed_hosts,settings.model_allowed_origins)
        model=body.model.strip()
        if require_model and (not model or any(ord(c)<32 for c in model)):
            raise HTTPException(422,'请填写模型名称')
        key=body.api_key.get_secret_value().strip()
        if not key:
            saved=await asyncio.to_thread(accounts.model,user_id)
            if not saved or saved['base_url'] != url:
                raise HTTPException(422,'请填写此服务的 API Key；更换 API 地址时必须重新填写')
            record=await storage.get_credential(user_id,saved['credential_id'])
            if not record: raise HTTPException(422,'已保存的凭据不可用，请重新填写 API Key')
            key=record.data['api_key']
        if any(ord(c)<33 or ord(c)>126 for c in key):
            raise HTTPException(422,'API Key 格式无效')
        return url,model,key

    @router.put('')
    async def save(body:ModelInput,user_id: str=Depends(get_current_user_id)):
        url,model,key=await resolve(body,user_id)
        # Version credentials: changing providers never redirects a running session's key.
        cid='personal-model-'+uuid4().hex
        await storage.upsert_credential(user_id,OpenAICredential(id=cid,name='个人模型',api_key=key,base_url=url))
        await asyncio.to_thread(accounts.save_model,user_id,cid,model,url)
        return {'base_url':url,'model':model,'has_key':True}

    async def remote(body,user_id,discover):
        if not await asyncio.to_thread(accounts.limit,'model-test:'+user_id,12,60):
            raise HTTPException(429,'测试过于频繁，请稍后重试')
        url,model,key=await resolve(body,user_id,require_model=not discover)
        await public_destination(url)
        try:
            async with asyncio.timeout(35):
                async with tls_policy.client(url, headers={'Authorization':'Bearer '+key}) as client:
                    if discover:
                        payload=await bounded_json(client,'GET',url+'/models')
                        models=sorted({m['id'] for m in payload.get('data',[]) if isinstance(m,dict) and isinstance(m.get('id'),str)})[:500]
                        return {'models':models}
                    return await probe_stream(client,url,model)
        except httpx.HTTPStatusError as exc:
            code=exc.response.status_code
            message={401:'API Key 无效或已过期',403:'此 Key 无权访问所选模型',404:'模型或接口不存在；可手动填写模型名称',429:'服务商限流或额度不足'}.get(code,'服务商拒绝了请求，请检查 API 地址和模型配置')
            raise HTTPException(400,message) from None
        except httpx.ConnectError as exc:
            # httpx/httpcore wrap the TLS exception; never expose raw exceptions
            # or request headers in the user-facing response.
            if 'CERTIFICATE_VERIFY_FAILED' in str(exc):
                raise HTTPException(400,'模型服务的 HTTPS 证书校验失败：可能是自签名、证书链不完整或地址不匹配。请配置与访问地址匹配的可信证书，或由管理员配置受信任的 CA。') from None
            raise HTTPException(400,'无法连接模型服务，请检查 API 地址、端口和网络') from None
        except (httpx.HTTPError,TimeoutError,ValueError,TypeError,KeyError):
            raise HTTPException(400,'未收到有效响应，请检查网络、API 地址及 OpenAI 流式工具调用兼容性') from None

    @router.post('/test')
    async def test(body:ModelInput,user_id: str=Depends(get_current_user_id)):
        return await remote(body,user_id,False)

    @router.post('/discover')
    async def discover(body:ModelInput,user_id: str=Depends(get_current_user_id)):
        return await remote(body,user_id,True)

    return router
