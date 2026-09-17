import csv
import io
import json
import sqlite3

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import config, sync
from app.main import app
from app.models import Conversation, Message, Source


@pytest.fixture
def seeded(isolated_storage, monkeypatch):
    command.upgrade(Config(str(config.ROOT / 'backend' / 'alembic.ini')), 'head')
    async def forbid_mcp(*args):
        pytest.fail('archive must work without MCP')
    monkeypatch.setattr(sync, 'call_tool', forbid_mcp)
    with Session(isolated_storage) as db, db.begin():
        a = Source(provider='epointmsg', account_key='default', label='我的来源')
        b = Source(provider='epointmsg', account_key='second', label='另一个来源')
        db.add_all([a, b]); db.flush()
        c = Conversation(source_id=a.id, conversation_type=3, target_id='group', title='工程讨论群')
        other = Conversation(source_id=b.id, conversation_type=1, target_id='person', title='另一个私聊')
        db.add_all([c, other]); db.flush()
        ids = []
        for i in range(65):
            text = '中文测试 预算确认' if i == 0 else '字面 %_ 测试' if i == 1 else '=SUM(1,2)' if i == 2 else f'归档消息 {i}'
            msg = Message(conversation_id=c.id, dedup_key=str(i), sender_id='user-a', sender_name='张三',
                          sent_time=1000+i, sent_time_readable=f'客户端时间 {i}', message_type='TextMessage', text_content=text,
                          content_json=json.dumps({'content': {'content': text, 'extra': {'access_token': 'SECRET', 'imageInfo': 12}}}))
            db.add(msg); db.flush(); ids.append(msg.id)
        db.add(Message(conversation_id=other.id, dedup_key='other', sender_id='user-b', sender_name='李四', sent_time=2000,
                       sent_time_readable='另外的客户端时间', message_type='FileMessage', text_content='',
                       content_json=json.dumps({'content': {'name': '预算.xlsx', 'localPath': 'C:/test/预算.xlsx'}})))
        db.flush()
        return {'source': a.id, 'other_source': b.id, 'conversation': c.id, 'ids': ids, 'other_conversation': other.id}


def test_local_paging_scope_and_focus(seeded):
    with TestClient(app) as client:
        sources = client.get('/api/archive/sources').json()
        assert len(sources['list']) == 2
        rows = client.get('/api/archive/conversations?q=工程&conversation_type=3').json()
        assert rows['total'] == 1 and rows['list'][0]['message_count'] == 65
        page = client.get('/api/archive/messages').json()
        assert page['total'] == 65 and len(page['list']) == 50
        assert page['list'][0]['sent_time_readable'] == '客户端时间 64'
        focused = client.get(f'/api/archive/messages?focus_id={seeded["ids"][0]}').json()
        assert focused['page'] == 2
        assert seeded['ids'][0] in [row['id'] for row in focused['list']]
        assert client.get(f'/api/archive/messages?conversation_id={seeded["other_conversation"]}').json()['total'] == 0
        assert client.get('/api/archive/messages?focus_id=99999').status_code == 404


def test_chinese_literal_and_sender_filters(seeded):
    with TestClient(app) as client:
        assert client.get('/api/archive/messages', params={'q': '预算', 'sender': '张三', 'start_time': 1000, 'end_time': 1000}).json()['total'] == 1
        assert client.get('/api/archive/messages', params={'q': '%_'}).json()['total'] == 1
        assert client.get('/api/archive/messages', params={'q': "' OR 1=1 --"}).json()['total'] == 0
        assert client.get('/api/archive/messages', params={'sender': '%'}).json()['total'] == 0
        assert client.get('/api/archive/messages?start_time=10&end_time=1').status_code == 422
        assert client.get('/api/archive/conversations?conversation_type=2').status_code == 422
        assert client.get('/api/archive/messages?page_size=101').status_code == 422
        files = client.get('/api/archive/messages', params={'source_id': seeded['other_source'], 'q': '预算'}).json()
        assert files['total'] == 1


def test_json_and_csv_exports(seeded):
    with TestClient(app) as client:
        response = client.get('/api/archive/export?format=json')
        data = response.json()
        assert len(data['messages']) == 65
        assert data['messages'][0]['text_content'] == '中文测试 预算确认'
        assert 'SECRET' not in response.text
        assert 'attachment' in response.headers['content-disposition']
        filtered = client.get('/api/archive/export', params={'format': 'json', 'q': '预算', 'end_time': 1000}).json()
        assert len(filtered['messages']) == 1
        csv_response = client.get('/api/archive/export?format=csv')
        rows = list(csv.DictReader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
        assert len(rows) == 65
        assert rows[2]['text_content'] == "'=SUM(1,2)"
        assert 'SECRET' not in csv_response.text
        assert client.get('/api/archive/export?format=xlsx').status_code == 422
        assert client.get('/api/archive/export?q=不存在').json()['messages'] == []


def test_online_backup_and_download(seeded, tmp_path):
    with TestClient(app) as client:
        result = client.post('/api/archive/backups')
        assert result.status_code == 200
        filename = result.json()['filename']
        path = tmp_path / 'backups' / filename
        with sqlite3.connect(path) as restored:
            assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert restored.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 66
            assert restored.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '0002'
        download = client.get(f'/api/archive/backups/{filename}')
        assert download.status_code == 200 and download.content[:16] == b'SQLite format 3\x00'
        assert client.get('/api/archive/backups/setting_rong.json').status_code == 404
        second = client.post('/api/archive/backups').json()
        assert second['filename'] != filename


def test_current_account_and_frozen_export_scope(seeded):
    from app.archive import Filters, export_rows
    selected_scope = Filters(None, None, '', '', None, None, 'default')
    config.save_settings(config.Settings(account_key='second'))
    with TestClient(app) as client:
        assert client.get('/api/archive/messages').json()['total'] == 1
        assert client.get('/api/archive/conversations').json()['list'][0]['title'] == '另一个私聊'
    exported = json.loads(''.join(export_rows(selected_scope, 'json')))
    assert len(exported['messages']) == 65


def test_equal_timestamps_focus(seeded, isolated_storage):
    from sqlalchemy import update
    with Session(isolated_storage) as db, db.begin():
        db.execute(update(Message).where(Message.conversation_id == seeded['conversation']).values(sent_time=1000))
    with TestClient(app) as client:
        first = client.get('/api/archive/messages?page=1').json()['list']
        second = client.get('/api/archive/messages?page=2').json()['list']
        assert len({row['id'] for row in first + second}) == 65
        focused = client.get(f'/api/archive/messages?focus_id={seeded["ids"][0]}').json()
        assert focused['page'] == 2


def test_backup_busy_and_sanitized_failure(seeded, monkeypatch):
    from app import backup_api
    with TestClient(app) as client:
        backup_api.backup_lock.acquire()
        try:
            assert client.post('/api/archive/backups').status_code == 409
        finally:
            backup_api.backup_lock.release()
        def failed():
            raise OSError('SECRET filesystem details')
        monkeypatch.setattr(backup_api, 'create_backup', failed)
        result = client.post('/api/archive/backups')
        assert result.status_code == 500 and 'SECRET' not in result.text


@pytest.mark.parametrize('text', ['=1+1', ' +SUM(1)', '-1', '@SUM(1)', '\tplain', '\rplain', '\nplain'])
def test_csv_formula_guard(text):
    from app.archive import csv_cell
    assert csv_cell(text).startswith("'")
