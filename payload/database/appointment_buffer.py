"""Appointment arrival margins: scoped preferences, undo and presentation.

Storage is existing config rows plus one user block_attr per appointment.
No new schema. Same-scope conflicts choose strict first, then larger margin,
then stable key; a group and a person occupy the same precedence level.
"""
import travel_core
import argparse
import json
import uuid

PREFIX = 'appointment_buffer.rule.'
ATTR = 'appointment_buffer'
UNDO = 'appointment_buffer.undo'
SEEN = 'appointment_buffer.first.'


def previous(K, conn, anchor):
    """Immediate earlier real timed block, on the appointment's local day."""
    if not anchor.get('start_at'):
        return None
    since, _ = K._travel_local_day_window(anchor['start_at'])
    alternatives = ("AND NOT EXISTS (SELECT 1 FROM routine_placement p "
                    "JOIN routine_def r ON r.id=p.routine_id WHERE "
                    "r.recurrence_id=timeblock.recurrence_id) "
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE "
                                    "name='routine_placement'").fetchone() else '')
    return K._travel_row(conn,
        "SELECT * FROM timeblock WHERE id != ? AND block_class IS NULL "
        "AND settled_at IS NULL AND schedule_kind='timed' "
        "AND start_at >= ? AND start_at < ? AND end_at IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM routine_def r WHERE "
        "r.recurrence_id=timeblock.recurrence_id AND r.placement='ride_travel') "
        + alternatives + "ORDER BY end_at DESC, start_at DESC, id DESC LIMIT 1",
        (anchor.get('id', -1), since, anchor['start_at']))


def after_work(K, conn, anchor):
    prev = previous(K, conn, anchor)
    # A routine/presence instance, not a title or weekday guess. An intervening
    # appointment/task breaks the chain, including an intervening home block.
    return bool(prev and prev['kind'] == 'presence' and
                prev['end_at'] <= anchor['start_at'] and
                (prev.get('is_routine') == 1 or prev.get('recurrence_id')))


SITUATIONS = {'after-work': after_work}


def _read(K, conn, key):
    raw = K._config_stored(conn, PREFIX + key)
    return json.loads(raw) if raw else None


def _appointment(conn, bid):
    row = conn.execute("SELECT value FROM block_attr WHERE block_id=? "
                       "AND attr_token=? AND source='user'", (bid, ATTR)).fetchone()
    return json.loads(row[0]) if row else None


def resolve(K, conn, anchor):
    product = {'minutes': max(0, K.TRAVEL_APPT_BUFFER_MIN), 'strict': False,
               'scope': 'product', 'key': 'product'}
    if anchor.get('kind') != 'appointment':
        return dict(product, minutes=0)
    bid = anchor.get('id')
    own = _appointment(conn, bid)
    if own:
        return own
    choices = []
    for (pid,) in conn.execute('SELECT person_id FROM event_persons WHERE event_id=?', (bid,)):
        rule = _read(K, conn, 'person.%s' % pid)
        if rule:
            choices.append(rule)
    # Explicit appointment group links, not assumed membership of a person.
    for (gid,) in conn.execute('SELECT group_id FROM event_group WHERE event_id=?', (bid,)):
        rule = _read(K, conn, 'group.%s' % gid)
        if rule:
            choices.append(rule)
    if choices:
        return sorted(choices, key=lambda r: (-int(r['strict']), -r['minutes'], r['key']))[0]
    for token, matches in SITUATIONS.items():
        rule = _read(K, conn, 'situation.' + token)
        if rule and matches(K, conn, anchor):
            return rule
    return _read(K, conn, 'default') or product


def _put(K, conn, rule, key, bid=None):
    if bid is not None:
        conn.execute("DELETE FROM block_attr WHERE block_id=? AND attr_token=? "
                     "AND source='user'", (bid, ATTR))
        if rule is not None:
            conn.execute('INSERT INTO block_attr(block_id,attr_token,value,source) '
                         "VALUES (?,?,?,'user')", (bid, ATTR, json.dumps(rule)))
    elif rule is None:
        conn.execute('DELETE FROM config WHERE key=?', (PREFIX + key,))
    else:
        K._config_put(conn, PREFIX + key, json.dumps(rule))


