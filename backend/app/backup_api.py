import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .backup import backup_lock, create_backup
from .db import DATA

router = APIRouter(prefix='/api/archive/backups')


@router.post('')
def backup():
    if not backup_lock.acquire(blocking=False):
        raise HTTPException(409, '备份正在执行')
    try:
        path = create_backup()
        return {'filename': path.name, 'size': path.stat().st_size}
    except Exception:
        raise HTTPException(500, '备份失败，请检查数据目录及磁盘空间') from None
    finally:
        backup_lock.release()


@router.get('/{filename}')
def download(filename: str):
    if not re.fullmatch(r'workbench-\d{8}-\d{6}-[0-9a-f]{32}\.sqlite3', filename):
        raise HTTPException(404, '备份不存在')
    directory = (DATA / 'backups').resolve()
    path = directory / filename
    if path.resolve().parent != directory or not path.is_file():
        raise HTTPException(404, '备份不存在')
    return FileResponse(path, media_type='application/octet-stream', filename=filename)
