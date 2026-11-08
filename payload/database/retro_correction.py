"""Typed retro corrections, owner provenance and guarded receipts in config.

Receipts store only changed fields, plus full inserted/deleted rows. All writes
and receipts share the caller's writer transaction; no new schema or UI copy.
"""
import json

LINE_TABLES = ('block', 'block_attr', 'artifact', 'record', 'event',
               'record_event', 'event_timeblock', 'experience_person',
               'experience_capture_key', 'event_alias', 'event_record_exclusion',
               'event_child_exclusion', 'shop_capture', 'config')
VERBS = ('event-retro-undo', 'event-retro-clear', 'event-retro-clear-undo', 'retro-item-clear', 'retro-item-undo',
         'weekly-retro-reopen', 'weekly-retro-clear', 'weekly-retro-undo',
         'retro-schedule-show', 'retro-schedule-clear', 'retro-schedule-undo')


def stored(c, key):
    row = c.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(c, key, value):
    if value is None:
        c.execute('DELETE FROM config WHERE key=?', (key,))
    else:
        c.execute('INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (key, json.dumps(value, ensure_ascii=False)))


def remember(c, key, before, after):
    if before == after:
        return
    history = stored(c, 'retro-correction:' + key) or []
    history.append(dict(before=before, after=after, source='owner'))
    put(c, 'retro-correction:' + key, history)


def pop(c, key, current):
    history = stored(c, 'retro-correction:' + key) or []
    if not history:
        raise ValueError('no correction to undo')
    if current != history[-1]['after']:
        raise ValueError('correction changed; undo later edits first')
    before = history.pop()['before']
    put(c, 'retro-correction:' + key, history)
    return before


class ReceiptScope:
    """Rows in the correction-owned block/event graph (not the whole DB)."""
    def __init__(self, blocks=(), events=(), config=(), recurrences=(), artifacts=(), insights=()):
        self.blocks = {int(x) for x in blocks}
        self.events = {int(x) for x in events}
        self.config = set(config)
        self.recurrences = {x for x in recurrences if x is not None}
        self.artifacts = {int(x) for x in artifacts}
        self.insights = {int(x) for x in insights}

    def _expand(self, c):
        changed = True
        while changed:
            old = (len(self.blocks), len(self.events))
            if self.recurrences:
                q = ','.join('?' * len(self.recurrences))
                self.blocks.update(r[0] for r in c.execute(
                    "SELECT block_id FROM block_attr WHERE attr_token='recurrence_id' AND value IN ("+q+')',
                    tuple(self.recurrences)))
            if self.blocks:
                q = ','.join('?' * len(self.blocks)); args = tuple(self.blocks)
                self.blocks.update(r[0] for r in c.execute('SELECT id FROM block WHERE contained_by IN ('+q+')', args))
                self.blocks.update(r[0] for r in c.execute('SELECT contained_by FROM block WHERE id IN ('+q+') AND contained_by IS NOT NULL', args))
                self.events.update(r[0] for r in c.execute('SELECT event_id FROM event_timeblock WHERE timeblock_id IN ('+q+')', args))
            if self.events:
                q = ','.join('?' * len(self.events)); args = tuple(self.events)
                self.events.update(r[0] for r in c.execute('SELECT id FROM event WHERE parent_id IN ('+q+')', args))
                self.events.update(r[0] for r in c.execute('SELECT parent_id FROM event WHERE id IN ('+q+') AND parent_id IS NOT NULL', args))
                self.blocks.update(r[0] for r in c.execute('SELECT timeblock_id FROM event_timeblock WHERE event_id IN ('+q+')', args))
                self.events.update(r[0] for r in c.execute('SELECT DISTINCT re2.event_id FROM record_event re1 JOIN record_event re2 USING(record_id) WHERE re1.event_id IN ('+q+')', args))
            changed = old != (len(self.blocks), len(self.events))

    def where(self, c, table):
        self._expand(c)
        def inside(column, values):
            return (column+' IN ('+','.join('?' * len(values))+')', tuple(values)) if values else ('0', ())
        if table in ('block', 'block_attr', 'project_block'):
            return inside('id' if table == 'block' else 'block_id', self.blocks)
        if table == 'event_timeblock':
            vals = self.events
            if not vals: return inside('timeblock_id', self.blocks)
            e, ea = inside('event_id', vals); b, ba = inside('timeblock_id', self.blocks)
            return '('+e+' OR '+b+')', ea+ba
        if table in ('event', 'event_persons', 'experience_person', 'experience_capture_key',
                     'event_record_exclusion', 'shop_capture'):
            col = 'id' if table == 'event' else 'event_id'
            return inside(col, self.events)
        if table == 'event_alias':
            return inside('canonical_id', self.events)
        if table == 'event_child_exclusion':
            p, pa = inside('parent_id', self.events); ch, cha = inside('child_id', self.events)
            return '('+p+' OR '+ch+')', pa+cha
        if table == 'record_event':
            return inside('event_id', self.events)
        if table == 'record':
            if not self.events: return '0', ()
            q = ','.join('?' * len(self.events))
            return 'id IN (SELECT record_id FROM record_event WHERE event_id IN ('+q+'))', tuple(self.events)
        if table == 'artifact':
            direct, direct_args = inside('id', self.artifacts)
            if not self.events: return direct, direct_args
            q = ','.join('?' * len(self.events))
            return '('+direct+" OR id IN (SELECT r.source_id FROM record r JOIN record_event re ON re.record_id=r.id WHERE r.source_table='artifact' AND re.event_id IN ("+q+')))', direct_args+tuple(self.events)
        if table == 'insight':
            return inside('id', self.insights)
        if table == 'routine_def':
            return inside('recurrence_id', self.recurrences)
        if table == 'routine_placement':
            if not self.recurrences: return '0', ()
            q = ','.join('?' * len(self.recurrences))
            return 'routine_id IN (SELECT id FROM routine_def WHERE recurrence_id IN ('+q+'))', tuple(self.recurrences)
        if table == 'spend_entry':
            if not self.events: return '0', ()
            q = ','.join('?' * len(self.events))
            return "id IN (SELECT r.source_id FROM record r JOIN record_event re ON re.record_id=r.id WHERE r.source_table='spend_entry' AND re.event_id IN ("+q+'))', tuple(self.events)
        if table == 'travel_rule':
            if not self.blocks: return '0', ()
            q = ','.join('?' * len(self.blocks))
            return 'anchor_ulid IN (SELECT ulid FROM block WHERE id IN ('+q+'))', tuple(self.blocks)
        if table == 'config':
            return inside('key', self.config)
        return None


def snapshot(c, tables, scope=None):
    out = {}
    for table in tables:
        info = c.execute('PRAGMA table_info(' + table + ')').fetchall()
        if not info:
            continue
        cols = [r[1] for r in info]
        pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
        if not pk:
            raise ValueError('receipt requires a primary key: ' + table)
        where = scope.where(c, table) if scope else None
        suffix, args = ((' WHERE '+where[0], where[1]) if where is not None else ('', ()))
        rows = [dict(zip(cols, r)) for r in c.execute('SELECT ' + ','.join(cols) + ' FROM ' + table + suffix, args)]
        out[table] = dict(pk=pk, rows={json.dumps([r[k] for k in pk]): r for r in rows})
    return out


def receipt(c, key, before, tables, scope=None):
    after = snapshot(c, tables, scope)
    changes = []
    for table, old in before.items():
        new = after[table]
        for identity in sorted(set(old['rows']) | set(new['rows'])):
            a, b = old['rows'].get(identity), new['rows'].get(identity)
            if a == b:
                continue
            if a is not None and b is not None:
                fields = [k for k in a if a[k] != b[k]]
                a, b = ({k: a[k] for k in fields}, {k: b[k] for k in fields})
            changes.append(dict(table=table, pk=dict(zip(old['pk'], json.loads(identity))), before=a, after=b))
    if changes:
        history = stored(c, 'retro-rows:' + key) or []
        history.append(dict(changes=changes, source='owner'))
        put(c, 'retro-rows:' + key, history)


def select(c, change):
    p = change['pk']
    cur = c.execute('SELECT * FROM ' + change['table'] + ' WHERE ' + ' AND '.join(k+' IS ?' for k in p), list(p.values()))
    row = cur.fetchone()
    return dict(zip((d[0] for d in cur.description), row)) if row else None


def undo_rows(c, key):
    history = stored(c, 'retro-rows:' + key) or []
    if not history:
        raise ValueError('no correction to undo')
    changes = history[-1]['changes']
    for ch in changes:
        current, expected = select(c, ch), ch['after']
        if expected is not None and current is not None:
            current = {k: current[k] for k in expected}
        if current != expected:
            raise ValueError('correction changed; undo later edits first')
    # Restoring/deleting a parent must not silently cascade a later link.
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for ch in changes:
        if ch['before'] is not None:
            continue
        parent = select(c, ch)
        for table in tables:
            for fk in c.execute('PRAGMA foreign_key_list(' + table + ')'):
                if fk[2] != ch['table'] or fk[4] not in parent:
                    continue
                cursor = c.execute('SELECT * FROM ' + table + ' WHERE ' + fk[3] + ' IS ?', (parent[fk[4]],))
                cols = [x[0] for x in cursor.description]
                for r in cursor:
                    data = dict(zip(cols, r))
                    tracked = next((x for x in changes if x['table'] == table and all(data[k] == v for k, v in x['pk'].items())), None)
                    if tracked is None or (tracked['before'] is not None and tracked['before'].get(fk[3], data[fk[3]]) == data[fk[3]]):
                        raise ValueError('correction has later dependent links')
    c.execute('PRAGMA defer_foreign_keys=ON')
    # Existing rows first (detach references), restore parents before children,
    # then delete inserted children before inserted parents.
    for ch in changes:
        if ch['before'] is not None and ch['after'] is not None:
            fields, p = ch['before'], ch['pk']
            c.execute('UPDATE '+ch['table']+' SET '+','.join(k+'=?' for k in fields)+' WHERE '+' AND '.join(k+' IS ?' for k in p), [*fields.values(), *p.values()])
    for ch in changes:
        if ch['before'] is not None and ch['after'] is None:
            fields = ch['before']
            c.execute('INSERT INTO '+ch['table']+'('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')', list(fields.values()))
    for ch in reversed(changes):
        if ch['before'] is None:
            p = ch['pk']
            c.execute('DELETE FROM '+ch['table']+' WHERE '+' AND '.join(k+' IS ?' for k in p), list(p.values()))
    if c.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('correction has changed references')
    history.pop()
    put(c, 'retro-rows:' + key, history)
    return [ch['pk']['id'] for ch in changes if ch['table'] == 'block']


def line(K, c, token, value, item=False, now=None):
    """Set/clear a line; propagate only explicitly inherited child answers."""
    if value is not None:
        value = str(value).strip()
        if not value:
            raise ValueError('line required')
    c.execute('BEGIN IMMEDIATE')
    try:
        r = K._retro_member(c, int(token)) if str(token).isdigit() else None
        if r is None:
            found = c.execute('SELECT id FROM timeblock WHERE ulid=?', (str(token),)).fetchone()
            r = K._retro_member(c, found[0]) if found else None
        if r is None and not item:
            r = K._travel_block_resolve(c, token)
            if r:
                r['tz'] = r.get('display_tz') or 'Asia/Seoul'
        if r is None:
            c.rollback()
            return None
        now = now or K.now_utc()
        day = str(K._local_date_of(now, r['tz'] or 'Asia/Seoul'))
        ends = K._retro_visit_ends(c)
        children, waiting = [], False
        if item:
            got = K._retro_group(c, r['id'], day, now, ends)
            if got:
                outer, children, _title, waiting = got
                if outer['id'] != r['id']:
                    children = []
        end = ends.get(r['id'])
        if value is not None and (waiting or (end and now < end and not K._retro_same_day_started(r['kind'], r['start_at'], r['schedule_kind'], end, day, now))):
            raise ValueError('RETRO_IN_PROGRESS: visit has not ended')
        key = 'item:' + r['ulid'] if item else 'event:' + r['ulid']
        markers_key = 'retro-inherited:' + r['ulid']
        markers = stored(c, markers_key) or {}
        copied, kept = [], []
        targets = [r]
        # Include prior inherited children even if the bundle changed since.
        candidates = {m['id']: m for m in children}
        for bid in markers:
            m = K._retro_member(c, int(bid))
            if m:
                candidates[m['id']] = m
        if item:
            for bid, m in candidates.items():
                owner = stored(c, 'retro-line-owner:' + m['ulid'])
                inherited = str(bid) in markers and (m['retro_line'] or None) == (markers[str(bid)] or None) and owner == {'parent': r['ulid']}
                if inherited or (value is not None and not m['retro_line'] and not owner):
                    targets.append(m)
                    copied.append(bid)
                else:
                    kept.append(bid)
        config_keys = {markers_key, 'retro-line-capture:' + r['ulid']}
        config_keys.update('retro-line-owner:' + m['ulid'] for m in targets)
        scope = ReceiptScope(blocks=(m['id'] for m in targets), config=config_keys)
        before = snapshot(c, LINE_TABLES, scope)
        for m in targets:
            c.execute('UPDATE block SET retro_line=? WHERE id=?', (value, m['id']))
            c.execute("DELETE FROM block_attr WHERE block_id=? AND attr_token='retro_line'", (m['id'],))
            owner_key = 'retro-line-owner:' + m['ulid']
            owner = {'parent': r['ulid']} if m['id'] != r['id'] else {'source': 'owner'}
            put(c, owner_key, owner)
            if m['id'] != r['id']:
                markers[str(m['id'])] = value
        # Capture mirrors are values too. Only a receipt-marked contribution
        # may be replaced/cleared; identical legacy words prove no ownership.
        capture_key = 'retro-line-capture:' + r['ulid']
        owned = stored(c, capture_key)
        linked = c.execute('SELECT event_id FROM event_timeblock WHERE timeblock_id=? ORDER BY event_id LIMIT 1', (r['id'],)).fetchone()
        same_capture = owned and linked and owned['event_id'] == linked[0]
        owned_artifacts = []
        if same_capture:
            current_note = c.execute('SELECT note FROM event WHERE id=?', (linked[0],)).fetchone()[0]
            if current_note == owned['note_after']:
                c.execute('UPDATE event SET note=? WHERE id=?', (owned['note_before'], linked[0]))
            for aid in owned['artifacts']:
                artifact = c.execute('SELECT kind,ref FROM artifact WHERE id=?', (aid,)).fetchone()
                other = c.execute("SELECT 1 FROM record r JOIN record_event re ON re.record_id=r.id WHERE r.source_table='artifact' AND r.source_id=? AND re.event_id<>?", (aid, linked[0])).fetchone()
                if not artifact or tuple(artifact) != ('note', owned['line']) or other:
                    continue
                if value is None:
                    c.execute("DELETE FROM record WHERE source_table='artifact' AND source_id=?", (aid,))
                    c.execute('DELETE FROM artifact WHERE id=?', (aid,))
                else:
                    c.execute('UPDATE artifact SET ref=? WHERE id=?', (value, aid))
                    owned_artifacts.append(aid)
        if value is not None:
            if item and not r['retro_line']:
                talk = K._retro_talk(c, r, children, now)
                if talk:
                    import visit_talk
                    visit_talk.mark_asked(c, r['id'], talk, now)
            note_before = c.execute('SELECT note FROM event WHERE id=?', (linked[0],)).fetchone()[0] if linked else None
            artifact_ids = {x[0] for x in c.execute('SELECT id FROM artifact')}
            eid = K._experience_block(c, r['id'], verbatim=value)
            c.execute('UPDATE event SET reaction=? WHERE id=?', (value, eid))
            for bid in copied:
                K._experience_block(c, bid, event_id=eid)
            owned_artifacts += [x[0] for x in c.execute('SELECT id FROM artifact') if x[0] not in artifact_ids]
            put(c, capture_key, dict(event_id=eid, line=value, note_before=note_before,
                                    note_after=c.execute('SELECT note FROM event WHERE id=?', (eid,)).fetchone()[0],
                                    artifacts=owned_artifacts, source='owner'))
        else:
            c.execute('UPDATE event SET reaction=NULL WHERE reaction IS ? AND id IN (SELECT event_id FROM event_timeblock WHERE timeblock_id=?)', (r['retro_line'], r['id']))
            put(c, capture_key, None)
        put(c, markers_key, markers)
        receipt(c, key, before, LINE_TABLES, scope)
        c.commit()
        for m in targets:
            K._outbox_enqueue_best_effort(m['ulid'])
        return dict(id=r['id'], ulid=r['ulid'], line=value, copied=copied, kept=kept)
    except BaseException:
        c.rollback()
        raise


def weekly(K, c, verb, day):
    key = K.weekly_retro_key(day)
    before = stored(c, key)
    if verb.endswith('undo'):
        after = pop(c, key, before)
    elif verb.endswith('clear'):
        after = None
    else:
        if not before or before.get('state') != 'done':
            raise ValueError('weekly retro is not done')
        after = dict(before, state='started')
        after.pop('done_utc', None)
    if not verb.endswith('undo'):
        remember(c, key, before, after)
    put(c, key, after)
    return after


def cli(K, verb, args):
    if verb.startswith('experience-'):
        return experience_cli(K, verb, args)
    if len(args) != 1:
        K._err('expected one id, date or retro lane')
    c = K.event_conn()
    invalidations = []
    try:
        if verb.startswith('retro-schedule-'):
            out = schedule(K, c, verb, args[0])
        else:
            c.execute('BEGIN IMMEDIATE') if verb not in ('retro-item-clear', 'event-retro-clear') else None
            if verb.startswith('weekly-'):
                out = weekly(K, c, verb, args[0])
            elif verb in ('retro-item-clear', 'event-retro-clear'):
                out = line(K, c, args[0], None, item=verb.startswith('retro-item'))
                if out is None:
                    raise ValueError('event not found')
                if verb == 'event-retro-clear':
                    out = dict(id=out['id'], ulid=out['ulid'], result='applied')
            else:
                r = c.execute('SELECT id,ulid FROM timeblock WHERE id=? OR ulid=?', (args[0], args[0])).fetchone()
                if not r:
                    raise ValueError('event not found')
                key = ('item:' if verb.startswith('retro-item') else 'event:') + r[1]
                if verb == 'event-retro-clear-undo' and c.execute('SELECT retro_line FROM block WHERE id=?', (r[0],)).fetchone()[0] is not None:
                    raise ValueError('correction changed; undo later edits first')
                invalidations = undo_rows(c, key)
                out = dict(id=r[0], ulid=r[1], result='applied')
            c.commit()
        for bid in invalidations:
            found = c.execute('SELECT ulid FROM block WHERE id=?', (bid,)).fetchone()
            if found:
                K._outbox_enqueue_best_effort(found[0])
        print(json.dumps(out, ensure_ascii=False))
    except (ValueError, K.sqlite3.IntegrityError) as exc:
        c.rollback()
        K._err(str(exc))
    finally:
        c.close()


class AtomicConnection:
    """Let existing convergent routine helpers share one outer transaction."""
    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    @property
    def row_factory(self):
        return self.conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.conn.row_factory = value

    def execute(self, sql, parameters=()):
        if sql.strip().upper().startswith('BEGIN') and self.conn.in_transaction:
            return self.conn.execute('SELECT 1')
        return self.conn.execute(sql, parameters)

    def commit(self):
        pass

    def rollback(self):
        raise ValueError('retro schedule transaction refused')


SCHEDULE_TABLES = ('routine_def', 'routine_placement', 'block', 'block_attr',
                   'travel_rule', 'project_block', 'config')


def schedule(K, c, verb, lane, clock=None):
    if lane not in ('daily', 'weekly'):
        raise ValueError('retro lane must be daily|weekly')
    key = 'schedule:' + K._pin_agent() + ':' + lane
    def rows():
        cur = c.execute("SELECT * FROM routine_def WHERE owning_agent=? AND purpose=? ORDER BY id", (K._pin_agent(), 'retro:' + lane))
        return [dict(zip((d[0] for d in cur.description), r)) for r in cur]
    if verb == 'retro-schedule-show':
        return rows()
    c.execute('BEGIN IMMEDIATE')
    affected_ulids = {x[0] for r in rows() for x in c.execute('SELECT ulid FROM timeblock WHERE recurrence_id=?', (r['recurrence_id'],))}
    state = K.sqlite3.connect(':memory:')
    state.row_factory = K.sqlite3.Row
    state.executescript(K._roller_mod()._STATE_MIN_DDL)
    try:
        recurrence_ids = [r['recurrence_id'] for r in rows()]
        scope = ReceiptScope(recurrences=recurrence_ids,
                             config=('retro-rows:' + key,))
        before = snapshot(c, SCHEDULE_TABLES, scope)
        if verb == 'retro-schedule-undo':
            undo_rows(c, key)
            result = dict(result='applied')
        elif verb == 'retro-schedule-clear':
            active = [r for r in rows() if r['status'] != 'retired']
            if not active:
                raise ValueError('retro schedule not found')
            result = [K.routine_retire(AtomicConnection(c), r['ulid'], state_conn=state) for r in active]
            scope.recurrences.update(r['recurrence_id'] for r in rows())
            receipt(c, key, before, SCHEDULE_TABLES, scope)
        else:
            result = K.routine_sync_retro(AtomicConnection(c), lane, clock, state_conn=state)
            scope.recurrences.update(r['recurrence_id'] for r in rows())
            receipt(c, key, before, SCHEDULE_TABLES, scope)
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        state.close()
    # The durable kit write is complete before best-effort mirror invalidation.
    affected_ulids.update(x[0] for r in rows() for x in c.execute('SELECT ulid FROM timeblock WHERE recurrence_id=?', (r['recurrence_id'],)))
    for ulid in affected_ulids:
        K._outbox_enqueue_best_effort(ulid)
    return result


EXPERIENCE_TABLES = ('artifact', 'record', 'event', 'record_event', 'event_timeblock',
                     'experience_person', 'experience_capture_key', 'event_alias',
                     'event_record_exclusion', 'event_child_exclusion', 'insight')
ENRICH_TABLES = ('block', 'block_attr', 'event_persons', 'spend_entry') + EXPERIENCE_TABLES + ('config',)
EXPERIENCE_VERBS = ('experience-enrich-undo', 'experience-merge-undo', 'experience-split-undo',
                    'experience-detach-person', 'experience-person-upd',
                    'experience-person-undo', 'experience-parent-undo',
                    'experience-insight-upd', 'experience-insight-undo',
                    'experience-artifact-list', 'experience-artifact-upd',
                    'experience-artifact-undo')
VERBS += EXPERIENCE_VERBS


def experience_cli(K, verb, args):
    c = K.get_conn()
    invalidations = []
    try:
        if verb == 'experience-artifact-list':
            if args:
                raise ValueError('artifact-list takes no arguments')
            cur = c.execute('SELECT id,ulid,kind,ref,ts,owning_agent FROM artifact ORDER BY id')
            print(json.dumps([dict(zip((d[0] for d in cur.description), r)) for r in cur], ensure_ascii=False))
            return
        if not args:
            raise ValueError('expected id')
        family = verb.removeprefix('experience-').split('-')[0]
        token = args[0]
        if family in ('person', 'detach', 'parent', 'enrich'):
            resolved = K.experience_resolve(c, token)
            if not resolved:
                raise ValueError('experience not found')
            token = str(resolved[0])
        if not str(token).isdigit():
            raise ValueError('expected numeric id')
        family = 'person' if family == 'detach' else family
        key = family + ':' + str(token)
        c.execute('BEGIN IMMEDIATE')
        scope = ReceiptScope(events=(int(token),) if family in ('person', 'parent', 'enrich') else (),
                             artifacts=(int(token),) if family == 'artifact' else (),
                             insights=(int(token),) if family == 'insight' else (),
                             config=('retro-rows:' + key,))
        before = snapshot(c, EXPERIENCE_TABLES, scope)
        if verb.endswith('-undo'):
            if len(args) != 1:
                raise ValueError('expected one id')
            invalidations = undo_rows(c, key)
        elif family == 'person':
            expected = 3 if verb.endswith('-upd') else 2
            if len(args) != expected or not all(str(a).isdigit() for a in args[1:]):
                raise ValueError('expected event id, old person id and optional new person id')
            if c.execute('DELETE FROM experience_person WHERE event_id=? AND person_id=?', (token, args[1])).rowcount != 1:
                raise ValueError('experience person link not found')
            if expected == 3:
                c.execute('INSERT INTO experience_person(event_id,person_id) VALUES(?,?)', (token, args[2]))
            receipt(c, key, before, EXPERIENCE_TABLES, scope)
        else:
            table = 'insight' if family == 'insight' else 'artifact'
            allowed = {'text'} if table == 'insight' else {'kind', 'ref', 'ts'}
            fields = {}
            for arg in args[1:]:
                if '=' not in arg:
                    raise ValueError('expected field=value')
                k, v = arg.split('=', 1)
                if k not in allowed or k in fields:
                    raise ValueError('invalid or duplicate field: ' + k)
                if k in ('text', 'kind') and not v.strip():
                    raise ValueError('nonempty field required')
                if k == 'ts' and v:
                    K._experience_time(occurred_at=v) if 'T' in v else K.datetime.date.fromisoformat(v)
                fields[k] = v or None
            if not fields:
                raise ValueError('expected field=value')
            if c.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', [*fields.values(), token]).rowcount != 1:
                raise ValueError(table + ' not found')
            if table == 'artifact' and 'ts' in fields:
                c.execute("UPDATE record SET ts=? WHERE source_table='artifact' AND source_id=?", (fields['ts'], token))
            receipt(c, key, before, EXPERIENCE_TABLES, scope)
        c.commit()
        for bid in invalidations:
            found = c.execute('SELECT ulid FROM block WHERE id=?', (bid,)).fetchone()
            if found:
                K._outbox_enqueue_best_effort(found[0])
        print(json.dumps(dict(id=int(token), result='applied')))
    except (ValueError, K.sqlite3.IntegrityError) as exc:
        c.rollback()
        K._err(str(exc))
    finally:
        c.close()
