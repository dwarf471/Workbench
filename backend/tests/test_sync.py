import asyncio
import json

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config, sync
from app.mcp_client import ToolFailure
from app.models import Message, SyncItem, SyncTask
from app.sync import CreateTask, create_task, recover_tasks, resume_task, run_task


def setup_db():
    command.upgrade(Config(str(config.ROOT / 'backend' / 'alembic.ini')), 'head')


def request(target='person', start=100, end=500):
    return CreateTask(account_key=config.load_settings().account_key, conversations=[{'conversation_type': 1, 'target_id': target, 'title': 'test'}],
                      start_time=start, end_time=end, account_confirmed=True)


def message(uid, time, target='person'):
    return {'messageUId': uid, 'sentTime': time, 'sentTimeReadable': 'client formatted time',
            'targetId': target, 'conversationType': 1, 'senderUserId': 'sender', 'messageType': 'TextMessage',
            'content': {'content': '中文 token 讨论', 'user': {'name': 'name'},
                        'extra': json.dumps({'access_token': 'SECRET', 'device_id': 'SECRET', 'imageInfo': 'retained'})}}


def counts(engine):
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(Message))


def test_paging_dedup_and_credentials(isolated_storage, monkeypatch):
    setup_db()
    calls = []
    async def fake(settings, name, args):
        calls.append(args['timestamp'])
        if args['timestamp'] == 501:
            return {'list': [message('a', 400), message('b', 200)], 'hasMore': True}
        return {'list': [message('b', 200), message('c', 150), message('old', 50)], 'hasMore': False}
    monkeypatch.setattr(sync, 'call_tool', fake)
    task = create_task(request())
    asyncio.run(run_task(task))
    assert calls == [501, 201]
    assert counts(isolated_storage) == 3
    second = create_task(request())
    asyncio.run(run_task(second))
    assert counts(isolated_storage) == 3
    with Session(isolated_storage) as db:
        rows = list(db.scalars(select(Message)))
        assert all('SECRET' not in row.content_json for row in rows)
        assert all(row.text_content == '中文 token 讨论' for row in rows)
        assert all(row.sent_time_readable == 'client formatted time' for row in rows)
        assert db.get(SyncTask, task).status == 'finished'
        item = db.scalar(select(SyncItem).where(SyncItem.task_id == second))
        assert item.inserted == 0


def test_failed_page_resume_checkpoint(isolated_storage, monkeypatch):
    setup_db()
    async def failed(settings, name, args):
        if args['timestamp'] == 501:
            return {'list': [message('a', 400), message('b', 200)], 'hasMore': True}
        raise ToolFailure('CONNECTION_FAILED')
    monkeypatch.setattr(sync, 'call_tool', failed)
    task = create_task(request())
    asyncio.run(run_task(task))
    with Session(isolated_storage) as db:
        assert db.get(SyncTask, task).status == 'failed'
        assert db.scalar(select(SyncItem)).cursor == 200
    resumed = []
    async def success(settings, name, args):
        resumed.append(args['timestamp'])
        return {'list': [message('b', 200), message('c', 150)], 'hasMore': False}
    monkeypatch.setattr(sync, 'call_tool', success)
    resume_task(task, True)
    asyncio.run(run_task(task))
    assert resumed == [201]
    assert counts(isolated_storage) == 3


def test_cursor_stalled_warning(isolated_storage, monkeypatch):
    setup_db()
    async def fake(*args):
        return {'list': [message('a', 200)], 'hasMore': True}
    monkeypatch.setattr(sync, 'call_tool', fake)
    task = create_task(request())
    asyncio.run(run_task(task))
    with Session(isolated_storage) as db:
        item = db.scalar(select(SyncItem))
        assert item.status == 'warning' and item.error_code == 'CURSOR_STALLED'
        assert item.pages == 2
        assert db.get(SyncTask, task).status == 'warning'
    assert counts(isolated_storage) == 1


def test_invalid_page_rollback(isolated_storage, monkeypatch):
    setup_db()
    async def fake(*args):
        return {'list': [message('a', 200), message('wrong', 150, 'other')], 'hasMore': False}
    monkeypatch.setattr(sync, 'call_tool', fake)
    task = create_task(request())
    asyncio.run(run_task(task))
    assert counts(isolated_storage) == 0
    with Session(isolated_storage) as db:
        assert db.scalar(select(SyncItem)).pages == 0


def test_cancel_and_recovery(isolated_storage, monkeypatch):
    setup_db()
    task = create_task(request())
    sync.cancel_task(task)
    async def unexpected(*args):
        pytest.fail('cancelled task must not read history')
    monkeypatch.setattr(sync, 'call_tool', unexpected)
    asyncio.run(run_task(task))
    with Session(isolated_storage) as db:
        assert db.get(SyncTask, task).status == 'cancelled'
    resume_task(task, True)
    recover_tasks()
    with Session(isolated_storage) as db:
        assert db.get(SyncTask, task).status == 'interrupted'
    assert counts(isolated_storage) == 0


def test_account_isolation_and_resume_guard(isolated_storage, monkeypatch):
    setup_db()
    async def fake(*args):
        return {'list': [message('a', 200)], 'hasMore': False}
    monkeypatch.setattr(sync, 'call_tool', fake)
    first = create_task(request())
    asyncio.run(run_task(first))
    config.save_settings(config.Settings(account_key='second'))
    second = create_task(request())
    asyncio.run(run_task(second))
    assert counts(isolated_storage) == 2
    third = create_task(request())
    recover_tasks()
    config.save_settings(config.Settings(account_key='default'))
    with pytest.raises(ValueError):
        resume_task(third, True)
    with pytest.raises(ValueError):
        resume_task(third, False)


def test_confirmation_and_duplicates():
    with pytest.raises(ValueError):
        CreateTask(account_key='default', conversations=[{'conversation_type': 1, 'target_id': 'x'}])
    with pytest.raises(ValueError):
        CreateTask(account_key='default', conversations=[{'conversation_type': 1, 'target_id': 'x'}] * 2, account_confirmed=True)


def test_fallback_dedup(monkeypatch, isolated_storage):
    setup_db()
    row = message('', 200)
    row.pop('messageUId')
    async def fake(*args):
        return {'list': [row, row], 'hasMore': False}
    monkeypatch.setattr(sync, 'call_tool', fake)
    asyncio.run(run_task(create_task(request())))
    assert counts(isolated_storage) == 1


def test_cancellation_during_request(isolated_storage, monkeypatch):
    setup_db()
    task = create_task(request())
    async def fake(*args):
        sync.cancel_task(task)
        return {'list': [message('a', 200)], 'hasMore': False}
    monkeypatch.setattr(sync, 'call_tool', fake)
    asyncio.run(run_task(task))
    assert counts(isolated_storage) == 0
    with Session(isolated_storage) as db:
        assert db.get(SyncTask, task).status == 'cancelled'


def test_create_account_changed(isolated_storage):
    setup_db()
    chosen = request()
    config.save_settings(config.Settings(account_key='other'))
    with pytest.raises(ValueError):
        create_task(chosen)
