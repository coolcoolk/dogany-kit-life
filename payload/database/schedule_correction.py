"""Typed schedule corrections. History and owner markers use existing config.

All focused writes and their history share a transaction. Undo compares the
fields it owns and refuses stale corrections rather than overwriting them.
No delivery copy lives here: CLI results are data and reuse existing cards.
"""
import json

PROJECT_LIVE = "NOT EXISTS (SELECT 1 FROM config pdel WHERE pdel.key='schedule-project-deleted:'||projects.id AND pdel.value='1')"

SETTLE = ('settled_at', 'settled_outcome', 'settled_by', 'settled_verdict')
VERBS = ('appt-revive', 'appt-uncancel', 'appt-revive-undo', 'task-revive',
         'task-revive-undo', 'task-upd', 'task-upd-undo', 'presence-upd-undo',
         'travel-skip-undo', 'travel-skip-redo', 'task-alias-list',
         'task-alias-rm', 'task-alias-undo', 'travel-rule-list',
         'travel-rule-show', 'event-notify-show', 'project-del', 'project-del-undo')


def load(K, c, key):
    return json.loads(K._config_stored(c, 'schedule-correction:' + key) or '[]')


def save(K, c, key, entries):
    K._config_put(c, 'schedule-correction:' + key, json.dumps(entries))


def remember(K, c, key, before, after):
    entries = load(K, c, key)
    entries.append(dict(before=before, after=after, source='owner'))
    save(K, c, key, entries)


def pop(K, c, key, current):
    entries = load(K, c, key)
    if not entries:
        raise ValueError('no correction to undo')
    if current != entries[-1]['after']:
        raise ValueError('correction changed; undo later edits first')
    before = entries.pop()['before']
    save(K, c, key, entries)
    return before


def row(K, c, token):
    result = K._travel_block_resolve(c, token)
    if result is None:
        raise ValueError('event not found')
    if K._is_v32(c):
        result['settled_verdict'] = c.execute('SELECT settled_verdict FROM block WHERE id=?', (result['id'],)).fetchone()[0]
        for attr in ('location_cleared', 'location_edited', 'sub_place_id'):
            found = c.execute('SELECT value FROM block_attr WHERE block_id=? AND attr_token=?', (result['id'], attr)).fetchone()
            result[attr] = found[0] if found else None
    result['_rule'] = rule_snapshot(K, c, result['ulid'])
    return result


def snapshot(r, fields):
    return {k: r[k] for k in fields}


def write(K, c, r, fields):
    fields = dict(fields)
    rule = fields.pop('_rule', None)
    if rule is not None:
        data = {k: v for k, v in rule.items() if k != 'id'}
        c.execute('UPDATE travel_rule SET '+','.join(k+'=?' for k in data)+',version=version+1,updated_at=? WHERE id=?',
                  [*data.values(), K.now_utc(), rule['id']])
    if K._tb_update(c, r['id'], dict(fields, updated_at=K.now_utc()),
                    bump_version=True, cas_version=r['version']) != 1:
        raise ValueError('conflict, please retry')
    if 'retro_line' in fields and K._is_v32(c):
        c.execute('UPDATE block SET retro_line=? WHERE id=?', (fields['retro_line'], r['id']))
        c.execute("DELETE FROM block_attr WHERE block_id=? AND attr_token='retro_line'", (r['id'],))
    if K._is_v32(c):
        for attr in ('location_cleared', 'location_edited'):
            if attr in fields:
                c.execute('DELETE FROM block_attr WHERE block_id=? AND attr_token=?', (r['id'], attr))
                if fields[attr] is not None:
                    c.execute("INSERT INTO block_attr VALUES(?,?,?,'user')", (r['id'], attr, fields[attr]))
    K._recompute_and_store(c, r['id'])


