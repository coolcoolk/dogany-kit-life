"""Pure lived-occurrence projection shared by owner-facing readers.

Event aliases are identity.  ``parent_id`` is only containment: a date, trip,
or course therefore keeps its independently meaningful children.  During the
identity transition callers may supply the alias map which a migration will
install; the projection then folds only those explicitly aliased facet rows
into their canonical occurrence.
"""


def alias_map(conn):
    """Return ``old event id -> canonical event id`` on schema 114+ DBs."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_alias'"
    ).fetchone()
    if not exists:
        return {}
    return dict(conn.execute("SELECT old_id,canonical_id FROM event_alias"))


def _canonical(event_id, aliases):
    seen = set()
    while event_id in aliases:
        if event_id in seen:
            raise ValueError("event alias cycle: %s" % event_id)
        seen.add(event_id)
        event_id = aliases[event_id]
    return event_id


def project(events, aliases=None):
    """Return occurrence rows without mutating input or touching the DB.

    ``events`` are ordinary event dictionaries.  Aliased rows contribute their
    ids in ``facet_event_ids`` and their typed facts in ``facets``.  Unaliased
    containment remains in ``children``.  This makes a test-only future alias
    map useful before migration while keeping today's empty-map output exact.
    """
    aliases = dict(aliases or {})
    rows = [dict(row) for row in events]
    by_id = {row['id']: row for row in rows}
    groups = {}
    for row in rows:
        groups.setdefault(_canonical(row['id'], aliases), []).append(row)
    out = []
    for canonical_id, members in groups.items():
        root = by_id.get(canonical_id)
        if root is None:
            # A normal post-migration read contains only the survivor.  A
            # partial/test projection may contain an old row only.
            root = members[0]
        root = dict(root)
        facet_rows = [m for m in members if m['id'] != root['id']]
        root['facet_event_ids'] = [m['id'] for m in facet_rows]
        root['facets'] = [dict(m) for m in facet_rows]
        root['children'] = []
        out.append(root)
    projected = {row['id']: row for row in out}
    for row in out:
        parent_id = row.get('parent_id')
        parent_id = _canonical(parent_id, aliases) if parent_id is not None else None
        parent = projected.get(parent_id)
        if parent and parent is not row:
            parent['children'].append(row)
    for row in out:
        row['children'].sort(key=lambda child: child['id'])
    return [row for row in out if not any(row in parent['children'] for parent in out)]


def opted_in(conn, day=None, person_id=None):
    """Read canonical occurrences whose identity migration is explicitly on.

    The presence of aliases is the activation switch.  Unmigrated experiences
    are intentionally omitted, which keeps slice 2 owner-visible output byte
    identical while letting every adapter consume the same records after the
    bounded migration.
    """
    aliases = alias_map(conn)
    canonical_ids = sorted(set(aliases.values()))
    if not canonical_ids:
        return []
    marks = ','.join('?' * len(canonical_ids))
    where = ["e.id IN (%s)" % marks]
    args = list(canonical_ids)
    if day is not None:
        where.append("e.occurred_on=?")
        args.append(day)
    if person_id is not None:
        where.append("EXISTS (SELECT 1 FROM experience_person ep "
                     "WHERE ep.event_id=e.id AND ep.person_id=?)")
        args.append(person_id)
    rows = conn.execute(
        "SELECT e.id,e.ulid,e.title,e.kind_hint,e.parent_id,e.occurred_on,"
        "e.occurred_at,e.reaction FROM event e WHERE " + " AND ".join(where) +
        " ORDER BY e.occurred_at,e.id", args).fetchall()
    names = [c[0] for c in conn.execute(
        "SELECT e.id,e.ulid,e.title,e.kind_hint,e.parent_id,e.occurred_on,"
        "e.occurred_at,e.reaction FROM event e LIMIT 0").description]
    return project([dict(zip(names, row)) for row in rows], aliases)


def for_spend(conn, spend_id):
    """Canonical occurrence navigation for a ledger spend, if attached."""
    rows = conn.execute(
        "SELECT e.id,e.ulid,e.title,e.kind_hint,e.parent_id,e.occurred_on,"
        "e.occurred_at,e.reaction FROM spend_entry s "
        "JOIN record r ON r.source_table='spend_entry' AND r.source_id=s.id "
        "JOIN record_event re ON re.record_id=r.id "
        "JOIN event e ON e.id=re.event_id WHERE s.id=? ORDER BY e.id",
        (spend_id,)).fetchall()
    if not rows:
        return []
    names = [c[0] for c in conn.execute(
        "SELECT id,ulid,title,kind_hint,parent_id,occurred_on,occurred_at,"
        "reaction FROM event LIMIT 0").description]
    return project([dict(zip(names, row)) for row in rows], alias_map(conn))
