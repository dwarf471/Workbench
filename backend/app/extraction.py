import asyncio
import base64
import hashlib
import json

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import llm
from .db import engine
from .models import Conversation, ExtractionTask, Message, Source, now_text
from .sync import sanitize
from .prompts import OUTPUT_RULES

router = APIRouter(prefix='/api/extraction')
mutation_lock = asyncio.Lock()
inflight: dict[int, asyncio.Task] = {}
CHUNK_CHARS = 12000
MAX_MESSAGES = 10000
MAX_CHARS = 300000
ERRORS = {
    'MODEL_FAILED': '模型请求失败，请检查地址、凭证、模型参数和响应协议',
    'MODEL_TIMEOUT': '模型请求超时，本次未生成完整结果',
    'INVALID_RESULT': '模型结果无效或来源证据不合法，本次未生成完整结果',
    'CONFIG_CHANGED': '模型配置已变化，请重新准备并确认提取',
    'TOO_LARGE': '消息或合并结果超过容量限制，请缩小时间范围',
    'INTERRUPTED': '服务已停止或重启；任务不自动继续，需重新确认提取',
}


class Failure(Exception):
    def __init__(self, code):
        self.code = code


class Scope(BaseModel):
    source_id: int = Field(ge=1)
    conversation_id: int = Field(ge=1)
    start_time: int = Field(ge=0, le=32503680000000)
    end_time: int = Field(ge=0, le=32503680000000)


class Consent(BaseModel):
    confirmed: bool


class Candidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str = Field(min_length=1, max_length=1000)
    uncertainties: str = Field(default='', max_length=2000)
    evidence_ids: list[StrictInt] = Field(min_length=1, max_length=100)

    @field_validator('summary', 'uncertainties')
    @classmethod
    def one_line(cls, value):
        value = ' '.join(value.split())
        if sanitize(value) != value:
            raise ValueError('Sensitive credential content')
        return value


class Output(BaseModel):
    model_config = ConfigDict(extra='forbid')
    requirements: list[Candidate] = Field(max_length=200)


class Edited(BaseModel):
    id: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=1000)
    uncertainties: str = Field(max_length=2000)
    selected: bool


class Review(BaseModel):
    rows: list[Edited] = Field(max_length=200)


def model_config():
    try:
        settings, encrypted = llm.load()
        if not settings.base_url or not settings.model:
            raise HTTPException(422, '请先配置大模型地址和模型名称')
        fingerprint = hashlib.sha256(json.dumps([settings.model_dump(), encrypted], sort_keys=True).encode()).hexdigest()
        return settings, encrypted, fingerprint
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, '模型配置读取失败，请检查大模型设置') from None


def chunks(snapshot):
    result, current, size = [], [], 0
    for row in snapshot:
        length = len(json.dumps(row, ensure_ascii=False))
        if length > CHUNK_CHARS // 2:
            raise Failure('TOO_LARGE')
        if current and size + length > CHUNK_CHARS:
            result.append(current)
            overlap = current[-2:]
            current = list(overlap)
            size = sum(len(json.dumps(item, ensure_ascii=False)) for item in current)
            # Reduce overlap when necessary; never drop or truncate the new message.
            while current and size + length > CHUNK_CHARS:
                size -= len(json.dumps(current.pop(0), ensure_ascii=False))
        current.append(row)
        size += length
    if current:
        result.append(current)
    return result


def get_task(db, task_id):
    task = db.get(ExtractionTask, task_id)
    if task is None:
        raise HTTPException(404, '提取任务不存在')
    return task


def serialize(task, detail=False):
    config = json.loads(task.config_json)
    value = {name: getattr(task, name) for name in ('id', 'source_id', 'conversation_id', 'start_time', 'end_time', 'status',
             'total_messages', 'skipped_messages', 'total_chunks', 'completed_chunks', 'created_at', 'updated_at')}
    value.update({'text_messages': task.total_messages - task.skipped_messages,
                  'conversation_title': config['conversation_title'], 'source_label': config['source_label'],
                  'base_url': config['base_url'], 'model': config['model'], 'error': ERRORS.get(task.error_code, '')})
    value['registration'] = json.loads(task.registration_json)
    if detail:
        candidates = json.loads(task.candidates_json)
        value['candidates'] = candidates
        ids = {id for row in candidates for id in row['evidence_ids']}
        value['evidence'] = [row for row in json.loads(task.snapshot_json) if row['id'] in ids]
    return value


