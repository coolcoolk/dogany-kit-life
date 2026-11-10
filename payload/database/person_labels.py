"""Read-only person labels and grouped event rosters for shared surfaces."""
import json
import sqlite3

DEFAULT_EMOJI = "\U0001f642"


def person(conn, pid):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(persons)')}
    fields = ['name'] + [c if c in columns else 'NULL' for c in
                         ('display_name', 'emoji')]
    row = conn.execute('SELECT %s FROM persons WHERE id=?' % ','.join(fields),
                       (pid,)).fetchone()
    if not row:
        return '', DEFAULT_EMOJI
    name, display, emoji = row
    # Aliases are lookup spellings, never presentation names.
    return display or name, emoji or DEFAULT_EMOJI


def label(conn, pid):
    name, emoji = person(conn, pid)
    return emoji + ' ' + name


def participants(conn, eid, limit=None):
    try:
        ids = [r[0] for r in conn.execute(
            'SELECT p.id FROM persons p JOIN event_persons ep ON ep.person_id=p.id '
            'WHERE ep.event_id=? ORDER BY p.name,p.id', (eid,))]
        return [label(conn, pid) for pid in (ids if limit is None else ids[:limit])]
    except sqlite3.Error:
        return []


def participant_lines(conn, eid, limit=None, *, collapse_over=None):
    """An event roster: linked groups first, then largest shared memberships.

    Classify the full roster before applying an optional display cap. The
    name-ordered cap selects identities; display stays in person-id
    order, as on the day card. Membership inference never writes a group.
    Only callers opting into collapse_over replace member names with counts
    when the total attendee count exceeds that threshold.
    """
    columns = {r[1] for r in conn.execute('PRAGMA table_info(persons)')}
    group_field = 'p.groups' if 'groups' in columns else 'NULL'
    rows = list(conn.execute(
        'SELECT p.id,p.name,%s FROM persons p JOIN event_persons ep '
        'ON ep.person_id=p.id WHERE ep.event_id=? ORDER BY p.id' % group_field,
        (eid,)))
    shown = {r[0] for r in (rows if limit is None else
                            sorted(rows, key=lambda r: (r[1], r[0]))[:limit])}
    collapse = collapse_over is not None and len(rows) > collapse_over
    memberships = {pid: [g.strip() for g in (raw or '').split(',') if g.strip()]
                   for pid, _name, raw in rows}
    groups = {}
    aliases = {}
    try:
        fields = {r[1] for r in conn.execute('PRAGMA table_info(person_group)')}
        emoji_field = 'emoji' if 'emoji' in fields else 'NULL'
        for gid, name, display, emoji, raw_aliases in conn.execute(
                'SELECT id,name,display_name,%s,aliases FROM person_group '
                'ORDER BY id' % emoji_field):
            try:
                extra = json.loads(raw_aliases or '[]')
            except (ValueError, TypeError):
                extra = []
            spellings = [name, display] + (extra if isinstance(extra, list) else [])
            groups[gid] = dict(name=display or name, emoji=emoji or '👥',
                               members=set(), tokens=[], linked=False)
            for spelling in spellings:
                if isinstance(spelling, str) and spelling.strip():
                    aliases.setdefault(spelling.strip().casefold(), gid)
        for (gid,) in conn.execute('SELECT group_id FROM event_group WHERE event_id=?', (eid,)):
            if gid in groups:
                groups[gid]['linked'] = True
    except sqlite3.OperationalError:
        # Older read-only schemas can still render people and memberships.
        pass
    for pid, tokens in memberships.items():
        for token in tokens:
            key = aliases.get(token.casefold(), ('membership', token.casefold()))
            group = groups.setdefault(key, dict(name=token, emoji='👥',
                                      members=set(), tokens=[], linked=False))
            group['members'].add(pid)
            group['tokens'].append(token)
    for group in groups.values():
        # Lower-case registry imports (cbbg) take the attended persons' casing
        # (CBBG). An explicitly cased registry display remains authoritative.
        if group['name'].islower():
            group['name'] = next((t for t in group['tokens'] if not t.islower()),
                                 group['name'])
    remaining = set(memberships)
    lines = []

    def emit(group):
        members = group['members'] & remaining
        labels = [label(conn, pid) for pid, _n, _g in rows
                  if pid in members and pid in shown]
        if labels or (group['linked'] and not group['members']):
            suffix = ' - ' + ' '.join(labels) if labels else ''
            if collapse:
                suffix = ' %d명' % len(members & shown)
            lines.append(group['emoji'] + ' ' + group['name'] + suffix)
        remaining.difference_update(members)

    linked = [g for g in groups.values() if g['linked']]
    while linked:
        group = min(linked, key=lambda g: (-len(g['members'] & remaining),
                                          g['name'].casefold(), g['name']))
        linked.remove(group)
        emit(group)
    inferred = [g for g in groups.values() if not g['linked']]
    while inferred:
        group = min(inferred, key=lambda g: (-len(g['members'] & remaining),
                                            g['name'].casefold(), g['name']))
        inferred.remove(group)
        if len(group['members'] & remaining) >= 2:
            emit(group)
    rest = [label(conn, pid) for pid, _n, _g in rows if pid in remaining & shown]
    if rest:
        lines.append(' '.join(rest))
    return lines


def normalize_emoji(value):
    """Accept one emoji sequence, including hearts, flags, skin and ZWJ forms."""
    import unicodedata
    if len(value) > 32 or any(c.isspace() for c in value):
        raise ValueError('emoji wants one emoji sequence')
    # Emoji bases are symbols; digits and #/* only form a keycap sequence.
    keycap = value.endswith('\u20e3') and value[0] in '0123456789#*'
    bases = [c for c in value if unicodedata.category(c) == 'So'
             and not 0x1F3FB <= ord(c) <= 0x1F3FF]
    allowed = all(unicodedata.category(c) in ('So','Mn','Me') or
                  c == '\u200d' or 0x1F3FB <= ord(c) <= 0x1F3FF or
                  (keycap and c in '0123456789#*') for c in value)
    flag = len(bases) == 2 and all(0x1F1E6 <= ord(c) <= 0x1F1FF for c in bases)
    if not allowed or not (keycap or len(bases) == 1 or flag or
                            (bases and '\u200d' in value)):
        raise ValueError('emoji wants one emoji sequence')
    return value
