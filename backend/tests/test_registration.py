import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from filelock import FileLock
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config, extraction, requirements
from app.main import app
from app.models import Conversation, ExtractionTask, RequirementExport, RequirementRecord, Source


@pytest.fixture
def finished(isolated_storage):
    command.upgrade(Config(str(config.ROOT / 'backend' / 'alembic.ini')), 'head')
    with Session(isolated_storage) as db, db.begin():
        source = Source(provider='epointmsg', account_key='default', label='测试来源')
        db.add(source); db.flush()
        conversation = Conversation(source_id=source.id, conversation_type=3, target_id='group', title='测试群')
        db.add(conversation); db.flush()
        task = ExtractionTask(source_id=source.id, conversation_id=conversation.id, status='finished', start_time=1789574400000, end_time=1789660799999,
            config_json=json.dumps({'conversation_title': conversation.title, 'source_label': source.label, 'base_url': 'https://example.com/v1', 'model': 'test'}),
            snapshot_json=json.dumps([{'id': index, 'text': '开发需求'} for index in [11, 12, 13]]),
            candidates_json=json.dumps([
                {'id': 1, 'summary': '问答增加时间显示', 'uncertainties': '格式待确认', 'selected': True, 'evidence_ids': [11, 12]},
                {'id': 2, 'summary': '新增权限筛选', 'uncertainties': '', 'selected': False, 'evidence_ids': [13]}]),
            total_messages=3, skipped_messages=0, total_chunks=1, completed_chunks=1)
        db.add(task); db.flush()
        return {'id': task.id, 'engine': isolated_storage, 'source_id': source.id, 'conversation_id': conversation.id}


def preview(client, task_id):
    response = client.get(f'/api/extraction/tasks/{task_id}/registration-preview')
    assert response.status_code == 200, response.text
    return response.json()


def register(client, task_id, plan=None):
    plan = plan or preview(client, task_id)
    return client.post(f'/api/extraction/tasks/{task_id}/register', json={'confirmed': True, 'expected_updated_at': plan['expected_updated_at']})


def copy_task(finished, change=None):
    with Session(finished['engine']) as db, db.begin():
        original = db.get(ExtractionTask, finished['id'])
        values = {name: getattr(original, name) for name in ('source_id', 'conversation_id', 'start_time', 'end_time', 'config_json', 'snapshot_json',
                 'candidates_json', 'total_messages', 'skipped_messages', 'total_chunks', 'completed_chunks')}
        if change:
            rows = json.loads(values['candidates_json'])
            rows[0].update(change)
            values['candidates_json'] = json.dumps(rows)
        task = ExtractionTask(**values, status='finished')
        db.add(task); db.flush()
        return task.id


def test_selected_registration_csv_and_locking(finished):
    with TestClient(app) as client:
        plan = preview(client, finished['id'])
        assert plan['added'] == 1 and len(plan['rows']) == 1
        result = register(client, finished['id'], plan).json()
        assert result['file_ready'] and result['receipt']['added'] == 1
        path = requirements.DATA / 'requirements' / requirements.FILENAME
        assert path.read_bytes().startswith(b'\xef\xbb\xbf')
        rows = list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
        assert len(rows) == 1 and rows[0]['需求概述'] == '问答增加时间显示'
        assert rows[0]['查询结束时间'].endswith('23:59:59.999+08:00')
        assert rows[0]['来源消息编号'] == '11;12' and rows[0]['状态'] == '待评审'
        task = client.get(f'/api/extraction/tasks/{finished["id"]}').json()
        assert task['registration'] == result['receipt']
        assert client.put(f'/api/extraction/tasks/{finished["id"]}/review', json={'rows': []}).status_code == 409
        assert client.get(f'/api/extraction/tasks/{finished["id"]}/registration-preview').status_code == 409
        listed = client.get('/api/extraction/requirements').json()
        assert listed['total'] == 1 and listed['file_state'] == 'ready'
        downloaded = client.get('/api/extraction/requirements/file')
        assert downloaded.status_code == 200 and downloaded.content == path.read_bytes()
        assert 'attachment' in downloaded.headers['content-disposition']


