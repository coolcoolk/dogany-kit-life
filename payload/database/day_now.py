"""Read the current day before food or timing advice. No writes or migrations."""
import argparse
from contextlib import closing
import datetime as dt
import json
import sqlite3
from pathlib import Path

import meal_slots
import person_labels


def instant(value, tz):
    if not value or len(value) <= 10:
        return None
    stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    # block capture stamps without a suffix are legacy UTC stamps.
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.timezone.utc)).astimezone(tz)


def next_meal(clock, logged):
    """Do not turn missing historical meals into a question about this meal."""
    slots = ('\uc544\uce68', '\uc810\uc2ec', '\uc800\ub141')
    by_clock = 0 if clock.hour < 11 else 1 if clock.hour < 15 else 2
    after_logged = max((slots.index(s) + 1 for s in logged if s in slots), default=0)
    index = max(by_clock, after_logged)
    return slots[index] if index < len(slots) else '\uac04\uc2dd'


def read(K, conn, now=None):
    tz = K._day_tz(tz_offset=K._tz_offset_hours())
    clock = dt.datetime.now(tz) if now is None else dt.datetime.fromisoformat(now.replace('Z', '+00:00'))
    # A fixed clock without an offset is explicitly a LOCAL time.
    clock = clock.replace(tzinfo=tz) if clock.tzinfo is None else clock.astimezone(tz)
    day = clock.date().isoformat()
    lo, hi = K._day_window(day, tz)
    stamp = clock.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    meals = []
    for mid, slot, name, captured in conn.execute(
            'SELECT id,meal,name,created_at FROM ' + K.MEALS_V32 +
            ' WHERE date=? ORDER BY created_at,id', (day,)):
        if (name or '').endswith(K.PLANNED_SUFFIX):
            continue
        at = instant(captured, tz)
        meals.append(dict(id=mid, slot=meal_slots.label(slot), name=name, logged_at=captured,
                          logged_time=at.strftime('%H:%M') if at else None))
    # Read block truth, including date-only tasks and overnight presence.
    rows = conn.execute(
        'SELECT b.id,b.title,b.start_at,b.end_at,b.date,b.kind_token,b.source_module,b.settled_at,'
        'p.is_base,(SELECT value FROM block_attr WHERE block_id=b.id '
        "AND attr_token='schedule_kind' LIMIT 1) FROM block b "
        'LEFT JOIN place p ON p.id=b.place_id '
        "WHERE b.kind_token!='record' AND b.settled_outcome IS NOT 'abandoned' AND "
        '((b.start_at < ? AND (b.end_at > ? OR (b.end_at IS NULL AND b.start_at >= ?))) '
        'OR b.date=?) ORDER BY b.start_at,b.id', (hi, lo, lo, day)).fetchall()
    modes, remaining, planned = [], [], []
    for bid, title, start, end, date, kind, module, settled, base, schedule in rows:
        mode = K.presence_subtype(kind, title, schedule, base)
        if mode:
            label = '\uc7ac\ud0dd' if mode == 'wfh' else '\ucd9c\uadfc'
            if label not in modes:
                modes.append(label)
        if settled or (end and end <= stamp):
            continue
        # A start-only event before today is excluded above; no invented end.
        people = [dict(id=pid, name=person_labels.person(conn, pid)[0]) for (pid,) in conn.execute(
            'SELECT p.id FROM event_persons e JOIN persons p ON p.id=e.person_id '
            'WHERE e.event_id=? ORDER BY p.id', (bid,))]
        groups = [dict(id=gid, name=name) for gid, name in conn.execute(
            'SELECT g.id,g.name FROM event_group e JOIN person_group g ON g.id=e.group_id '
            'WHERE e.event_id=? ORDER BY g.id', (bid,))]
        local_start, local_end = instant(start, tz), instant(end, tz)
        row = dict(id=bid, title=title, start_at=start, end_at=end, date=date, kind=kind,
                   start_time=local_start.strftime('%H:%M') if local_start else None,
                   end_time=local_end.strftime('%H:%M') if local_end else None,
                   attendees=people, groups=groups)
        remaining.append(row)
        # Workout membership is explicit metadata, never guessed from title.
        workout = conn.execute("SELECT 1 FROM block_attr WHERE block_id=? AND "
                               "attr_token='action_kind' AND value='workout' LIMIT 1", (bid,)).fetchone()
        if workout or module == 'training' or kind == 'workout':
            planned.append(row)
    done, active = [], []
    columns = {r[1] for r in conn.execute('PRAGMA table_info(workouts)')}
    phase = 'w.phase' if 'phase' in columns else 'NULL'
    version = 'w.session_version' if 'session_version' in columns else '1'
    for wid, minutes, captured, ver, status in conn.execute(
            'SELECT w.id,w.minutes,w.created_at,' + version + ',' + phase +
            ' FROM workouts w WHERE w.date=? ORDER BY w.id', (day,)):
        labels = [r[0] for r in conn.execute(
            'SELECT t.subtype FROM workout_classifications c JOIN workout_types t '
            'ON t.id=c.type_id WHERE c.workout_id=? ORDER BY t.id', (wid,))]
        row = dict(id=wid, names=labels, minutes=minutes, logged_at=captured, phase=status)
        (active if ver == 2 and status != 'closed' else done).append(row)
    return dict(now_local=clock.isoformat(timespec='seconds'), date=day,
                day_type=' / '.join(modes) if modes else None,
                meals=meals, next_eating_occasion=next_meal(clock, [m['slot'] for m in meals]),
                remaining_blocks=remaining, workout=dict(done=done, active=active, planned=planned))


