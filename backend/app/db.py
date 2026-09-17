from sqlalchemy import create_engine, event, text

from .config import ROOT

DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
engine = create_engine(f'sqlite:///{(DATA / "workbench.sqlite3").as_posix()}')


@event.listens_for(engine, 'connect')
def sqlite_options(connection, _):
    cursor = connection.cursor()
    cursor.execute('PRAGMA journal_mode=WAL')
    cursor.execute('PRAGMA foreign_keys=ON')
    cursor.execute('PRAGMA busy_timeout=5000')
    cursor.close()


def database_status():
    with engine.connect() as connection:
        revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
        return {'ready': True, 'revision': revision, 'path': str(DATA / 'workbench.sqlite3')}
