import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app
from app.prompts import DEFAULT_EXTRACTION_PROMPT


@pytest.fixture(autouse=True)
def model_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, 'PATH', tmp_path / 'model.json')


def test_key_roundtrip():
    if os.name != 'nt':
        pytest.skip('Windows DPAPI')
    secret = b'test-key-not-a-real-credential'
    encrypted = llm.protect(secret)
    assert secret not in encrypted
    assert llm.protect(encrypted, decrypt=True) == secret


def test_config_and_key_lifecycle():
    with TestClient(app) as client:
        assert not client.get('/api/model/settings').json()['key_configured']
        value = {'base_url': 'https://example.com/v1/', 'model': 'test', 'api_key': 'fake-key'}
        response = client.put('/api/model/settings', json=value)
        assert response.status_code == 200
        assert response.json()['key_configured']
        assert 'fake-key' not in response.text
        assert 'fake-key' not in llm.PATH.read_text(encoding='utf-8')
        value.pop('api_key')
        assert client.put('/api/model/settings', json=value).json()['key_configured']
        assert 'encrypted_key' not in client.get('/api/model/settings').text
        value['clear_key'] = True
        assert not client.put('/api/model/settings', json=value).json()['key_configured']


@pytest.mark.parametrize('address', ['https://user:fake-key@example.com', 'https://example.com/v1?token=fake-key', 'ftp://localhost/v1', 'https://example.com:bad/v1'])
def test_bad_addresses_do_not_echo_secrets(address):
    with TestClient(app) as client:
        response = client.put('/api/model/settings', json={'base_url': address, 'api_key': 'fake-key'})
        assert response.status_code == 422
        assert 'fake-key' not in response.text


@pytest.mark.parametrize('address', ['http://example.com/v1', 'http://192.168.1.10:8000/v1', 'http://localhost:1234/v1', 'http://127.0.0.1:1234/v1', 'http://[::1]:1234/v1', 'https://example.com/v1'])
def test_allowed_addresses(address):
    assert llm.ModelSettings(base_url=address).base_url == address


@pytest.mark.parametrize('status,body,connected', [(200, {'choices': [{'message': {'content': 'OK'}}]}, True), (200, {}, False), (302, {}, False), (401, {}, False), (500, {'error': 'fake-key'}, False)])
def test_fixed_prompt_only(monkeypatch, status, body, connected):
    original = httpx.AsyncClient
    def handler(request):
        value = json.loads(request.content)
        assert value['messages'] == [{'role': 'user', 'content': 'Connection test. Reply OK.'}]
        assert str(request.url) == 'https://example.com/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer fake-key'
        return httpx.Response(status, json=body, headers={'Location': 'https://other.example'})
    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False
        assert kwargs['trust_env'] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(llm.httpx, 'AsyncClient', factory)
    with TestClient(app) as client:
        client.put('/api/model/settings', json={'base_url': 'https://example.com/v1', 'model': 'test', 'api_key': 'fake-key'})
        response = client.post('/api/model/check')
        assert response.json()['connected'] is connected
        assert 'fake-key' not in response.text


def test_missing_settings_and_timeout(monkeypatch):
    with TestClient(app) as client:
        assert client.post('/api/model/check').status_code == 422
        assert client.put('/api/model/settings', json={'timeout': 0, 'api_key': 'fake-key'}).status_code == 422


def test_network_timeout(monkeypatch):
    original = httpx.AsyncClient
    def handler(request):
        raise httpx.ReadTimeout('fake-key internal details', request=request)
    monkeypatch.setattr(llm.httpx, 'AsyncClient', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        client.put('/api/model/settings', json={'base_url': 'https://example.com/v1', 'model': 'test'})
        response = client.post('/api/model/check')
        assert response.json() == {'connected': False, 'message': '模型请求超时'}


def test_corrupt_credentials_are_sanitized():
    llm.PATH.write_text(json.dumps({'base_url': 'https://example.com/v1', 'model': 'test', 'encrypted_key': 'broken'}))
    with TestClient(app) as client:
        response = client.post('/api/model/check')
        assert not response.json()['connected']
        assert 'broken' not in response.text


def test_prompt_defaults_custom_roundtrip_and_omitted_field():
    llm.PATH.write_text(json.dumps({'base_url': 'https://example.com/v1', 'model': 'test'}))
    with TestClient(app) as client:
        assert client.get('/api/model/settings').json()['extraction_prompt'] == DEFAULT_EXTRACTION_PROMPT
        assert client.get('/api/model/prompt-default').json()['extraction_prompt'] == DEFAULT_EXTRACTION_PROMPT
        custom = '优先提取权限调整需求。\n保留角色、资源和操作范围。'
        assert client.put('/api/model/settings', json={'extraction_prompt': custom}).json()['extraction_prompt'] == custom
        assert client.get('/api/model/settings').json()['extraction_prompt'] == custom
        assert json.loads(llm.PATH.read_text(encoding='utf-8'))['extraction_prompt'] == custom
        assert client.put('/api/model/settings', json={'model': 'new-model'}).json()['extraction_prompt'] == custom


@pytest.mark.parametrize('prompt', ['', '  \n ', '中' * 8001, None], ids=['empty', 'whitespace', 'too-long', 'null'])
def test_prompt_validation_does_not_echo_input(prompt):
    with TestClient(app) as client:
        response = client.put('/api/model/settings', json={'extraction_prompt': prompt, 'api_key': 'fake-key'})
        assert response.status_code == 422
        assert 'fake-key' not in response.text
