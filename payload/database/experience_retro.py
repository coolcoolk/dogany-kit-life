"""Lived-day review over the capture layer; no capture inference or new menu.

Calendar plans are context. Explicit occurrence dates select lived experiences.
Unclocked reports sort after clocked ones, in capture order (never invented time).
Detail-only answers use a settled record block, never an appointment or task.
"""
import datetime as dt
import json
import re

import meal_slots

VISIT_KINDS = {'visit', 'restaurant', 'cafe', 'course', 'outing', 'social', 'date'}
MONEY_KINDS = {'purchase', 'spend', 'shopping'}
FIELDS = ('time', 'place', 'people', 'amount')


def _dicts(cursor):
    return [dict(zip((c[0] for c in cursor.description), row)) for row in cursor]


def _instant(value):
    if not value or 'T' not in value:
        return None
    try:
        stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp if stamp.tzinfo else None
    except ValueError:
        return None


def _order(item):
    stamp = _instant(item.get('occurred_at'))
    return (0, stamp.timestamp(), item['id']) if stamp else (1, 0, item['id'])


def _blocks(conn, eid):
    return _dicts(conn.execute(
        "WITH linked(id) AS (SELECT timeblock_id FROM event_timeblock WHERE event_id=? UNION "
        "SELECT r.source_id FROM record r JOIN record_event re ON re.record_id=r.id "
        "WHERE re.event_id=? AND r.source_table='block') "
        "SELECT DISTINCT b.* FROM block b WHERE b.id IN linked OR b.contained_by IN linked ORDER BY b.id", (eid, eid)))


def _attrs(conn, bid):
    return dict(conn.execute('SELECT attr_token,value FROM block_attr WHERE block_id=? ORDER BY source', (bid,)))


def _block_clock(conn, block):
    """Occurrence evidence, excluding calendar deadlines and enrichment writes."""
    attrs = _attrs(conn, block['id'])
    if block['kind_token'] == 'task' and (
            attrs.get('schedule_kind') == 'all_day' or attrs.get('time_mode') == 'due'):
        return block['settled_at'] if block['settled_outcome'] == 'done' else None
    if _instant(block['start_at']):
        return block['start_at']
    if block['kind_token'] == 'record' and block['settled_by'] != 'experience-retro':
        return block['settled_at'] if block['settled_outcome'] == 'done' else None
    return None


