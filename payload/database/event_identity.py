"""Canonical event lookups. Containment (parent_id) never implies identity.

No consolidation is enabled here. Alias registration requires the caller's
transaction and an already retired source; it never deletes an event or source.
"""
import functools
import sqlite3


def _has_aliases(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_alias'").fetchone() is not None


def resolve(conn, token):
    """Return canonical (id, ulid, title), or None. Accept ID, ULID, capture key.

    Missing aliases on a pre-114 DB are supported for read-only compatibility.
    Numeric identifiers take precedence; neither dates nor titles are inferred.
    """
    token = str(token)
    numeric = token.isdigit() and len(token) <= 19 and int(token) <= 9223372036854775807
    aliases = _has_aliases(conn)
    seen = set()
    while token not in seen:
        seen.add(token)
        column = 'id' if numeric else 'ulid'
        row = conn.execute('SELECT id,ulid,title FROM event WHERE ' + column + '=?', (int(token) if numeric else token,)).fetchone()
        if row:
            return row
        alias = conn.execute('SELECT canonical_id FROM event_alias WHERE ' + ('old_id' if numeric else 'old_ulid') + '=?',
                             (int(token) if numeric else token,)).fetchone() if aliases else None
        if not alias:
            alias = conn.execute('SELECT event_id FROM experience_capture_key WHERE capture_key=?', (token,)).fetchone()
        if not alias:
            return None
        token, numeric = str(alias[0]), True
    raise ValueError('event alias cycle: ' + token)


def require(conn, token):
    row = resolve(conn, token)
    if row is None:
        raise ValueError('experience not found: %s' % token)
    return row[0]


def next_id(conn):
    """Call under the writer transaction, including domain/import writers."""
    if not conn.in_transaction:
        raise ValueError('event ID allocation requires caller transaction')
    live = conn.execute('SELECT COALESCE(MAX(id),0) FROM event').fetchone()[0]
    retired = conn.execute('SELECT COALESCE(MAX(old_id),0) FROM event_alias').fetchone()[0] if _has_aliases(conn) else 0
    return max(live, retired) + 1


def add_alias(conn, old_id, old_ulid, canonical):
    """Register an already retired identity; flatten alias/capture targets.

    The caller remaps all source/config references before retiring anything.
    No commit and no event delete. SQL guards reject live identities and cycles.
    """
    if not conn.in_transaction:
        raise ValueError('event alias registration requires caller transaction')
    target = require(conn, canonical)
    existing = conn.execute('SELECT old_ulid,canonical_id FROM event_alias WHERE old_id=?', (old_id,)).fetchone()
    if existing:
        if tuple(existing) != (old_ulid, target):
            raise ValueError('event alias already registered differently')
        return target
    conn.execute('INSERT INTO event_alias(old_id,old_ulid,canonical_id) VALUES(?,?,?)',
                 (old_id, old_ulid, target))
    # Capture aliases can outlive source events on historical/imported DBs.
    conn.execute('UPDATE experience_capture_key SET event_id=? WHERE event_id=?', (target, old_id))
    return target


def lookup_boundary(kit, *positions, comma=False, ulid=False):
    """Resolve public experience arguments before legacy validation.

    Keep domain writer/render logic unchanged. Calendar event-* IDs are blocks
    and deliberately never pass through this occurrence resolver.
    """
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(argv):
            args = list(argv)
            if any(p < len(args) and args[p] for p in positions):
                conn = sqlite3.connect(kit._inspect_uri(kit.DB_PATH), uri=True)
                try:
                    conn.execute('PRAGMA query_only=ON')
                    for pos in positions:
                        if pos >= len(args) or not args[pos]:
                            continue
                        tokens = str(args[pos]).split(',') if comma else [args[pos]]
                        resolved = []
                        for token in tokens:
                            row = resolve(conn, token)
                            resolved.append(str(row[1] if ulid else row[0]) if row else str(token))
                        args[pos] = ','.join(resolved) if comma else resolved[0]
                finally:
                    conn.close()
            return fn(args)
        return wrapped
    return decorate
