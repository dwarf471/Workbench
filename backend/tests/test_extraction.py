import asyncio
import json

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import config, extraction, llm, sync
from app.main import app
from app.models import Conversation, ExtractionTask, Message, Source


@pytest.fixture
def seeded(isolated_storage, monkeypatch):
    command.upgrade(Config(str(config.ROOT / 'backend' / 'alembic.ini')), 'head')
    async def idle():
        await asyncio.Event().wait()
    monkeypatch.setattr(extraction, 'worker', idle)
    async def forbid(*args, **kwargs):
        pytest.fail('no MCP or real model request allowed')
    monkeypatch.setattr(sync, 'call_tool', forbid)
    monkeypatch.setattr(extraction, 'completion', forbid)
    llm.PATH.write_text(json.dumps({'base_url': 'http://example.com/v1', 'model': 'test'}))
    with Session(isolated_storage) as db, db.begin():
        source = Source(provider='epointmsg', account_key='default', label='测试来源')
        other = Source(provider='epointmsg', account_key='other', label='其他来源')
        db.add_all([source, other]); db.flush()
        conversation = Conversation(source_id=source.id, conversation_type=3, target_id='group', title='测试工程群')
        db.add(conversation); db.flush()
        ids = []
        for index, text in enumerate(['范围前', '问答增加时间显示', '问、答都增加', '', '收到', '范围后']):
            message = Message(conversation_id=conversation.id, dedup_key=str(index), sender_id=f'user-{index % 2}', sender_name='',
                sent_time=1000 + index, sent_time_readable=f'时间{index}', message_type='TextMessage' if text else 'ImageMessage',
                text_content=text, content_json=json.dumps({'messageDirection': 1 if index % 2 else 2,
                    'content': {'imageUri': 'https://private.example/image', 'localPath': 'C:/private'}}))
            db.add(message); db.flush(); ids.append(message.id)
        return {'scope': {'source_id': source.id, 'conversation_id': conversation.id, 'start_time': 1001, 'end_time': 1004},
                'ids': ids, 'other_source': other.id, 'engine': isolated_storage}


def prepared(client, seeded):
    response = client.post('/api/extraction/prepare', json=seeded['scope'])
    assert response.status_code == 200, response.text
    return response.json()


def mark_running(seeded, task_id):
    with Session(seeded['engine']) as db, db.begin():
        db.get(ExtractionTask, task_id).status = 'running'


def test_prepare_local_snapshot_and_boundaries(seeded):
    with TestClient(app) as client:
        task = prepared(client, seeded)
        assert task['status'] == 'draft'
        assert task['total_messages'] == 4 and task['text_messages'] == 3 and task['skipped_messages'] == 1
        with Session(seeded['engine']) as db:
            saved = db.get(ExtractionTask, task['id'])
            snapshot = json.loads(saved.snapshot_json)
            assert [row['id'] for row in snapshot] == [seeded['ids'][1], seeded['ids'][2], seeded['ids'][4]]
            assert snapshot[0]['direction'] == '发出'
            assert snapshot[1]['direction'] == '收到'
            assert 'private.example' not in saved.snapshot_json
            assert 'localPath' not in saved.snapshot_json and 'user-' not in saved.snapshot_json
            db.get(Message, seeded['ids'][1]).text_content = '更改后的文字'
            db.commit()
            assert json.loads(db.get(ExtractionTask, task['id']).snapshot_json)[0]['text'] == '问答增加时间显示'
        assert client.get('/api/extraction/tasks', params=seeded['scope']).json()['list'] == []


@pytest.mark.parametrize('change', [{'source_id': 2}, {'start_time': 1005, 'end_time': 1001}, {'start_time': 2000, 'end_time': 3000}, {'start_time': 1003, 'end_time': 1003}])
def test_invalid_or_empty_scope(seeded, change):
    with TestClient(app) as client:
        assert client.post('/api/extraction/prepare', json={**seeded['scope'], **change}).status_code in (404, 422)


def test_confirmation_idempotence_and_config_change(seeded):
    with TestClient(app) as client:
        task = prepared(client, seeded)
        path = f'/api/extraction/tasks/{task["id"]}/start'
        assert client.post(path, json={'confirmed': False}).status_code == 422
        llm.PATH.write_text(json.dumps({'base_url': 'https://different.example/v1', 'model': 'test'}))
        assert client.post(path, json={'confirmed': True}).status_code == 409
        llm.PATH.write_text(json.dumps({'base_url': 'http://example.com/v1', 'model': 'test'}))
        assert client.post(path, json={'confirmed': True}).json()['status'] == 'queued'
        assert client.post(path, json={'confirmed': True}).json()['status'] == 'queued'
        second = prepared(client, seeded)
        assert client.post(f'/api/extraction/tasks/{second["id"]}/start', json={'confirmed': True}).status_code == 409
        assert client.post(f'/api/extraction/tasks/{task["id"]}/cancel').json()['status'] == 'cancelled'
        assert client.get('/api/extraction/tasks', params=seeded['scope']).json()['list'][0]['id'] == task['id']