def event_change(K, c, verb, args):
    r = row(K, c, args[0])
    undo = verb.endswith('-undo') or verb == 'travel-skip-redo'
    base = verb.removesuffix('-undo')
    if verb == 'appt-uncancel':
        base = 'appt-revive'
    if verb.startswith('travel-skip-'):
        base = 'travel-skip-undo'
        undo = verb == 'travel-skip-redo'
    key = base + ':' + r['ulid']
    before_anchor = K.appt_snapshot(c, r['ulid'])
    if base == 'task-upd' and (r['kind'] != 'task' or r['block_class'] is not None):
        raise ValueError('wrong event kind')
    prepared = parse_patch(K, c, args[1:], {'note', 'place'}, r) if base == 'task-upd' and not undo else None
    c.execute('BEGIN IMMEDIATE')
    try:
        # Re-read under the write lock.
        r = row(K, c, r['id'])
        if undo:
            entries = load(K, c, key)
            if not entries:
                raise ValueError('no correction to undo')
            fields = pop(K, c, key, snapshot(r, entries[-1]['after']))
        elif base in ('appt-revive', 'task-revive', 'travel-skip-undo'):
            expected = 'appointment' if base == 'appt-revive' else 'task'
            if base == 'travel-skip-undo':
                if r['block_class'] != 'travel' or r['settled_by'] not in ('travel-skip', 'gcal-owner-delete'):
                    raise ValueError('not an owner-skipped travel block')
                anchor = row(K, c, r['derived_from'])
                if anchor['settled_at'] is not None:
                    raise ValueError('anchor is settled')
                if c.execute("SELECT 1 FROM timeblock WHERE derived_from=? AND derived_role=? "
                             "AND settled_at IS NULL AND id<>?", (r['derived_from'], r['derived_role'], r['id'])).fetchone():
                    raise ValueError('live travel block already exists')
            elif r['kind'] != expected or r['block_class'] is not None:
                raise ValueError('wrong event kind')
            if r['settled_outcome'] != 'abandoned':
                raise ValueError('event is not cancelled')
            fields = dict.fromkeys(SETTLE)
            restore = cancelled_rule(K, c, r)
            if restore is not None:
                fields['_rule'] = restore
        elif base == 'task-upd':
            if r['kind'] != 'task' or r['block_class'] is not None:
                raise ValueError('wrong event kind')
            fields = prepared
        else:
            raise ValueError('unknown correction')
        if not undo:
            before = snapshot(r, fields)
        write(K, c, r, fields)
        if not undo:
            remember(K, c, key, before, snapshot(row(K, c, r['id']), fields))
        c.commit()
    except BaseException:
        c.rollback()
        raise
    fresh = row(K, c, r['id'])
    if fresh['settled_outcome'] == 'abandoned' and base in ('appt-revive', 'task-revive', 'travel-skip-undo'):
        K._travel_on_anchor_settled(c, fresh['id'], fresh['settled_at'], outcome='abandoned')
    if base == 'task-upd' and 'place_id' in fields:
        sync_presence(K, c, r, before_anchor)
    if not (base == 'task-upd' and 'place_id' in fields):
        K.appt_rederive(c, r['ulid'], before=before_anchor, declared=True)
        if base == 'travel-skip-undo':
            K.travel_refresh(c, None, r['derived_from'], declared=True)
    K._outbox_enqueue_best_effort(r['ulid'])
    print(json.dumps(dict(id=r['id'], ulid=r['ulid'], result='applied')))
    fresh = row(K, c, r['id'])
    state = 'changed'
    if base in ('appt-revive', 'task-revive', 'travel-skip-undo'):
        state = ('skipped' if base == 'travel-skip-undo' else 'cancelled') if fresh['settled_at'] else 'open'
    K._print_cards(c, [r['id']], mutation_state=state)


def parse_patch(K, c, args, allowed, r):
    if not args or any('=' not in a for a in args):
        raise ValueError('expected field=value')
    patch = {}
    for a in args:
        k, v = a.split('=', 1)
        if k not in allowed or k in patch:
            raise ValueError('invalid or duplicate field: ' + k)
        if k == 'title' and not v.strip():
            raise ValueError('title required')
        patch[k] = v
    if 'place' in patch and 'location' in patch:
        raise ValueError('one place field required')
    fields = {k: v or None for k, v in patch.items() if k in ('note', 'title')}
    place = patch.get('place', patch.get('location'))
    if place is not None:
        pid = K._routine_place_arg(c, place) if place else None
        name = c.execute('SELECT name FROM place WHERE id=?', (pid,)).fetchone()[0] if pid else None
        fields.update(place_id=pid, location=name, location_url=None)
        if K._is_v32(c):
            fields.update(location_cleared=None if pid else '1', location_edited='1', sub_place_id=None)
    if r.get('recurrence_id'):
        fields['rec_exception'] = 1
    return fields


