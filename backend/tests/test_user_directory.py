import pytest
from sqlalchemy.orm import Session

from app import config, user_directory, sync
from app.models import Conversation, Message, Source
from test_sync import setup_db, message


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'gb18030'])
def test_lookup_and_reload(tmp_path, monkeypatch, encoding):
    path = tmp_path / 'users.csv'
    monkeypatch.setattr(user_directory, 'PATH', path)
    path.write_bytes('UserGuid,DisplayName\nSENDER,张三\n'.encode(encoding))
    assert user_directory.lookup_name(' sender ') == '张三'
    raw = message('a', 100)
    raw['content'].pop('user')
    conversation = Conversation(id=1, target_id='person', conversation_type=1)
    assert sync.normalize_message(raw, conversation)['sender_name'] == '张三'
    raw['content']['user'] = {'name': '原始姓名'}
    assert sync.normalize_message(raw, conversation)['sender_name'] == '原始姓名'
    path.write_bytes('UserGuid,DisplayName\nSENDER,李四四\n'.encode(encoding))
    assert user_directory.lookup_name('sender') == '李四四'
    assert user_directory.lookup_name('unknown') == ''


def test_backfill(isolated_storage, tmp_path, monkeypatch):
    setup_db()
    path = tmp_path / 'users.csv'
    path.write_bytes(b'UserGuid,DisplayName\nsender,Mapped\n')
    monkeypatch.setattr(user_directory, 'PATH', path)
    with Session(isolated_storage) as db, db.begin():
        source = Source(provider='epointmsg', account_key='test', label='test')
        db.add(source)
        db.flush()
        conversation = Conversation(source_id=source.id, target_id='person', conversation_type=1, title='test')
        db.add(conversation)
        db.flush()
        for index, name in enumerate(['', 'Existing']):
            row = sync.normalize_message(message(str(index), 100), conversation)
            row['sender_name'] = name
            db.add(Message(**row))
    assert user_directory.backfill_names(isolated_storage) == 1
    assert user_directory.backfill_names(isolated_storage) == 0
    with Session(isolated_storage) as db:
        assert [m.sender_name for m in db.query(Message).order_by(Message.id)] == ['Mapped', 'Existing']


def test_missing_and_conflicting(tmp_path, monkeypatch):
    path = tmp_path / 'users.csv'
    monkeypatch.setattr(user_directory, 'PATH', path)
    assert user_directory.lookup_name('sender') == ''
    path.write_bytes(b'UserGuid,DisplayName\na,One\nA,Two\n')
    with pytest.raises(ValueError, match='Conflicting'):
        user_directory.user_names()