def _details(kit, conn, event):
    eid = event['id']
    if event['kind_hint'] == 'cooking-occasion':
        event['title'] = meal_slots.occasion_title(event['title'])
    event['date_basis'] = 'occurred_on' if event['occurred_on'] else 'report_context'
    blocks = _blocks(conn, eid)
    # The actual detail record wins over calendar context, without moving plans.
    details = [b for b in blocks if b['ulid'] == 'x-experience-' + event['ulid']]
    attrs = _attrs(conn, details[0]['id']) if details else {}
    clocked = sorted((v for b in blocks for v in [_block_clock(conn, b)] if _instant(v)),
                     key=lambda v: _instant(v).timestamp())
    event['occurred_at'] = event['occurred_at'] or (clocked[0] if clocked else None)
    sessions = [b for b in blocks if _instant(b['end_at']) and (
        (b['settled_by'] == 'workout-session' and b['start_at'] == event['occurred_at']) or
        (b['settled_by'] == 'cooking-session' and _instant(b['start_at']) == _instant(event['occurred_at'])))]
    event['ended_at'] = sessions[0]['end_at'] if sessions else None
    if event['kind_hint'] == 'cooking' and event['ended_at']:
        event['ended_at'] = _instant(event['ended_at']).astimezone(_instant(event['occurred_at']).tzinfo).isoformat()
    if event['kind_hint'] == 'cooking-meal':
        def dish_order(block):
            dish = conn.execute('SELECT dish_id FROM made_dish_meal WHERE block_id=?', (block['id'],)).fetchone()
            return (0, dish[0]) if dish else (1, block['id'])
        event['meal_dishes'] = [b['title'] for b in sorted(blocks, key=dish_order) if b['settled_by'] == 'meal-record']
    detail_places = [b['place_id'] for b in details if b['place_id'] is not None]
    places = detail_places or [b['place_id'] for b in blocks if b['place_id'] is not None]
    names = [r[0] for pid in dict.fromkeys(places) for r in conn.execute('SELECT COALESCE(display_name,name) FROM place WHERE id=?', (pid,))]
    people = [r[0] for r in conn.execute(
        'SELECT DISTINCT COALESCE(p.display_name,p.name) FROM persons p WHERE p.id IN ('
        'SELECT person_id FROM experience_person WHERE event_id=? UNION '
        'SELECT ep.person_id FROM event_persons ep JOIN event_timeblock et ON et.timeblock_id=ep.event_id WHERE et.event_id=?) ORDER BY p.id', (eid, eid))]
    spends = _dicts(conn.execute(
        "SELECT s.id,s.amount FROM spend_entry s WHERE s.id IN ("
        "SELECT r.source_id FROM record r JOIN record_event re ON re.record_id=r.id WHERE re.event_id=? AND r.source_table='spend_entry' UNION "
        "SELECT o.spend_id FROM owned_item o JOIN record r ON r.source_table='owned_item' AND r.source_id=o.id JOIN record_event re ON re.record_id=r.id WHERE re.event_id=?) ORDER BY s.id", (eid, eid)))
    owned = conn.execute(
        "SELECT o.price,o.spend_id FROM owned_item o JOIN record r ON r.source_table='owned_item' AND r.source_id=o.id "
        'JOIN record_event re ON re.record_id=r.id WHERE re.event_id=?', (eid,)).fetchall()
    amount = sum(s['amount'] for s in spends) if spends else None
    if amount is None and owned and all(r[0] is not None for r in owned):
        amount = sum(r[0] for r in owned)
    kind = (event['kind_hint'] or '').lower()
    wants_visit = kind in VISIT_KINDS or any(b['kind_token'] in ('appointment', 'presence') for b in blocks)
    missing = []
    # A report without a clock is already a complete memory.  Treat its time
    # as unknown instead of turning an unknowable detail into a retro question.
    # Keep the state in the ordinary detail facet so every downstream reader
    # sees the same answer as an explicit ``time=unknown`` enrichment.
    if not event['occurred_at'] and attrs.get('retro.time') != 'unknown':
        bid = _detail_block(kit, conn, event)
        conn.execute("INSERT OR REPLACE INTO block_attr VALUES(?,?,?,'system')",
                     (bid, 'retro.time', 'unknown'))
        attrs['retro.time'] = 'unknown'
    if wants_visit and not names and attrs.get('retro.place') != 'unknown':
        missing.append('place')
    if wants_visit and not people and attrs.get('retro.people') not in ('alone', 'unknown'):
        missing.append('people')
    if (kind in MONEY_KINDS or owned) and amount is None and attrs.get('retro.amount') not in ('none', 'unknown'):
        missing.append('amount')
    event.update(block_ids=[b['id'] for b in blocks], places=names, people=people,
                 people_state=attrs.get('retro.people'), amount=amount, missing=missing)
    return event


def _imported_payment_only(conn, event):
    """A statement capture is money evidence, not a standalone day activity.

    Existing parent, visit and non-payment links still supply lived context.
    Ignore detail blocks that earlier retro reads created for unknown time.
    """
    if event['parent_id'] is not None or event['kind_hint'] in VISIT_KINDS:
        return False
    detail_ulid = 'x-experience-' + event['ulid']
    if any(b['ulid'] != detail_ulid for b in _blocks(conn, event['id'])):
        return False
    sources = conn.execute(
        "SELECT r.source_table,s.source FROM record r "
        "JOIN record_event re ON re.record_id=r.id "
        "LEFT JOIN spend_entry s ON r.source_table='spend_entry' AND s.id=r.source_id "
        "WHERE re.event_id=? AND NOT (r.source_table='block' AND r.source_id IN "
        "(SELECT id FROM block WHERE ulid=?))", (event['id'], detail_ulid)).fetchall()
    return bool(sources) and all(table == 'spend_entry' and source == 'import'
                                 for table, source in sources)


