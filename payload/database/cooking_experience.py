"""Capture cooking and eating as legs of one occasion, in the writer's tx.

Times come from owner progress, never the renderer's clock or planned slots.
The ordinary event hierarchy and record blocks outlive the current session.
"""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import person_labels
import meal_slots


def _parent(K, c, child, parent):
    old = c.execute('SELECT parent_id FROM event WHERE id=?', (child,)).fetchone()[0]
    if old not in (None, parent):
        return False
    if c.execute('SELECT 1 FROM event_child_exclusion WHERE parent_id=? AND child_id=?',
                 (parent, child)).fetchone() or K._parent_link_error(c, child, parent):
        return False
    c.execute('UPDATE event SET parent_id=? WHERE id=?', (parent, child))
    return True


def _people(K, c, state, meal=None):
    ids = set()
    for ref in state.get('source', {}).get('eaters', []):
        # Eaters include the owner. Only existing person identities are guests.
        rows = K._person_exact_matches(c, ref)
        if not rows:
            rows = K.person_find(ref, conn=c)
        if len(rows) == 1:
            ids.add(rows[0][0])
    if meal is not None:
        ids.update(r[0] for r in c.execute('SELECT person_id FROM experience_person WHERE event_id=?', (meal,)))
    return sorted(ids)


def _title(K, c, slot, people, labels=None):
    slot = meal_slots.label(slot)
    labels = labels or {}
    names = ', '.join(labels.get(str(pid), person_labels.person(c, pid)[0]) for pid in people)
    return K._i18n('retro.cooking.occasion').format(
        people=names, rang=K._josa(names, 'irang'), slot=slot) if names else slot


def _occasion(K, c, eid, start, slot, people):
    labels = {}
    for raw, in c.execute("SELECT a.value FROM block_attr a JOIN record r ON r.source_table='block' AND r.source_id=a.block_id JOIN record_event re ON re.record_id=r.id WHERE re.event_id=? AND a.attr_token='cooking.people'", (eid,)):
        labels.update(json.loads(raw))
    people = sorted(set(people) | {int(pid) for pid in labels})
    key = 'cooking-occasion:' + str(eid)
    found = c.execute('SELECT event_id FROM experience_capture_key WHERE capture_key=?', (key,)).fetchone()
    if found:
        parent = found[0]
    else:
        # Different sessions with the same guests/slot remain different occasions.
        if c.execute('SELECT parent_id FROM event WHERE id=?', (eid,)).fetchone()[0] is not None:
            return None
        parent = c.execute('INSERT INTO event(id,ulid,title,kind_hint,owning_agent,occurred_on,occurred_at) VALUES(?,?,?,?,?,?,?)',
            (K.next_event_id(c), K.new_ulid(), _title(K, c, slot, people, labels), 'cooking-occasion', K._pin_agent(), start[:10], start)).lastrowid
        if not _parent(K, c, eid, parent):
            c.execute('DELETE FROM event WHERE id=?', (parent,))
            return None
        c.execute('INSERT INTO experience_capture_key VALUES(?,?)', (key, parent))
    for pid in people:
        c.execute('INSERT OR IGNORE INTO experience_person(event_id,person_id) VALUES(?,?)', (parent, pid))
    people = [r[0] for r in c.execute('SELECT person_id FROM experience_person WHERE event_id=? ORDER BY person_id', (parent,))]
    c.execute('UPDATE event SET title=?,occurred_on=?,occurred_at=? WHERE id=?',
              (_title(K, c, slot, people, labels), start[:10], start, parent))
    return parent


def session_event(K, c, state, title):
    """A session identity wins over generic capture's same-title retry key."""
    linked = c.execute("SELECT re.event_id FROM made_dish d JOIN record r ON r.source_table='made_dish' AND r.source_id=d.id JOIN record_event re ON re.record_id=r.id WHERE d.session_id=? AND d.owning_agent=? ORDER BY re.event_id LIMIT 1", (state['id'], K._pin_agent())).fetchone()
    if linked:
        return linked[0]
    eid = c.execute('INSERT INTO event(id,ulid,title,owning_agent) VALUES(?,?,?,?)',
                    (K.next_event_id(c), K.new_ulid(), title, K._pin_agent())).lastrowid
    c.execute('INSERT INTO experience_capture_key VALUES(?,?)', ('created:' + str(eid), eid))
    return eid