@router.post('/prepare')
async def prepare(scope: Scope):
    if scope.start_time > scope.end_time:
        raise HTTPException(422, '开始时间不能晚于结束时间')
    async with mutation_lock:
        settings, _, fingerprint = model_config()
        with Session(engine) as db:
            db.connection().exec_driver_sql('BEGIN')
            conversation = db.get(Conversation, scope.conversation_id)
            source = db.get(Source, scope.source_id)
            if not source or source.provider != 'epointmsg' or not conversation or conversation.source_id != source.id:
                raise HTTPException(404, '会话不属于指定来源')
            rows = db.scalars(select(Message).where(Message.conversation_id == conversation.id,
                 Message.sent_time >= scope.start_time, Message.sent_time <= scope.end_time).order_by(Message.sent_time, Message.id).limit(MAX_MESSAGES + 1)).all()
            if len(rows) > MAX_MESSAGES:
                raise HTTPException(422, ERRORS['TOO_LARGE'])
            snapshot, skipped, participants = [], 0, {}
            for row in rows:
                text = sanitize(row.text_content)
                if not text.strip():
                    skipped += 1
                    continue
                if row.sender_id not in participants:
                    participants[row.sender_id] = f'参与者{len(participants) + 1}'
                content = json.loads(row.content_json)
                direction = {1: '发出', 2: '收到'}.get(content.get('messageDirection'), '未知')
                snapshot.append({'id': row.id, 'time': row.sent_time_readable, 'sender': sanitize(row.sender_name) or participants[row.sender_id],
                                 'direction': direction, 'text': text})
            if not snapshot:
                raise HTTPException(422, '所选范围没有可分析的文本消息')
            if len(json.dumps(snapshot, ensure_ascii=False)) > MAX_CHARS:
                raise HTTPException(422, ERRORS['TOO_LARGE'])
            try:
                count = len(chunks(snapshot))
            except Failure as error:
                raise HTTPException(422, ERRORS[error.code]) from None
            task = ExtractionTask(**scope.model_dump(), snapshot_json=json.dumps(snapshot, ensure_ascii=False),
                config_json=json.dumps({**settings.model_dump(), 'fingerprint': fingerprint,
                    'conversation_title': conversation.title, 'source_label': source.label}, ensure_ascii=False),
                total_messages=len(rows), skipped_messages=skipped, total_chunks=count)
            db.add(task)
            db.commit()
            return serialize(task, True)


@router.post('/tasks/{task_id}/start')
async def start(task_id: int, consent: Consent):
    if not consent.confirmed:
        raise HTTPException(422, '需确认向模型发送该范围的聊天文本')
    async with mutation_lock:
        with Session(engine) as db:
            task = get_task(db, task_id)
            if task.status != 'draft':
                return serialize(task, True)
            _, _, fingerprint = model_config()
            if fingerprint != json.loads(task.config_json)['fingerprint']:
                raise HTTPException(409, ERRORS['CONFIG_CHANGED'])
            if db.scalar(select(ExtractionTask.id).where(ExtractionTask.status.in_(['queued', 'running'])).limit(1)):
                raise HTTPException(409, '已有提取任务执行中，请等待或取消')
            task.status = 'queued'
            task.updated_at = now_text()
            db.commit()
            return serialize(task, True)


@router.get('/tasks')
def list_tasks(source_id: int, conversation_id: int):
    with Session(engine) as db:
        rows = db.scalars(select(ExtractionTask).where(ExtractionTask.source_id == source_id,
            ExtractionTask.conversation_id == conversation_id, ExtractionTask.status != 'draft').order_by(ExtractionTask.id.desc()).limit(20))
        return {'list': [serialize(task) for task in rows]}


@router.get('/tasks/{task_id}')
def detail(task_id: int):
    with Session(engine) as db:
        return serialize(get_task(db, task_id), True)


@router.post('/tasks/{task_id}/cancel')
async def cancel(task_id: int):
    async with mutation_lock:
        with Session(engine) as db:
            task = get_task(db, task_id)
            if task.status in ('draft', 'queued', 'running'):
                task.status = 'cancelled'
                task.updated_at = now_text()
                db.commit()
                if task_id in inflight:
                    inflight[task_id].cancel()
            return serialize(task, True)


@router.put('/tasks/{task_id}/review')
async def review(task_id: int, value: Review):
    async with mutation_lock:
        with Session(engine) as db:
            task = get_task(db, task_id)
            if task.status != 'finished':
                raise HTTPException(409, '仅可编辑已完成任务的候选结果')
            if json.loads(task.registration_json):
                raise HTTPException(409, '已登记的候选已锁定，不可修改')
            rows = json.loads(task.candidates_json)
            if len(value.rows) != len(rows) or {row.id for row in value.rows} != {row['id'] for row in rows}:
                raise HTTPException(422, '候选编号不匹配')
            edits = {row.id: row for row in value.rows}
            for row in rows:
                edit = edits[row['id']]
                try:
                    checked = Candidate(summary=edit.summary, uncertainties=edit.uncertainties, evidence_ids=row['evidence_ids'])
                    if not checked.summary:
                        raise ValueError('empty')
                except Exception:
                    raise HTTPException(422, '需求概述不能为空或包含敏感凭证') from None
                row.update(summary=checked.summary, uncertainties=checked.uncertainties, selected=edit.selected)
            task.candidates_json = json.dumps(rows, ensure_ascii=False)
            task.updated_at = now_text()
            db.commit()
            return serialize(task, True)


