import asyncio
import json
import httpx
import pytest
from traceglass.accounts.models import probe_stream, bounded_json


def test_asiainfo_gateway_accepts_base_or_completion_url_without_duplicating_endpoint():
    from fastapi import HTTPException
    from traceglass.accounts.models import validate_url
    from traceglass.config import Settings
    settings = Settings()
    for url in ('https://llm.asiainfo-sec.com/v1',
                'https://llm.asiainfo-sec.com/v1/chat/completions',
                'https://llm.asiainfo-sec.com/v1/chat/completions/'):
        assert validate_url(url, settings.model_allowed_hosts) == 'https://llm.asiainfo-sec.com/v1'
    for url in ('https://llm.asiainfo-sec.com.evil.example/v1/chat/completions',
                'https://llm.asiainfo-sec.com:8001/v1/chat/completions',
                'https://llm.asiainfo-sec.com/v1/chat/completions?key=private'):
        with pytest.raises(HTTPException):
            validate_url(url, settings.model_allowed_hosts)


def test_explicit_model_origin_allows_only_the_configured_https_host_and_port():
    from fastapi import HTTPException
    from traceglass.accounts.models import validate_url
    hosts = ('api.deepseek.com',)
    origins = ('https://182.44.7.227:8001',)
    assert validate_url('https://182.44.7.227:8001/v1/', hosts, origins) == 'https://182.44.7.227:8001/v1'
    assert validate_url('https://api.deepseek.com/v1', hosts, origins) == 'https://api.deepseek.com/v1'
    for url in ('http://182.44.7.227:8001/v1', 'https://182.44.7.227:8002/v1',
                'https://182.44.7.227/v1', 'https://182.44.7.227.evil.example:8001/v1',
                'https://user:pass@182.44.7.227:8001/v1', 'https://182.44.7.227:8001/v1?key=secret'):
        with pytest.raises(HTTPException):
            validate_url(url, hosts, origins)


def test_public_destination_checks_configured_port(monkeypatch):
    import socket
    from traceglass.accounts.models import public_destination
    ports = []
    def lookup(host, port, **kwargs):
        ports.append(port)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('182.44.7.227', port))]
    monkeypatch.setattr(socket, 'getaddrinfo', lookup)
    asyncio.run(public_destination('https://182.44.7.227:8001/v1'))
    assert ports == [8001]


def test_stream_probe_reassembles_tool_arguments_and_checks_actual_support():
    async def run():
        def provider(request):
            payload=json.loads(request.content)
            assert payload['stream'] is True
            assert payload['tools'][0]['function']['name']=='connection_check'
            return httpx.Response(200,text='\n'.join([
                'data: '+json.dumps({'choices':[{'delta':{'tool_calls':[{'function':{'name':'connection_check','arguments':'{"ok":'}}]}}]}),
                '', 'data: '+json.dumps({'choices':[{'delta':{'tool_calls':[{'function':{'arguments':'true}'}}]}}]}),
                '', 'data: [DONE]','']))
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            result=await probe_stream(client,'https://api.deepseek.com/v1','fixture-model')
            assert result['streaming'] is True and result['tool_calling'] is True
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n'))) as client:
            result=await probe_stream(client,'https://api.deepseek.com/v1','fixture-model')
            assert result['streaming'] is True and result['tool_calling'] is False
    asyncio.run(run())


def test_probe_does_not_follow_redirects_or_accept_html():
    async def run():
        async with httpx.AsyncClient(follow_redirects=False,transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://127.0.0.1/secret'}))) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await probe_stream(client,'https://api.deepseek.com/v1','fixture')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='<html>not an API</html>'))) as client:
            with pytest.raises(ValueError):
                await probe_stream(client,'https://api.deepseek.com/v1','fixture')
    asyncio.run(run())
