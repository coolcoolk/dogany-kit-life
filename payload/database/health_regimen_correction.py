"""Typed owner corrections and guarded undo in the existing config store.

Only changed rows are restored. Polling and other members' records are not
rolled back. Deleted occurrences have owner markers so prepare cannot revive
them; undo restores both the occurrence and its marker atomically.
"""
import json

TABLES = {
    'health_regimen': ('id',),
    'health_regimen_reminder': ('id',),
    'health_regimen_taken': ('regimen_id', 'planned_at'),
}
TRACKED = 'meal_tracked_regimens'


def key(member, suffix):
    return 'regimen-correction:' + json.dumps([member, suffix], ensure_ascii=False)


def load(c, name, default=None):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='config'").fetchone():
        return default  # Minimal pre-110 migration fixtures have no config.
    row = c.execute('SELECT value FROM config WHERE key=?', (name,)).fetchone()
    return json.loads(row[0]) if row else default


def put(c, name, value):
    c.execute('INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO '
              "UPDATE SET value=excluded.value,updated_at=datetime('now','localtime')",
              (name, json.dumps(value, ensure_ascii=False)))


def reserve(c, name, minimum):
    """Do not reuse an identity/position that undo may need to restore."""
    value = max(load(c, name, 0), minimum) + 1
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='config'").fetchone():
        put(c, name, value)
    return value


def suppressed(c, member, regimen_id, planned_at):
    return [regimen_id, planned_at] in load(c, key(member, 'deleted-doses'), [])


def suppress(c, member, regimen_id, planned_at):
    markers = load(c, key(member, 'deleted-doses'), [])
    if [regimen_id, planned_at] not in markers:
        markers.append([regimen_id, planned_at])
        put(c, key(member, 'deleted-doses'), markers)


def intake_marker(c, member, regimen_id, planned_at, clear=False):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='config'").fetchone():
        return
    name = key(member, 'untaken-doses')
    markers = load(c, name, [])
    dose = [regimen_id, planned_at]
    if clear:
        if dose in markers:
            markers.remove(dose)
    elif dose not in markers:
        markers.append(dose)
    put(c, name, markers)


def snapshot(c, member):
    import health_regimen as H
    queries = {
        'health_regimen': 'SELECT * FROM health_regimen WHERE member_id=?',
        'health_regimen_reminder': 'SELECT * FROM health_regimen_reminder WHERE member_id=?',
        'health_regimen_taken': 'SELECT t.* FROM health_regimen_taken t JOIN health_regimen r '
                                'ON r.id=t.regimen_id WHERE r.member_id=?',
    }
    result = {table: {json.dumps([r[k] for k in TABLES[table]]): r
                      for r in H.rows(c, sql, (member,))}
              for table, sql in queries.items()}
    names = [key(member, 'deleted-doses'), key(member, 'untaken-doses')]
    if member == 'owner':
        names.append(TRACKED)
    result['config'] = {name: load(c, name) for name in names}
    return result


def save(c, member, verb, before):
    after = snapshot(c, member)
    delta = {table: {k: [before[table].get(k), after[table].get(k)]
                     for k in before[table].keys() | after[table].keys()
                     if before[table].get(k) != after[table].get(k)}
             for table in before}
    if not any(delta.values()):
        return
    history = load(c, key(member, 'history'), [])
    history.append(dict(verb=verb, owner_corrected=True, changes=delta))
    put(c, key(member, 'history'), history)
    put(c, key(member, 'redo'), [])