def text(data):
    lines = ['\uc9c0\uae08 ' + data['now_local'] + ' | \uc624\ub298 ' + (data['day_type'] or '\uadfc\ubb34 \ud615\ud0dc \uae30\ub85d \uc5c6\uc74c')]
    slots = {}
    for meal in data['meals']:
        slots.setdefault(meal['slot'], []).append((meal['logged_time'] or '\uc2dc\uac01 \uc5c6\uc74c') + ' ' + meal['name'])
    lines.append('\uc2dd\uc0ac \uae30\ub85d: ' + ('; '.join(s + ': ' + ', '.join(items) for s, items in slots.items()) or '\uc5c6\uc74c'))
    lines.append('\ub2e4\uc74c \uc2dd\uc0ac: ' + data['next_eating_occasion'])
    blocks = []
    for b in data['remaining_blocks']:
        when = (b['start_time'] or '\uc2dc\uac04 \ubbf8\uc815') + ('\u2013' + b['end_time'] if b['end_time'] else '')
        party = [p['name'] for p in b['attendees']] + [g['name'] for g in b['groups']]
        blocks.append(when + ' ' + (b['title'] or '') + ' (\ucc38\uc11d\uc790: ' + (', '.join(party) or '\uae30\ub85d \uc5c6\uc74c') + ')')
    lines.append('\ub0a8\uc740 \uc77c\uc815: ' + ('; '.join(blocks) or '\uc5c6\uc74c'))
    w = data['workout']
    done = ', '.join(('/'.join(r['names']) or '#' + str(r['id'])) + ' ' + str(r['minutes']) + '\ubd84' for r in w['done'])
    lines.append('\uc6b4\ub3d9: \uc644\ub8cc \uae30\ub85d ' + (done or '\uc5c6\uc74c') + '; \uc9c4\ud589 ' + str(len(w['active'])) +
                 '\uac74; \ub0a8\uc740 \uacc4\ud68d ' + (', '.join(b['title'] for b in w['planned']) or '\uc5c6\uc74c'))
    return '\n'.join(lines)


def cli(K, argv):
    parser = argparse.ArgumentParser(prog='kit_life.sh day-now')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--now', help='fixed local or offset ISO clock for replay')
    args = parser.parse_args(argv)
    # Do not use get_conn(): its PRAGMAs/migration path is a writer.
    with closing(sqlite3.connect(Path(K.DB_PATH).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')  # one consistent snapshot across all readers
        result = read(K, conn, args.now)
    print(json.dumps(result, ensure_ascii=False) if args.json else text(result))