def retire_target(K, conn, kind, ident, into=None):
    """Follow registry deletes/merges so a reused integer ID inherits no rule.

    A merge keeps the destination's explicit preference if it has one;
    otherwise it carries the source preference to the surviving identity.
    This is part of the registry transaction; the caller commits/refreshes.
    """
    key = '%s.%d' % (kind, ident)
    old = _read(K, conn, key)
    destination = _read(K, conn, '%s.%d' % (kind, into)) if into is not None else None
    if old and into is not None:
        dest = '%s.%d' % (kind, into)
        if destination is None:
            table = 'persons' if kind == 'person' else 'person_group'
            name = conn.execute('SELECT name FROM ' + table + ' WHERE id=?', (into,)).fetchone()[0]
            _put(K, conn, dict(old, key=dest, name=name), dest)
    if old:
        _put(K, conn, None, key)
    undo_raw = K._config_stored(conn, UNDO)
    if undo_raw and json.loads(undo_raw)['key'] == key:
        conn.execute('DELETE FROM config WHERE key=?', (UNDO,))
    return bool(old or destination)


def refresh(K, conn, now=None, bid=None):
    """Apply a buffer preference through the shared travel event service."""
    return travel_core.react(K, conn, 'buffer_rule_changed', now=now, bid=bid)


def set_rule(K, conn, scope, minutes, target=None, strict=False, now=None):
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
        raise ValueError('minutes must be a nonnegative integer')
    if strict and scope not in ('appointment', 'person'):
        raise ValueError('strict is only supported for appointment or person/group rules')
    now = now or K.now_utc()
    bid, name = None, ''
    if scope == 'appointment':
        row = K._travel_row(conn, 'SELECT * FROM timeblock WHERE id=? OR ulid=?',
                            (str(target), str(target)))
        if not row or row['kind'] != 'appointment' or row['settled_at'] or not row['start_at'] or row['start_at'] <= now:
            raise ValueError('appointment must be live and future')
        bid, name = row['id'], row['title']
        key = 'appointment.%d' % bid
    elif scope == 'person':
        kind, sep, ref = str(target).partition(':')
        if not sep:
            kind, ref = 'person', str(target)
        if kind not in ('person', 'group'):
            raise ValueError('target must be person:<id> or group:<id>')
        table = 'persons' if kind == 'person' else 'person_group'
        rows = conn.execute("SELECT id,COALESCE(NULLIF(display_name,''),name) FROM " + table +
                            ' WHERE id=? OR name=?', (ref, ref)).fetchall()
        if len(rows) != 1:
            raise ValueError('person/group target must resolve exactly once')
        ident, name = rows[0]
        key = '%s.%d' % (kind, ident)
    elif scope == 'situation':
        target = str(target).replace('_', '-').replace(' ', '-')
        if target not in SITUATIONS:
            raise ValueError('unknown situation: %s' % target)
        key = 'situation.' + target
    elif scope == 'default' and target is None:
        key = 'default'
    else:
        raise ValueError('invalid scope or target')
    old = _appointment(conn, bid) if bid is not None else _read(K, conn, key)
    rule = dict(key=key, scope=scope, minutes=minutes, strict=bool(strict),
                name=name, revision=uuid.uuid4().hex)
    with conn:
        _put(K, conn, rule, key, bid)
        K._config_put(conn, UNDO, json.dumps(dict(key=key, bid=bid, old=old)))
    refresh(K, conn, now=now, bid=bid)
    return rule


def undo(K, conn, now=None):
    raw = K._config_stored(conn, UNDO)
    if not raw:
        return False
    change = json.loads(raw)
    with conn:
        _put(K, conn, change['old'], change['key'], change['bid'])
        conn.execute('DELETE FROM config WHERE key=?', (UNDO,))
    refresh(K, conn, now=now, bid=change['bid'])
    return True


def applied(K, conn, anchor, now):
    """Persist which future appointment first used a zero situation rule.

    The explanation belongs to that appointment only, remains stable across
    pin refreshes, and is not consumed by read-only preview/JSON readers.
    """
    if anchor.get('kind') != 'appointment' or not anchor.get('start_at') or anchor['start_at'] <= now:
        return
    rule = resolve(K, conn, anchor)
    leg = conn.execute(
        "SELECT end_at FROM timeblock WHERE derived_from=? AND derived_role='before' "
        "AND settled_at IS NULL ORDER BY id DESC LIMIT 1", (anchor['ulid'],)).fetchone()
    if (rule['scope'] == 'situation' and rule['minutes'] == 0 and leg
            and leg[0] == anchor['start_at']):
        with conn:
            conn.execute('INSERT OR IGNORE INTO config(key,value) VALUES (?,?)',
                         (SEEN + rule['revision'], anchor['ulid']))