def replay(c, member, redo=False):
    source, dest = ('redo', 'history') if redo else ('history', 'redo')
    history = load(c, key(member, source), [])
    if not history:
        raise ValueError('no regimen correction to ' + ('redo' if redo else 'undo'))
    entry = history[-1]
    changes = entry['changes']
    current = snapshot(c, member)
    expected, target = (0, 1) if redo else (1, 0)
    transport = ('revision', 'sent_revision', 'sending_agent', 'message_id', 'chat_id')
    for table, entries in changes.items():
        for identity, pair in entries.items():
            actual, wanted = current[table].get(identity), pair[expected]
            if table == 'health_regimen_reminder' and actual and wanted is None:
                # Polling may have attached an unrelated dose to a carrier
                # first created by this intake. Reuse that same carrier.
                wanted = pair[target]
            if table == 'health_regimen_reminder' and actual and wanted:
                actual = {k: v for k, v in actual.items() if k not in transport}
                wanted = {k: v for k, v in wanted.items() if k not in transport}
            if actual != wanted:
                raise ValueError('regimen correction changed; undo later edits first')
    # Polling can materialize more pending rows after the correction. Keep
    # those derived snapshots consistent with the restored definition too.
    for pair in changes['health_regimen'].values():
        old, new = pair[expected], pair[target]
        if old is None:
            continue
        extra = [r for identity, r in current['health_regimen_taken'].items()
                 if r['regimen_id'] == old['id'] and identity not in changes['health_regimen_taken']]
        schedule = ('schedule_kind', 'clock_time', 'meal_slot', 'weekdays', 'timezone')
        for row in extra:
            if row['taken_at']:
                if new is None:
                    raise ValueError('regimen has a later intake; undo that intake first')
                continue
            identity = (row['regimen_id'], row['planned_at'])
            if new is None or not new['active'] or any(old[k] != new[k] for k in schedule):
                c.execute('DELETE FROM health_regimen_taken WHERE regimen_id=? AND planned_at=?', identity)
            elif any(old[k] != new[k] for k in ('name', 'amount', 'unit')):
                fields = [k for k in ('name', 'amount', 'unit') if old[k] != new[k]]
                c.execute('UPDATE health_regimen_taken SET ' + ','.join(k+'=?' for k in fields)
                          + ' WHERE regimen_id=? AND planned_at=?', [new[k] for k in fields] + list(identity))
    # Children first when deleting; parents first when inserting. Updates
    # retain ids and preserve unrelated group entries and stable positions.
    for table in reversed(TABLES):
        for identity, pair in changes[table].items():
            if pair[target] is None:
                if table == 'health_regimen_reminder' and c.execute(
                        'SELECT 1 FROM health_regimen_taken WHERE reminder_id=?', json.loads(identity)).fetchone():
                    continue
                where = ' AND '.join(k + '=?' for k in TABLES[table])
                c.execute('DELETE FROM ' + table + ' WHERE ' + where, json.loads(identity))
    for table in TABLES:
        for pair in changes[table].values():
            row = pair[target]
            if row is None:
                continue
            row = dict(row)
            if table == 'health_regimen_reminder':
                actual = current[table].get(json.dumps([row['id']]))
                expected_row = pair[expected] or pair[target]
                if actual and any(actual[k] != expected_row[k] for k in transport):
                    row.update({k: actual[k] for k in transport})
                    row['revision'] = max(actual['revision'], pair[target]['revision']) + 1
            columns = list(row)
            updates = [k for k in columns if k not in TABLES[table]]
            c.execute('INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES('
                      + ','.join('?' for _ in columns) + ') ON CONFLICT('
                      + ','.join(TABLES[table]) + ') DO UPDATE SET '
                      + ','.join(k + '=excluded.' + k for k in updates), list(row.values()))
    for name, pair in changes['config'].items():
        if pair[target] is None:
            c.execute('DELETE FROM config WHERE key=?', (name,))
        else:
            put(c, name, pair[target])
    history.pop()
    put(c, key(member, source), history)
    stack = load(c, key(member, dest), [])
    stack.append(entry)
    put(c, key(member, dest), stack)
    return dict(status='RESTORED', verb=entry['verb'], owner_corrected=True)


def sync_tracking(K, c, old=None, new=None):
    """Keep an explicitly tracked supplement's identity through a correction."""
    if (old or new)['member_id'] != 'owner':
        return
    tracked = load(c, TRACKED, [])
    if old and old['kind'] == 'supplement' and old['name'] in tracked:
        others = c.execute("SELECT 1 FROM health_regimen WHERE member_id='owner' "
                           "AND name=? AND kind='supplement' AND active=1 AND id<>?",
                           (old['name'], old['id'])).fetchone()
        if not others and (not new or new['kind'] != 'supplement'
                           or not new['active'] or new['name'] != old['name']):
            index = tracked.index(old['name'])
            tracked.remove(old['name'])
            if new and new['kind'] == 'supplement' and new['active'] and new['name'] not in tracked:
                tracked.insert(index, new['name'])
    if new and new['kind'] == 'supplement' and new['active'] and new['name'] not in tracked:
        tracked.append(new['name'])
    if tracked != load(c, TRACKED, []):
        put(c, TRACKED, tracked)


