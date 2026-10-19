from contextlib import closing
import json
from pathlib import Path
import socket
import sqlite3
import threading

from fastapi.testclient import TestClient
import httpx
import pytest

from app.desktop_http import COOKIE
from app.desktop_runtime import LocalService, activate_existing
from app.desktop_storage import import_workspace
from app.main import app


@pytest.fixture
def desktop_app(monkeypatch):
    monkeypatch.setattr(app.state, 'desktop_token', 'session-test-only', raising=False)
    monkeypatch.setattr(app.state, 'desktop_origin', 'http://127.0.0.1:32123', raising=False)
    monkeypatch.setattr(app.state, 'desktop_activate', lambda: None, raising=False)
    return app


def test_desktop_requires_session_for_api_and_assets(desktop_app):
    with TestClient(desktop_app, base_url='http://127.0.0.1:32123') as client:
        for path in ('/', '/api/health', '/api/settings', '/api/archive/backups/nope'):
            assert client.get(path).status_code == 401
        assert client.get('/_desktop/bootstrap?token=wrong').status_code == 401
        response = client.get('/_desktop/bootstrap?token=session-test-only', follow_redirects=False)
        assert response.status_code == 303
        assert 'HttpOnly' in response.headers['set-cookie']
        assert 'SameSite=strict' in response.headers['set-cookie']
        assert client.get('/api/health').status_code == 200
        assert client.post('/api/connection/check', headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.get('/api/health', headers={'Host': '127.0.0.1:1234'}).status_code == 400
        assert client.get('/api/health', headers={'Origin': 'http://127.0.0.1:32124'}).status_code == 403


def test_service_uses_free_port_activates_and_releases_it(desktop_app, tmp_path):
    activated = threading.Event()
    service = LocalService(desktop_app, activated.set)
    session_file = tmp_path / 'session.json'
    try:
        service.start()
        service.publish(session_file)
        assert activate_existing(session_file, timeout=2)
        assert activated.wait(1)
        with httpx.Client(trust_env=False) as client:
            assert client.get(service.origin + '/api/health').status_code == 401
            assert client.get(service.origin + '/api/health', headers={
                'Authorization': 'Bearer ' + service.token}).json()['database']['revision'] == '0004'
    finally:
        service.stop()
    assert not service.thread.is_alive()
    with socket.socket() as connection:
        assert connection.connect_ex(('127.0.0.1', int(service.origin.rsplit(':', 1)[1]))) != 0


def make_source(root, version='0004'):
    data = root / 'data'
    data.mkdir(parents=True)
    source = sqlite3.connect(data / 'workbench.sqlite3')
    source.execute('PRAGMA journal_mode=WAL')
    source.executescript('CREATE TABLE alembic_version (version_num TEXT); CREATE TABLE sample (text TEXT);')
    source.execute('INSERT INTO alembic_version VALUES (?)', (version,))
    source.execute('INSERT INTO sample VALUES (?)', ('尚在 WAL 中的中文归档',))
    source.commit()
    return source


def test_import_includes_wal_and_preserves_source(tmp_path):
    root, target = tmp_path / 'old', tmp_path / 'desktop'
    with closing(make_source(root)) as source:
        import_workspace(root, target)
        assert source.execute('SELECT text FROM sample').fetchone()[0] == '尚在 WAL 中的中文归档'
        with closing(sqlite3.connect(target / 'workbench.sqlite3')) as imported:
            assert imported.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert imported.execute('SELECT text FROM sample').fetchone()[0] == '尚在 WAL 中的中文归档'
        with pytest.raises(ValueError, match='已有数据'):
            import_workspace(root, target)


def test_import_rejects_future_schema_without_partial_files(tmp_path):
    root, target = tmp_path / 'old', tmp_path / 'desktop'
    with closing(make_source(root, '9999')):
        with pytest.raises(ValueError, match='版本'):
            import_workspace(root, target)
    assert not list(target.iterdir())


def test_import_rejects_invalid_config_without_partial_files(tmp_path):
    root, target = tmp_path / 'old', tmp_path / 'desktop'
    with closing(make_source(root)):
        (root / 'workbench.local.json').write_text('{', encoding='utf-8')
        with pytest.raises(ValueError):
            import_workspace(root, target)
    assert not list(target.iterdir())


def test_import_preserves_prompt_but_drops_unreadable_key(tmp_path):
    root, target = tmp_path / 'old', tmp_path / 'desktop'
    with closing(make_source(root)):
        (root / 'data' / 'model.local.json').write_text(json.dumps({
            'model': 'example', 'extraction_prompt': '自定义提示词', 'encrypted_key': 'unreadable'}), encoding='utf-8')
        import_workspace(root / 'data', target)
    settings = json.loads((target / 'model.local.json').read_text(encoding='utf-8'))
    assert settings['extraction_prompt'] == '自定义提示词'
    assert not settings['encrypted_key']


def test_native_save_rejects_remote_and_unapproved_routes(tmp_path):
    from desktop import DesktopBridge
    bridge = DesktopBridge(tmp_path)
    for url in ('https://example.com/', '//example.com/api/archive/export', '/api/settings', '/api/archive/export/../settings'):
        assert bridge.save_file(url, 'test.json').get('error')


def test_native_save_cancel_and_atomic_failure(tmp_path, monkeypatch):
    from desktop import DesktopBridge
    from types import SimpleNamespace
    bridge = DesktopBridge(tmp_path / 'desktop')
    bridge._service = SimpleNamespace(origin='http://127.0.0.1:32123', token='fake')
    bridge._window = SimpleNamespace(create_file_dialog=lambda *a, **kw: None)
    assert bridge.save_file('/api/archive/export', '聊天.json') == {'saved': False}
    output = tmp_path / '聊天.json'
    output.write_text('original', encoding='utf-8')
    bridge._window = SimpleNamespace(create_file_dialog=lambda *a, **kw: (str(output),))
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(**kwargs,
        transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    assert bridge.save_file('/api/archive/export', '聊天.json').get('error')
    assert output.read_text(encoding='utf-8') == 'original'
    assert not list(tmp_path.glob('.workbench-*'))


def test_native_save_uses_authenticated_local_response(tmp_path, monkeypatch):
    from desktop import DesktopBridge
    from types import SimpleNamespace
    bridge = DesktopBridge(tmp_path / 'desktop')
    bridge._service = SimpleNamespace(origin='http://127.0.0.1:32123', token='fake')
    output = tmp_path / '中文.json'
    bridge._window = SimpleNamespace(create_file_dialog=lambda *a, **kw: (str(output),))
    original = httpx.Client
    def handler(request):
        assert request.headers['authorization'] == 'Bearer fake'
        assert request.url.host == '127.0.0.1'
        return httpx.Response(200, content='中文内容'.encode())
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    assert bridge.save_file('/api/archive/export?format=json', '中文.json') == {'saved': True}
    assert output.read_text(encoding='utf-8') == '中文内容'
