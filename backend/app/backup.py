import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from .db import DATA, engine

backup_lock = threading.Lock()


def create_backup():
    directory = DATA / 'backups'
    directory.mkdir(parents=True, exist_ok=True)
    filename = f'workbench-{datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")}-{uuid4().hex}.sqlite3'
    path = directory / filename
    source = engine.raw_connection()
    try:
        if source.driver_connection.execute('SELECT version_num FROM alembic_version').fetchone() is None:
            raise RuntimeError('database has not been initialized')
        with closing(sqlite3.connect(path)) as destination:
            source.driver_connection.backup(destination, pages=256)
            if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('backup integrity failed')
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    return path


if __name__ == '__main__':
    with backup_lock:
        print(create_backup())
