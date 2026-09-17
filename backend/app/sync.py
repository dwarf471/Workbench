import asyncio
import hashlib
import json
import time

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from .config import Settings, load_settings
from .db import engine
from .mcp_client import ToolFailure, call_tool
from .models import Conversation, Message, Source, SyncItem, SyncTask, now_text

ACTIVE = ('queued', 'running')
ERRORS = {
    'TOKEN_READ_FAILED': '无法读取或解密新点令牌', 'UNAUTHORIZED': '新点令牌未获授权',
    'CONNECTION_FAILED': '客户端连接失败或请求超时', 'UNEXPECTED_SERVER': '不是预期的新点服务',
    'TOOL_ERROR': '新点工具返回错误', 'INVALID_RESULT': '新点返回的数据格式不符合预期',
    'CURSOR_STALLED': '时间游标停滞，可能存在同时间戳边界遗漏',
    'PAGE_LIMIT': '达到分页安全上限', 'INTERRUPTED': '后端停止，任务已中断',
    'ACCOUNT_CHANGED': '连接配置或来源账户已变化，请切回原来源后继续',
    'INTERNAL_ERROR': '任务处理失败，已有分页结果已保留',
}


class SelectedConversation(BaseModel):
    conversation_type: int = Field(strict=True)
    target_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default='', max_length=300)

    @model_validator(mode='after')
    def supported_type(self):
        if self.conversation_type not in (1, 3):
            raise ValueError('只支持私聊和群聊')
        return self


class CreateTask(BaseModel):
    account_key: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    conversations: list[SelectedConversation] = Field(min_length=1, max_length=100)
    start_time: int = Field(default=0, ge=0, le=32503680000000)
    end_time: int = Field(default=0, ge=0, le=32503680000000)
    account_confirmed: bool = False

    @model_validator(mode='after')
    def valid_range(self):
        if not self.account_confirmed:
            raise ValueError('请确认当前新点登录账号与来源一致')
        if self.end_time and self.start_time > self.end_time:
            raise ValueError('时间范围无效')
        keys = [(c.conversation_type, c.target_id) for c in self.conversations]
        if len(set(keys)) != len(keys):
            raise ValueError('选择的会话重复')
        return self


def has_active():
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(SyncTask).where(SyncTask.status.in_(ACTIVE))) > 0


def create_task(request: CreateTask):
    settings = load_settings()
    if request.account_key != settings.account_key:
        raise ValueError(ERRORS['ACCOUNT_CHANGED'])
    with Session(engine) as db, db.begin():
        source = db.scalar(select(Source).where(Source.provider == 'epointmsg', Source.account_key == settings.account_key))
        if not source:
            source = Source(provider='epointmsg', account_key=settings.account_key, label=settings.account_label)
            db.add(source)
            db.flush()
        source.label = settings.account_label
        end = request.end_time or int(time.time() * 1000)
        if request.start_time > end:
            raise ValueError('开始时间不能晚于结束时间')
        task = SyncTask(source_id=source.id, start_time=request.start_time, end_time=end,
                        settings_json=settings.model_dump_json())
        db.add(task)
        db.flush()
        for selected in request.conversations:
            conversation = db.scalar(select(Conversation).where(Conversation.source_id == source.id,
                Conversation.conversation_type == selected.conversation_type, Conversation.target_id == selected.target_id))
            if not conversation:
                conversation = Conversation(source_id=source.id, conversation_type=selected.conversation_type,
                                            target_id=selected.target_id, title=selected.title or selected.target_id)
                db.add(conversation)
                db.flush()
            elif selected.title:
                conversation.title = selected.title
            db.add(SyncItem(task_id=task.id, conversation_id=conversation.id))
        return task.id


SENSITIVE = {'accesstoken', 'refreshtoken', 'authorization', 'authtoken', 'deviceid', 'mcptoken', 'password', 'token'}