def test_result_review_and_evidence_persist(seeded, monkeypatch):
    async def fake(settings, encrypted, rows, allowed, merge=False):
        assert len(rows) == 3 and '范围前' not in json.dumps(rows, ensure_ascii=False)
        return [{'summary': '问题与回答增加时间显示', 'uncertainties': '时间格式待确认', 'evidence_ids': [seeded['ids'][1], seeded['ids'][2]]}]
    monkeypatch.setattr(extraction, 'completion', fake)
    with TestClient(app) as client:
        task = prepared(client, seeded)
        mark_running(seeded, task['id'])
        asyncio.run(extraction.analyze(task['id']))
        result = client.get(f'/api/extraction/tasks/{task["id"]}').json()
        assert result['status'] == 'finished' and result['completed_chunks'] == 1
        assert len(result['evidence']) == 2
        review = {'rows': [{'id': 1, 'summary': '  问答增加时间\n按年份格式展示  ', 'uncertainties': '', 'selected': False}]}
        response = client.put(f'/api/extraction/tasks/{task["id"]}/review', json=review)
        assert response.json()['candidates'][0]['summary'] == '问答增加时间 按年份格式展示'
        assert response.json()['candidates'][0]['selected'] is False
        assert response.json()['candidates'][0]['evidence_ids'] == [seeded['ids'][1], seeded['ids'][2]]
        review['rows'][0]['id'] = 99
        assert client.put(f'/api/extraction/tasks/{task["id"]}/review', json=review).status_code == 422


@pytest.mark.parametrize('code', ['MODEL_FAILED', 'INVALID_RESULT', 'MODEL_TIMEOUT'])
def test_failure_has_no_partial_results(seeded, monkeypatch, code):
    async def fake(*args, **kwargs):
        raise extraction.Failure(code)
    monkeypatch.setattr(extraction, 'completion', fake)
    with TestClient(app) as client:
        task = prepared(client, seeded)
        mark_running(seeded, task['id'])
        asyncio.run(extraction.analyze(task['id']))
        value = client.get(f'/api/extraction/tasks/{task["id"]}').json()
        assert value['status'] == 'failed' and value['candidates'] == []
        assert client.put(f'/api/extraction/tasks/{task["id"]}/review', json={'rows': []}).status_code == 409


def test_cancellation_after_model_reply_and_restart(seeded, monkeypatch):
    async def fake(*args, **kwargs):
        with Session(seeded['engine']) as db, db.begin():
            task = db.get(ExtractionTask, task_id)
            task.status = 'cancelled'
        return [{'summary': '不能写入', 'uncertainties': '', 'evidence_ids': [seeded['ids'][1]]}]
    monkeypatch.setattr(extraction, 'completion', fake)
    with TestClient(app) as client:
        task_id = prepared(client, seeded)['id']
        mark_running(seeded, task_id)
        asyncio.run(extraction.analyze(task_id))
        assert client.get(f'/api/extraction/tasks/{task_id}').json()['candidates'] == []
        mark_running(seeded, task_id)
        extraction.recover_tasks()
        assert client.get(f'/api/extraction/tasks/{task_id}').json()['status'] == 'interrupted'


def test_chunks_overlap_and_capacity():
    rows = [{'id': index, 'text': '中' * 1800} for index in range(20)]
    batches = extraction.chunks(rows)
    assert len(batches) > 1
    assert {row['id'] for batch in batches for row in batch} == set(range(20))
    assert {row['id'] for row in batches[0]} & {row['id'] for row in batches[1]}
    with pytest.raises(extraction.Failure):
        extraction.chunks([{'id': 1, 'text': '中' * 20000}])