def presence_update(K, c, args):
    r = row(K, c, args[0])
    if r['kind'] != 'presence':
        raise ValueError('wrong event kind')
    args, people = K._pop_people_args(args)
    before_anchor = K.appt_snapshot(c, r['ulid'])
    party = K._presence_people(c, people)
    fields = parse_patch(K, c, args[1:], {'title', 'place', 'location'}, r)
    c.execute('BEGIN IMMEDIATE')
    try:
        r = row(K, c, r['id'])
        before = snapshot(r, fields)
        # Store participants with the same correction when provided.
        if people is not None:
            before['_people'] = party_snapshot(c, r['id'])
            K._presence_people_store(c, r['id'], party)
        write(K, c, r, fields)
        after = snapshot(row(K, c, r['id']), fields)
        if people is not None:
            after['_people'] = party_snapshot(c, r['id'])
        remember(K, c, 'presence-upd:' + r['ulid'], before, after)
        c.commit()
    except BaseException:
        c.rollback()
        raise
    sync_presence(K, c, r, before_anchor)
    print(json.dumps(dict(id=r['id'], result='applied')))
    K._print_cards(c, [r['id']], mutation_state='changed')


def party_snapshot(c, eid):
    return {t: [x[0] for x in c.execute('SELECT '+col+' FROM '+t+' WHERE event_id=? ORDER BY '+col, (eid,))]
            for t, col in (('event_persons', 'person_id'), ('event_group', 'group_id'))}


def sync_presence(K, c, r, before):
    fresh = row(K, c, r['id'])
    if fresh['place_id'] is None:
        K.travel_core.attendance_clear_travel(K, c, fresh)
    elif fresh['place_id'] != before['place_id']:
        # A prior clear retired the auto rule. Reattach through the existing
        # venue rule path rather than leaving an owner place without travel.
        rule = K._travel_row(c, "SELECT * FROM travel_rule WHERE anchor_type='event' AND anchor_ulid=? ORDER BY id DESC LIMIT 1", (fresh['ulid'],))
        if rule and rule['status'] == 'retired' and not K.travel_core.attendance_owner_retired(c, fresh, rule):
            K.travel_rule_upd(c, rule['id'], {'status': 'active', 'place_id': fresh['place_id']})
        K.travel_attach_event(c, r['ulid'], fresh['location'], declared=True)
    K.appt_rederive(c, r['ulid'], before=before, declared=True)
    K._outbox_enqueue_best_effort(r['ulid'])


def presence_undo(K, c, args):
    r = row(K, c, args[0])
    key = 'presence-upd:' + r['ulid']
    before_anchor = K.appt_snapshot(c, r['ulid'])
    c.execute('BEGIN IMMEDIATE')
    try:
        r = row(K, c, r['id'])
        entries = load(K, c, key)
        if not entries:
            raise ValueError('no correction to undo')
        keys = entries[-1]['after']
        current = snapshot(r, [k for k in keys if k != '_people'])
        if '_people' in keys:
            current['_people'] = party_snapshot(c, r['id'])
        fields = pop(K, c, key, current)
        party = fields.pop('_people', None)
        if party is not None:
            for t, col in (('event_persons', 'person_id'), ('event_group', 'group_id')):
                c.execute('DELETE FROM '+t+' WHERE event_id=?', (r['id'],))
                c.executemany('INSERT INTO '+t+'(event_id,'+col+') VALUES(?,?)', [(r['id'], x) for x in party[t]])
        write(K, c, r, fields)
        c.commit()
    except BaseException:
        c.rollback()
        raise
    sync_presence(K, c, r, before_anchor)
    print(json.dumps(dict(id=r['id'], result='applied')))
    K._print_cards(c, [r['id']], mutation_state='changed')