def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(child) for key, child in value.items()
                if key.lower().replace('_', '').replace('-', '') not in SENSITIVE}
    if isinstance(value, list):
        return [sanitize(child) for child in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            import re
            if re.search(r'(?:access[_-]?token|refresh[_-]?token|authorization|password|mcp[_-]?token|device[_-]?id)[\s"\x27]*[:=]|[?&]token=|Bearer\s+[a-zA-Z0-9_-]{20,}', value, re.I):
                return '[已过滤敏感字段]'
            return value
        if isinstance(parsed, (dict, list)):
            return json.dumps(sanitize(parsed), ensure_ascii=False)
    return value


def normalize_message(raw: dict, conversation: Conversation):
    if not isinstance(raw, dict) or type(raw.get('sentTime')) is not int or raw['sentTime'] <= 0:
        raise ToolFailure('INVALID_RESULT')
    if raw.get('targetId') != conversation.target_id or raw.get('conversationType') != conversation.conversation_type:
        raise ToolFailure('INVALID_RESULT')
    safe = sanitize(raw)
    content = safe.get('content') or {}
    if not isinstance(content, dict):
        content = {'content': str(content)}
    user = content.get('user') or {}
    sender = str(safe.get('senderUserId') or '')
    text = str(content.get('content') or content.get('text') or '')
    uid = safe.get('messageUId') or safe.get('messageUid')
    local_id = safe.get('messageId')
    if uid:
        key = 'uid:' + hashlib.sha256(str(uid).encode()).hexdigest()
    elif local_id:
        key = 'local:' + hashlib.sha256(str(local_id).encode()).hexdigest()
    else:
        identity = [raw['sentTime'], sender, safe.get('messageType'), safe.get('messageDirection'), content]
        key = 'hash:' + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'conversation_id': conversation.id, 'dedup_key': key, 'sender_id': sender,
            'sender_name': str(user.get('name') or '') if isinstance(user, dict) else '',
            'sent_time': raw['sentTime'], 'sent_time_readable': str(safe.get('sentTimeReadable') or ''),
            'message_type': str(safe.get('messageType') or ''), 'text_content': text,
            'content_json': json.dumps(safe, ensure_ascii=False, sort_keys=True)}


def recover_tasks():
    with Session(engine) as db, db.begin():
        db.execute(update(SyncTask).where(SyncTask.status.in_(ACTIVE)).values(status='interrupted', error_code='INTERRUPTED', updated_at=now_text()))
        db.execute(update(SyncItem).where(SyncItem.status == 'running').values(status='interrupted', error_code='INTERRUPTED'))


def task_detail(db, task):
    source = db.get(Source, task.source_id)
    items = []
    for item in db.scalars(select(SyncItem).where(SyncItem.task_id == task.id).order_by(SyncItem.id)):
        conversation = db.get(Conversation, item.conversation_id)
        items.append({'id': item.id, 'title': conversation.title, 'conversation_type': conversation.conversation_type,
                      'target_id': conversation.target_id, 'status': item.status, 'pages': item.pages,
                      'fetched': item.fetched, 'inserted': item.inserted, 'oldest': item.oldest, 'newest': item.newest,
                      'error_code': item.error_code, 'message': ERRORS.get(item.error_code, '')})
    return {'id': task.id, 'status': task.status, 'source_label': source.label, 'account_key': source.account_key,
            'start_time': task.start_time, 'end_time': task.end_time, 'created_at': task.created_at,
            'updated_at': task.updated_at, 'cancel_requested': task.cancel_requested,
            'message': ERRORS.get(task.error_code, ''), 'items': items}


def list_tasks(page=1, page_size=20):
    with Session(engine) as db:
        total = db.scalar(select(func.count()).select_from(SyncTask))
        tasks = db.scalars(select(SyncTask).order_by(SyncTask.id.desc()).offset((page-1)*page_size).limit(page_size))
        return {'total': total, 'list': [task_detail(db, task) for task in tasks]}


def cancel_task(task_id):
    with Session(engine) as db, db.begin():
        task = db.get(SyncTask, task_id)
        if not task:
            raise LookupError()
        if task.status not in ACTIVE:
            raise ValueError('任务不在等待或运行状态')
        task.cancel_requested = True


def resume_task(task_id, confirmed):
    if not confirmed:
        raise ValueError('请确认新点登录账号与任务来源一致')
    current = load_settings()
    with Session(engine) as db, db.begin():
        task = db.get(SyncTask, task_id)
        if not task:
            raise LookupError()
        if task.status not in ('failed', 'interrupted', 'cancelled', 'warning'):
            raise ValueError('任务不能继续')
        snapshot = Settings.model_validate_json(task.settings_json)
        if (snapshot.account_key, snapshot.mcp_url, snapshot.settings_path) != (current.account_key, current.mcp_url, current.settings_path):
            raise ValueError(ERRORS['ACCOUNT_CHANGED'])
        task.status, task.error_code, task.cancel_requested = 'queued', '', False
        task.updated_at = now_text()
        for item in db.scalars(select(SyncItem).where(SyncItem.task_id == task_id, SyncItem.status != 'finished')):
            if item.status == 'warning':
                item.cursor = 0
            item.status, item.error_code = 'queued', ''