def listing(kit, conn, day, now=None, route='retro'):
    if dt.date.fromisoformat(day).isoformat() != day:
        raise ValueError('date must be YYYY-MM-DD')
    # Include an outer container even when only its child has a known day.
    selected = """SELECT e.id FROM event e WHERE e.occurred_on=? OR
        (e.occurred_on IS NULL AND EXISTS (SELECT 1 FROM experience_capture_key k
         WHERE k.event_id=e.id AND CASE WHEN json_valid(k.capture_key)
         THEN json_extract(k.capture_key,'$[0]') END=?))"""
    rows = _dicts(conn.execute('SELECT id,ulid,title,kind_hint,parent_id,occurred_on,occurred_at,reaction FROM event WHERE id IN (' + selected + ') OR id IN (SELECT parent_id FROM event WHERE id IN (' + selected + ')) ORDER BY id', (day, day, day, day)))
    # A capture linked only to workflow tasks is not a lived experience.
    # Independent owner captures (including same-title ones) have no such proof.
    system_ids = kit.retro_system_task_ids(conn)
    def workflow_only(event):
        blocks = [b for b in _blocks(conn, event['id'])
                  if b['ulid'] != 'x-experience-' + event['ulid']]
        return bool(blocks) and all(b['id'] in system_ids for b in blocks)
    rows = [r for r in rows if not workflow_only(r) and not _imported_payment_only(conn, r)]
    # Keep unknown-time writes inside an existing walk transaction.
    own_transaction = not conn.in_transaction
    # One identity projection is shared with the other owner-facing readers.
    # An empty alias registry deliberately reproduces the legacy hierarchy;
    # identity migration remains off until every adapter has landed.
    import occurrence_projection
    detailed = [_details(kit, conn, r) for r in rows]
    aliases = occurrence_projection.alias_map(conn)
    roots = occurrence_projection.project(detailed, aliases)
    if not aliases:
        # Projection metadata is internal. Preserve the legacy public JSON,
        # including the absence of a children key on non-root leaves.
        root_ids = {item['id'] for item in roots}
        pending = list(roots)
        while pending:
            item = pending.pop()
            pending.extend(item['children'])
            item.pop('facet_event_ids')
            item.pop('facets')
            if not item['children'] and item['id'] not in root_ids:
                item.pop('children')
    for root in roots:
        root.setdefault('children', []).sort(key=_order)
        members = [root] + root['children']
        # Companions belong to the outer visit. A root answer covers its
        # unnamed inner stops; explicit child companions remain untouched.
        people_known = bool(root['people']) or root.get('people_state') in ('alone', 'unknown')
        needs_people = any('people' in m['missing'] for m in members)
        for member in members:
            if 'people' in member['missing']:
                member['missing'].remove('people')
        if needs_people and not people_known:
            root['missing'].append('people')
        pending = [dict(event_id=m['id'], field=f, title=m['title']) for m in members for f in FIELDS if f in m['missing']]
        represented = {b for c in root['children'] for b in c['block_ids']}
        stops = []
        for b in _blocks(conn, root['id']):
            if b['id'] in represented or b['title'] == root['title'] or (b['kind_token'] not in ('appointment', 'presence') and b['settled_by'] != 'meal-record'):
                continue
            # No opinions or completion claims are inferred from a linked plan.
            stop = dict(id=b['id'], title=b['title'], start_at=b['start_at'], source='linked_block')
            stops.append(stop)
        stops.sort(key=lambda s: (0, _instant(s['start_at']).timestamp(), s['id']) if _instant(s['start_at']) else (1, 0, s['id']))
        root['stops'] = stops
        root['pending'] = pending
        root['next_question'] = None
        if pending:
            target = pending[0]
            title = target['title'] if target['event_id'] == root['id'] else kit._i18n('retro.experience.nested_title').format(outer=root['title'], inner=target['title'])
            root['next_question'] = dict(target, root_id=root['id'], text=kit._i18n('retro.experience.ask.' + target['field']).format(title=title))
        root['review_kind'] = 'course' if root['children'] or root['stops'] or (root['kind_hint'] or '').lower() == 'course' else 'experience'
        import course_review
        if root['kind_hint'] != 'cooking-occasion':
            course_review.annotate(kit, conn, root, day, now, route)
    if own_transaction:
        conn.commit()
    return sorted(roots, key=lambda r: min((_order(m) for m in [r] + r['children']), default=_order(r)))


