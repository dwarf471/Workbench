import asyncio
import csv
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import hashlib
import io
import json
import os
import tempfile

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from filelock import FileLock, Timeout
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import extraction
from .archive import csv_cell
from .db import DATA, engine
from .models import ExtractionTask, RequirementExport, RequirementRecord, Source, now_text

router = APIRouter(prefix='/api/extraction')
FILENAME = '需求清单.csv'
HEADERS = ['需求编号', '登记时间', '来源账号', '来源名称', '会话名称', '查询开始时间', '查询结束时间',
           '需求概述', '待确认事项', '来源消息编号', '状态', '提取任务编号', '候选编号']


class Register(BaseModel):
    confirmed: bool
    expected_updated_at: str = Field(min_length=1, max_length=100)


def identifier(record):
    date = datetime.fromisoformat(record.registered_at).astimezone(timezone(timedelta(hours=8))).strftime('%Y%m%d')
    return f'REQ-{date}-{record.id:06d}'


def date_text(value):
    return datetime.fromtimestamp(value / 1000, timezone(timedelta(hours=8))).isoformat(timespec='milliseconds')


def serialize(record):
    return {'id': identifier(record), 'task_id': record.task_id, 'candidate_id': record.candidate_id,
            'registered_at': record.registered_at, 'source_id': record.source_id, 'account_key': record.account_key,
            'source_label': record.source_label, 'conversation_id': record.conversation_id,
            'conversation_title': record.conversation_title, 'start_time': record.start_time, 'end_time': record.end_time,
            'summary': record.summary, 'uncertainties': record.uncertainties,
            'evidence_ids': json.loads(record.evidence_json), 'status': record.status}


def dedup_key(task, candidate):
    value = [task.source_id, task.conversation_id, sorted(set(candidate['evidence_ids'])), ' '.join(candidate['summary'].split())]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def plan(db, task):
    if task.status != 'finished':
        raise HTTPException(409, '仅可登记已完成任务')
    rows = [row for row in json.loads(task.candidates_json) if row['selected']]
    if not rows:
        raise HTTPException(422, '请至少勾选一项需求')
    prior = db.scalars(select(RequirementRecord).where(RequirementRecord.source_id == task.source_id,
        RequirementRecord.conversation_id == task.conversation_id).order_by(RequirementRecord.id.desc()).limit(500)).all()
    selected, seen = [], {}
    evidence = {row['id'] for row in json.loads(task.snapshot_json)}
    for row in rows:
        checked = extraction.Candidate.model_validate({key: row[key] for key in ('summary', 'uncertainties', 'evidence_ids')})
        if not checked.summary or not set(checked.evidence_ids).issubset(evidence):
            raise HTTPException(422, '需求内容或来源证据无效')
        key = dedup_key(task, row)
        duplicate = db.scalar(select(RequirementRecord).where(RequirementRecord.dedup_key == key))
        similar = []
        if not duplicate:
            for existing in prior:
                matcher = SequenceMatcher(None, row['summary'], existing.summary)
                if (set(row['evidence_ids']) & set(json.loads(existing.evidence_json)) or
                        (matcher.quick_ratio() >= 0.82 and matcher.ratio() >= 0.82)):
                    similar.append({'id': identifier(existing), 'summary': existing.summary})
                    if len(similar) == 5:
                        break
        selected.append({**row, 'duplicate_id': identifier(duplicate) if duplicate else '',
                         'duplicate_candidate_id': seen.get(key), 'similar': similar})
        seen.setdefault(key, row['id'])
    return selected


def csv_bytes(db):
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow(HEADERS)
    for row in db.scalars(select(RequirementRecord).order_by(RequirementRecord.id)):
        values = [identifier(row), date_text(datetime.fromisoformat(row.registered_at).timestamp() * 1000), row.account_key,
                  row.source_label, row.conversation_title, date_text(row.start_time), date_text(row.end_time),
                  row.summary, row.uncertainties, ';'.join(map(str, json.loads(row.evidence_json))), row.status, row.task_id, row.candidate_id]
        writer.writerow([csv_cell(' '.join(str(value).split())) for value in values])
    return buffer.getvalue().encode('utf-8-sig')


def file_state(db, payload):
    path = DATA / 'requirements' / FILENAME
    if not path.exists():
        return 'missing'
    current = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = hashlib.sha256(payload).hexdigest()
    if current == expected:
        return 'ready'
    exported = db.get(RequirementExport, 1)
    if exported and current == exported.sha256:
        return 'pending'
    return 'conflict'


def project_locked():
    path = DATA / 'requirements' / FILENAME
    with Session(engine) as db:
        db.connection().exec_driver_sql('BEGIN')
        payload = csv_bytes(db)
        if file_state(db, payload) == 'conflict':
            raise HTTPException(409, 'CSV 被外部修改，已保留；请先另存或移走该文件，再重新生成')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='requirements-', suffix='.tmp', delete=False) as file:
                temporary = file.name
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            if file_state(db, payload) == 'conflict':
                raise HTTPException(409, 'CSV 被外部修改，已保留；请先另存或移走该文件，再重新生成')
            os.replace(temporary, path)
            state = db.get(RequirementExport, 1)
            digest = hashlib.sha256(payload).hexdigest()
            if state:
                state.sha256 = digest
                state.exported_at = now_text()
            else:
                db.add(RequirementExport(id=1, sha256=digest))
            db.commit()
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    return {'filename': FILENAME, 'file_ready': True}


