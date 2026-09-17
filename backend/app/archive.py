import csv
import io
import json
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .config import load_settings
from .db import engine
from .models import Conversation, Message, Source, SyncItem
from .sync import sanitize

router = APIRouter(prefix='/api/archive')


@dataclass
class Filters:
    source_id: int | None
    conversation_id: int | None
    q: str
    sender: str
    start_time: int | None
    end_time: int | None
    account_key: str


def filters(source_id: int | None = Query(None, ge=1), conversation_id: int | None = Query(None, ge=1),
            q: str = Query('', max_length=200), sender: str = Query('', max_length=100),
            start_time: int | None = Query(None, ge=0, le=32503680000000),
            end_time: int | None = Query(None, ge=0, le=32503680000000)):
    if start_time is not None and end_time is not None and start_time > end_time:
        raise HTTPException(422, '开始时间不能晚于结束时间')
    return Filters(source_id, conversation_id, q, sender, start_time, end_time, load_settings().account_key)


def source_conditions(source_id, account_key=None):
    if source_id is not None:
        return [Source.id == source_id, Source.provider == 'epointmsg']
    return [Source.account_key == (account_key or load_settings().account_key), Source.provider == 'epointmsg']


def message_query(f: Filters):
    statement = select(Message, Conversation, Source).join(Conversation, Message.conversation_id == Conversation.id).join(Source, Conversation.source_id == Source.id)
    conditions = source_conditions(f.source_id, f.account_key)
    if f.conversation_id is not None:
        conditions.append(Conversation.id == f.conversation_id)
    if f.q:
        conditions.append(or_(func.instr(Message.text_content, f.q) > 0,
                              func.instr(func.json_extract(Message.content_json, '$.content.name'), f.q) > 0))
    if f.sender:
        conditions.append(or_(Message.sender_id.contains(f.sender, autoescape=True), Message.sender_name.contains(f.sender, autoescape=True)))
    if f.start_time is not None:
        conditions.append(Message.sent_time >= f.start_time)
    if f.end_time is not None:
        conditions.append(Message.sent_time <= f.end_time)
    return statement.where(*conditions)


def serialize(row):
    message, conversation, source = row
    content = sanitize(json.loads(message.content_json))
    return {'id': message.id, 'conversation_id': conversation.id, 'conversation_title': conversation.title,
            'conversation_type': conversation.conversation_type, 'source_id': source.id, 'account_key': source.account_key,
            'source_label': source.label, 'sender_id': message.sender_id, 'sender_name': message.sender_name,
            'sent_time': message.sent_time, 'sent_time_readable': message.sent_time_readable,
            'message_type': message.message_type, 'text_content': sanitize(message.text_content), 'content': content}


@router.get('/sources')
def sources():
    with Session(engine) as db:
        rows = db.scalars(select(Source).where(Source.provider == 'epointmsg').order_by(Source.id))
        return {'list': [{'id': row.id, 'label': row.label, 'account_key': row.account_key} for row in rows],
                'current_account_key': load_settings().account_key}


@router.get('/conversations')
def conversations(source_id: int | None = Query(None, ge=1), q: str = Query('', max_length=200),
                  conversation_type: int | None = Query(None), page: int = Query(1, ge=1),
                  page_size: int = Query(20, ge=1, le=50)):
    if conversation_type not in (None, 1, 3):
        raise HTTPException(422, '会话类型无效')
    conditions = source_conditions(source_id)
    if q:
        conditions.append(func.instr(Conversation.title, q) > 0)
    if conversation_type:
        conditions.append(Conversation.conversation_type == conversation_type)
    summary = select(Message.conversation_id.label('cid'), func.count().label('count'),
                     func.min(Message.sent_time).label('oldest'), func.max(Message.sent_time).label('newest')).group_by(Message.conversation_id).subquery()
    statement = select(Conversation, summary.c.count, summary.c.oldest, summary.c.newest).join(Source).outerjoin(summary, summary.c.cid == Conversation.id).where(*conditions)
    with Session(engine) as db:
        db.connection().exec_driver_sql('BEGIN')
        total = db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = db.execute(statement.order_by(summary.c.newest.desc(), Conversation.id.desc()).offset((page-1)*page_size).limit(page_size))
        result = []
        for c, count, oldest, newest in rows:
            latest_sync = db.scalar(select(SyncItem).where(SyncItem.conversation_id == c.id).order_by(SyncItem.id.desc()).limit(1))
            latest = db.scalar(select(Message).where(Message.conversation_id == c.id).order_by(Message.sent_time.desc(), Message.id.desc()).limit(1))
            result.append({'id': c.id, 'title': c.title, 'conversation_type': c.conversation_type, 'target_id': c.target_id,
                           'message_count': count or 0, 'oldest': oldest, 'newest': newest,
                           'latest_time_readable': latest.sent_time_readable if latest else '',
                           'sync_status': latest_sync.status if latest_sync else None})
        return {'total': total, 'list': result}


@router.get('/messages')
def messages(f: Filters = Depends(filters), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
             focus_id: int | None = Query(None, ge=1)):
    statement = message_query(f)
    with Session(engine) as db:
        db.connection().exec_driver_sql('BEGIN')
        if focus_id is not None:
            target = db.execute(statement.where(Message.id == focus_id)).first()
            if target is None:
                raise HTTPException(404, '消息不在当前归档范围内')
            message = target[0]
            preceding = statement.where(or_(Message.sent_time > message.sent_time,
                                            and_(Message.sent_time == message.sent_time, Message.id > message.id)))
            rank = db.scalar(select(func.count()).select_from(preceding.subquery()))
            page = rank // page_size + 1
        total = db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = db.execute(statement.order_by(Message.sent_time.desc(), Message.id.desc()).offset((page-1)*page_size).limit(page_size))
        return {'total': total, 'page': page, 'list': [serialize(row) for row in rows]}


def csv_cell(value):
    text = str(value if value is not None else '')
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text


def export_rows(f: Filters, format: str):
    # Keep one read transaction and stream rows without loading the whole archive.
    with Session(engine) as db:
        rows = db.execute(message_query(f).order_by(Message.sent_time, Message.id).execution_options(yield_per=200))
        if format == 'json':
            yield '{"filters":' + json.dumps(f.__dict__, ensure_ascii=False) + ',"messages":['
            first = True
            for row in rows:
                yield ('' if first else ',') + json.dumps(serialize(row), ensure_ascii=False)
                first = False
            yield ']}'
        else:
            buffer = io.StringIO(newline='')
            writer = csv.writer(buffer)
            columns = ['account_key', 'source_label', 'conversation_id', 'conversation_title', 'conversation_type',
                       'sender_id', 'sender_name', 'sent_time', 'sent_time_readable', 'message_type', 'text_content', 'content']
            yield '\ufeff'
            writer.writerow(columns)
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            for row in rows:
                data = serialize(row)
                data['content'] = json.dumps(data['content'], ensure_ascii=False)
                writer.writerow([csv_cell(data[column]) for column in columns])
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)


@router.get('/export')
def export(f: Filters = Depends(filters), format: str = Query('json', pattern='^(json|csv)$')):
    return StreamingResponse(export_rows(f, format), media_type='application/json' if format == 'json' else 'text/csv',
                             headers={'Content-Disposition': f'attachment; filename="chat-export.{format}"'})
