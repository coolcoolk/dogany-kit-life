# -*- coding: utf-8 -*-
"""Voice vocabulary hints, kit side (DGN-1694).

Speech recognition mangles proper nouns. The framework's reader
(routines/lib/voice_vocab.py, contract docs/VOICE-VOCAB-KIT-CONTRACT.md)
runs the verb this kit's pack-manifest.json names in `vocab_verb`:

    kit_life.sh vocab --json

and folds the answer into the recognizer's hint list. This module IS that
answer: the display names the owner SAYS -- people, places, foods, exercises
-- each with the time it was last used.

CONTRACT (the reader's side of it)
    stdout = one JSON array [{"term", "last_used", "kind"}], exit 0.
    last_used = epoch seconds, or null when never used. "kind" is extra and
    ignored by the reader. At most MAX_TERMS rows, each term <= MAX_TERM_LEN
    characters and single-line, total < MAX_BYTES.
    Any failure (no database, unreadable file, time budget spent) -> exit 1
    with NOTHING on stdout or stderr: the reader treats it as "no kit
    vocabulary" and stays silent, so this verb does too.

READ-ONLY, NO MIGRATION. The database is opened `mode=ro` with query_only on;
kit_life.get_conn() (WAL pragma, migrations) is never reached -- kit_life.sh
runs this file directly (kit_life.py is not even compiled), and
`python3 kit_life.py vocab` dispatches here before anything touches the DB. A table or column an
older database does not have just drops that source.

WHAT "USED" MEANS
    person   latest block the person is attached to (event_persons);
             aliases ride with the name. Never attached -> null.
    place    latest block at the place (block.place_id); active places only.
    food     latest meal record whose title is the food (portion suffix such
             as "(200g)" stripped), then food-cache names and aliases by
             their updated_at.
    exercise latest workout classified under a workout_types subtype; set
             rows' exercise names by recorded_at. snake_case slugs
             (v_squat) are ids, not speech, and are skipped.
Only blocks at or before now count: materialized future occurrences of a
routine are not a use. Any other future stamp is clamped to now.

    group    (073) latest block the group attended (event_group); name,
             display_name and aliases each.
    sub_place (073) rooms whose name carries an ASCII/digit core ("A2-room"),
             by their latest booking (block_attr sub_place_id).
Persons and places emit EVERY surface form (name, display_name, aliases).

SELECTION. Each kind first gets up to MAX_TERMS // (kinds with terms) of its
most recent terms (so 200 cached foods cannot push every person out), then
the rest of MAX_TERMS fills by recency across kinds. Duplicates fold
case-insensitively to the most recent use.

`vocab --stamp` (073, the framework maintainer seam M1): one 16-hex line, a hash of the
dictionary's surface forms -- a consumer re-reads `--json` only when it
changed. Same silent-failure rule.
"""

import workout_history as _workout_history
import calendar
import json
import os
import re
import sqlite3
import sys
import time
from urllib.parse import quote

# The reader's clock starts when it spawns the verb, not when the DB opens:
# the time budget is spent from here (the earliest point this module can
# see), so interpreter start-up under load comes out of it instead of
# pushing the answer past the reader's 5 s timeout.
_T0 = time.monotonic()

MAX_TERMS = 100
PER_KIND_FLOOR = 25            # pre-073 fixed floor; select() now derives it
MAX_TERM_LEN = 40
MAX_BYTES = 64 * 1024
TIME_BUDGET_S = 3.5
KINDS = ('person', 'place', 'food', 'exercise', 'group', 'sub_place')

_SLUG_RE = re.compile(r'^[a-z0-9]+(?:_[a-z0-9]+)+$')
_PORTION_RE = re.compile(r'\s*\([^()]*\d[^()]*\)\s*$')
_WS_RE = re.compile(r'\s+')


class _Quiet(Exception):
    """Any condition that must end the verb silently with a non-zero exit."""


# -- time -----------------------------------------------------------------