def alias(K, c, verb, args):
    aliases = K._task_title_aliases(c)
    if verb == 'task-alias-list':
        if len(args) > 1:
            raise ValueError('expected optional exact title')
        print(json.dumps({a: t for a, t in sorted(aliases.items()) if not args or t == args[0]}, ensure_ascii=False))
        return
    if len(args) != (2 if verb == 'task-alias-rm' else 1):
        raise ValueError('expected exact-title and alias, or alias for undo')
    name = args[-1].strip().casefold()
    key = 'task-alias:' + name
    c.execute('BEGIN IMMEDIATE')
    try:
        aliases = K._task_title_aliases(c)
        if verb == 'task-alias-rm':
            if aliases.get(name) != args[0]:
                raise ValueError('alias not found for title')
            remember(K, c, key, aliases.pop(name), None)
        else:
            title = pop(K, c, key, aliases.get(name))
            titles = c.execute("SELECT title FROM timeblock WHERE kind='task' UNION SELECT title FROM routine_def WHERE kind='task'").fetchall()
            if title not in {t[0] for t in titles}:
                raise ValueError('alias title no longer exists')
            if any(t[0].casefold() == name and t[0] != title for t in titles):
                raise ValueError('alias conflicts with an existing title')
            aliases[name] = title
        K._config_put(c, 'task_title_aliases', json.dumps(aliases, ensure_ascii=False))
        c.commit()
    except BaseException:
        c.rollback()
        raise
    print('OK')


def project(K, c, verb, args):
    if len(args) != 1:
        raise ValueError('expected project id')
    pid = int(args[0])
    key = 'project-del:' + str(pid)
    c.execute('BEGIN IMMEDIATE')
    try:
        if verb == 'project-del':
            found = K._travel_row(c, 'SELECT * FROM projects WHERE id=?', (pid,))
            if not found or K._config_stored(c, 'schedule-project-deleted:' + str(pid)) == '1':
                raise ValueError('project not found')
            links = [list(x) for x in c.execute('SELECT project_id,block_id FROM project_block WHERE project_id=?', (pid,))]
            defs = [x[0] for x in c.execute('SELECT id FROM routine_def WHERE project_id=?', (pid,))]
            c.execute('UPDATE routine_def SET project_id=NULL WHERE project_id=?', (pid,))
            c.execute('DELETE FROM project_block WHERE project_id=?', (pid,))
            # Immutable grading snapshots retain their project FK. Hide the
            # project from current readers using the existing config store;
            # historical snapshots and their project metadata stay intact.
            soft = bool(c.execute('SELECT 1 FROM project_scope WHERE project_id=? LIMIT 1', (pid,)).fetchone())
            if soft:
                K._config_put(c, 'schedule-project-deleted:' + str(pid), '1')
            else:
                c.execute('DELETE FROM projects WHERE id=?', (pid,))
            remember(K, c, key, dict(row=found, links=links, defs=defs, soft=soft), None)
        else:
            entries = load(K, c, key)
            if not entries:
                raise ValueError('no correction to undo')
            soft = entries[-1]['before'].get('soft', False)
            current = K._travel_row(c, 'SELECT * FROM projects WHERE id=?', (pid,))
            if soft:
                if not current or current['ulid'] != entries[-1]['before']['row']['ulid'] or K._config_stored(c, 'schedule-project-deleted:' + str(pid)) != '1':
                    raise ValueError('project deletion changed')
            elif current:
                raise ValueError('project id reused')
            old = pop(K, c, key, None)
            for _, bid in old['links']:
                if c.execute('SELECT 1 FROM project_block WHERE block_id=?', (bid,)).fetchone():
                    raise ValueError('project link changed')
            for did in old['defs']:
                d = c.execute('SELECT project_id FROM routine_def WHERE id=?', (did,)).fetchone()
                if d is None or d[0] is not None:
                    raise ValueError('routine project changed')
            data = old['row']
            if soft:
                c.execute('DELETE FROM config WHERE key=?', ('schedule-project-deleted:' + str(pid),))
            else:
                c.execute('INSERT INTO projects('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')', list(data.values()))
            c.executemany('INSERT INTO project_block(project_id,block_id) VALUES(?,?)', old['links'])
            c.executemany('UPDATE routine_def SET project_id=? WHERE id=?', [(pid, did) for did in old['defs']])
        c.commit()
    except BaseException:
        c.rollback()
        raise
    print(json.dumps(dict(id=pid, result='applied')))


