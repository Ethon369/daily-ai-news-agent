import httpx
import pytest

from app.agent import AgentError, DeepSeekClient
from conftest import run


def test_api_failure_does_not_leak_response(settings,monkeypatch):
    settings.api_key='private-key'
    class Client:
        def __init__(self,*a,**k):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,*a,**k):return httpx.Response(401,text='secret credential private-key')
    monkeypatch.setattr('app.agent.httpx.AsyncClient',Client)
    with pytest.raises(AgentError,match='401') as exc:run(DeepSeekClient(settings).complete([],[]))
    assert 'private-key' not in str(exc.value)


def test_rate_limit_retry(settings,monkeypatch):
    settings.api_key='private-key'
    responses=iter([httpx.Response(429),httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}],'usage':{}})])
    class Client:
        def __init__(self,*a,**k):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,*a,**k):return next(responses)
    async def no_wait(*a):pass
    monkeypatch.setattr('app.agent.httpx.AsyncClient',Client)
    monkeypatch.setattr('app.agent.asyncio.sleep',no_wait)
    assert run(DeepSeekClient(settings).complete([],[]))[0]['content']=='{}'


def test_connection_failure_retried(settings,monkeypatch):
    settings.api_key='private-key'
    attempts=[]
    class Client:
        def __init__(self,*a,**k):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,*a,**k):
            attempts.append(1)
            if len(attempts)==1:raise httpx.ConnectError('connection reset')
            return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    async def no_wait(*a):pass
    monkeypatch.setattr('app.agent.httpx.AsyncClient',Client)
    monkeypatch.setattr('app.agent.asyncio.sleep',no_wait)
    assert run(DeepSeekClient(settings).complete([],[]))[0]['content']=='{}'
    assert len(attempts)==2