def to_epoch(text, now):
    """A stored time string -> epoch seconds (int), or None.

    The kit stores three grades: UTC 'YYYY-MM-DDTHH:MM:SSZ', local
    'YYYY-MM-DD HH:MM:SS' (datetime('now','localtime')) and date-only local
    'YYYY-MM-DD'. Local grades are read in this machine's zone -- the one
    that wrote them. A future instant is clamped to now."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    try:
        if len(s) >= 19 and s[10] in 'T ':
            st = time.strptime(s[:19], '%Y-%m-%d' + s[10] + '%H:%M:%S')
            if s[19:].startswith('Z') or s[19:].startswith('+00:00'):
                ep = calendar.timegm(st)
            elif len(s) == 19:
                ep = time.mktime(st)
            else:
                return None
        elif len(s) == 10:
            ep = time.mktime(time.strptime(s, '%Y-%m-%d'))
        else:
            return None
    except (ValueError, OverflowError):
        return None
    return int(min(ep, now))


# -- terms ----------------------------------------------------------------


def clean_term(raw):
    """The spoken form of a stored name, or None when it is not one."""
    if not isinstance(raw, str) or '\n' in raw or '\r' in raw:
        return None
    t = _WS_RE.sub(' ', raw).strip()
    if not t or len(t) > MAX_TERM_LEN or _SLUG_RE.match(t):
        return None
    if not any(ch.isalpha() for ch in t):
        return None
    return t


def food_name(title):
    """'\uce58\uc988\uae40\ubc25(\ucf58) (200g)' -> '\uce58\uc988\uae40\ubc25(\ucf58)': drop trailing portion groups
    (a parenthesis holding a digit); a flavour like '(\ucf58)' stays."""
    if not isinstance(title, str):
        return None
    prev = None
    t = title.strip()
    while prev != t:
        prev, t = t, _PORTION_RE.sub('', t)
    return t


# -- sources --------------------------------------------------------------
#
# Each source is (kind, [sql, fallback_sql, ...], row -> [(raw, ts)]).  The
# first statement that runs wins; one that raises (older schema) moves on;
# none running drops the source.

_BLOCK_TS = "COALESCE(b.start_at, b.date, b.rec_date)"
# Only blocks that already happened count as a use: a daily routine's
# occurrences are materialized weeks ahead and would all read as "now".
# ':now' is UTC 'YYYY-MM-DDTHH:MM:SSZ'; a date-only or local-grade stamp
# compares by its date prefix, off by at most the zone offset.
_PAST = " AND " + _BLOCK_TS + " <= :now"


def _person_rows(row):
    # 073: EVERY surface form the owner may say -- the name, the display
    # name and each alias -- not COALESCE(display, name): a recognizer that
    # only knows one spelling mangles the other.
    name, disp, aliases, ts = row
    out = [(name, ts), (disp, ts)]
    for a in (aliases or '').split(','):
        out.append((a, ts))
    return out


def _json_list(blob):
    try:
        arr = json.loads(blob) if blob else []
    except (TypeError, ValueError):
        return []
    return [a for a in arr if isinstance(a, str)] if isinstance(arr, list) \
        else []


def _named_rows(row):
    """(name, display_name, aliases JSON, ts) -> every surface form."""
    name, disp, aliases, ts = row
    return [(name, ts), (disp, ts)] + [(a, ts) for a in _json_list(aliases)]


_ROOM_CORE_RE = re.compile(r'[A-Za-z0-9]')


def _room_rows(row):
    """Rooms the recognizer mangles: names carrying an ASCII/digit core
    ("A2-room"); a Hangul-only room name is ordinary speech."""
    name, ts = row
    if isinstance(name, str) and _ROOM_CORE_RE.search(name):
        return [(name, ts)]
    return []


def _plain(row):
    return [(row[0], row[1])]


def _food_rows(row):
    return [(food_name(row[0]), row[1])]


SOURCES = (
    ('person', [
        "SELECT p.name, p.display_name, p.aliases, "
        "MAX(" + _BLOCK_TS + ") FROM persons p "
        "LEFT JOIN event_persons ep ON ep.person_id = p.id "
        "LEFT JOIN block b ON b.id = ep.event_id" + _PAST + " GROUP BY p.id",
        "SELECT p.name, NULL, p.aliases, NULL FROM persons p",
    ], _person_rows),
    ('place', [
        "SELECT pl.name, pl.display_name, pl.aliases, "
        "MAX(" + _BLOCK_TS + ") FROM place pl "
        "LEFT JOIN block b ON b.place_id = pl.id" + _PAST + " "
        "WHERE pl.status = 'active' GROUP BY pl.id",
        "SELECT pl.name, pl.display_name, pl.aliases, NULL FROM place pl",
    ], _named_rows),
    # 073 proper-noun registry: groups (used = latest block they attended)
    # and rooms with an ASCII/digit core (used = latest booking in them).
    ('group', [
        "SELECT g.name, g.display_name, g.aliases, MAX(" + _BLOCK_TS + ") "
        "FROM person_group g LEFT JOIN event_group eg ON eg.group_id = g.id "
        "LEFT JOIN block b ON b.id = eg.event_id" + _PAST + " "
        "WHERE g.status = 'active' GROUP BY g.id",
    ], _named_rows),
    ('sub_place', [
        "SELECT sp.name, MAX(" + _BLOCK_TS + ") FROM sub_place sp "
        "JOIN place pl ON pl.id = sp.place_id AND pl.status = 'active' "
        "LEFT JOIN block_attr ba ON ba.attr_token = 'sub_place_id' "
        "AND ba.value = CAST(sp.id AS TEXT) "
        "LEFT JOIN block b ON b.id = ba.block_id" + _PAST + " GROUP BY sp.id",
    ], _room_rows),
    ('food', [
        "SELECT b.title, MAX(" + _BLOCK_TS + ") FROM block b "
        "WHERE b.settled_by = 'meal-record'" + _PAST + " GROUP BY b.title",
    ], _food_rows),
    ('food', ["SELECT name, updated_at FROM meal_food"], _food_rows),
    ('food', ["SELECT alias, updated_at FROM meal_alias"], _plain),
    ('exercise', [
        "SELECT t.subtype, MAX(w.date) FROM workout_types t "
        "LEFT JOIN workout_classifications c ON c.type_id = t.id "
        "LEFT JOIN workouts w ON w.id = c.workout_id AND w.date <= :now "
        "WHERE t.active = 1 GROUP BY t.id",
    ], _plain),
    ('exercise', [
        "SELECT exercise_name, MAX(recorded_at) FROM workout_sets "
        "GROUP BY exercise_name",
    ], _plain),
)


def collect(conn, now):
    """{kind: {casefold: [term, epoch|None]}} across every source."""
    best = {}           # casefold -> [term, epoch, kind]
    params = {'now': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}
    for kind, stmts, expand in SOURCES:
        rows = None
        for sql in stmts:
            try:
                rows = _workout_history.execute(conn, sql, params).fetchall()
                break
            except sqlite3.OperationalError as e:
                if 'interrupted' in str(e):
                    raise _Quiet()
                continue
        for row in rows or ():
            for raw, ts in expand(row):
                term = clean_term(raw)
                if term is None:
                    continue
                ep = to_epoch(ts, now)
                key = term.casefold()
                cur = best.get(key)
                if cur is None:
                    best[key] = [term, ep, kind]
                elif ep is not None and (cur[1] is None or ep > cur[1]):
                    cur[1] = ep
    return best


def select(best, limit=MAX_TERMS, floor=None):
    """Most-recent-first rows: a per-kind floor, then global recency. The
    floor is DERIVED (073): MAX_TERMS // the kinds that have terms, so a new
    kind never needs a hand-tuned constant (25 x 4 kinds before 073)."""
    if floor is None:
        present = len(set(v[2] for v in best.values())) or 1
        floor = max(1, limit // present)

    def rank(item):
        return (item[1] is None, -(item[1] or 0), item[0])
    items = sorted(best.values(), key=rank)
    chosen, taken = [], {}
    for it in items:
        if taken.get(it[2], 0) < floor and len(chosen) < limit:
            chosen.append(it)
            taken[it[2]] = taken.get(it[2], 0) + 1
    picked = set(id(it) for it in chosen)
    for it in items:
        if len(chosen) >= limit:
            break
        if id(it) not in picked:
            chosen.append(it)
    chosen.sort(key=rank)
    return [{'term': t, 'last_used': ep, 'kind': k} for t, ep, k in chosen]


def render(rows):
    """Compact JSON, cut from the least recent end until it fits."""
    while True:
        out = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
        if len(out.encode('utf-8')) < MAX_BYTES or not rows:
            return out
        rows = rows[:-1]


def open_ro(db_path):
    """Read-only connection that can never create, write or migrate."""
    import ro_open as _ro_open  # noqa: E402
    conn = _ro_open.open_ro(db_path, timeout=1.0)
    conn.execute('PRAGMA query_only = ON')
    deadline = _T0 + TIME_BUDGET_S
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 10000)
    return conn


def vocab(db_path, now=None):
    now = time.time() if now is None else now
    conn = open_ro(db_path)
    try:
        return select(collect(conn, now))
    finally:
        conn.close()


# The dictionary tables the stamp covers (073 M1 freshness stamp).
_STAMP_SQL = (
    "SELECT kind, ref_id, surface, surface_role, status FROM v_proper_noun "
    "ORDER BY kind, ref_id, surface_role, surface",
    "SELECT 'person', id, name, aliases, display_name FROM persons "
    "UNION ALL SELECT 'place', id, name, aliases, status FROM place "
    "ORDER BY 1, 2",
)


def stamp(db_path):
    """16-hex freshness stamp of the dictionary: changes whenever a name, a
    display name, an alias, a room or a group is added, renamed or retired.
    A consumer re-reads `vocab --json` only when it changed."""
    import hashlib
    conn = open_ro(db_path)
    try:
        rows = None
        for sql in _STAMP_SQL:
            try:
                rows = conn.execute(sql).fetchall()
                break
            except sqlite3.OperationalError as e:
                if 'interrupted' in str(e):
                    raise _Quiet()
                continue
        if rows is None:
            raise _Quiet()
        h = hashlib.sha256()
        h.update(str(len(rows)).encode())
        for row in rows:
            h.update(json.dumps(row, ensure_ascii=False).encode('utf-8'))
            h.update(b'\n')
        return h.hexdigest()[:16]
    finally:
        conn.close()


def cli(db_path, argv):
    """`kit_life.sh vocab --json` (exit 0 + JSON) / `vocab --stamp` (exit 0
    + one 16-hex line), or exit 1 silently."""
    if argv == ['--stamp']:
        try:
            out = stamp(db_path)
        except Exception:
            if os.environ.get('LIFEKIT_VOCAB_DEBUG') == '1':
                import traceback
                traceback.print_exc()
            sys.exit(1)
        sys.stdout.write(out + '\n')
        sys.exit(0)
    if argv != ['--json']:
        sys.exit(2)
    try:
        out = render(vocab(db_path))
    except Exception:
        # Silent by contract (the reader treats any output as noise). For a
        # human chasing a failure, LIFEKIT_VOCAB_DEBUG=1 prints the cause.
        if os.environ.get('LIFEKIT_VOCAB_DEBUG') == '1':
            import traceback
            traceback.print_exc()
        sys.exit(1)
    sys.stdout.write(out + '\n')
    sys.exit(0)


if __name__ == '__main__':
    # kit_life.sh runs `vocab` straight through this file: compiling the
    # 44k-line kit_life.py first cost 0.4 s of CPU idle and several seconds
    # under a loaded machine, all of it before the budget above could see.
    cli(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kit_life.db'),
        sys.argv[1:])
