import pytest
from sqlalchemy import create_engine, event

from app import archive, backup, backup_api, config, db, sync, sync_api, extraction, llm, requirements


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    test_engine = create_engine(f'sqlite:///{(tmp_path / "workbench.sqlite3").as_posix()}')
    @event.listens_for(test_engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=WAL')
    for module in (db, sync, sync_api, archive, backup, extraction, requirements):
        monkeypatch.setattr(module, 'engine', test_engine)
    for module in (db, backup, backup_api, requirements):
        monkeypatch.setattr(module, 'DATA', tmp_path)
    monkeypatch.setattr(config, 'CONFIG_PATH', tmp_path / 'local.json')
    monkeypatch.setattr(llm, 'PATH', tmp_path / 'model.json')
    yield test_engine
    test_engine.dispose()