@pytest.mark.parametrize('output', [
    {'requirements': [{'summary': '增加功能', 'uncertainties': '', 'evidence_ids': [999]}]},
    {'requirements': [{'summary': '增加功能', 'uncertainties': '', 'evidence_ids': ['1']}]},
    {'requirements': [{'summary': '   ', 'uncertainties': '', 'evidence_ids': [1]}]},
    {'requirements': [{'summary': '增加功能', 'uncertainties': '', 'evidence_ids': []}]},
    {'requirements': [], 'unexpected': 'invalid'},
])
def test_completion_rejects_invalid_evidence_and_schema(monkeypatch, output):
    original = httpx.AsyncClient
    def handler(request):
        value = json.loads(request.content)
        assert value['messages'][0]['role'] == 'system'
        assert value['messages'][1]['content'] == '[{"id": 1, "text": "增加功能"}]'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}]})
    monkeypatch.setattr(extraction.httpx, 'AsyncClient', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    with pytest.raises(extraction.Failure) as error:
        asyncio.run(extraction.completion(llm.ModelSettings(base_url='https://example.com/v1', model='test'), '', [{'id': 1, 'text': '增加功能'}], {1}))
    assert error.value.code == 'INVALID_RESULT'


def test_completion_rejects_truncation_and_redirect(monkeypatch):
    original = httpx.AsyncClient
    for status, reason, code in [(200, 'length', 'INVALID_RESULT'), (302, 'stop', 'MODEL_FAILED')]:
        def handler(request):
            return httpx.Response(status, json={'choices': [{'finish_reason': reason, 'message': {'content': '{"requirements":[]}'}}]})
        def factory(**kwargs):
            assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
            return original(**kwargs, transport=httpx.MockTransport(handler))
        monkeypatch.setattr(extraction.httpx, 'AsyncClient', factory)
        with pytest.raises(extraction.Failure) as error:
            asyncio.run(extraction.completion(llm.ModelSettings(base_url='https://example.com/v1', model='test'), '', [], set()))
        assert error.value.code == code


def test_multichunk_merge_and_partial_failure(seeded, monkeypatch):
    monkeypatch.setattr(extraction, 'CHUNK_CHARS', 500)
    with Session(seeded['engine']) as db, db.begin():
        for id in [seeded['ids'][1], seeded['ids'][2], seeded['ids'][4]]:
            db.get(Message, id).text_content = '开发需求讨论' * 25
    calls = []
    async def fake(settings, encrypted, materials, allowed_ids, merge=False):
        calls.append(merge)
        if merge:
            assert 'evidence' in materials and 'candidates' in materials
            assert {row['id'] for row in materials['evidence']} == allowed_ids
            return [{'summary': '合并后的需求', 'uncertainties': '', 'evidence_ids': sorted(allowed_ids)}]
        return [{'summary': '同一需求', 'uncertainties': '', 'evidence_ids': [materials[0]['id']]}]
    monkeypatch.setattr(extraction, 'completion', fake)
    with TestClient(app) as client:
        task = prepared(client, seeded)
        assert task['total_chunks'] > 1
        mark_running(seeded, task['id'])
        asyncio.run(extraction.analyze(task['id']))
        result = client.get(f'/api/extraction/tasks/{task["id"]}').json()
        assert result['status'] == 'finished' and len(result['candidates']) == 1
        assert calls[-1] is True and len(calls) == task['total_chunks'] + 1
        count = 0
        async def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise extraction.Failure('MODEL_TIMEOUT')
            return [{'summary': '仅第一段结果', 'uncertainties': '', 'evidence_ids': [seeded['ids'][1]]}]
        monkeypatch.setattr(extraction, 'completion', fail_second)
        task = prepared(client, seeded)
        mark_running(seeded, task['id'])
        asyncio.run(extraction.analyze(task['id']))
        result = client.get(f'/api/extraction/tasks/{task["id"]}').json()
        assert result['status'] == 'failed' and result['completed_chunks'] == 1
        assert result['candidates'] == []


def test_real_worker_user_cancellation_continues_serving(seeded, monkeypatch):
    async def fake(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(extraction, 'completion', fake)
    from threading import Event
    began = Event()
    cancelled = Event()
    async def signaling(*args, **kwargs):
        began.set()
        try:
            return await fake()
        finally:
            cancelled.set()
    monkeypatch.setattr(extraction, 'completion', signaling)
    # Restore the real worker independently of the fixture's idle loop.
    real_worker = original_worker
    monkeypatch.setattr(extraction, 'worker', real_worker)
    with TestClient(app) as client:
        task = prepared(client, seeded)
        path = f'/api/extraction/tasks/{task["id"]}'
        assert client.post(path + '/start', json={'confirmed': True}).status_code == 200
        assert began.wait(3)
        assert client.post(path + '/cancel').json()['status'] == 'cancelled'
        assert cancelled.wait(3)
        assert client.get(path).json()['candidates'] == []
        began.clear()
        second = prepared(client, seeded)
        client.post(f'/api/extraction/tasks/{second["id"]}/start', json={'confirmed': True})
        assert began.wait(3)
    with Session(seeded['engine']) as db:
        assert db.get(ExtractionTask, second['id']).status == 'interrupted'


original_worker = extraction.worker