def cli(K, verb, args):
    c = K.event_conn()
    try:
        if verb.startswith('task-alias-'):
            return alias(K, c, verb, args)
        if verb.startswith('project-del'):
            return project(K, c, verb, args)
        if verb == 'travel-rule-list':
            if args:
                raise ValueError('expected no arguments')
            print(json.dumps(K._travel_rows(c, 'SELECT * FROM travel_rule ORDER BY id', ()), ensure_ascii=False))
        elif verb == 'travel-rule-show':
            if len(args) != 1:
                raise ValueError('expected rule id or ulid')
            rule = K.travel_rule_get(c, args[0])
            if rule is None:
                raise ValueError('travel rule not found')
            print(json.dumps(rule, ensure_ascii=False))
        elif verb == 'event-notify-show':
            if len(args) != 1:
                raise ValueError('expected event id or ulid')
            r = row(K, c, args[0])
            print(json.dumps(snapshot(r, ('id', 'ulid', 'notify_policy', 'notify_lead_min'))))
        elif verb == 'presence-upd-undo':
            if len(args) != 1:
                raise ValueError('expected event id or ulid')
            presence_undo(K, c, args)
        else:
            if len(args) < (2 if verb == 'task-upd' else 1) or (verb != 'task-upd' and len(args) != 1):
                raise ValueError('expected event id or ulid and optional field=value')
            event_change(K, c, verb, args)
    except (ValueError, K.sqlite3.IntegrityError) as exc:
        K._err(str(exc))
    finally:
        c.close()


