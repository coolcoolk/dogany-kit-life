"""Card SMS from the Dogany app, read from the transporter receiver's DB.

The owner's iPhone hands each card payment SMS to the Dogany app (iOS
Shortcut message automation -> action "Send to Dogany" (in Korean UI) / Send to Dogany). The
app sends it to the host's transporter receiver, which stores one row:

  ~/.transporter/events.sqlite3
  events(source, kind, id, occurred_at, sent_at, received_at, payload, callback_fired)
    source = 'shortcut', kind = 'card_sms'
    id     = sha256(text + occurred_at)  (the app's; a resend keeps it)
    payload = {"text": <SMS, verbatim>, "app_version": "0.4 (12)"
               [, "sender": <the automation's sender variable, verbatim>]}

`sender` exists from app 0.4 on. The receiver DB is the host's, not this
module's: it is opened read-only (sqlite `mode=ro` URI) and never written.
The SMS text stays there; the module keeps event ids, never the text.

The file is LEDGER_RECEIVER_DB (conf) or, for tests and offline replays, the
LEDGER_RECEIVER_DB environment variable.
"""
import datetime
import json
import os
import re
import sqlite3
from pathlib import Path

from . import paths

SOURCE = 'shortcut'
KIND = 'card_sms'
MIN_APP = (0, 4)
KST = datetime.timezone(datetime.timedelta(hours=9))


class ReceiverMissing(Exception):
    pass


def db_path():
    raw = os.environ.get('LEDGER_RECEIVER_DB') or paths.conf()['LEDGER_RECEIVER_DB']
    return Path(os.path.expanduser(raw))


def _connect(path=None):
    p = Path(path) if path else db_path()
    if not p.is_file():
        raise ReceiverMissing(str(p))
    return sqlite3.connect('file:%s?mode=ro' % p, uri=True)


def _row(r):
    source, kind, eid, occurred, received, payload = r
    try:
        p = json.loads(payload)
    except ValueError:
        p = {}
    if not isinstance(p, dict):
        p = {}
    return {'source': source, 'kind': kind, 'id': eid, 'occurred_at': occurred, 'received_at': received,
            'text': p.get('text') if isinstance(p.get('text'), str) else '',
            'sender': p.get('sender') if isinstance(p.get('sender'), str) else None,
            'app_version': p.get('app_version') if isinstance(p.get('app_version'), str) else None}


COLS = 'source, kind, id, occurred_at, received_at, payload'


def timestamp(raw):
    """Require a zoned ISO8601 instant; never compare offsets as strings."""
    try:
        dt = datetime.datetime.fromisoformat((raw or '').replace('Z', '+00:00'))
        return dt.astimezone(datetime.timezone.utc) if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def eligible(row, floor):
    floor = timestamp(floor)
    return (row['source'] == SOURCE and row['kind'] == KIND and floor is not None
            and all(timestamp(row.get(k)) is not None and timestamp(row[k]) >= floor
                    for k in ('received_at', 'occurred_at')))


def rows_after(cursor=None, since=None, path=None):
    """Read only this lane at/after its consent floor, then apply its cursor.
    No floor means no read, never a historical fallback window.
    """
    if timestamp(since) is None:
        return []
    c = _connect(path)
    try:
        q = ('SELECT %s FROM events WHERE source=? AND kind=? '
             'AND julianday(received_at) >= julianday(?) ' % COLS)
        args = [SOURCE, KIND, since]
        if cursor and timestamp(cursor.get('received_at')):
            q += ('AND (julianday(received_at) > julianday(?) OR '
                  '(julianday(received_at) = julianday(?) AND id > ?)) ')
            args += [cursor['received_at'], cursor['received_at'], cursor['id']]
        q += 'ORDER BY julianday(received_at), id'
        return [_row(r) for r in c.execute(q, args)]
    finally:
        c.close()


def rows_by_id(ids, path=None):
    ids = sorted(set(ids))
    if not ids:
        return []
    c = _connect(path)
    try:
        q = 'SELECT %s FROM events WHERE source=? AND kind=? AND id IN (%s) ORDER BY julianday(received_at), id' % (
            COLS, ','.join('?' * len(ids)))
        return [_row(r) for r in c.execute(q, [SOURCE, KIND] + ids)]
    finally:
        c.close()


def occurred_kst(row):
    """The app's occurred_at (UTC, ISO with Z) as a KST datetime, or None."""
    s = (row.get('occurred_at') or '').strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(KST)


def app_version(raw):
    """'0.4 (12)' -> (0, 4); unparseable -> None."""
    m = re.match(r'\s*(\d+)\.(\d+)', raw or '')
    return (int(m.group(1)), int(m.group(2))) if m else None


def app_ok(raw):
    v = app_version(raw)
    return v is not None and v >= MIN_APP
