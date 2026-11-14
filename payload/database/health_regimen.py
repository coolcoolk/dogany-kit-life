"""Owner-stated regimens; the existing reminder heartbeat owns delivery.

No meal writes, dose suggestions, adherence calculations or independent poller.
Occurrences keep stable numbering and dose snapshots. Names are private; shared
cards contain counts only. The selector's ordinary TSV fallback never carries
regimen names: strict delivery must target an explicit positive private chat.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shlex
import sqlite3
from zoneinfo import ZoneInfo

import meal_slots
import health_regimen_correction as correction

FMT = '%Y-%m-%dT%H:%M:%SZ'
UTC = dt.timezone.utc
ALL_DAYS = '0,1,2,3,4,5,6'


def instant(value=None):
    if value is None:
        return dt.datetime.now(UTC)
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            raise ValueError('Use a time with a timezone')
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, UTC)
    out = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if out.tzinfo is None:
        raise ValueError('Use a time with a timezone')
    return out.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).strftime(FMT)


def ready(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='health_regimen'").fetchone())


def copy(K, key, **values):
    # Resolve from the same kit copy bundle as all other reminders.
    return K._i18n('regimen.' + key, key).format(**values)


def inbound(K):
    try:
        value = json.loads((Path(K._kit_root()) / '.telegram_bot/inbound-context.json').read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def private_chat(K):
    """Owner destination using the framework's owner resolver, then legacy env.

    Never infer a destination from the current group/reply. Positive Telegram
    chat ids identify private users. Missing owner identity prevents delivery.
    """
    root = Path(K._kit_root())
    try:
        import sys
        sys.path.insert(0, str(root / 'bridge'))
        from ownership import read_owner_lock
        owner = read_owner_lock(str(root / '.telegram_bot'))
        if str(owner).isdigit() and int(owner) > 0:
            return int(owner)
    except (ImportError, OSError, ValueError, TypeError):
        pass
    try:
        for line in (root / '.telegram_bot/.env').read_text().splitlines():
            if line.startswith('ALLOWED_USER_IDS='):
                value = line.split('=', 1)[1].strip().strip('\"\'').split(',')[0]
                if value.isdigit() and int(value) > 0:
                    return int(value)
    except OSError:
        pass
    return None


def weekdays(value):
    if value in ('weekdays', '평일'):
        return '0,1,2,3,4'
    if value in ('daily', '매일', None):
        return ALL_DAYS
    parts = [int(x) for x in value.split(',')]
    if not parts or any(x not in range(7) for x in parts):
        raise ValueError('weekdays: 0=Mon through 6=Sun')
    return ','.join(str(x) for x in sorted(set(parts)))


def validate_amount(amount):
    if not re.fullmatch(r'\d+(?:\.\d+)?', str(amount)) or float(amount) <= 0:
        raise ValueError('Use the positive amount the owner stated')


def add(conn, *, member, name, kind, amount, unit, at=None, meal=None,
        workout=False, after_workout=False, record_only=False, days=None, tz='Asia/Seoul', now=None):
    stamp = iso(instant(now))
    ZoneInfo(tz)
    if sum((at is not None, meal is not None, bool(workout), bool(after_workout), bool(record_only))) > 1:
        raise ValueError('Choose at most one schedule: --at, --meal, --before-workout, --after-workout or --record-only')
    if at is not None and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', at):
        raise ValueError('Use HH:MM')
    if meal is not None:
        meal = meal_slots.canonical(meal)
    if not all(str(x).strip() for x in (member, name, amount, unit)):
        raise ValueError('Name, owner-stated amount and unit are required')
    validate_amount(amount)
    sk = ('clock' if at else 'meal' if meal else 'before_workout' if workout
          else 'after_workout' if after_workout else 'record_only')
    days = weekdays(days)
    row = conn.execute('SELECT id FROM health_regimen WHERE member_id=? AND name=? '
        'AND schedule_kind=? AND clock_time IS ? AND meal_slot IS ? AND weekdays=? AND active=1',
        (member, name.strip(), sk, at, meal, days)).fetchone()
    if row:
        old = conn.execute('SELECT amount,unit FROM health_regimen WHERE id=?', row).fetchone()
        if old != (str(amount), unit):
            raise ValueError('Regimen already exists with a different amount; use regimen edit %s' % row[0])
        return row[0]
    identity = correction.reserve(conn, 'regimen-id-sequence',
                                  conn.execute('SELECT COALESCE(MAX(id),0) FROM health_regimen').fetchone()[0])
    return conn.execute('INSERT INTO health_regimen(id,member_id,name,kind,amount,unit,'
        'schedule_kind,clock_time,meal_slot,weekdays,timezone,created_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (identity, member, name.strip(), kind, str(amount), unit,
        sk, at, meal, days, tz, stamp)).lastrowid


def parse_registration(value):
    """Compatibility registration accepts typed JSON, with prose as evidence only.

    Normal calls use regimen add with schedule/name/amount/unit arguments.
    """
    data = json.loads(value) if isinstance(value, str) else value
    if not isinstance(data, dict) or not isinstance(data.get('items'), list) or not data['items']:
        raise ValueError('registration requires a typed schedule and nonempty items list; use regimen add')
    schedule = data.get('schedule', {})
    if not isinstance(schedule, dict) or set(schedule) - {'at','meal','workout','after_workout','record_only','days','tz'}:
        raise ValueError('invalid typed registration schedule')
    items = data['items']
    if any(not isinstance(r, dict) or set(r) != {'name','amount','unit'} for r in items):
        raise ValueError('each registration item requires name, amount and unit')
    return schedule, items


def schedule_text(K, row):
    if row['schedule_kind'] == 'clock':
        when = row['clock_time']
    elif row['schedule_kind'] == 'meal':
        when = copy(K, 'with_meal', slot=meal_slots.label(row['meal_slot']))
    else:
        when = copy(K, row['schedule_kind'])
    if row['weekdays'] != ALL_DAYS:
        when += ' (' + ', '.join(copy(K, 'day.' + x) for x in row['weekdays'].split(',')) + ')'
    return when


def rows(conn, query, params=()):
    cur = conn.execute(query, params)
    keys = [x[0] for x in cur.description]
    return [dict(zip(keys, row)) for row in cur]


def anchors(conn, regimen, date):
    """Use actual scheduled block anchors, without inventing meal times.

    Meal-slot anchors are block_attr.meal_slot, then the actual meal report;
    workout anchors use action_kind=workout on routine occurrences.
    """
    if regimen['schedule_kind'] == 'record_only':
        return []
    if regimen['schedule_kind'] == 'after_workout':
        if not session_ready(conn):
            return []
        tz = ZoneInfo(regimen['timezone'])
        found = [instant(s) for (s,) in conn.execute(
            'SELECT closed_at FROM health_regimen_session_end WHERE member_id=?',
            (regimen['member_id'],)) if s >= regimen['created_at']
                 and instant(s).astimezone(tz).date() == date]
        return [min(found)] if found else []
    token = 'meal_slot' if regimen['schedule_kind'] == 'meal' else 'action_kind'
    value = meal_slots.stored(regimen['meal_slot']) if token == 'meal_slot' else 'workout'
    slot_expression = meal_slots.stored_sql('a.value') if token == 'meal_slot' else 'a.value'
    found = conn.execute('SELECT e.start_at FROM timeblock e JOIN block_attr a '
        'ON a.block_id=e.id WHERE a.attr_token=? AND ' + slot_expression + '=? '
        "AND e.schedule_kind='timed' AND e.settled_at IS NULL AND e.start_at IS NOT NULL",
        (token, value)).fetchall()
    tz = ZoneInfo(regimen['timezone'])
    planned = [instant(s) for (s,) in found if instant(s).astimezone(tz).date() == date]
    if planned or token != 'meal_slot':
        return planned
    # Unscheduled meals still have a real slot boundary: the first food
    # recorded in that slot today. Repeated dishes are one occasion, and a
    # historical/backfilled meal must never become today's medication alert.
    first = conn.execute("SELECT MIN(b.created_at) FROM block b JOIN block_attr a "
        "ON a.block_id=b.id WHERE b.settled_by='meal-record' AND b.date=? "
        "AND a.attr_token='meal_slot' AND " + slot_expression + "=?", (str(date), value)).fetchone()[0]
    try:
        observed = instant(first) if first else None
    except ValueError:
        observed = None
    return [observed] if observed and observed.astimezone(tz).date() == date else []


def prepare(conn, now=None, scope=None):
    """Materialize today's occurrences on the existing reminder heartbeat.

    Future rows allow card readback but input cannot close them before due.
    The UNIQUE occurrence key and snapshots make repeats and retries safe.
    """
    if not ready(conn) or (scope is not None and not scope.accepts('health')):
        return
    now = instant(now)
    for r in rows(conn, 'SELECT * FROM health_regimen WHERE active=1 ORDER BY id'):
        tz = ZoneInfo(r['timezone'])
        # Include adjacent local dates for polls around midnight.
        for delta in (-1, 0, 1):
            date = now.astimezone(tz).date() + dt.timedelta(days=delta)
            if str(date.weekday()) not in r['weekdays'].split(','):
                continue
            if r['schedule_kind'] == 'clock':
                h, m = map(int, r['clock_time'].split(':'))
                planned = [dt.datetime.combine(date, dt.time(h, m), tz)]
            else:
                planned = anchors(conn, r, date)
            for at in planned:
                stamp = iso(at)
                if stamp < r['created_at']:
                    continue
                # A corrected clock must not offer a second dose on a local
                # day already completed under this same regimen identity.
                if r['schedule_kind'] == 'clock' and any(
                        instant(t).astimezone(tz).date() == date
                        for (t,) in conn.execute(
                            'SELECT planned_at FROM health_regimen_taken '
                            'WHERE regimen_id=? AND taken_at IS NOT NULL', (r['id'],))):
                    continue
                if r['schedule_kind'] == 'after_workout' and completed_on(conn, r, date):
                    continue
                occurrence(conn, r, at)


def session_ready(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='health_regimen_session_end'").fetchone())


def occurrence(conn, regimen, at, silent=False):
    stamp = iso(at)
    if correction.suppressed(conn, regimen['member_id'], regimen['id'], stamp):
        return None
    gid = hashlib.sha256((regimen['member_id'] + ':' + stamp).encode()).hexdigest()[:20]
    conn.execute('INSERT OR IGNORE INTO health_regimen_reminder '
        '(id,member_id,planned_at,due_at,timezone,sent_revision) VALUES(?,?,?,?,?,?)',
        (gid, regimen['member_id'], stamp, stamp, regimen['timezone'], 0 if silent else -1))
    if conn.execute('SELECT 1 FROM health_regimen_taken WHERE regimen_id=? AND planned_at=?',
                    (regimen['id'], stamp)).fetchone():
        return gid
    position = correction.reserve(conn, 'regimen-position:' + gid,
        conn.execute('SELECT COALESCE(MAX(position),0) FROM health_regimen_taken WHERE reminder_id=?', (gid,)).fetchone()[0])
    conn.execute('INSERT OR IGNORE INTO health_regimen_taken '
        '(regimen_id,planned_at,reminder_id,position,name,amount,unit) VALUES(?,?,?,?,?,?,?)',
        (regimen['id'], stamp, gid, position, regimen['name'], regimen['amount'], regimen['unit']))
    return gid


def completed_on(conn, regimen, date):
    tz = ZoneInfo(regimen['timezone'])
    return any(instant(t).astimezone(tz).date() == date for (t,) in conn.execute(
        'SELECT planned_at FROM health_regimen_taken WHERE regimen_id=? AND taken_at IS NOT NULL',
        (regimen['id'],)))


def session_end(conn, session_id, now=None, member='owner'):
    """Called in the successful close transaction. The heartbeat alone sends.

    First close wins across retries and legacy/v2 callers. A late replay never
    moves yesterday's reminder into today. No task, routine or meal is written.
    """
    if not ready(conn) or not session_ready(conn):
        return False  # older kit schema remains usable by the health pack
    row = conn.execute('SELECT closed_at FROM workouts WHERE id=?', (session_id,)).fetchone()
    if not row or row[0] is None:
        return False
    closed = instant(row[0] / 1000 if isinstance(row[0], (int, float)) else row[0])
    conn.execute('INSERT OR IGNORE INTO health_regimen_session_end '
                 '(session_id,member_id,closed_at) VALUES(?,?,?)',
                 (session_id, member, iso(closed)))
    prepare(conn, now)
    return True


def daily_items(conn, date, member='owner', now=None):
    """Read-only daily snapshot, including unmaterialized record-only items.

    After-workout regimens stay visible for the daily miss check on rest days.
    They have at most one intake per local day; clock/meal regimens retain
    their existing occurrence semantics. Future clock/anchor doses stay out
    of the miss check, while the supplement denominator includes today's plan.
    """
    if not ready(conn):
        return []
    now = instant(iso(now) if isinstance(now, dt.datetime) else now)
    day = dt.date.fromisoformat(str(date))
    result = []
    for r in rows(conn, 'SELECT * FROM health_regimen WHERE active=1 AND member_id=? ORDER BY id', (member,)):
        tz = ZoneInfo(r['timezone'])
        if (str(day.weekday()) not in r['weekdays'].split(',')
                or instant(r['created_at']).astimezone(tz).date() > day):
            continue
        if r['schedule_kind'] == 'clock':
            h, m = map(int, r['clock_time'].split(':'))
            planned = [dt.datetime.combine(day, dt.time(h, m), tz)]
        elif r['schedule_kind'] in ('record_only', 'after_workout'):
            planned = [max(dt.datetime.combine(day, dt.time(), tz), instant(r['created_at']))]
        else:
            planned = anchors(conn, r, day)
        planned = [at for at in planned if iso(at) >= r['created_at']]
        if not planned:
            continue
        r['taken'] = completed_on(conn, r, day)
        r['due'] = any(at <= now for at in planned)
        result.append(r)
    return result


def snapshot(conn, date, member='owner', now=None):
    supplements = [r for r in daily_items(conn, date, member, now) if r['kind'] == 'supplement']
    return dict(date=str(date), taken=sum(r['taken'] for r in supplements), total=len(supplements))


def retro_items(K, conn, date, member='owner', now=None):
    # Reuse the approved single-item reminder verbatim; names stay private.
    return [dict(kind='module', id='regimen:%s:%s' % (r['id'], date),
                 text=copy(K, 'single', **{k:r[k] for k in ('name','amount','unit')}))
            for r in daily_items(conn, date, member, now) if r['due'] and not r['taken']]


def recordable(conn, member, now, regimen_ids=()):
    day = now.astimezone(ZoneInfo('Asia/Seoul')).date()
    return [r for r in daily_items(conn, day, member, now)
            if r['id'] in regimen_ids and r['schedule_kind'] in ('record_only', 'after_workout')
            and not r['taken']]


def prepare_input(conn, member, now, regimen_ids):
    for r in recordable(conn, member, now, regimen_ids):
        tz = ZoneInfo(r['timezone'])
        if any(instant(t).astimezone(tz).date() == now.astimezone(tz).date()
               for (t,) in conn.execute('SELECT planned_at FROM health_regimen_taken '
                   'WHERE regimen_id=? AND taken_at IS NULL AND planned_at<=?', (r['id'], iso(now)))):
            continue
        occurrence(conn, r, now, silent=True)


def items(conn, gid):
    return rows(conn, 'SELECT * FROM health_regimen_taken WHERE reminder_id=? ORDER BY position', (gid,))


def render(K, conn, group, numbered=False):
    entries = items(conn, group['id'])
    left = [r for r in entries if r['taken_at'] is None]
    if not left:
        return copy(K, 'closed'), []
    if numbered or group['check_mode']:
        return '\n'.join(copy(K, 'numbered', n=r['position'], name=r['name'],
            amount=r['amount'], unit=r['unit'], mark=copy(K, 'checked' if r['taken_at'] else 'open'))
            for r in entries), []
    if len(entries) == 1:
        r = left[0]
        text = copy(K, 'single', **{k:r[k] for k in ('name','amount','unit')})
        actions = [('taken', 'taken'), ('later', 'later')]
    else:
        h = instant(group['planned_at']).astimezone(ZoneInfo(group['timezone'])).hour
        title = copy(K, 'morning' if h < 12 else 'afternoon' if h < 18 else 'evening')
        text = copy(K, 'group', title=title, n=len(left)) + '\n' + '\n'.join(
            copy(K, 'item', **{k:r[k] for k in ('name','amount','unit')}) for r in left)
        actions = [('all', 'all'), ('check', 'check')]
    buttons = [[dict(text=copy(K, label), callback_data='opt:regimen:%s:%s' % (group['id'], action))
                for label, action in actions]]
    return text, buttons


def alerts(K, conn, now, scope):
    if not ready(conn) or not scope.accepts('health'):
        return []
    now = instant(now)
    out = []
    # No early pills: the five-minute poll catches up after the due instant.
    for g in rows(conn, 'SELECT * FROM health_regimen_reminder WHERE due_at<=? '
        'AND due_at>=? AND sent_revision<revision ORDER BY due_at,id',
        (iso(now), iso(now-dt.timedelta(minutes=10)))):
        if not any(x['taken_at'] is None for x in items(conn, g['id'])):
            continue
        text, buttons = render(K, conn, g)
        out.append(dict(key='regimen:%s:%s' % (g['id'],g['revision']),
            kind='regimen', alert='start', header_time=0, lead_min=0,
            hhmm='', title=text, lines=text.splitlines(), message=text,
            helper=True, links=[], location='', purpose='', group_id=g['id'],
            buttons=buttons, domain='health', private=True))
    return out


def deliver(K, db_path, alert, now):
    """Strict existing carrier; no name-bearing fallback to a shared channel."""
    import start_card
    import reminder_owner
    chat = private_chat(K)
    push = K._photo_push_sh()
    if not chat or not push:
        return False
    with sqlite3.connect(db_path, timeout=5) as conn:
        conn.execute('BEGIN IMMEDIATE')
        g = rows(conn, 'SELECT * FROM health_regimen_reminder WHERE id=?', (alert['group_id'],))[0]
        if (g['sent_revision'] >= g['revision'] or g['due_at'] > iso(instant(now))
                or not any(r['taken_at'] is None for r in items(conn,g['id']))):
            return True
        text, buttons = render(K, conn, g)
        rc, ack = start_card._push(push, ['--strict', '--chat', str(chat), '--text', text,
                        '--buttons-json', json.dumps(buttons, ensure_ascii=False)])
        if rc != 0 or not ack.get('message_id') or str(ack.get('chat_id')) != str(chat):
            return False
        conn.execute('UPDATE health_regimen_reminder SET sent_revision=revision,'
            'sending_agent=?,message_id=?,chat_id=? WHERE id=?',
            (reminder_owner.instance_agent(K) or 'ag', ack['message_id'], chat, g['id']))
    return True


def matching(conn, member, now, text='', gid=None, message_id=None):
    if not ready(conn):
        return []
    groups = rows(conn, 'SELECT DISTINCT g.* FROM health_regimen_reminder g '
        'JOIN health_regimen_taken t ON t.reminder_id=g.id '
        'WHERE g.member_id=? AND g.planned_at<=? AND g.planned_at>=? '
        'AND t.taken_at IS NULL ORDER BY g.planned_at DESC',
        (member, iso(now), iso(now-dt.timedelta(hours=24))))
    def eligible(g):
        return rows(conn, 'SELECT t.name,r.schedule_kind,r.timezone FROM health_regimen_taken t '
                    'JOIN health_regimen r ON r.id=t.regimen_id '
                    'WHERE t.reminder_id=? AND t.taken_at IS NULL AND r.active=1', (g['id'],))

    def current_entries(g):
        return [r for r in eligible(g) if r['schedule_kind'] not in ('record_only','after_workout')
                or instant(g['planned_at']).astimezone(ZoneInfo(r['timezone'])).date()
                == now.astimezone(ZoneInfo(r['timezone'])).date()]

    groups = [g for g in groups if current_entries(g)]
    if gid:
        groups = [g for g in groups if g['id'] == gid]
    if message_id is not None:
        groups = [g for g in groups if str(g['message_id']) == str(message_id)]
    return groups


def completion(text):
    """Compatibility predicate; owner prose never authorizes intake."""
    return False


def apply(K, conn, text='', *, member='owner', now=None, gid=None, source='words',
          message_id=None, action=None, positions=None, regimen_ids=()):
    now = instant(now)
    callback = re.fullmatch(r'(?:opt:)?regimen:([a-f0-9]{20}):(all|taken|check|later)', text) if action is None else None
    if callback:
        gid, action = callback.groups()
        source = 'button'
    numeric = bool(re.fullmatch(r'\d+(?:\s*[, ]\s*\d+)*', text.strip()))
    if action is None and source == 'button':
        # Issued transport button value, never a free owner sentence.
        action = next((a for a in ('all', 'taken', 'check', 'later') if text == copy(K, a)), None)
    if action is None and not numeric:
        return dict(status='NO_MATCH', reply='')
    if action is not None and action not in ('all', 'taken', 'check', 'later'):
        raise ValueError('unknown regimen action')
    prepare(conn, iso(now))
    if action in ('all', 'taken'):
        prepare_input(conn, member, now, regimen_ids)
    candidates = matching(conn, member, now, gid=gid, message_id=message_id)
    if regimen_ids:
        candidates = [g for g in candidates if any(r['regimen_id'] in regimen_ids for r in items(conn, g['id']))]
    if not candidates:
        return dict(status='NO_OPEN', reply=copy(K,'no_open'))
    if len(candidates) != 1:
        return dict(status='AMBIGUOUS', reply=copy(K,'choose'))
    g = candidates[0]
    if source == 'button':
        import reminder_owner
        sender = reminder_owner.instance_agent(K)
        if g['sending_agent'] and sender != g['sending_agent']:
            return dict(status='WRONG_SENDER', reply=copy(K,'no_open'))
    if action == 'check':
        conn.execute('UPDATE health_regimen_reminder SET check_mode=1 WHERE id=?', (g['id'],))
        g['check_mode'] = 1
        return dict(status='CHECK', group_id=g['id'], reply=render(K,conn,g)[0])
    if action == 'later':
        conn.execute('UPDATE health_regimen_reminder SET due_at=?, revision=revision+1 WHERE id=?',
                     (iso(now+dt.timedelta(minutes=30)),g['id']))
        return dict(status='SNOOZED', group_id=g['id'], reply=copy(K,'snoozed'))
    entries = items(conn,g['id'])
    if positions is not None:
        selected = set(positions)
    elif action in ('all', 'taken'):
        selected = {r['position'] for r in entries if not regimen_ids or r['regimen_id'] in regimen_ids}
    elif numeric and g['check_mode']:
        selected = {int(n) for n in re.findall(r'\d+', text)}
    else:
        return dict(status='NO_MATCH',reply='')
    if not selected or not selected <= {r['position'] for r in entries}:
        return dict(status='INVALID',reply=copy(K,'choose'))
    for r in entries:
        if r['position'] in selected and r['taken_at'] is None:
            if not conn.execute('SELECT 1 FROM health_regimen WHERE id=? AND member_id=? AND active=1',
                                (r['regimen_id'], member)).fetchone():
                continue
            if source == 'button' and [r['regimen_id'], r['planned_at']] in correction.load(
                    conn, correction.key(member, 'untaken-doses'), []):
                continue  # An old button cannot overwrite an owner correction.
            if source != 'button':
                correction.intake_marker(conn, member, r['regimen_id'], r['planned_at'], clear=True)
            conn.execute('UPDATE health_regimen_taken SET taken_at=?,source=? '
                'WHERE regimen_id=? AND planned_at=? AND taken_at IS NULL',
                (iso(now),source,r['regimen_id'],r['planned_at']))
    remaining = sum(r['taken_at'] is None for r in items(conn,g['id']))
    return dict(status='OPEN' if remaining else 'CLOSED', group_id=g['id'], remaining=remaining,
                reply=render(K,conn,g,numbered=bool(remaining))[0])


def taken_count(conn, date, member='owner'):
    """Private regimen count, independent of display language."""
    if not ready(conn):
        return 0
    count = 0
    for r in rows(conn, 'SELECT t.taken_at,g.timezone FROM health_regimen_taken t '
        'JOIN health_regimen_reminder g ON g.id=t.reminder_id WHERE g.member_id=? '
        'AND t.taken_at IS NOT NULL', (member,)):
        if str(instant(r['taken_at']).astimezone(ZoneInfo(r['timezone'])).date()) == str(date):
            count += 1
    return count


def card_line(K, conn, date, member='owner'):
    """Shared day/diet line intentionally has no names, including supplements."""
    count = taken_count(conn, date, member)
    return copy(K,'card',n=count) if count else ''


def hook_context(K, conn, text, context=None):
    """Read-only prompt routing before meal capture, including plain words."""
    if not ready(conn):
        return None
    ctx = context or {}
    if str(ctx.get('chat_id','')).startswith('-') or ctx.get('chat_type') in ('group','supergroup','channel'):
        return None
    token = ctx.get('callback_data','') if ctx.get('source') == 'callback' else ''
    if token.startswith('opt:regimen:'):
        return '[health-regimen] FIRST run database/kit_life.sh regimen input ' + shlex.quote(token[4:]) + '. Relay reply only. Never task-done, routine add, meal-add or ask fullness for this intake; never recreate a retired supplement task.'
    # Route from open reminder state, regardless of the owner's wording.
    groups = matching(conn, 'owner', instant(), message_id=ctx.get('reply_to_message_id'))
    if not groups:
        return None
    return ('[health-regimen] Open reminder groups: ' + ','.join(g['id'] for g in groups) +
            '. Interpret the owner intent; use regimen take --group ID --positions N,N '
            'or regimen input with --action check|later. Registration uses regimen add '
            'with typed name, amount, unit and schedule. Owner words are evidence only.')


def check_duplicate(conn, r):
    duplicate = conn.execute(
        'SELECT id FROM health_regimen WHERE member_id=? AND name=? AND active=1 '
        'AND schedule_kind=? AND clock_time IS ? AND meal_slot IS ? AND weekdays=? AND id<>?',
        tuple(r[k] for k in ('member_id', 'name', 'schedule_kind', 'clock_time', 'meal_slot', 'weekdays', 'id'))).fetchone()
    if duplicate:
        raise ValueError('Regimen already exists for that schedule')


def edit_definition(K, conn, args, now):
    old = correction.definition(conn, args.member, args.id)
    r = dict(old)
    for field, flag in (('name', 'name'), ('kind', 'kind'), ('amount', 'amount'),
                        ('unit', 'unit'), ('weekdays', 'days'), ('timezone', 'tz')):
        value = getattr(args, flag)
        if value is not None:
            r[field] = weekdays(value) if field == 'weekdays' else value
    r['name'] = r['name'].strip()
    if not r['name'] or not r['unit'].strip():
        raise ValueError('Name, owner-stated amount and unit are required')
    validate_amount(r['amount'])
    ZoneInfo(r['timezone'])
    schedules = [(kind, value) for kind, value in (
        ('clock', args.at), ('meal', args.meal), ('before_workout', args.before_workout),
        ('after_workout', args.after_workout), ('record_only', args.record_only))
        if value is not None and value is not False]
    if len(schedules) > 1:
        raise ValueError('Choose at most one schedule')
    if schedules:
        r['schedule_kind'], value = schedules[0]
        r['clock_time'] = value if r['schedule_kind'] == 'clock' else None
        r['meal_slot'] = meal_slots.canonical(value) if r['schedule_kind'] == 'meal' else None
    if r['clock_time'] and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', r['clock_time']):
        raise ValueError('Use HH:MM')
    check_duplicate(conn, r)
    fields = ('name', 'kind', 'amount', 'unit', 'weekdays', 'timezone', 'schedule_kind', 'clock_time', 'meal_slot')
    conn.execute('UPDATE health_regimen SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?',
                 [r[k] for k in fields] + [r['id']])
    changed_schedule = any(r[k] != old[k] for k in ('schedule_kind', 'clock_time', 'meal_slot', 'weekdays', 'timezone'))
    if changed_schedule:
        conn.execute('DELETE FROM health_regimen_taken WHERE regimen_id=? AND taken_at IS NULL', (r['id'],))
        prepare(conn, iso(now))
    else:
        changed = [k for k in ('name', 'amount', 'unit') if r[k] != old[k]]
        if changed:
            conn.execute('UPDATE health_regimen_taken SET ' + ','.join(k+'=?' for k in changed)
                         + ' WHERE regimen_id=? AND taken_at IS NULL', [r[k] for k in changed] + [r['id']])
            conn.execute('UPDATE health_regimen_reminder SET revision=revision+1 WHERE id IN '
                         '(SELECT reminder_id FROM health_regimen_taken WHERE regimen_id=? AND taken_at IS NULL)', (r['id'],))
    correction.sync_tracking(K, conn, old, r)
    return r


def cli(K, argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', default=K.DB_PATH)
    ap.add_argument('--now', default=None)
    ap.add_argument('--member', default='owner')
    sub = ap.add_subparsers(dest='cmd', required=True)
    reg = sub.add_parser('register'); reg.add_argument('text')
    reg.add_argument('--kind', choices=('supplement','medication'), default='supplement')
    addp = sub.add_parser('add')
    for name in ('name','amount','unit'):
        addp.add_argument('--'+name,required=True)
    addp.add_argument('--kind', choices=('supplement','medication'), default='supplement')
    addp.add_argument('--at'); addp.add_argument('--meal'); addp.add_argument('--before-workout',action='store_true')
    addp.add_argument('--after-workout',action='store_true'); addp.add_argument('--record-only',action='store_true')
    addp.add_argument('--days'); addp.add_argument('--tz',default='Asia/Seoul')
    inp = sub.add_parser('input'); inp.add_argument('text'); inp.add_argument('--group')
    inp.add_argument('--source',choices=('button','words'),default='words'); inp.add_argument('--message-id')
    inp.add_argument('--action', choices=('all','taken','check','later'))
    take = sub.add_parser('take'); take.add_argument('--group'); take.add_argument('--positions')
    take.add_argument('--ids'); take.add_argument('--said', default='')
    listing = sub.add_parser('list'); listing.add_argument('--all', action='store_true')
    sub.add_parser('due')
    doses = sub.add_parser('doses'); doses.add_argument('--id', type=int); doses.add_argument('--group')
    sub.add_parser('undo'); sub.add_parser('redo')
    for verb in ('unretire', 'delete'):
        sub.add_parser(verb).add_argument('id', type=int)
    for verb in ('untake', 'dose-edit', 'dose-delete'):
        dose = sub.add_parser(verb)
        dose.add_argument('id', type=int)
        dose.add_argument('--planned-at', required=True)
        if verb == 'dose-edit':
            for field in ('name', 'amount', 'unit', 'taken-at', 'move-to'):
                dose.add_argument('--' + field)
            dose.add_argument('--source', choices=('button', 'words'))
    snap = sub.add_parser('snapshot'); snap.add_argument('date')
    retro = sub.add_parser('retro'); retro.add_argument('date')
    ended = sub.add_parser('session-end'); ended.add_argument('session_id', type=int)
    card = sub.add_parser('card'); card.add_argument('date')
    retire = sub.add_parser('retire'); retire.add_argument('id',type=int)
    edit = sub.add_parser('edit'); edit.add_argument('id', type=int)
    edit.add_argument('--amount'); edit.add_argument('--unit'); edit.add_argument('--name')
    edit.add_argument('--kind', choices=('supplement', 'medication'))
    edit.add_argument('--days'); edit.add_argument('--tz')
    edit.add_argument('--at'); edit.add_argument('--meal')
    edit.add_argument('--before-workout', action='store_true')
    edit.add_argument('--after-workout', action='store_true')
    edit.add_argument('--record-only', action='store_true')
    args = ap.parse_args(argv)
    now = instant(args.now)
    ctx = inbound(K)
    if str(ctx.get('chat_id','')).startswith('-') or ctx.get('chat_type') in ('group','supergroup','channel'):
        print(json.dumps(dict(status='PRIVATE_ONLY',reply=copy(K,'private_only')),ensure_ascii=False))
        return
    if not Path(args.db).is_file():
        ap.error('Database does not exist')
    try:
        with sqlite3.connect(args.db,timeout=5) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            mutations = ('add', 'register', 'take', 'input', 'edit', 'retire', 'unretire',
                         'delete', 'untake', 'dose-edit', 'dose-delete')
            if args.cmd not in ('snapshot', 'retro', 'card', 'list', 'doses'):
                conn.execute('BEGIN IMMEDIATE')
            if not ready(conn):
                raise ValueError('Apply migration 105 after 104 first')
            before = correction.snapshot(conn, args.member) if args.cmd in mutations else None
            if args.cmd in ('add','register'):
                if args.cmd == 'register':
                    schedule, entries = parse_registration(args.text)
                else:
                    schedule = dict(at=args.at,meal=args.meal,workout=args.before_workout,after_workout=args.after_workout,record_only=args.record_only,days=args.days,tz=args.tz)
                    entries = [dict(name=args.name,amount=args.amount,unit=args.unit)]
                ids = [add(conn,member=args.member,kind=args.kind,now=iso(now),**schedule,**r) for r in entries]
                saved = [rows(conn,'SELECT * FROM health_regimen WHERE id=?',(i,))[0] for i in ids]
                if args.member == 'owner':
                    # Owner pick 2026-10-30/31: a supplement the owner names shows on
                    # the meal card (and health agent's pin diet block) as the count line
                    # plus its item line; registering it is the opt-in.
                    import meal_tracking
                    tracked = meal_tracking.load(K, conn, meal_tracking.REGIMENS_KEY)
                    added = [r['name'] for r in saved if r['kind'] == 'supplement' and r['name'] not in tracked]
                    if added:
                        K._config_put(conn, meal_tracking.REGIMENS_KEY,
                                      json.dumps(tracked + list(dict.fromkeys(added)), ensure_ascii=False))
                receipt = copy(K,'saved',items=', '.join(r['name']+' '+r['amount']+r['unit'] for r in saved),when=schedule_text(K,saved[0]))
                print(receipt)
            elif args.cmd in ('input', 'take'):
                # Explicit group/source are useful to transports and offline replay.
                # With ordinary chat input, the inbound callback is authoritative.
                text = args.text if args.cmd == 'input' else args.said
                if ctx.get('source') == 'callback' and str(ctx.get('callback_data','')).startswith('opt:regimen:'):
                    text = ctx['callback_data']
                prepare(conn,iso(now))
                out = apply(K,conn,text,member=args.member,now=iso(now),gid=args.group,
                    source=args.source if args.cmd == 'input' else 'words',
                    message_id=args.message_id if args.cmd == 'input' else None,
                    action=args.action if args.cmd == 'input' else 'taken',
                    positions=[int(n) for n in args.positions.split(',')] if args.cmd == 'take' and args.positions else None,
                    regimen_ids=[int(n) for n in args.ids.split(',')] if args.cmd == 'take' and args.ids else ())
                print(json.dumps(out,ensure_ascii=False))
            elif args.cmd in ('undo', 'redo'):
                print(json.dumps(correction.replay(conn, args.member, redo=args.cmd == 'redo')))
            elif args.cmd in ('untake', 'dose-edit', 'dose-delete'):
                print(json.dumps(correction.correct_dose(conn, args.member, args, now), ensure_ascii=False))
            elif args.cmd in ('retire', 'unretire', 'delete'):
                old = correction.definition(conn, args.member, args.id)
                if args.cmd == 'delete':
                    conn.execute('DELETE FROM health_regimen_taken WHERE regimen_id=?', (args.id,))
                    conn.execute('DELETE FROM health_regimen WHERE id=?', (args.id,))
                    new = None
                else:
                    new = dict(old, active=int(args.cmd == 'unretire'))
                    if new['active']:
                        check_duplicate(conn, new)
                    conn.execute('UPDATE health_regimen SET active=? WHERE id=?', (new['active'], args.id))
                    if not new['active']:
                        conn.execute('DELETE FROM health_regimen_taken WHERE regimen_id=? AND taken_at IS NULL', (args.id,))
                if args.cmd != 'retire':
                    correction.sync_tracking(K, conn, old, new)
                if args.cmd == 'retire':
                    print(copy(K, 'retired'))
                else:
                    print(json.dumps(dict(status='UPDATED', id=args.id, owner_corrected=True)))
            elif args.cmd == 'edit':
                saved = edit_definition(K, conn, args, now)
                print(copy(K, 'saved', items=saved['name']+' '+saved['amount']+saved['unit'],
                           when=schedule_text(K, saved)))
            elif args.cmd == 'session-end':
                print(json.dumps(dict(prepared=session_end(conn, args.session_id, iso(now), args.member))))
            elif args.cmd == 'snapshot':
                print(json.dumps(snapshot(conn, args.date, args.member, iso(now))))
            elif args.cmd == 'retro':
                print(json.dumps(dict(items=retro_items(K, conn, args.date, args.member, iso(now))), ensure_ascii=False))
            elif args.cmd == 'card':
                print(card_line(K,conn,args.date,args.member))
            elif args.cmd == 'list':
                print(json.dumps(rows(conn, 'SELECT * FROM health_regimen WHERE member_id=?' +
                    ('' if args.all else ' AND active=1') + ' ORDER BY id', (args.member,)), ensure_ascii=False))
            elif args.cmd == 'doses':
                query = 'SELECT t.* FROM health_regimen_taken t JOIN health_regimen r ON r.id=t.regimen_id WHERE r.member_id=?'
                params = [args.member]
                if args.id is not None:
                    query += ' AND r.id=?'; params.append(args.id)
                if args.group is not None:
                    query += ' AND t.reminder_id=?'; params.append(args.group)
                print(json.dumps(rows(conn, query + ' ORDER BY t.planned_at,t.position', params), ensure_ascii=False))
            else:
                prepare(conn,iso(now))
                print(json.dumps(rows(conn,'SELECT g.*,t.position,t.name,t.amount,t.unit,t.taken_at,t.source '
                    'FROM health_regimen_reminder g JOIN health_regimen_taken t ON t.reminder_id=g.id '
                    'WHERE g.member_id=? AND g.planned_at<=? AND t.taken_at IS NULL ORDER BY g.planned_at,t.position',
                    (args.member,iso(now))),ensure_ascii=False))

            if before is not None:
                correction.save(conn, args.member, args.cmd, before)

        if (args.cmd in mutations + ('undo', 'redo', 'session-end')
                and Path(args.db).resolve() == Path(K.DB_PATH).resolve()):
            K._health_pin_refresh()
    except (ValueError, sqlite3.Error, KeyError) as error:
        ap.error(str(error))
