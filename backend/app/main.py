from contextlib import asynccontextmanager
import asyncio
import mimetypes
from contextlib import suppress

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import ROOT, Settings, load_settings, save_settings
from .db import database_status
from .mcp_client import check_connection
from .sync import has_active, recover_tasks, worker
from .sync_api import router, mutation_lock
from .archive import router as archive_router
from .backup_api import router as backup_router
from .llm import router as model_router
from . import extraction
from . import requirements


@asynccontextmanager
async def lifespan(app):
    config = Config(str(ROOT / 'backend' / 'alembic.ini'))
    command.upgrade(config, 'head')
    recover_tasks()
    extraction.recover_tasks()
    await asyncio.to_thread(requirements.recover_export)
    background = asyncio.create_task(worker())
    model_background = asyncio.create_task(extraction.worker())
    try:
        yield
    finally:
        background.cancel()
        with suppress(asyncio.CancelledError):
            await background
        model_background.cancel()
        with suppress(asyncio.CancelledError):
            await model_background
        recover_tasks()
        extraction.recover_tasks()


app = FastAPI(title='个人工作台', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])
check_lock = asyncio.Lock()


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    if request.url.path.startswith('/api/extraction'):
        return JSONResponse({'detail': '提取请求无效，请检查会话、时间范围或候选字段长度'}, status_code=422)
    return await request_validation_exception_handler(request, error)


@app.middleware('http')
async def same_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    if origin and origin not in ('http://127.0.0.1:8765', 'http://localhost:8765'):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': '不允许跨站访问'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"
    return response


@app.get('/api/health')
def health():
    return {'status': 'ok', 'stage': 3, 'database': database_status()}


@app.get('/api/settings', response_model=Settings)
def settings():
    return load_settings()


@app.put('/api/settings', response_model=Settings)
async def update_settings(value: Settings):
    async with mutation_lock:
        if has_active():
            raise HTTPException(409, '有同步任务等待或运行中，请完成或取消后再修改配置')
        save_settings(value)
        return value


@app.post('/api/connection/check')
async def connection_check():
    if check_lock.locked():
        raise HTTPException(409, '连接检查正在进行')
    async with check_lock:
        return await check_connection(load_settings())


app.include_router(router)
app.include_router(archive_router)
app.include_router(backup_router)
app.include_router(model_router)
app.include_router(extraction.router)
app.include_router(requirements.router)
mimetypes.init()
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')
dist = ROOT / 'frontend' / 'dist'
if dist.exists():
    app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
