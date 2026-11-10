"""Pack-owned occurrence admission. JSON argv in, one JSON reply out.

No shell, delivery, or interpretation of pack copy. A bad/unavailable hook
refuses the occurrence and records one diagnostic in roller_log.
"""
import datetime as dt
import json
import re
import subprocess
from zoneinfo import ZoneInfo

HOOK_TIMEOUT = 30


def command(value):
    """Validate the nullable typed command at the registration boundary."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('ROUTINE_ADMISSION_COMMAND_INVALID')
    try:
        argv = json.loads(value)
    except ValueError:
        raise ValueError('ROUTINE_ADMISSION_COMMAND_INVALID') from None
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(a, str) or not a or chr(0) in a for a in argv)):
        raise ValueError('ROUTINE_ADMISSION_COMMAND_INVALID')
    return argv


def admit(conn, defn, date, start_at, end_at, schedule_kind,
          source='roller', exclude=(), now=None):
    """Return admitted (start_at, end_at, schedule_kind), or None.

    Missing hook returns the original tuple without executing or logging.
    Hooks receive version/date/start/length/routine_id plus read context on
    stdin. Replies declare decision=place|defer|skip. Place may override
    start (local HH:MM) and length (positive integer minutes), on this date.
    Defer belongs to the pack: it persists/relays its own pending question.
    """
    original = (start_at, end_at, schedule_kind)
    value = defn.get('admit_cmd')
    if value is None:
        return original
    decision = 'failure'
    reason = None
    try:
        argv = command(value)
        tz = ZoneInfo(defn.get('display_tz') or 'Asia/Seoul')
        start = dt.datetime.fromisoformat(start_at.replace('Z', '+00:00')).astimezone(tz)
        end = dt.datetime.fromisoformat(end_at.replace('Z', '+00:00')).astimezone(tz)
        db = next(r[2] for r in conn.execute('PRAGMA database_list') if r[1] == 'main')
        request = dict(version=1, routine_id=defn['id'],
                       recurrence_id=defn['recurrence_id'], date=date,
                       start=start.strftime('%H:%M') if schedule_kind == 'timed' else None,
                       length=int((end - start).total_seconds() / 60),
                       schedule_kind=schedule_kind, display_tz=str(tz),
                       start_at=start_at, end_at=end_at, db_path=db,
                       source=source, exclude=list(exclude))
        result = subprocess.run(argv, input=json.dumps(request), capture_output=True,
                                text=True, timeout=HOOK_TIMEOUT, check=True)
        reply = json.loads(result.stdout)
        if not isinstance(reply, dict) or reply.get('decision') not in ('place', 'defer', 'skip'):
            raise ValueError('invalid admission decision')
        decision = reply['decision']
        if decision == 'place':
            if schedule_kind == 'all_day' and not {'start', 'length'} & reply.keys():
                return original
            clock, length = reply.get('start', request['start']), reply.get('length', request['length'])
            if (not isinstance(clock, str) or not re.fullmatch(r'[0-2][0-9]:[0-5][0-9]', clock)
                    or type(length) is not int or not 0 < length <= 1440):
                raise ValueError('invalid admission span')
            day = dt.date.fromisoformat(date)
            start = dt.datetime.combine(day, dt.time.fromisoformat(clock), tz)
            end = start + dt.timedelta(minutes=length)
            if end > dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tz):
                raise ValueError('admission crosses date')
            fmt = '%Y-%m-%dT%H:%M:%SZ'
            return (start.astimezone(dt.timezone.utc).strftime(fmt),
                    end.astimezone(dt.timezone.utc).strftime(fmt), 'timed')
    except (ValueError, TypeError, KeyError, OSError, OverflowError, subprocess.SubprocessError) as exc:
        decision, reason = 'failure', type(exc).__name__
    # Never include hook stdout/stderr: it may contain unapproved owner copy.
    stamp = now or dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    conn.execute('INSERT INTO roller_log(ts,recurrence_id,category,detail) VALUES(?,?,?,?)',
                 (stamp, defn['recurrence_id'], 'admission',
                  'd=%s source=%s decision=%s%s' % (
                      date, source, decision, ' reason=' + reason if reason else '')))
    conn.commit()
    return None


def stamp(conn, defn, block_id, span):
    """Keep the pack's measured span as conformance evidence, not an exception."""
    if defn.get('admit_cmd') is not None:
        conn.execute("INSERT INTO block_attr(block_id,attr_token,value,source) "
                     "VALUES(?,'routine_admission',?,'routine')",
                     (block_id, json.dumps(dict(version=defn['version'], span=span))))


def conforms(conn, defn, row):
    evidence = conn.execute("SELECT value FROM block_attr WHERE block_id=? "
                            "AND attr_token='routine_admission'", (row['id'],)).fetchone()
    if not evidence:
        return False
    try:
        saved = json.loads(evidence[0])
        return (saved['version'] == defn['version'] and saved['span'] ==
                [row['start_at'], row['end_at'], row['schedule_kind']])
    except (ValueError, TypeError, KeyError):
        return False