async def completion(settings, encrypted, materials, allowed_ids, merge=False):
    key = ''
    try:
        key = llm.protect(base64.b64decode(encrypted), decrypt=True).decode() if encrypted else ''
        instructions = settings.extraction_prompt + '\n\n' + OUTPUT_RULES + ('\n本轮输入为分段候选，合并重复项并保留补充与修正，不新增缺乏证据的需求。' if merge else '')
        async with httpx.AsyncClient(timeout=settings.timeout, follow_redirects=False, trust_env=False) as client:
            response = await client.post(settings.base_url + '/chat/completions',
                headers={'Authorization': f'Bearer {key}'} if key else {},
                json={'model': settings.model, 'messages': [{'role': 'system', 'content': instructions},
                    {'role': 'user', 'content': json.dumps(materials, ensure_ascii=False)}], 'max_tokens': 4096, 'stream': False})
        if not 200 <= response.status_code < 300:
            raise Failure('MODEL_FAILED')
        data = response.json()
        choice = data['choices'][0]
        if choice.get('finish_reason') not in (None, 'stop'):
            raise Failure('INVALID_RESULT')
        content = choice['message']['content']
        if not isinstance(content, str) or len(content) > 500000:
            raise Failure('INVALID_RESULT')
        content = content.strip()
        if content.startswith('```json\n') and content.endswith('```'):
            content = content[len('```json\n'):-3].strip()
        output = Output.model_validate(json.loads(content))
        rows = []
        for row in output.requirements:
            if not row.summary or not set(row.evidence_ids).issubset(allowed_ids):
                raise Failure('INVALID_RESULT')
            rows.append({**row.model_dump(), 'evidence_ids': sorted(set(row.evidence_ids))})
        return rows
    except Failure:
        raise
    except httpx.TimeoutException:
        raise Failure('MODEL_TIMEOUT') from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise Failure('INVALID_RESULT') from None
    except Exception:
        raise Failure('MODEL_FAILED') from None
    finally:
        key = ''


def active(task_id):
    with Session(engine) as db:
        return get_task(db, task_id).status == 'running'


async def analyze(task_id):
    try:
        with Session(engine) as db:
            task = get_task(db, task_id)
            snapshot = json.loads(task.snapshot_json)
            config = json.loads(task.config_json)
        settings, encrypted, fingerprint = model_config()
        if fingerprint != config['fingerprint']:
            raise Failure('CONFIG_CHANGED')
        batches = chunks(snapshot)
        candidates = []
        for batch in batches:
            if not active(task_id):
                return
            candidates.extend(await completion(settings, encrypted, batch, {row['id'] for row in batch}))
            if not active(task_id):
                return
            with Session(engine) as db:
                task = get_task(db, task_id)
                task.completed_chunks += 1
                task.updated_at = now_text()
                db.commit()
        if len(batches) > 1 and candidates:
            ids = {id for row in candidates for id in row['evidence_ids']}
            materials = {'candidates': candidates, 'evidence': [row for row in snapshot if row['id'] in ids]}
            if len(json.dumps(materials, ensure_ascii=False)) > 60000:
                raise Failure('TOO_LARGE')
            candidates = await completion(settings, encrypted, materials, ids, merge=True)
        if not active(task_id):
            return
        merged = {}
        for row in candidates:
            identity = row['summary'].strip()
            if identity in merged:
                existing = merged[identity]
                existing['evidence_ids'] = sorted(set(existing['evidence_ids'] + row['evidence_ids']))
                if row['uncertainties'] and row['uncertainties'] != existing['uncertainties']:
                    existing['uncertainties'] = '；'.join(filter(None, [existing['uncertainties'], row['uncertainties']]))
            else:
                merged[identity] = row
        if len(merged) > 200:
            raise Failure('TOO_LARGE')
        for row in merged.values():
            Candidate.model_validate(row)
        with Session(engine) as db:
            task = get_task(db, task_id)
            if task.status == 'running':
                task.candidates_json = json.dumps([{**row, 'id': index + 1, 'selected': True} for index, row in enumerate(merged.values())], ensure_ascii=False)
                task.status = 'finished'
                task.updated_at = now_text()
                db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        with Session(engine) as db:
            task = get_task(db, task_id)
            if task.status == 'running':
                task.status = 'failed'
                task.error_code = error.code if isinstance(error, Failure) else 'MODEL_FAILED'
                task.updated_at = now_text()
                db.commit()


def recover_tasks():
    with Session(engine) as db:
        for task in db.scalars(select(ExtractionTask).where(ExtractionTask.status.in_(['queued', 'running']))):
            task.status = 'interrupted'
            task.error_code = 'INTERRUPTED'
            task.updated_at = now_text()
        db.commit()


async def worker():
    while True:
        task_id = None
        async with mutation_lock:
            with Session(engine) as db:
                task = db.scalar(select(ExtractionTask).where(ExtractionTask.status == 'queued').order_by(ExtractionTask.id).limit(1))
                if task:
                    task.status = 'running'
                    task.updated_at = now_text()
                    task_id = task.id
                    db.commit()
        if task_id is None:
            await asyncio.sleep(0.5)
            continue
        job = asyncio.create_task(analyze(task_id))
        inflight[task_id] = job
        try:
            await job
        except asyncio.CancelledError:
            # A user cancellation stops the request, whereas shutdown stops the worker.
            if asyncio.current_task().cancelling():
                raise
        finally:
            inflight.pop(task_id, None)