async def process_item(task_id, item_id):
    while True:
        with Session(engine) as db, db.begin():
            task, item = db.get(SyncTask, task_id), db.get(SyncItem, item_id)
            if task.cancel_requested:
                return
            item.status = 'running'
            settings = Settings.model_validate_json(task.settings_json)
            current = load_settings()
            if (settings.account_key, settings.settings_path, settings.mcp_url) != (current.account_key, current.settings_path, current.mcp_url):
                raise ToolFailure('ACCOUNT_CHANGED')
            conversation = db.get(Conversation, item.conversation_id)
            cursor = item.cursor
            args = {'conversationType': conversation.conversation_type, 'targetId': conversation.target_id,
                    'timestamp': cursor + 1 if cursor else task.end_time + 1, 'count': 50, 'before': True}
        payload = await call_tool(settings, 'get_history_messages', args)
        rows, more = payload.get('list'), payload.get('hasMore')
        if not isinstance(rows, list) or len(rows) > 50 or type(more) is not bool:
            raise ToolFailure('INVALID_RESULT')
        # A page and its cursor commit together, so retries cannot skip uncommitted messages.
        with Session(engine) as db, db.begin():
            task, item = db.get(SyncTask, task_id), db.get(SyncItem, item_id)
            if task.cancel_requested:
                return
            conversation = db.get(Conversation, item.conversation_id)
            normalized = [normalize_message(row, conversation) for row in rows]
            if any(row['sent_time'] > args['timestamp'] for row in normalized):
                raise ToolFailure('INVALID_RESULT')
            kept = [row for row in normalized if task.start_time <= row['sent_time'] <= task.end_time]
            for row in kept:
                statement = insert(Message).values(**row).on_conflict_do_nothing(index_elements=['conversation_id', 'dedup_key'])
                item.inserted += db.execute(statement).rowcount
            item.pages += 1
            item.fetched += len(rows)
            if kept:
                oldest, newest = min(row['sent_time'] for row in kept), max(row['sent_time'] for row in kept)
                item.oldest = min(item.oldest, oldest) if item.oldest is not None else oldest
                item.newest = max(item.newest, newest) if item.newest is not None else newest
            next_cursor = min((row['sent_time'] for row in normalized), default=cursor)
            reached_start = bool(normalized) and next_cursor < task.start_time
            if more and not reached_start and (not rows or (cursor and next_cursor >= cursor)):
                item.status, item.error_code = 'warning', 'CURSOR_STALLED'
            elif item.pages >= 10000 and more and not reached_start:
                item.status, item.error_code = 'warning', 'PAGE_LIMIT'
            elif not more or reached_start:
                item.status = 'finished'
            else:
                item.cursor = next_cursor
            task.updated_at = now_text()
            if item.status != 'running':
                return
        await asyncio.sleep(0.15)


async def run_task(task_id):
    with Session(engine) as db, db.begin():
        task = db.get(SyncTask, task_id)
        task.status = 'running'
        ids = list(db.scalars(select(SyncItem.id).where(SyncItem.task_id == task_id, SyncItem.status != 'finished').order_by(SyncItem.id)))
    for item_id in ids:
        with Session(engine) as db:
            if db.get(SyncTask, task_id).cancel_requested:
                break
        try:
            await process_item(task_id, item_id)
        except Exception as error:
            code = error.code if isinstance(error, ToolFailure) else 'INTERNAL_ERROR'
            with Session(engine) as db, db.begin():
                item = db.get(SyncItem, item_id)
                item.status, item.error_code = 'failed', code
                db.get(SyncTask, task_id).updated_at = now_text()
    with Session(engine) as db, db.begin():
        task = db.get(SyncTask, task_id)
        statuses = list(db.scalars(select(SyncItem.status).where(SyncItem.task_id == task_id)))
        task.status = 'cancelled' if task.cancel_requested else 'failed' if 'failed' in statuses else 'warning' if 'warning' in statuses else 'finished'
        if task.cancel_requested:
            db.execute(update(SyncItem).where(SyncItem.task_id == task_id, SyncItem.status.in_(ACTIVE)).values(status='cancelled'))
        task.updated_at = now_text()


async def worker():
    while True:
        with Session(engine) as db:
            task_id = db.scalar(select(SyncTask.id).where(SyncTask.status == 'queued').order_by(SyncTask.id).limit(1))
        if task_id is None:
            await asyncio.sleep(0.5)
            continue
        try:
            await run_task(task_id)
        except Exception:
            with Session(engine) as db, db.begin():
                task = db.get(SyncTask, task_id)
                task.status, task.error_code = 'failed', 'INTERNAL_ERROR'
                task.updated_at = now_text()
