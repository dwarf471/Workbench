from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app import config
from app.main import app


def test_health_and_migration(isolated_storage):
    with TestClient(app) as client:
        response = client.get('/api/health')
        assert response.status_code == 200
        assert response.json()['database']['revision'] == '0002'
        assert 'sources' in inspect(isolated_storage).get_table_names()
        assert 'messages' in inspect(isolated_storage).get_table_names()


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CONFIG_PATH', tmp_path / 'settings.json')
    with TestClient(app) as client:
        settings = client.get('/api/settings').json()
        settings['account_label'] = 'test account'
        response = client.put('/api/settings', json=settings)
        assert response.status_code == 200
        assert client.get('/api/settings').json() == settings
        assert 'token' not in (tmp_path / 'settings.json').read_text(encoding='utf-8')
        settings['mcp_url'] = 'http://example.com/mcp'
        assert client.put('/api/settings', json=settings).status_code == 422


def test_cross_origin_denied():
    with TestClient(app) as client:
        assert client.post('/api/connection/check', headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.get('/api/health', headers={'Host': 'evil.example'}).status_code == 400


def test_missing_token_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CONFIG_PATH', tmp_path / 'settings.json')
    config.save_settings(config.Settings(settings_path=str(tmp_path / 'setting_rong.json')))
    with TestClient(app) as client:
        response = client.post('/api/connection/check')
        assert response.status_code == 200
        assert response.json()['code'] == 'TOKEN_READ_FAILED'
        assert str(tmp_path) not in response.text
