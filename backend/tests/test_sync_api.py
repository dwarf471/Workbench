from fastapi.testclient import TestClient

from app import sync_api
from app import sync
from app.main import app


def test_discovery_no_chat_fields(monkeypatch):
    async def fake(settings, name, arguments):
        if name == 'get_conversation_list':
            return {'total': 1, 'list': [{'conversationType': 3, 'targetId': 'group', 'conversationTitle': 'test group',
                                        'latestMessage': {'content': 'private preview'}}]}
        return {'items': [{'conversationType': 1, 'targetId': 'person', 'name': 'test person'}]}
    monkeypatch.setattr(sync_api, 'call_tool', fake)
    with TestClient(app) as client:
        result = client.get('/api/discovery/conversations')
        assert result.status_code == 200
        assert 'private preview' not in result.text
        assert result.json()['list'][0]['title'] == 'test group'
        assert client.get('/api/discovery/contacts?keyword=test').json()['list'][0]['target_id'] == 'person'
        assert client.get('/api/discovery/conversations?page_size=51').status_code == 422


def test_task_validation_and_missing():
    with TestClient(app) as client:
        assert client.post('/api/sync/tasks', json={'conversations': []}).status_code == 422
        assert client.get('/api/sync/tasks/999').status_code == 404
        assert client.post('/api/sync/tasks/999/cancel').status_code == 404
        assert client.post('/api/sync/tasks/999/resume', json={'account_confirmed': True}).status_code == 404
        assert client.get('/api/sync/tasks').json()['total'] == 0


def test_task_creation_queue_and_cancel(monkeypatch):
    async def slow_tool(*args):
        import asyncio
        await asyncio.sleep(1)
        return {'list': [], 'hasMore': False}
    monkeypatch.setattr(sync, 'call_tool', slow_tool)
    with TestClient(app) as client:
        settings = client.get('/api/settings').json()
        response = client.post('/api/sync/tasks', json={'account_key': settings['account_key'], 'account_confirmed': True,
            'conversations': [{'conversation_type': 3, 'target_id': 'test-group', 'title': 'test group'}]})
        assert response.status_code == 201
        task_id = response.json()['id']
        assert client.get(f'/api/sync/tasks/{task_id}').json()['items'][0]['title'] == 'test group'
        assert client.put('/api/settings', json=settings).status_code == 409
        assert client.post(f'/api/sync/tasks/{task_id}/cancel').status_code == 200
        import time
        for _ in range(40):
            detail = client.get(f'/api/sync/tasks/{task_id}').json()
            if detail['status'] == 'cancelled':
                break
            time.sleep(0.05)
        assert detail['status'] == 'cancelled'
        assert client.post(f'/api/sync/tasks/{task_id}/resume', json={'account_confirmed': False}).status_code == 409
        assert client.post(f'/api/sync/tasks/{task_id}/resume', json={'account_confirmed': True}).status_code == 200