def render(kit, roots, quote=False):
    if not roots:
        return ''
    def line(item, child=False):
        stamp = _instant(item['occurred_at'])
        time = stamp.strftime('%H:%M') if stamp else ('' if child else kit._i18n('retro.experience.unknown_time'))
        # Explicit offsets retain the owner's clock. Calendar UTC uses display timezone.
        def local_clock(value):
            stamp = _instant(value)
            if not stamp:
                return ''
            if not value.endswith('Z'):
                return stamp.strftime('%H:%M')
            from zoneinfo import ZoneInfo
            zone = kit._root_conf_get(kit._kit_root(), 'AGENT_TZ')
            offset = kit._root_conf_get(kit._kit_root(), 'AGENT_TZ_OFFSET_HOURS')
            if zone:
                stamp = stamp.astimezone(ZoneInfo(zone))
            elif offset:
                stamp = stamp.astimezone(dt.timezone(dt.timedelta(hours=float(offset))))
            return stamp.strftime('%H:%M')
        if stamp:
            time = local_clock(item['occurred_at'])
        if item.get('ended_at'):
            time = kit._i18n('retro.daily.time_range').format(
                start=time, end=local_clock(item['ended_at']))
        title = re.sub(r'<(?=[/A-Za-z])', '<\u2060', ' '.join(item['title'].split()))
        detail = re.sub(r'<(?=[/A-Za-z])', '<\u2060', ' / '.join(item['places']))
        if child and time:
            time += ' '
        return kit._i18n('retro.experience.child' if child else 'retro.experience.row').format(time=time, title=title, detail=detail).rstrip()
    lines, reviews = [], []
    for root in roots:
        lines.append(line(root))
        # Keep reported course order: an unclocked middle stop must not move
        # past the final timed stop. Legacy linked stops keep their clock order.
        inner = sorted(root['children'], key=lambda c: c['id']) + [dict(id=s['id'], title=s['title'], occurred_at=s['start_at'], places=[]) for s in root['stops']]
        if root['kind_hint'] == 'cooking-occasion':
            inner.sort(key=_order)
        for child in inner:
            lines.append(line(child, True))
            lines.extend(kit._i18n('retro.cooking.dish').format(
                dish=re.sub(r'<(?=[/A-Za-z])', '<\u2060', ' '.join(dish.split())))
                         for dish in child.get('meal_dishes', []))
        review = root.get('course_review')
        if review and review['ready']:
            reviews.append(re.sub(r'<(?=[/A-Za-z])', '<\u2060', review['text']))
    section = kit._i18n('retro.experience.section')
    if quote:
        section = kit._i18n('retro.experience.layout.section').format(title=section)
        template = kit._i18n('retro.experience.layout.quote')
        return '\n'.join([section, template.format(rows='\n'.join(lines))]
                         + [template.format(rows=text) for text in reviews])
    return '\n'.join([section] + lines + reviews)


def _detail_block(kit, conn, event):
    uid = 'x-experience-' + event['ulid']
    row = conn.execute('SELECT id FROM block WHERE ulid=?', (uid,)).fetchone()
    if row:
        return row[0]
    now = kit.now_utc()
    bid = conn.execute("INSERT INTO block(ulid,source_module,title,date,kind_token,settled_at,settled_outcome,settled_by,settled_verdict) VALUES(?,?,?,?,'record',?,'done','experience-retro','user')", (uid, 'kit_life', event['title'], event['occurred_on'], now)).lastrowid
    kit.experience_ensure(conn, event['title'], event_id=event['id'], evidence=[('block', bid)])
    return bid