def late(K, conn, anchor, leg):
    if leg[1] > anchor['start_at']:
        return True
    rule = K._travel_rule_for_anchor_row(conn, anchor)
    prev = previous(K, conn, anchor)
    dest = anchor.get('place_id') or (rule or {}).get('place_id')
    if (prev and leg[0] < prev['end_at'] <= anchor['start_at']
            and (not prev.get('place_id') or prev['place_id'] != dest)):
        return True
    if rule is None:
        return False
    needed = K._travel_resolve_before_leg(conn, rule, anchor)[0]
    return needed > K._iso_diff_min(leg[1], leg[0])


def card_parts(K, conn, anchor, leg, tz):
    rule = resolve(K, conn, anchor)
    actual = max(0, K._iso_diff_min(anchor['start_at'], leg[1]))
    text = K._i18n('buffer.card').format(dep=K._day_hm(leg[0], tz), n=actual,
                                        arrival=K._day_hm(leg[1], tz))
    tail = ''
    if rule.get('strict') and actual < rule['minutes']:
        tail += ' ' + K._i18n('buffer.squeeze').format(n=actual)
    if (rule['key'] == 'situation.after-work' and rule['minutes'] == 0 and actual == 0 and
            K._config_stored(conn, SEEN + rule['revision']) == anchor['ulid']):
        tail += ' ' + K._i18n('buffer.reason.after-work')
    return text, tail


def card(K, conn, anchor, leg, tz):
    return ''.join(card_parts(K, conn, anchor, leg, tz))


def confirmation(K, rule):
    key = rule['scope']
    if key == 'situation':
        key = 'after-work.zero' if rule['minutes'] == 0 else 'after-work'
    return K._i18n('buffer.set.' + key).format(n=rule['minutes'], name=rule['name'])


def show(K, conn, now=None):
    now = now or K.now_utc()
    rules = [json.loads(r[0]) for r in conn.execute(
        'SELECT value FROM config WHERE key GLOB ? ORDER BY key', (PREFIX + '*',))]
    rules += [json.loads(r[0]) for r in conn.execute(
        'SELECT a.value FROM block_attr a JOIN timeblock b ON b.id=a.block_id '
        "WHERE a.attr_token=? AND a.source='user' AND b.settled_at IS NULL "
        'AND b.start_at>? ORDER BY b.start_at,b.id', (ATTR, now))]
    lines = [K._i18n('buffer.show.precedence')]
    if not any(r['scope'] == 'default' for r in rules):
        lines.append(K._i18n('buffer.show.product').format(n=max(0, K.TRAVEL_APPT_BUFFER_MIN)))
    for r in rules:
        label = r['name'] or K._i18n('buffer.label.' + r['key'])
        line = K._i18n('buffer.show.rule').format(label=label, n=r['minutes'])
        if r['scope'] == 'appointment':
            line = K._i18n('buffer.show.appointment').format(label=label, n=r['minutes'])
        if r['strict']:
            line += K._i18n('buffer.show.strict')
        lines.append(line)
    return '\n'.join(lines)


def cli(K, verb, argv):
    p = argparse.ArgumentParser(prog=verb)
    if verb == 'buffer-set':
        p.add_argument('--scope', choices=('appointment', 'person', 'situation', 'default'), required=True)
        p.add_argument('--minutes', type=int, required=True)
        p.add_argument('--target')
        p.add_argument('--strict', action='store_true')
    args = p.parse_args(argv)
    conn = K.event_conn()
    try:
        if verb == 'buffer-set':
            rule = set_rule(K, conn, args.scope, args.minutes, args.target, args.strict)
            print(confirmation(K, rule))
        elif verb == 'buffer-show':
            print(show(K, conn))
        else:
            print(K._i18n('buffer.undo.ok' if undo(K, conn) else 'buffer.undo.empty'))
    except ValueError as exc:
        p.error(str(exc))
    finally:
        conn.close()
