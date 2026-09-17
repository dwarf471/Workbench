import csv
import io
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import ROOT
from .models import Message

PATH = ROOT / 'data' / 'Frame_User.csv'


@lru_cache(maxsize=1)
def _load(path, modified, size):
    raw = path.read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('gb18030')
    reader = csv.DictReader(io.StringIO(text))
    if not {'UserGuid', 'DisplayName'}.issubset(reader.fieldnames or []):
        raise ValueError('User directory requires UserGuid and DisplayName columns')
    names = {}
    for row in reader:
        guid = (row.get('UserGuid') or '').strip().lower()
        name = (row.get('DisplayName') or '').strip()
        if guid and name:
            if guid in names and names[guid] != name:
                raise ValueError('Conflicting names for user GUID: ' + guid)
            names[guid] = name
    return names


def user_names():
    try:
        stat = PATH.stat()
    except FileNotFoundError:
        return {}
    return _load(PATH, stat.st_mtime_ns, stat.st_size)


def lookup_name(guid):
    return user_names().get(guid.strip().lower(), '')


def backfill_names(engine):
    names = user_names()
    if not names:
        return 0
    updated = 0
    with Session(engine) as db, db.begin():
        for message in db.scalars(select(Message).where(func.trim(Message.sender_name) == '')):
            name = names.get(message.sender_id.strip().lower())
            if name:
                message.sender_name = name
                updated += 1
    return updated