def enrich(kit, conn, eid, fields):
    """One atomic answer; existing capture preserves owner words and evidence."""
    allowed = {'reaction','note','kind_hint','title','occurred_on','occurred_at',
               'place_id','person_id','people','amount','spend_id','verbatim','time','place'}
    if not fields or set(fields) - allowed:
        raise ValueError('unsupported experience enrichment field')
    for name, accepted in (('time', {'unknown'}), ('place', {'unknown'}), ('people', {'alone','unknown'})):
        if name in fields and fields[name] not in accepted:
            raise ValueError('unsupported ' + name + ' answer')
    if 'spend_id' in fields and 'amount' not in fields:
        raise ValueError('spend_id requires amount')
    conn.execute('BEGIN IMMEDIATE')
    try:
        import retro_correction
        correction_scope = retro_correction.ReceiptScope(events=(eid,))
        correction_before = retro_correction.snapshot(conn, retro_correction.ENRICH_TABLES, correction_scope)
        rows = _dicts(conn.execute('SELECT * FROM event WHERE id=?', (eid,)))
        if not rows:
            raise ValueError('experience missing: ' + str(eid))
        event = rows[0]
        day, instant = kit._experience_time(fields.get('occurred_on', event['occurred_on']), fields.get('occurred_at'))
        # A date correction must clear the old timestamp through experience_ensure.
        import course_review
        routed = 'reaction' in fields and course_review.answer(kit, conn, event, fields['reaction'])
        kit.experience_ensure(conn, fields.get('title') or event['title'], event_id=eid,
            occurred_on=day, occurred_at=instant, verbatim=fields.get('verbatim'),
            reaction=None if routed else fields.get('reaction'), note=fields.get('note'))
        if day != event['occurred_on']:
            detail = conn.execute('SELECT id FROM block WHERE ulid=?', ('x-experience-' + event['ulid'],)).fetchone()
            if detail:
                conn.execute('UPDATE block SET start_at=NULL,end_at=NULL,date=? WHERE id=?', (day, detail[0]))
                conn.execute("UPDATE record SET ts=? WHERE source_table='block' AND source_id=?", (day, detail[0]))
        for name in ('title', 'kind_hint'):
            if name in fields:
                conn.execute('UPDATE event SET ' + name + '=? WHERE id=?', (fields[name] or None, eid))
        detail_fields = set(fields) & {'place_id','person_id','people','amount','time','place','occurred_at'}
        if detail_fields:
            event['occurred_on'] = day
            bid = _detail_block(kit, conn, event)
            for name, accepted in (('time', {'unknown'}), ('place', {'unknown'}),
                                   ('people', {'alone','unknown'}), ('amount', {'none','unknown'})):
                if fields.get(name) in accepted:
                    conn.execute("INSERT OR REPLACE INTO block_attr VALUES(?,?,?,'user')", (bid, 'retro.' + name, fields[name]))
            if instant:
                # Preserve actual calendar plans; only actual record evidence changes.
                utc = _instant(instant).astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                conn.execute('UPDATE block SET start_at=?,date=NULL WHERE id=?', (utc, bid))
                conn.execute("UPDATE record SET ts=? WHERE source_table='block' AND source_id=?", (instant, bid))
            if 'place_id' in fields:
                pid = int(fields['place_id'])
                if not conn.execute('SELECT 1 FROM place WHERE id=?', (pid,)).fetchone():
                    raise ValueError('place missing')
                conn.execute('UPDATE block SET place_id=? WHERE id=?', (pid, bid))
                # Existing actual records learn the answer; child experiences do not.
                conn.execute("UPDATE block SET place_id=? WHERE kind_token='record' AND place_id IS NULL AND id IN (SELECT r.source_id FROM record r JOIN record_event re ON re.record_id=r.id WHERE re.event_id=? AND r.source_table='block')", (pid, eid))
            if 'person_id' in fields:
                pid = int(fields['person_id'])
                if not conn.execute('SELECT 1 FROM persons WHERE id=?', (pid,)).fetchone():
                    raise ValueError('person missing')
                conn.execute('INSERT OR IGNORE INTO experience_person(event_id,person_id) VALUES(?,?)', (eid, pid))
                conn.execute('INSERT OR IGNORE INTO event_persons(event_id,person_id) VALUES(?,?)', (bid, pid))
            if 'amount' in fields and fields['amount'] not in ('none','unknown'):
                amount = int(fields['amount'])
                spends = [r[0] for r in conn.execute("SELECT s.id FROM spend_entry s JOIN record r ON r.source_table='spend_entry' AND r.source_id=s.id JOIN record_event re ON re.record_id=r.id WHERE re.event_id=?", (eid,))]
                sid = int(fields['spend_id']) if 'spend_id' in fields else spends[0] if len(spends) == 1 else None
                if len(spends) > 1 and sid is None:
                    raise ValueError('ambiguous amount: supply linked spend_id')
                if sid is not None:
                    if sid not in spends:
                        raise ValueError('spend_id is not linked to this experience')
                    # Same writer and money shadow as spend-upd, without an early commit.
                    conn.execute('UPDATE spend_entry SET amount=? WHERE id=?', (amount, sid))
                    s = conn.execute('SELECT date,name,kind,method FROM spend_entry WHERE id=?', (sid,)).fetchone()
                    kit._spend_write_v32(conn, sid, s[0], s[1], amount, s[2], s[3])
                else:
                    if not day:
                        raise ValueError('amount needs a known occurrence date')
                    kit.spend_add(day, amount, event['title'], conn=conn, commit=False,
                                  match_captures=False, experience_id=eid)
        retro_correction.receipt(conn, 'enrich:' + str(eid), correction_before,
                                 retro_correction.ENRICH_TABLES, correction_scope)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _opening_place(kit, value):
    # Reuse the presence reader's legacy no-place sentinels for visit copy.
    value = (kit._presence_place_text('presence', value) or '').strip()
    return '' if value == '미정' else value