def test_idempotent_repeated_request(finished):
    with TestClient(app) as client:
        plan = preview(client, finished['id'])
        first = register(client, finished['id'], plan).json()
        second = register(client, finished['id'], plan).json()
        assert first['receipt'] == second['receipt']
        assert client.get('/api/extraction/requirements').json()['total'] == 1


def test_cross_task_exact_dedup_and_similarity(finished):
    with TestClient(app) as client:
        first = register(client, finished['id']).json()
        same = copy_task(finished, {'uncertainties': '新备注不会覆盖旧备案'})
        plan = preview(client, same)
        assert plan['added'] == 0 and plan['skipped'] == 1
        assert plan['rows'][0]['duplicate_id'] == first['receipt']['items'][0]['requirement_id']
        assert register(client, same, plan).json()['receipt']['skipped'] == 1
        changed = copy_task(finished, {'summary': '问题和回答增加时间显示'})
        plan = preview(client, changed)
        assert plan['added'] == 1 and plan['rows'][0]['similar']
        assert register(client, changed, plan).json()['receipt']['added'] == 1
        assert client.get('/api/extraction/requirements').json()['total'] == 2


def test_same_task_duplicate_rows(finished):
    with Session(finished['engine']) as db, db.begin():
        task = db.get(ExtractionTask, finished['id'])
        rows = json.loads(task.candidates_json)
        rows[1] = {**rows[0], 'id': 2}
        task.candidates_json = json.dumps(rows)
    with TestClient(app) as client:
        plan = preview(client, finished['id'])
        assert plan['added'] == 1 and plan['skipped'] == 1
        assert plan['rows'][1]['duplicate_candidate_id'] == 1
        result = register(client, finished['id'], plan).json()['receipt']
        assert result['added'] == 1 and result['skipped'] == 1


def test_registration_requires_consent_selection_and_fresh_review(finished):
    with TestClient(app) as client:
        plan = preview(client, finished['id'])
        assert client.post(f'/api/extraction/tasks/{finished["id"]}/register', json={'confirmed': False, 'expected_updated_at': plan['expected_updated_at']}).status_code == 422
        with Session(finished['engine']) as db, db.begin():
            db.get(ExtractionTask, finished['id']).updated_at = 'changed'
        assert register(client, finished['id'], plan).status_code == 409
        with Session(finished['engine']) as db, db.begin():
            task = db.get(ExtractionTask, finished['id'])
            rows = json.loads(task.candidates_json)
            for row in rows:
                row['selected'] = False
            task.candidates_json = json.dumps(rows)
        assert client.get(f'/api/extraction/tasks/{finished["id"]}/registration-preview').status_code == 422
        assert client.get('/api/extraction/requirements').json()['total'] == 0


def test_failure_before_file_replace_and_explicit_recovery(finished, monkeypatch):
    with TestClient(app) as client:
        plan = preview(client, finished['id'])
        original = requirements.os.replace
        def fail(*args):
            raise PermissionError('private path and credentials')
        monkeypatch.setattr(requirements.os, 'replace', fail)
        response = register(client, finished['id'], plan)
        assert 'private path' not in response.text
        result = response.json()
        assert not result['file_ready'] and result['receipt']['added'] == 1
        assert client.get('/api/extraction/requirements').json()['total'] == 1
        assert client.get('/api/extraction/requirements/file').status_code == 409
        assert not list((requirements.DATA / 'requirements').glob('requirements-*.tmp'))
        monkeypatch.setattr(requirements.os, 'replace', original)
        assert register(client, finished['id'], plan).json()['file_ready']
        assert client.get('/api/extraction/requirements').json()['total'] == 1


def test_existing_file_preserved_when_update_fails(finished, monkeypatch):
    with TestClient(app) as client:
        register(client, finished['id'])
        path = requirements.DATA / 'requirements' / requirements.FILENAME
        original_bytes = path.read_bytes()
        second = copy_task(finished, {'summary': '新增问题时间显示'})
        def fail(*args):
            raise OSError('replace failed')
        monkeypatch.setattr(requirements.os, 'replace', fail)
        assert not register(client, second).json()['file_ready']
        assert path.read_bytes() == original_bytes
        assert client.get('/api/extraction/requirements').json()['file_state'] == 'pending'