def definition(c, member, regimen_id):
    import health_regimen as H
    found = H.rows(c, 'SELECT * FROM health_regimen WHERE member_id=? AND id=?', (member, regimen_id))
    if not found:
        raise ValueError('regimen not found')
    return found[0]


def dose(c, member, regimen_id, planned_at):
    import health_regimen as H
    definition(c, member, regimen_id)
    found = H.rows(c, 'SELECT * FROM health_regimen_taken WHERE regimen_id=? AND planned_at=?',
                   (regimen_id, H.iso(H.instant(planned_at))))
    if not found:
        raise ValueError('regimen dose not found; use doses')
    return found[0]


def correct_dose(c, member, args, now):
    import health_regimen as H
    row = dose(c, member, args.id, args.planned_at)
    original = dict(row)
    identity = (row['regimen_id'], row['planned_at'])
    if args.cmd == 'dose-delete':
        suppress(c, member, *identity)
        c.execute('DELETE FROM health_regimen_taken WHERE regimen_id=? AND planned_at=?', identity)
    elif args.cmd == 'untake':
        if row['taken_at'] is None and list(identity) in load(c, key(member, 'untaken-doses'), []):
            return dict(status='UPDATED', regimen_id=args.id, planned_at=row['planned_at'], owner_corrected=True)
        intake_marker(c, member, *identity)
        c.execute('UPDATE health_regimen_taken SET taken_at=NULL,source=NULL '
                  'WHERE regimen_id=? AND planned_at=?', identity)
    else:
        for field in ('name', 'amount', 'unit', 'taken_at', 'source'):
            value = getattr(args, field)
            if value is not None:
                row[field] = value
        if not row['name'].strip() or not row['unit'].strip():
            raise ValueError('Name, owner-stated amount and unit are required')
        H.validate_amount(row['amount'])
        if row['taken_at']:
            row['taken_at'] = H.iso(H.instant(row['taken_at']))
            if H.instant(row['taken_at']) > now:
                raise ValueError('dose cannot be taken in the future')
            row['source'] = row['source'] or 'words'
        elif row['source']:
            raise ValueError('source requires taken_at')
        if row == original and (args.move_to is None or H.iso(H.instant(args.move_to)) == row['planned_at']):
            return dict(status='UPDATED', regimen_id=args.id, planned_at=row['planned_at'], owner_corrected=True)
        if args.move_to is not None:
            planned = H.iso(H.instant(args.move_to))
            if planned != row['planned_at']:
                if H.instant(planned) > now and row['taken_at']:
                    raise ValueError('dose cannot be taken before planned_at')
                if c.execute('SELECT 1 FROM health_regimen_taken WHERE regimen_id=? AND planned_at=?',
                             (args.id, planned)).fetchone() or suppressed(c, member, args.id, planned):
                    raise ValueError('regimen dose already exists or was deleted at move-to')
                regimen = definition(c, member, args.id)
                gid = H.occurrence(c, regimen, H.instant(planned), silent=True)
                new = dose(c, member, args.id, planned)
                suppress(c, member, *identity)
                c.execute('DELETE FROM health_regimen_taken WHERE regimen_id=? AND planned_at=?', identity)
                c.execute('UPDATE health_regimen_reminder SET revision=revision+1 WHERE id=?', (row['reminder_id'],))
                row.update(planned_at=planned, reminder_id=gid, position=new['position'])
                identity = (args.id, planned)
        c.execute('UPDATE health_regimen_taken SET name=?,amount=?,unit=?,taken_at=?,source=? '
                  'WHERE regimen_id=? AND planned_at=?',
                  [row[k] for k in ('name', 'amount', 'unit', 'taken_at', 'source')] + list(identity))
        if row['taken_at']:
            intake_marker(c, member, *identity, clear=True)
    # Reopening/correcting an already sent dose uses the ordinary carrier.
    c.execute('UPDATE health_regimen_reminder SET revision=revision+1 WHERE id=?', (row['reminder_id'],))
    return dict(status='UPDATED', regimen_id=args.id, planned_at=row['planned_at'], owner_corrected=True)