def _online_blocks(kit, conn, ids):
    return any(kit.travel_core.attendance_online(
        kit, conn, dict(_attrs(conn, bid), id=bid)) for bid in ids)


def _opener_choices(kit, conn, day, roots, now=None):
    """One evidenced outing, never a task, capture note, or calendar-only plan.

    Captures need an explicit visit/event kind. Calendar items need attendance
    evidence (done or an owner retro line); merely reaching their time is not
    proof that the owner went. Keep the source identity for reply routing.
    """
    choices = []
    for root in roots:
        if (root.get('kind_hint') or '').lower() not in VISIT_KINDS:
            continue
        if root.get('occurred_on') != day:
            continue
        choices.append(dict(kind='experience', id=str(root['id']), title=root['title'],
                            place=next((place for raw in root.get('places', [])
                                        if (place := _opening_place(kit, raw))), ''),
                            visit=not _online_blocks(kit, conn, root['block_ids']),
                            score=2 + bool(root.get('children'))))
    try:
        calendar = kit.retro_items(day, conn=conn, now=now, ratings=False)
    except (TypeError, ValueError):
        # Legacy calendar rows can lack display_tz. The existing card already
        # falls back to lived captures when that optional reader cannot render.
        calendar = []
    for item in calendar:
        if item.get('carried') or item.get('kind') != 'appointment':
            continue
        evidence = conn.execute('SELECT settled_outcome,retro_line FROM block WHERE id=?',
                                (item['id'],)).fetchone()
        if not evidence or not (evidence[0] == 'done' or evidence[1]):
            continue
        member = kit._retro_member(conn, item['id'])
        place = (_opening_place(kit, member['place_name']) or
                 _opening_place(kit, member['location']))
        choices.append(dict(kind='event', id=str(item['id']), title=item['title'],
                            place=place, visit=not _online_blocks(
                                kit, conn, [item['id']]), score=1))
    movie = re.compile(r'movie|cinema|4dx|\uc601\ud654|\uc2dc\uc0ac\ud68c', re.I)
    for choice in choices:
        choice['movie'] = bool(movie.search(choice['title']))
        choice['day'] = day
    return choices