def test_external_csv_edits_preserved(finished):
    with TestClient(app) as client:
        register(client, finished['id'])
        path = requirements.DATA / 'requirements' / requirements.FILENAME
        changed = '手动修改的数据'.encode('utf-8-sig')
        path.write_bytes(changed)
        assert client.get('/api/extraction/requirements').json()['file_state'] == 'conflict'
        assert client.post('/api/extraction/requirements/rebuild').status_code == 409
        requirements.recover_export()
        assert path.read_bytes() == changed
        second = copy_task(finished, {'summary': '另一项开发需求'})
        assert register(client, second).status_code == 409
        assert client.get('/api/extraction/requirements').json()['total'] == 1
        path.rename(path.with_suffix('.manual.csv'))
        assert client.post('/api/extraction/requirements/rebuild').json()['file_ready']
        assert path.with_suffix('.manual.csv').read_bytes() == changed


def test_recovery_after_replace_before_export_metadata_commit(finished, monkeypatch):
    with TestClient(app) as client:
        original = Session.commit
        def fail_export(db):
            if any(isinstance(row, RequirementExport) for row in db.new):
                raise OSError('metadata commit failure')
            return original(db)
        monkeypatch.setattr(Session, 'commit', fail_export)
        assert not register(client, finished['id']).json()['file_ready']
        path = requirements.DATA / 'requirements' / requirements.FILENAME
        assert path.exists()
        monkeypatch.setattr(Session, 'commit', original)
        requirements.recover_export()
        with Session(finished['engine']) as db:
            assert db.get(RequirementExport, 1)
            assert db.scalar(select(func.count()).select_from(RequirementRecord)) == 1
        assert client.get('/api/extraction/requirements').json()['file_state'] == 'ready'


def test_csv_one_physical_line_per_requirement_and_formula_protection(finished):
    with Session(finished['engine']) as db, db.begin():
        task = db.get(ExtractionTask, finished['id'])
        task.config_json = json.dumps({'conversation_title': '@危险群\n名称', 'source_label': '=危险来源', 'base_url': '', 'model': ''})
        rows = json.loads(task.candidates_json)
        rows[0].update(summary='=SUM(1,2)', uncertainties='含逗号,和"引号"')
        task.candidates_json = json.dumps(rows)
    with TestClient(app) as client:
        assert register(client, finished['id']).json()['file_ready']
        content = client.get('/api/extraction/requirements/file').content.decode('utf-8-sig')
        assert len(content.splitlines()) == 2
        row = list(csv.DictReader(io.StringIO(content)))[0]
        assert row['需求概述'] == "'=SUM(1,2)"
        assert row['来源名称'].startswith("'=")
        assert row['会话名称'].startswith("'@")
        assert row['待确认事项'] == '含逗号,和"引号"'


def test_file_lock_prevents_concurrent_projection(finished):
    with TestClient(app) as client:
        plan = preview(client, finished['id'])
        directory = requirements.DATA / 'requirements'
        directory.mkdir()
        with FileLock(str(directory / '.requirements.lock')):
            assert register(client, finished['id'], plan).status_code == 409
        assert register(client, finished['id'], plan).json()['file_ready']


def test_concurrent_registration_is_idempotent(finished):
    with TestClient(app) as client:
        plan = preview(client, finished['id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: register(client, finished['id'], plan), range(2)))
        assert all(response.status_code == 200 for response in responses)
        assert responses[0].json()['receipt'] == responses[1].json()['receipt']
        assert client.get('/api/extraction/requirements').json()['total'] == 1


def test_db_failure_before_commit_does_not_write_csv(finished, monkeypatch):
    with TestClient(app) as client:
        plan = preview(client, finished['id'])
        original = Session.commit
        def fail_ledger(db):
            if any(isinstance(row, ExtractionTask) and row.registration_json != '{}' for row in db.dirty):
                raise OSError('ledger commit failure')
            return original(db)
        monkeypatch.setattr(Session, 'commit', fail_ledger)
        assert register(client, finished['id'], plan).status_code == 500
        monkeypatch.setattr(Session, 'commit', original)
        assert client.get('/api/extraction/requirements').json()['total'] == 0
        assert not (requirements.DATA / 'requirements' / requirements.FILENAME).exists()
        assert register(client, finished['id'], plan).json()['receipt']['added'] == 1
