"""Member-scoped shared satiety answers; JSON caches are import sources only."""
import copy
import json
import os

import meal_slots


def member_id():
    # The kit DB has one owner by default. Both agents must use the same member
    # token; an agent identity must never become a calibration partition.
    return os.environ.get('LIFEKIT_MEMBER_ID', 'owner')


class State(dict):
    """Track the read snapshot so saving one occasion cannot clobber others."""
    def __init__(self, entries):
        super().__init__(meal_times=entries)
        self.original = copy.deepcopy(entries)


def load(conn, member=None):
    entries = {}
    for key, ids, grams, kcal, level, at, agent, raw in conn.execute(
            'SELECT occasion,food_ids,grams,kcal,level,recorded_at,source_agent,state '
            'FROM satiety_answer WHERE member_id=? ORDER BY rowid',
            (member or member_id(),)):
        entry = json.loads(raw)
        entry.update(occasion_ids=json.loads(ids), grams=grams, kcal=kcal,
                     fullness=level, recorded_at=at, source_agent=agent)
        entries[key] = entry
    return State(entries)


def resolve(kit, conn, key, entry):
    """Recover whole-occasion food IDs and amounts without promoting provenance."""
    day, slot = key[:10], meal_slots.stored(key[11:].split('@')[0])
    groups = kit._satiety_occasions(conn, day)
    ids = entry.get('occasion_ids') or []
    if not ids and not entry.get('occasion_pending_at'):
        ids = [int(i) for i in (entry.get('scaled') or {})]
    if ids:
        # The first ID is the anchor: deleting a bridge must not spread an
        # answer over both halves of what used to be one occasion.
        group = next((g for g in groups if ids[0] in [r[0] for r in g]), None)
        if group:
            ids = [r[0] for r in group]
    elif not entry.get('occasion_pending_at'):
        group = next((g for g in groups if any(r[1] == slot and r[3] == day for r in g)), [])
        ids = [r[0] for r in group]
    rows = kit._satiety_occasion_rows(conn, day, ids)
    entry['occasion_ids'] = ids
    entry['grams'] = (round(sum(r[2] for r in rows), 1)
                      if rows and all(r[2] is not None and r[2] > 0 for r in rows)
                      else None)
    entry['kcal'] = round(sum(r[3] for r in rows), 1) if rows else None
    if rows:
        entry['resolved'] = ('exact' if all(r[4] == 'exact' for r in rows) else
                             'estimate' if any(r[4] == 'estimate' for r in rows)
                             else 'unknown')
    return day, slot


def save(conn, data, kit, member=None, source=None):
    original = getattr(data, 'original', {})
    for key, entry in (data.get('meal_times') or {}).items():
        if not isinstance(entry, dict) or original.get(key) == entry:
            continue
        if not kit._satiety_date(key):
            raise ValueError('invalid satiety occasion: %r' % key)
        day, slot = resolve(kit, conn, key, entry)
        at = entry.get('recorded_at') or entry.get('recorded') or day
        agent = entry.get('source_agent') or source or os.environ.get(
            'DOGANY_AGENT_ID') or os.environ.get('LIFEKIT_AGENT') or os.path.basename(
                os.path.dirname(kit.SCRIPT_DIR))
        entry.update(recorded_at=at, source_agent=agent)
        conn.execute(
            'INSERT INTO satiety_answer '
            '(member_id,occasion,meal_date,slot,food_ids,grams,kcal,level,recorded_at,source_agent,state) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(member_id,occasion) DO UPDATE SET '
            'food_ids=excluded.food_ids,grams=excluded.grams,kcal=excluded.kcal,'
            'level=excluded.level,recorded_at=excluded.recorded_at,'
            'source_agent=excluded.source_agent,state=excluded.state',
            (member or member_id(), key, day, slot,
             json.dumps(entry.get('occasion_ids') or []), entry.get('grams'),
             entry.get('kcal'), entry.get('fullness'), at, agent,
             json.dumps(entry, ensure_ascii=False, sort_keys=True)))
    if isinstance(data, State):
        data.original = copy.deepcopy(data['meal_times'])


def import_cache(kit, conn, data, source, member=None):
    """Idempotent import: same anchored occasion across agents teaches once.

    A later recorded answer wins; ties retain the first imported answer. Existing
    shared writes win over older legacy answers on re-import. Source order is
    explicit in the migration command for undated conflicting legacy answers.
    """
    shared = load(conn, member)
    imported = merge_cache(kit, conn, shared, data, source)
    save(conn, shared, kit, member, source)
    return imported


def merge_cache(kit, conn, shared, data, source):
    """Merge legacy facts into a snapshot, also used by the recovery reader."""
    imported = 0
    for key, raw in (data.get('meal_times') or {}).items():
        if not isinstance(raw, dict) or raw.get('fullness') not in kit.SATIETY_LEVELS:
            continue
        entry = copy.deepcopy(raw)
        day, slot = resolve(kit, conn, key, entry)
        ids = entry.get('occasion_ids') or []
        existing = next((k for k, e in shared['meal_times'].items()
                         if ids and e.get('occasion_ids') and
                         e['occasion_ids'][0] == ids[0]), None)
        target = existing or key
        # A newer occasion may have the same legacy date/slot key. Keep both.
        old = shared['meal_times'].get(target)
        if old and ids and old.get('occasion_ids') and old['occasion_ids'][0] != ids[0]:
            target = '%s@%s' % (key.split('@')[0], ids[0])
            old = shared['meal_times'].get(target)
        at = entry.get('recorded_at') or entry.get('recorded') or day
        if old and old.get('fullness') in kit.SATIETY_LEVELS and old.get('recorded_at', day) >= at:
            continue
        entry.update(recorded_at=at, source_agent=source)
        shared['meal_times'][target] = entry
        imported += 1
    return imported
