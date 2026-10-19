"""Import an old workspace without modifying it or overwriting desktop data."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


def import_workspace(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    source_data = source / 'data' if (source / 'data' / 'workbench.sqlite3').is_file() else source
    database = source_data / 'workbench.sqlite3'
    if not database.is_file():
        raise ValueError('所选目录没有 workbench.sqlite3，请选择旧工作台目录或其 data 目录。')
    if source_data == destination or source_data in destination.parents:
        raise ValueError('导入来源和桌面数据目录必须分开。')
    targets = ['workbench.sqlite3', 'workbench.local.json', 'model.local.json', 'Frame_User.csv', 'requirements']
    if any((destination / name).exists() for name in targets):
        raise ValueError('桌面已有数据，不能覆盖导入。请先保留现有数据。')
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='workbench-import-', dir=destination.parent) as staging:
        staging = Path(staging)
        snapshot = staging / 'workbench.sqlite3'
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(snapshot)) as dst:
                src.backup(dst, pages=256)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('来源数据库完整性检查失败。')
                versions = dst.execute('SELECT version_num FROM alembic_version').fetchall()
                if len(versions) != 1 or versions[0][0] not in ('0001', '0002', '0003', '0004'):
                    raise ValueError('来源数据库版本不受此客户端支持。')
        configuration = source_data / 'workbench.local.json'
        if not configuration.exists():
            configuration = source_data.parent / 'workbench.local.json'
        if configuration.exists():
            value = json.loads(configuration.read_text(encoding='utf-8'))
            if sys.platform != 'win32':
                value['settings_path'] = ''
            from .config import Settings
            value = Settings.model_validate(value).model_dump()
            (staging / 'workbench.local.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        for name in ('model.local.json', 'Frame_User.csv'):
            if (source_data / name).is_file():
                shutil.copy2(source_data / name, staging / name)
        model = staging / 'model.local.json'
        if model.exists():
            import base64
            from .credentials import protect
            value = json.loads(model.read_text(encoding='utf-8'))
            if value.get('encrypted_key'):
                try:
                    protect(base64.b64decode(value['encrypted_key']), decrypt=True)
                except Exception:
                    value['encrypted_key'] = ''
            model.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        csv = source_data / 'requirements' / '需求清单.csv'
        if csv.is_file():
            (staging / 'requirements').mkdir()
            shutil.copy2(csv, staging / 'requirements' / csv.name)
        # No live DB connections exist yet. Roll back all installed files on failure.
        installed = []
        try:
            for item in staging.iterdir():
                target = destination / item.name
                if target.exists():
                    raise ValueError('导入目标已存在，已停止导入。')
                item.rename(target)
                installed.append(target)
        except BaseException:
            for target in reversed(installed):
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            raise