def with_file_lock(operation):
    directory = DATA / 'requirements'
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(directory / '.requirements.lock'), timeout=0):
            return operation()
    except Timeout:
        raise HTTPException(409, '需求清单操作正在进行，请稍后重试') from None


def register_locked(task_id, value):
    with Session(engine) as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        task = extraction.get_task(db, task_id)
        receipt = json.loads(task.registration_json)
        if not receipt:
            if task.updated_at != value.expected_updated_at:
                raise HTTPException(409, '候选已变化，请重新预览确认')
            selected = plan(db, task)
            # Refuse external edits before committing a new ledger transaction.
            if file_state(db, csv_bytes(db)) == 'conflict':
                raise HTTPException(409, 'CSV 被外部修改，已保留；请先另存或移走该文件，再重新生成')
            source = db.get(Source, task.source_id)
            config = json.loads(task.config_json)
            receipt = {'registered_at': now_text(), 'added': 0, 'skipped': 0, 'items': []}
            for candidate in selected:
                key = dedup_key(task, candidate)
                existing = db.scalar(select(RequirementRecord).where(RequirementRecord.dedup_key == key))
                if existing:
                    record = existing
                    receipt['skipped'] += 1
                else:
                    record = RequirementRecord(dedup_key=key, task_id=task.id, candidate_id=candidate['id'],
                        source_id=task.source_id, conversation_id=task.conversation_id, account_key=source.account_key,
                        source_label=config['source_label'], conversation_title=config['conversation_title'],
                        start_time=task.start_time, end_time=task.end_time, summary=candidate['summary'],
                        uncertainties=candidate['uncertainties'], evidence_json=json.dumps(sorted(set(candidate['evidence_ids']))),
                        registered_at=receipt['registered_at'])
                    db.add(record)
                    db.flush()
                    receipt['added'] += 1
                receipt['items'].append({'candidate_id': candidate['id'], 'requirement_id': identifier(record), 'duplicate': bool(existing)})
            task.registration_json = json.dumps(receipt, ensure_ascii=False)
            task.updated_at = now_text()
            db.commit()
    try:
        project_locked()
        return {'receipt': receipt, 'file_ready': True, 'filename': FILENAME, 'error': ''}
    except Exception:
        # The durable ledger/receipt is already committed; retry only its CSV projection.
        return {'receipt': receipt, 'file_ready': False, 'filename': FILENAME,
                'error': '需求已备案，但 CSV 写入失败或冲突；请关闭占用文件的程序或另存外部修改，再重新生成'}


@router.get('/tasks/{task_id}/registration-preview')
def preview(task_id: int):
    with Session(engine) as db:
        task = extraction.get_task(db, task_id)
        if json.loads(task.registration_json):
            raise HTTPException(409, '本任务已登记，不可再次修改')
        try:
            rows = plan(db, task)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(422, '候选内容无效，请重新核对草稿') from None
        return {'task_id': task_id, 'expected_updated_at': task.updated_at, 'rows': rows,
                'filename': FILENAME, 'added': sum(not row['duplicate_id'] and not row['duplicate_candidate_id'] for row in rows),
                'skipped': sum(bool(row['duplicate_id'] or row['duplicate_candidate_id']) for row in rows)}


@router.post('/tasks/{task_id}/register')
async def register(task_id: int, value: Register):
    if not value.confirmed:
        raise HTTPException(422, '需人工确认登记')
    async with extraction.mutation_lock:
        try:
            return await asyncio.to_thread(with_file_lock, lambda: register_locked(task_id, value))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(500, '登记失败，未确认写入结果；请重新查看任务后重试') from None


@router.get('/requirements')
def records(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    try:
        with Session(engine) as db:
            db.connection().exec_driver_sql('BEGIN')
            total = db.scalar(select(func.count()).select_from(RequirementRecord))
            rows = db.scalars(select(RequirementRecord).order_by(RequirementRecord.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            return {'total': total, 'list': [serialize(row) for row in rows], 'file_state': file_state(db, csv_bytes(db)),
                    'filename': FILENAME, 'path': 'data/requirements/' + FILENAME}
    except Exception:
        raise HTTPException(500, '需求清单读取失败') from None


@router.post('/requirements/rebuild')
async def rebuild():
    async with extraction.mutation_lock:
        try:
            return await asyncio.to_thread(with_file_lock, project_locked)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(500, 'CSV 生成失败，请检查文件占用和目录权限') from None


@router.get('/requirements/file')
def download():
    try:
        def read():
            with Session(engine) as db:
                db.connection().exec_driver_sql('BEGIN')
                payload = csv_bytes(db)
                state = file_state(db, payload)
                if state != 'ready':
                    raise HTTPException(409, 'CSV 尚未就绪或存在外部修改，请先重新生成')
                return Response(payload, media_type='text/csv', headers={'Content-Disposition': "attachment; filename=\"requirements.csv\"; filename*=UTF-8''%E9%9C%80%E6%B1%82%E6%B8%85%E5%8D%95.csv"})
        return with_file_lock(read)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, 'CSV 下载失败') from None


def recover_export():
    try:
        with Session(engine) as db:
            if not db.scalar(select(RequirementRecord.id).limit(1)):
                return
        with_file_lock(project_locked)
    except Exception:
        # Preserve a conflicting CSV and let the user recover explicitly in the UI.
        pass