def _opening_subject(kit, choices, period):
    if not choices:
        return None
    # Same daily salience rule; the most recent day breaks weekly ties.
    chosen = max(choices, key=lambda x: (x['movie'], x['score'], x['day'], -int(x['id'])))
    place = chosen['place']
    key = 'retro.' + period + '.opener.event.' + ('movie' if chosen['movie'] else 'visit')
    if not chosen['visit'] or (chosen['kind'] == 'event' and not chosen['movie'] and not place):
        key = 'retro.daily.item.ask'
        place = ''
    elif chosen['movie'] and place:
        key += '_place'
    # A visit names its known venue, or the recorded activity when unknown.
    # Never invent an action, or repeat a venue as both place and activity.
    values = dict(place=place, title=place or chosen['title'])
    values = {k: re.sub(r'<(?=[/A-Za-z])', '<\u2060', ' '.join(v.split()))
              for k, v in values.items()}
    chosen['text'] = kit._i18n(key).format(**values)
    return {k: chosen[k] for k in ('kind', 'id', 'text')}


def daily_opener(kit, conn, day, roots, now=None):
    return _opening_subject(kit, _opener_choices(kit, conn, day, roots, now), 'daily')


def weekly_opening(kit, conn, day, roots, now=None, owning_agent=None):
    """Opening-only packet for Mon..Sun; existing weekly lists stay intact."""
    end = dt.date.fromisoformat(day)
    start = end - dt.timedelta(days=6)
    choices = []
    # Selecting a subject must not persist detail records for earlier days.
    conn.execute('SAVEPOINT weekly_opening')
    try:
        for offset in range(7):
            current = (start + dt.timedelta(days=offset)).isoformat()
            items = roots if current == day else listing(kit, conn, current, _instant(now) if now else None)
            choices.extend(_opener_choices(kit, conn, current, items, now))
    finally:
        conn.execute('ROLLBACK TO weekly_opening')
        conn.execute('RELEASE weekly_opening')
    lo = kit.all_day_instants(start.isoformat())[0]
    hi = kit.all_day_instants((end + dt.timedelta(days=1)).isoformat())[0]
    rows = conn.execute(
        "SELECT id FROM timeblock WHERE kind='task' AND block_class IS NULL "
        "AND settled_at IS NULL AND start_at>=? AND start_at<? "
        "AND (? IS NULL OR instr(','||?||',', ','||owning_agent||',')>0)",
        (lo, hi, owning_agent, owning_agent))
    unchecked = sum(kit.weekly_review_visible(conn, r[0]) for r in rows)
    return dict(opener=_opening_subject(kit, choices, 'weekly'), unchecked_count=unchecked)


def cli(kit, argv):
    import argparse
    parser = argparse.ArgumentParser(prog='experience-retro-list')
    parser.add_argument('day')
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--daily-opener', action='store_true', help='include the daily opening subject')
    parser.add_argument('--weekly-opener', action='store_true', help='include the reviewed week opening subject')
    parser.add_argument('--owning-agent', help='scope weekly unchecked tasks')
    parser.add_argument('--quote', action='store_true', help='group the list and each course question in HTML quotes')
    parser.add_argument('--now', help='read-only preview clock, ISO time with offset')
    args = parser.parse_args(argv)
    now = _instant(args.now) if args.now else None
    if args.now and not now:
        raise ValueError('preview clock requires an offset')
    conn = kit.get_conn()
    try:
        roots = listing(kit, conn, args.day, now)
        block = render(kit, roots, quote=args.quote)
        packet = dict(date=args.day, items=roots, block=block)
        if args.daily_opener:
            packet['opener'] = daily_opener(kit, conn, args.day, roots, args.now)
        if args.weekly_opener:
            if dt.date.fromisoformat(args.day).weekday() != 6:
                raise ValueError('weekly opening requires the reviewed Sunday')
            _, owner = kit.task_find_owning_agent(
                ['--owning-agent', args.owning_agent] if args.owning_agent else [])
            packet.update(weekly_opening(kit, conn, args.day, roots, args.now, owner))
        print(block if args.render else json.dumps(packet, ensure_ascii=False))
    finally:
        conn.close()