def capture(K, c, state, eid):
    """Called with made-dish evidence, including retries and bulk close."""
    kind = c.execute('SELECT kind_hint FROM event WHERE id=?', (eid,)).fetchone()[0]
    created = c.execute('SELECT 1 FROM experience_capture_key WHERE capture_key=?', ('created:' + str(eid),)).fetchone()
    # Explicit attachment can adopt the provisional capture into a date/trip.
    # A later dish/retry must preserve that curated container's identity.
    if kind not in (None, '', 'cooking') or (not created and kind != 'cooking'):
        return
    start = state.get('started_at')
    if start is None or not state.get('history'):
        return
    zone = ZoneInfo(state['source'].get('timezone', 'Asia/Seoul'))
    start = dt.datetime.fromtimestamp(start, zone).isoformat(timespec='seconds')
    names = ', '.join(r.get('dish_name', r['name']) for r in state['source']['recipes'])
    title = K._i18n('retro.cooking.leg').format(dishes=names)
    completed = [h['completed_at'] for h in state['history']]
    last = max(completed)
    # More than one final tick in one close is bulk completion, not duration.
    bulk = state.get('closed_owner_words') is not None or completed.count(last) > 1
    finished = len(state['completed']) == len(state['plan']['steps'])
    end = dt.datetime.fromtimestamp(last, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if finished and not bulk else None
    utc = dt.datetime.fromisoformat(start).astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    uid = 'x-cooking-%s-%s' % (K._pin_agent(), state['id'])
    row = c.execute('SELECT id FROM block WHERE ulid=?', (uid,)).fetchone()
    if row:
        bid = row[0]
        c.execute('UPDATE block SET title=?,start_at=?,end_at=? WHERE id=?', (title, utc, end, bid))
    else:
        bid = c.execute("INSERT INTO block(ulid,source_module,title,kind_token,start_at,end_at,settled_at,settled_outcome,settled_by,settled_verdict) VALUES(?,?,?,'record',?,?,?,'done','cooking-session','user')",
                        (uid, 'kit_life', title, utc, end, K.now_utc())).lastrowid
    c.execute('UPDATE event SET title=?,kind_hint=?,occurred_on=?,occurred_at=? WHERE id=?',
              (title, 'cooking', start[:10], start, eid))
    labels = {}
    for ref in state['source'].get('eaters', []):
        matches = K._person_exact_matches(c, ref) or K.person_find(ref, conn=c)
        if len(matches) == 1:
            pid = matches[0][0]
            display = c.execute('SELECT display_name FROM persons WHERE id=?', (pid,)).fetchone()[0]
            labels[str(pid)] = display or ref
    c.execute("INSERT OR REPLACE INTO block_attr VALUES(?,?,?,'user')", (bid, 'cooking.people', json.dumps(labels, ensure_ascii=False)))
    K.experience_ensure(c, title, event_id=eid, occurred_at=start, evidence=[('block', bid)])
    target = state['source'].get('target_at')
    slot = None
    if target is not None:
        local = dt.datetime.fromtimestamp(target, zone)
        minute = local.hour * 60 + local.minute
        slot = next((s for s, (lo, hi) in K._MEAL_SLOT_WINDOWS.items() if lo <= minute < hi), None)
    if slot:
        _occasion(K, c, eid, start, slot, _people(K, c, state))


def link_meal(K, c, dish_id, bid):
    """Join only a cooked dish eaten on its cooking day; leftovers stay separate."""
    rows = c.execute("SELECT DISTINCT e.id,e.occurred_at FROM made_dish d JOIN record r ON r.source_table='made_dish' AND r.source_id=d.id JOIN record_event re ON re.record_id=r.id JOIN event e ON e.id=re.event_id WHERE d.id=? AND e.kind_hint='cooking'", (dish_id,)).fetchall()
    meal = c.execute('SELECT date,meal FROM ' + K.MEALS_V32 + ' WHERE id=?', (bid,)).fetchone()
    if len(rows) != 1 or not meal:
        return
    eid, start = rows[0]
    if not start or start[:10] != meal[0]:
        return
    # The made-dish link is evidence; time overlap alone never creates a parent.
    linked = c.execute("SELECT e.id FROM event e JOIN record_event re ON re.event_id=e.id JOIN record r ON r.id=re.record_id WHERE r.source_table='block' AND r.source_id=? ORDER BY e.id", (bid,)).fetchall()
    if len(linked) != 1:
        return
    mid = linked[0][0]
    kind = c.execute('SELECT kind_hint FROM event WHERE id=?', (mid,)).fetchone()[0]
    if kind not in (None, '', 'meal', 'cooking-meal'):
        return
    if not c.execute('SELECT 1 FROM experience_capture_key WHERE capture_key=?', ('created:' + str(mid),)).fetchone():
        return
    if mid == eid or c.execute('SELECT 1 FROM event WHERE parent_id=?', (mid,)).fetchone():
        return
    # Do not rewrite an explicitly curated visit or move its records.
    if c.execute("SELECT 1 FROM record r JOIN record_event re ON re.record_id=r.id WHERE re.event_id=? AND (r.source_table NOT IN ('block','artifact') OR (r.source_table='block' AND r.source_id NOT IN (SELECT id FROM " + K.MEALS_V32 + ' WHERE date=? AND meal=?)))', (mid, meal[0], meal[1])).fetchone():
        return
    parent = _occasion(K, c, eid, start, meal[1], _people(K, c, {}, mid))
    if parent is None or not _parent(K, c, mid, parent):
        return
    c.execute('UPDATE event SET title=?,kind_hint=? WHERE id=?', (K._i18n('retro.cooking.meal'), 'cooking-meal', mid))


def meal_event(c, dish_id, day, slot):
    """Use an already linked meal leg for subsequent dish writes, before capture."""
    rows = c.execute("SELECT DISTINCT meal.id FROM made_dish d "
        "JOIN record r ON r.source_table='made_dish' AND r.source_id=d.id "
        "JOIN record_event re ON re.record_id=r.id JOIN event cook ON cook.id=re.event_id "
        "JOIN event meal ON meal.parent_id=cook.parent_id AND meal.kind_hint='cooking-meal' "
        "JOIN record_event mre ON mre.event_id=meal.id JOIN record mr ON mr.id=mre.record_id "
        "AND mr.source_table='block' JOIN block b ON b.id=mr.source_id "
        "JOIN block_attr a ON a.block_id=b.id AND a.attr_token='meal_slot' "
        "WHERE d.id=? AND cook.kind_hint='cooking' AND cook.occurred_on=? "
        "AND b.date=? AND " + meal_slots.stored_sql('a.value') + "=?",
        (dish_id, day, day, meal_slots.stored(slot))).fetchall()
    return rows[0][0] if len(rows) == 1 else None
