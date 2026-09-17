from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_text():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    account_key: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint('provider', 'account_key'),)


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey('sources.id'))
    conversation_type: Mapped[int] = mapped_column(Integer)
    target_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    __table_args__ = (UniqueConstraint('source_id', 'conversation_type', 'target_id'),)


class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id'))
    dedup_key: Mapped[str] = mapped_column(String(100))
    sender_id: Mapped[str] = mapped_column(String(200))
    sender_name: Mapped[str] = mapped_column(String(300))
    sent_time: Mapped[int] = mapped_column(Integer)
    sent_time_readable: Mapped[str] = mapped_column(String(100))
    message_type: Mapped[str] = mapped_column(String(100))
    text_content: Mapped[str] = mapped_column(Text)
    content_json: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('conversation_id', 'dedup_key'), Index('ix_messages_time', 'conversation_id', 'sent_time'))


class SyncTask(Base):
    __tablename__ = 'sync_tasks'
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey('sources.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    start_time: Mapped[int] = mapped_column(Integer)
    end_time: Mapped[int] = mapped_column(Integer)
    settings_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(100), default=now_text)
    updated_at: Mapped[str] = mapped_column(String(100), default=now_text)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    error_code: Mapped[str] = mapped_column(String(50), default='')


class SyncItem(Base):
    __tablename__ = 'sync_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('sync_tasks.id'))
    conversation_id: Mapped[int] = mapped_column(ForeignKey('conversations.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    oldest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    newest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str] = mapped_column(String(50), default='')
    __table_args__ = (UniqueConstraint('task_id', 'conversation_id'),)
