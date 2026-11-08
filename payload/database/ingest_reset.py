"""Archive and reverse proven ingest effects. See docs/ingest-reset.md.

Never infer ownership from timestamps, a measured kcal flag, or missing sets.
Those are all possible on owner-created records. Unknown provenance blocks the
whole apply, preserving the refs needed to investigate and retry safely.
"""
import base64
import json
import sqlite3
import uuid

from ingest_ledger import _open, _parser, _refuse, _tok, now_utc

TARGETS = {'workouts', 'metric_log'}
FIELDS = {
    'workouts': {'minutes', 'kcal', 'kcal_origin', 'avg_hr', 'note', 'date'},
    'metric_log': {'value', 'note', 'created_at'},
}
LINK_KEYS = ('health_link', 'health_link_on')
GATE_TRIGGERS = ('trg_ingest_consent_no_backdate', 'trg_ingest_ref_gate')


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def encode(obj):
    def blob(value):
        if isinstance(value, bytes):
            return {'$blob': base64.b64encode(value).decode('ascii')}
        raise TypeError(type(value).__name__)
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=blob,
                      allow_nan=False)


def decode(raw):
    return json.loads(raw, object_hook=lambda v: base64.b64decode(v['$blob'])
                      if set(v) == {'$blob'} else v)


def rows(conn, table, where='1', args=()):
    cur = conn.execute('SELECT * FROM %s WHERE %s' % (quote(table), where), args)
    cols = [x[0] for x in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def key_for(conn, table, row):
    cols = sorted((r for r in conn.execute('PRAGMA table_info(%s)' % quote(table))
                   if r[5]), key=lambda r: r[5])
    if not cols:
        raise ValueError('no primary key: %s' % table)
    return {r[1]: row[r[1]] for r in cols}


def predicate(key):
    return ' AND '.join(quote(k) + ' IS ?' for k in key), tuple(key.values())


def get(conn, table, key):
    found = rows(conn, table, *predicate(key))
    return found[0] if found else None


def children(conn, wid):
    return sorted(rows(conn, 'workout_classifications', 'workout_id=?', (wid,)),
                  key=encode)


def record_effect(conn, source, kind, ext_id, table, target, raw, old_ref):
    """Adapter supplies evidence immediately after its domain write.

    created:true asserts creation; otherwise before/after maps explicitly name
    fields written. Store the original baseline across subsequent revisions.
    Never manufacture evidence retrospectively for an existing untracked ref.
    """
    if raw is None:
        return
    if table not in TARGETS or not target:
        raise ValueError('effect requires target-table workouts or metric_log and target-id')
    spec = json.loads(raw)
    if not isinstance(spec, dict) or not isinstance(spec.get('created'), bool):
        raise ValueError('effect-json requires boolean created')
    row = get(conn, table, {'id': target})
    if row is None:
        raise ValueError('effect target does not exist')
    previous = get(conn, 'ingest_effect', {'source': source, 'kind': kind, 'ext_id': ext_id})
    if old_ref is not None and previous is None:
        raise ValueError('cannot retrofit evidence onto an existing untracked ref')
    old = decode(previous['effect_json']) if previous else None
    if previous and (previous['target_table'], previous['target_id']) != (table, str(target)):
        raise ValueError('cannot move a target with recorded provenance')
    if old and old['created'] != spec['created']:
        raise ValueError('cannot change creation provenance')
    if spec['created']:
        if old and set(spec) == {'created', 'before', 'after'}:
            before, after = spec['before'], spec['after']
            if (not isinstance(before, dict) or not isinstance(after, dict) or
                    not before or set(before) != set(after) or
                    not set(before) <= FIELDS[table] or
                    any(old['row'][k] != v for k, v in before.items()) or
                    dict(old['row'], **after) != row):
                raise ValueError('created revision must match the previous and current row images')
        elif set(spec) != {'created'}:
            raise ValueError('first created effect accepts only created:true')
        elif old and old['row'] != row:
            raise ValueError('created target changed; revision requires explicit before/after fields')
        effect = {'created': True, 'row': row}
        if table == 'workouts':
            effect['classifications'] = children(conn, row['id'])
            if old and old.get('classifications') != effect['classifications']:
                raise ValueError('created target classifications changed')
    else:
        before, after = spec.get('before'), spec.get('after')
        if (set(spec) != {'created', 'before', 'after'} or
                not isinstance(before, dict) or not isinstance(after, dict) or
                not before or set(before) != set(after) or
                not set(before) <= FIELDS[table]):
            raise ValueError('effect before/after must name the same supported fields')
        if any(v is not None and not isinstance(v, (str, int, float, bytes))
               for v in list(before.values()) + list(after.values())):
            raise ValueError('effect field values must be SQLite scalars')
        if any(row[k] != v for k, v in after.items()):
            raise ValueError('effect after values do not match target')
        if table == 'workouts' and bool('kcal' in before) != bool('kcal_origin' in before):
            raise ValueError('kcal and kcal_origin must be recorded together')
        if old:
            for k in set(before) & set(old['after']):
                if before[k] != old['after'][k]:
                    raise ValueError('owner changed previously tracked field: %s' % k)
            for k in set(old['after']) - set(after):
                if row[k] != old['after'][k]:
                    raise ValueError('owner changed previously tracked field: %s' % k)
            before = dict(before, **old['before'])
            after = dict(old['after'], **after)
        effect = {'created': False, 'before': before, 'after': after}
    conn.execute('INSERT INTO ingest_effect VALUES(?,?,?,?,?,?) '
                 'ON CONFLICT(source,kind,ext_id) DO UPDATE SET effect_json=excluded.effect_json',
                 (source, kind, ext_id, table, str(target), encode(effect)))


def dependent_rows(conn, table, row):
    """Refuse deletion if anything beyond archived classifications refers to it.

    Includes RESTRICT, SET NULL and CASCADE edges: owner children must never be
    silently changed by a reset. Polymorphic spend links are checked separately.
    """
    found = []
    for (child,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for fk in conn.execute('PRAGMA foreign_key_list(%s)' % quote(child)):
            if fk[2] != table or (table == 'workouts' and child == 'workout_classifications'):
                continue
            col = fk[4] or 'id'
            if col in row and conn.execute('SELECT 1 FROM %s WHERE %s=? LIMIT 1' %
                                          (quote(child), quote(fk[3])), (row[col],)).fetchone():
                found.append(child)
    if table == 'workouts' and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='spend_link'").fetchone():
        # spend_link stores links polymorphically, outside SQLite FK enforcement.
        if conn.execute("SELECT 1 FROM spend_link WHERE entity_kind='workout' AND entity_ref=?",
                        (str(row['id']),)).fetchone():
            found.append('spend_link')
    return sorted(set(found))


def plan(conn, source, kinds):
    where, args = 'source=?', [source]
    if kinds:
        where += ' AND kind IN (%s)' % ','.join('?' for _ in kinds)
        args += kinds
    refs = rows(conn, 'ingest_ref', where, args)
    consents = rows(conn, 'ingest_consent', where, args)
    effects = rows(conn, 'ingest_effect', where, args)
    changes, unresolved = [], []

    def add(table, before, after=None):
        key = key_for(conn, table, before)
        where, args = predicate(key)
        rowid = conn.execute('SELECT rowid FROM %s WHERE %s' %
                             (quote(table), where), args).fetchone()[0]
        changes.append({'table': table, 'key': key, 'archive_rowid': rowid,
                        'before': before, 'after': after})

    targets = sorted({(r['target_table'], r['target_id']) for r in refs}, key=str)
    for table, target in targets:
        if table is None and target is None:
            continue  # ledger-only receipt: no domain mutation recorded
        if table not in TARGETS or target is None:
            unresolved.append({'table': table, 'id': target, 'reason': 'unsupported target'})
            continue
        row = get(conn, table, {'id': target})
        if row is None:
            continue  # already removed; still clear its stale receipt
        if (table == 'workouts' and
                conn.execute('PRAGMA user_version').fetchone()[0] >= 78 and
                row['session_version'] == 2):
            unresolved.append({'table': table, 'id': target,
                               'reason': 'v2 session writes require service/kit_life/session-command'})
            continue
        selected = [r for r in refs if (r['target_table'], r['target_id']) == (table, target)]
        all_refs = rows(conn, 'ingest_ref', 'target_table=? AND target_id=?', (table, target))
        if len(selected) != len(all_refs):
            unresolved.append({'table': table, 'id': target, 'reason': 'target shared with unselected refs'})
            continue
        ev = [decode(e['effect_json']) for e in effects
              if (e['target_table'], e['target_id']) == (table, target)]
        ev = list({encode(e): e for e in ev}.values())
        # Even an hk:<id> note is insufficient: the legacy adapter's
        # fill_fields replaces an owner cardio-estimate note with that marker.
        if len(ev) != 1:
            unresolved.append({'table': table, 'id': target,
                               'reason': 'missing or conflicting creation/field provenance'})
            continue
        effect = ev[0]
        if not effect['created'] and any(
                not any((e['source'], e['kind'], e['ext_id']) ==
                        (r['source'], r['kind'], r['ext_id']) for e in effects)
                for r in selected):
            unresolved.append({'table': table, 'id': target,
                               'reason': 'attached target has untracked contributing refs'})
            continue
        if effect['created']:
            if row != effect['row'] or (table == 'workouts' and
                    children(conn, row['id']) != effect.get('classifications', [])):
                unresolved.append({'table': table, 'id': target, 'reason': 'created row changed since ingest'})
                continue
            deps = dependent_rows(conn, table, row)
            if deps:
                unresolved.append({'table': table, 'id': target, 'reason': 'dependent owner rows', 'tables': deps})
                continue
            if table == 'workouts':
                for child in children(conn, row['id']):
                    add('workout_classifications', child)
            add(table, row)
        else:
            if any(row[k] != v for k, v in effect['after'].items()):
                unresolved.append({'table': table, 'id': target, 'reason': 'ingest-written field changed since ingest'})
                continue
            add(table, row, dict(row, **effect['before']))
    for e in effects:
        add('ingest_effect', e)
    for r in refs:
        add('ingest_ref', r)
    for c in consents:
        add('ingest_consent', c)
    # A kind-filtered reset keeps the source-wide answer if any other kind is
    # still linked. Once the last kind is reset it is a first-time answer again.
    remaining = rows(conn, 'ingest_consent', 'source=?', (source,))
    if source == 'healthkit' and len(remaining) == len(consents):
        for key in LINK_KEYS:
            value = get(conn, 'config', {'key': key})
            if value:
                add('config', value)
    return changes, unresolved


def report(changes, unresolved, source, apply=False):
    print('INGEST-RESET %s source=%s' % ('apply' if apply else 'dry-run', source))
    for table in sorted({c['table'] for c in changes}):
        group = [c for c in changes if c['table'] == table]
        print('TABLE %s rows=%d' % (table, len(group)))
        for c in group:
            if c['after'] is None:
                print('DELETE ' + encode(c['before']))
            else:
                fields = {k: {'from': c['before'][k], 'to': v}
                          for k, v in c['after'].items() if v != c['before'][k]}
                print('CLEAR ' + encode({'key': c['key'], 'fields': fields}))
    for item in unresolved:
        print('UNRESOLVED ' + encode(item))
    if source == 'healthkit':
        print('PACK-OWNED unchanged: <health state dir>/.health-link-offer.json key=after_update; '
              'pack must archive/reset this marker to offer onboarding again')


def write_change(conn, change, reverse=False):
    table, key = change['table'], change['key']
    before, after = change['before'], change['after']
    if table == 'ledger_constraint':
        raise ValueError('ingest reset cannot mutate standing constraints')
    if table in ('workouts', 'workout_classifications'):
        import workout_history
        wid = key.get('id') if table == 'workouts' else key.get('workout_id')
        workout_history.reject_legacy_write(conn, wid)
        if table == 'workouts' and any(image and image.get('session_version') == 2 for image in (before, after)):
            raise ValueError('v2 session reconciliation requires the session command writer')
    if reverse:
        if after is None:
            # Composite/text primary keys still have an observable rowid.
            # Preserve it too (the health-reset archive uses archive_rowid).
            restored = dict(before, rowid=change['archive_rowid'])
            cols = list(restored)
            conn.execute('INSERT INTO %s (%s) VALUES(%s)' %
                         (quote(table), ','.join(map(quote, cols)), ','.join('?' for _ in cols)),
                         [restored[k] for k in cols])
            return
        before, after = after, before
    where, args = predicate(key)
    if after is None:
        conn.execute('DELETE FROM %s WHERE %s' % (quote(table), where), args)
    else:
        fields = {k: v for k, v in after.items() if before[k] != v}
        if fields:
            conn.execute('UPDATE %s SET %s WHERE %s' %
                         (quote(table), ','.join(quote(k) + '=?' for k in fields), where),
                         tuple(fields.values()) + args)


def cmd_reset(ctx, argv):
    p = _parser('ingest-reset')
    p.add_argument('--source', required=True)
    p.add_argument('--kind', action='append', default=[])
    p.add_argument('--apply', action='store_true')
    p.add_argument('--force', action='store_true')
    a = p.parse_args(argv)
    source = _tok(ctx, 'source', a.source)
    kinds = sorted({_tok(ctx, 'kind', k) for k in a.kind})
    conn = _open(ctx)
    try:
        conn.execute('BEGIN IMMEDIATE' if a.apply else 'BEGIN')
        changes, unresolved = plan(conn, source, kinds)
        report(changes, unresolved, source, a.apply)
        # Check the whole source, including kinds omitted by --kind.
        active = rows(conn, 'ingest_consent', 'source=? AND revoked_at IS NULL', (source,))
        if active and not a.force:
            return _refuse('active consent for source; revoke before reset or use --force '
                           '(an ingest writer may still be running)')
        if unresolved:
            return _refuse('ambiguous provenance; no changes applied; preserve refs for investigation')
        if not a.apply:
            return 0
        if not changes:
            print('INGEST-RESET empty; no reset_id allocated')
            return 0
        reset_id = uuid.uuid4().hex
        conn.execute('INSERT INTO archive_ingest_reset_runs VALUES(?,?,?,?,NULL)',
                     (reset_id, source, encode(kinds), now_utc()))
        for i, c in enumerate(changes):
            conn.execute('INSERT INTO archive_ingest_reset_rows VALUES(?,?,?,?,?,?,?)',
                         (reset_id, i, c['table'], encode(c['key']), encode(c['before']),
                          None if c['after'] is None else encode(c['after']), c['archive_rowid']))
        # Archive first. Children precede parents in plan order.
        for c in changes:
            write_change(conn, c)
        for c in changes:
            if get(conn, c['table'], c['key']) != c['after']:
                raise ValueError('trigger changed planned result: %s' % c['table'])
        conn.commit()
        print('RESET reset_id=%s rows=%d' % (reset_id, len(changes)))
        return 0
    except (sqlite3.Error, ValueError) as exc:
        return _refuse(str(exc))
    finally:
        conn.rollback()
        conn.close()


def cmd_undo(ctx, argv):
    p = _parser('ingest-reset-undo')
    p.add_argument('--reset-id', required=True)
    a = p.parse_args(argv)
    conn = _open(ctx)
    try:
        conn.execute('BEGIN IMMEDIATE')
        run = get(conn, 'archive_ingest_reset_runs', {'reset_id': a.reset_id})
        if run is None:
            return _refuse('unknown reset-id')
        if not isinstance(decode(run['kinds_json']), list):
            return _refuse('this is a provenance import; use ingest-provenance-import-undo')
        if run['undone_at']:
            print('UNDO SAME reset_id=%s' % a.reset_id)
            return 0
        archived = rows(conn, 'archive_ingest_reset_rows', 'reset_id=?', (a.reset_id,))
        changes = [{'table': r['table_name'], 'key': decode(r['key_json']),
                    'archive_rowid': r['archive_rowid'],
                    'before': decode(r['before_json']),
                    'after': None if r['after_json'] is None else decode(r['after_json'])}
                   for r in sorted(archived, key=lambda r: r['ordinal'], reverse=True)]
        for c in changes:
            where, args = predicate(c['key'])
            rowid = conn.execute('SELECT rowid FROM %s WHERE %s' %
                                 (quote(c['table']), where), args).fetchone()
            occupied = conn.execute('SELECT 1 FROM %s WHERE rowid=?' % quote(c['table']),
                                    (c['archive_rowid'],)).fetchone()
            if (get(conn, c['table'], c['key']) != c['after'] or
                    (rowid is not None and rowid[0] != c['archive_rowid']) or
                    (c['after'] is None and occupied)):
                return _refuse('undo conflict table=%s key=%s; no changes applied' %
                               (c['table'], encode(c['key'])))
        # Historical consent/ref rows intentionally predate now and can be
        # revoked. Suspend ONLY the two insert gates inside this write-locked
        # transaction, restoring their exact DDL before commit (rollback on any
        # failure also restores DDL). No other writer can see them disabled.
        triggers = list(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                                     "AND name IN (?,?)", GATE_TRIGGERS))
        for name, _ in triggers:
            conn.execute('DROP TRIGGER ' + quote(name))
        for c in changes:
            write_change(conn, c, reverse=True)
        for _, sql in triggers:
            conn.execute(sql)
        for c in changes:
            if get(conn, c['table'], c['key']) != c['before']:
                raise ValueError('restored row differs from archive: %s' % c['table'])
        conn.execute('UPDATE archive_ingest_reset_runs SET undone_at=? WHERE reset_id=?',
                     (now_utc(), a.reset_id))
        conn.commit()
        print('UNDO restored reset_id=%s rows=%d' % (a.reset_id, len(changes)))
        return 0
    except (sqlite3.Error, ValueError) as exc:
        return _refuse(str(exc))
    finally:
        conn.rollback()
        conn.close()
