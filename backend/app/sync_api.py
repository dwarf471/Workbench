import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .config import load_settings
from .db import engine
from .mcp_client import ToolFailure, call_tool
from .models import SyncTask
from .sync import CreateTask, ERRORS, cancel_task, create_task, list_tasks, resume_task, task_detail

router = APIRouter(prefix='/api')
mutation_lock = asyncio.Lock()


def candidates(rows):
    if not isinstance(rows, list):
        raise ToolFailure('INVALID_RESULT')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ToolFailure('INVALID_RESULT')
        if row.get('conversationType') not in (1, 3):
            continue
        target = row.get('targetId')
        if not isinstance(target, str) or not target or len(target) > 200:
            raise ToolFailure('INVALID_RESULT')
        result.append({'conversation_type': row['conversationType'], 'target_id': target,
                       'title': str(row.get('conversationTitle') or row.get('name') or target)[:300]})
    return result


@router.get('/discovery/conversations')
async def discover(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50)):
    try:
        data = await call_tool(load_settings(), 'get_conversation_list', {'page': page, 'pageSize': page_size})
        total = data.get('total')
        if type(total) is not int or total < 0:
            raise ToolFailure('INVALID_RESULT')
        return {'total': total, 'list': candidates(data.get('list'))}
    except ToolFailure as error:
        raise HTTPException(502, ERRORS.get(error.code, '会话发现失败')) from None


@router.get('/discovery/contacts')
async def contacts(keyword: str = Query(min_length=1, max_length=100)):
    try:
        data = await call_tool(load_settings(), 'search_contacts', {'keyword': keyword})
        return {'list': candidates(data.get('items'))}
    except ToolFailure as error:
        raise HTTPException(502, ERRORS.get(error.code, '联系人搜索失败')) from None


@router.post('/sync/tasks', status_code=201)
async def new_task(request: CreateTask):
    async with mutation_lock:
        try:
            return {'id': create_task(request)}
        except ValueError as error:
            raise HTTPException(422, str(error)) from None


@router.get('/sync/tasks')
def tasks(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50)):
    return list_tasks(page, page_size)


@router.get('/sync/tasks/{task_id}')
def detail(task_id: int):
    with Session(engine) as db:
        task = db.get(SyncTask, task_id)
        if not task:
            raise HTTPException(404, '任务不存在')
        return task_detail(db, task)


@router.post('/sync/tasks/{task_id}/cancel')
async def cancel(task_id: int):
    async with mutation_lock:
        try:
            cancel_task(task_id)
            return {'accepted': True}
        except LookupError:
            raise HTTPException(404, '任务不存在') from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None


class ResumeRequest(BaseModel):
    account_confirmed: bool = False


@router.post('/sync/tasks/{task_id}/resume')
async def resume(task_id: int, request: ResumeRequest):
    async with mutation_lock:
        try:
            resume_task(task_id, request.account_confirmed)
            return {'accepted': True}
        except LookupError:
            raise HTTPException(404, '任务不存在') from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
