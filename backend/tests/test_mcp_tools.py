import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from app import mcp_client
from app.config import Settings
from app.mcp_client import ToolFailure, call_tool, contains_401


def test_tool_json_parse(monkeypatch):
    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield None, None, None
    class FakeSession:
        def __init__(self, *args):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def initialize(self):
            return SimpleNamespace(serverInfo=SimpleNamespace(name='rongyun-im-mcp'))
        async def call_tool(self, name, arguments):
            assert name == 'get_conversation_list'
            return SimpleNamespace(isError=False, structuredContent=None,
                                   content=[SimpleNamespace(type='text', text='{"list":[],"total":0}')])
    monkeypatch.setattr(mcp_client, 'read_token', lambda _: 'ab' * 32)
    monkeypatch.setattr(mcp_client, 'streamablehttp_client', transport)
    monkeypatch.setattr(mcp_client, 'ClientSession', FakeSession)
    assert asyncio.run(call_tool(Settings(), 'get_conversation_list', {})) == {'list': [], 'total': 0}


def test_401_retry_and_safe_error(monkeypatch):
    reads = []
    def token(_):
        reads.append(1)
        return 'ab' * 32
    @asynccontextmanager
    async def transport(*args, **kwargs):
        response = httpx.Response(401, request=httpx.Request('POST', 'http://localhost/mcp'))
        raise ExceptionGroup('transport', [httpx.HTTPStatusError('SECRET', request=response.request, response=response)])
        yield
    monkeypatch.setattr(mcp_client, 'read_token', token)
    monkeypatch.setattr(mcp_client, 'streamablehttp_client', transport)
    with pytest.raises(ToolFailure) as error:
        asyncio.run(call_tool(Settings(), 'get_history_messages', {}))
    assert error.value.code == 'UNAUTHORIZED'
    assert len(reads) == 2
    assert 'SECRET' not in str(error.value)


def test_401_group():
    response = httpx.Response(401, request=httpx.Request('POST', 'http://localhost/mcp'))
    error = httpx.HTTPStatusError('unauthorized', request=response.request, response=response)
    assert contains_401(ExceptionGroup('nested', [ExceptionGroup('inner', [error])]))