def routine(K, c, verb, args):
    if len(args) != 1:
        raise ValueError('expected routine id or ulid')
    d = K.routine_def_get(c, args[0])
    if not d:
        raise ValueError('routine not found')
    key = 'routine-unretire:' + d['ulid']
    today = K.routine_today(d['display_tz'])
    before_ids = {x[0] for x in c.execute('SELECT id FROM timeblock WHERE recurrence_id=?', (d['recurrence_id'],))}
    changed = []
    c.execute('BEGIN IMMEDIATE')
    try:
        d = K.routine_def_get(c, d['ulid'])
        if verb == 'unretire':
            if d['status'] != 'retired':
                raise ValueError('routine is not retired')
            old = dict(definition=snapshot(d, ('status', 'valid_until')), rows={})
            # Only retire's own cancellations are revived. Owner cancellations,
            # completions and past lifelog rows retain their meaning.
            rows = K._travel_rows(c, "SELECT * FROM timeblock WHERE recurrence_id=? AND rec_date>=? "
                                 "AND settled_outcome='abandoned' AND settled_by='routine-retire'",
                                 (d['recurrence_id'], today))
            for r in rows:
                r = row(K, c, r['id'])
                if r['kind'] == 'presence' and K._presence_overlapping(c, r['start_at'], K._cand_eff_end(r['start_at'], r['end_at'], r['open_ended']), exclude_id=r['id']):
                    raise ValueError('presence overlap')
                fields = dict.fromkeys(SETTLE)
                restore = cancelled_rule(K, c, r)
                if restore is not None:
                    fields['_rule'] = restore
                old['rows'][str(r['id'])] = snapshot(r, fields)
                write(K, c, r, fields)
                changed.append(r['id'])
            valid = K._valid_until_for(None, today) if d['end_date'] is None else d['valid_until']
            c.execute("UPDATE routine_def SET status='active',valid_until=?,version=version+1,updated_at=? WHERE id=?",
                      (valid, K.now_utc(), d['id']))
            after = dict(definition=dict(status='active', valid_until=valid), rows={str(i): snapshot(row(K, c, i), old['rows'][str(i)]) for i in changed})
            remember(K, c, key, old, after)
        else:
            entries = load(K, c, key)
            if not entries:
                raise ValueError('no correction to undo')
            expected = entries[-1]['after']
            current = dict(definition=snapshot(K.routine_def_get(c, d['ulid']), expected['definition']),
                           rows={i: snapshot(row(K, c, i), values) for i, values in expected['rows'].items()})
            old = pop(K, c, key, current)
            c.execute('UPDATE routine_def SET status=?,valid_until=?,version=version+1,updated_at=? WHERE id=?',
                      (old['definition']['status'], old['definition']['valid_until'], K.now_utc(), d['id']))
            for i, fields in old['rows'].items():
                write(K, c, row(K, c, i), fields)
                changed.append(int(i))
        c.commit()
    except BaseException:
        c.rollback()
        raise
    if verb == 'unretire':
        fresh = K.routine_def_get(c, d['ulid'])
        K._roller_mod().materialize_def(c, None, fresh, today=today)
        new_ids = {x[0] for x in c.execute('SELECT id FROM timeblock WHERE recurrence_id=?', (d['recurrence_id'],))} - before_ids
        # Guard undo against later field edits, not only settlement changes.
        tracked = ('title', 'place_id', 'note', 'start_at', 'end_at', 'rec_exception', '_rule') + SETTLE
        with c:
            entries = load(K, c, key)
            entry = entries[-1]
            entry['after']['definition'] = snapshot(K.routine_def_get(c, d['ulid']), ('status', 'valid_until', 'version'))
            for i in set(changed) | new_ids:
                current = row(K, c, i)
                entry['after']['rows'][str(i)] = snapshot(current, tracked)
                if i in new_ids:
                    entry['before']['rows'][str(i)] = dict(settled_at=K.now_utc(), settled_outcome='abandoned', settled_by='routine-retire', settled_verdict='unknown')
            save(K, c, key, entries)
        changed.extend(new_ids)
    for i in changed:
        r = row(K, c, i)
        if r['settled_outcome'] == 'abandoned':
            K._travel_on_anchor_settled(c, r['id'], r['settled_at'], outcome='abandoned')
        K.appt_rederive(c, r['ulid'], declared=True)
        K._outbox_enqueue_best_effort(r['ulid'])
    print(json.dumps(dict(id=d['id'], result='applied', changed=changed)))
    cards = [K._mutation_card(c, 'routine', i, 'open' if verb == 'unretire' else 'cancelled') for i in changed]
    K._emit_card_block(cards, relay=K._timeblock_card_mod().RELAY)


_RULE_FIELDS = ('id', 'status', 'place_id', 'before_min', 'after_min', 'prep_min',
                'before_src', 'after_src', 'prep_src')


def rule_snapshot(K, c, ulid):
    rule = K._travel_row(c, "SELECT * FROM travel_rule WHERE anchor_type='event' AND anchor_ulid=? ORDER BY id DESC LIMIT 1", (ulid,))
    return snapshot(rule, _RULE_FIELDS) if rule else None


def remember_cancel_rule(K, c, eid, ts):
    """Called inside settlement's transaction, before its travel cleanup.

    Distinguish cancellation cleanup from an owner's independent travel
    opt-out. A missing legacy receipt never grants permission to undo one.
    """
    r = K._travel_block_resolve(c, eid)
    if r is None or r['block_class'] is not None:
        return
    rule = rule_snapshot(K, c, r['ulid'])
    if rule and rule['status'] != 'active':
        rule = None
    K._config_put(c, 'schedule-cancel-rule:' + r['ulid'], json.dumps(dict(settled_at=ts, rule=rule)))


def cancelled_rule(K, c, r):
    receipt = json.loads(K._config_stored(c, 'schedule-cancel-rule:' + r['ulid']) or '{}')
    original = receipt.get('rule')
    if original and receipt.get('settled_at') == r['settled_at']:
        if r['_rule'] == dict(original, status='retired'):
            return original
    return None
