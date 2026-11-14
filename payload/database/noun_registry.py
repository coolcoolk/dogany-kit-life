"""noun_registry.py -- the kit's proper-noun registry (migration 073).

Spec: docs/specs/2026-10-08-propernoun-registry.md (owner decisions
2026-10-08: typing ask cap 3/day; two skips -> that name is never asked
again; a group is retired (disbanded = status gone), never deleted while
history names it).

Founding incidents (2026-10-08): the owner's band (a person_group) was
written into a rehearsal ROOM because its name ends in the Korean word for
"room", and the answer "straight from the office" minted a second place under
the office's display name although place 1 carries that display_name -- no
resolver read display_name.

WHAT THIS FILE OWNS (engine; the skills only call verbs):
  resolve()      ONE match function for every kind.  Precedence, fixed:
                 exact name / token -> display_name -> alias -> composed
                 "<place> <room>".  Near variants are never resolved here
                 (place rule 6): they ask (near_match.py).
  scan()         every known surface in an utterance, longest first, at a
                 token start, ending at a boundary or a josa from JOSA.
  field gates    field_kind_gate rows decide which kinds may fill a field;
                 a wrong kind is FIELD_KIND_REFUSED, exit 8, nothing written.
  typing ask     TYPE_ASK for an unknown name -- once per name for life when
                 answered; offered at most twice (then auto_declined); at most
                 ledger_guard noun_ask_daily_cap asks per local day.
  rooms          the room of a booking is block_attr sub_place_id; an
                 unknown room accrues silently only by SIBLING SIGNATURE
                 (A2-room beside A3-room / D-room), never because a word
                 ends in a room noun.
  groups         person_group verbs + event_group participation.

Verbs are dispatched from kit_life.main as cli(K, verb, argv), K = the
running kit_life module (shop.py's convention: never import kit_life here).

EXIT CODES: 0 ok / judgment; 1 usage or not found (stderr); 3 EXISTS /
CROSS_KIND (nothing created); 4 IN_USE (ref-safe delete refused); 5
NEAR_MATCH; 6 PARENT_ASK (a room with no place); 8 FIELD_KIND_REFUSED.
"""
import datetime
import json
import sqlite3
import sys
import unicodedata

import ask_choices

SCHEMA_VERSION = 73
KINDS = ("person", "group", "place", "sub_place", "project", "method")
ASK_KINDS = ("person", "group", "place", "sub_place", "project")
FIELD_KIND_CODE = 8
EXISTS_CODE = 3
IN_USE_CODE = 4
PARENT_CODE = 6
ROOM_ATTR = "sub_place_id"
ROLE_RANK = {"name": 0, "token": 0, "display_name": 1, "alias": 2,
             "composed": 3}
# Re-offering the same name inside this window is the same turn retrying the
# call -- it neither counts as a second offer nor spends the daily cap.
REOFFER_SAME_TURN_MIN = 10
DECAY_K = 2               # offered twice and ignored twice -> auto_declined

# Trailing particles a name may carry in an utterance ("<name>-with",
# "<name>-to").
# Match corpus literals (the near_match RELATIONAL class), longest first.
JOSA = tuple(sorted((
    "\uc774\ub791", "\ub791", "\ud558\uace0", "\ud55c\ud14c", "\uc5d0\uac8c", "\uaed8\uc11c", "\uaed8", "\uc774\uc640", "\uc640", "\uacfc",
    "\uc774\ub294", "\uc740", "\ub294", "\uc774\uac00", "\uc774", "\uac00", "\uc744", "\ub97c", "\uc5d0\uc11c", "\uc5d0", "\uc758",
    "\ub3c4", "\uc73c\ub85c", "\ub85c", "\uc774\uc57c", "\uc57c", "\uc774\uc5d0\uc694", "\uc608\uc694", "\uc774\uace0", "\uace0", "\ub2d8",
    "\uc528", "\ub9cc", "\uae4c\uc9c0", "\ubd80\ud130", "\ucc98\ub7fc", "\ubcf4\ub2e4"), key=len, reverse=True))
# Trailing nouns that name a room (match corpus).  A word ending in one of
# these is NEVER by itself a room signal (founding incident) -- they only
# shape the sibling signature.
ROOM_NOUNS = ("\ud638\uc2e4", "\ub8f8", "\ubc29", "\uc2e4", "\ud640")

VERBS = (
    "noun-tag", "noun-context", "noun-type", "noun-ask-forget",
    "noun-ask-list", "noun-fields",
    "group-add", "group-find", "group-show", "group-set", "group-alias",
    "group-alias-rm", "group-del", "group-blocks", "group-member",
    "appt-group", "appt-group-rm", "block-group", "block-group-rm", "block-alias",
    "person-del", "near-match-forget",
)


class Refused(Exception):
    def __init__(self, code, lines):
        Exception.__init__(self, "; ".join(lines))
        self.code = code
        self.lines = lines


# -- normalisation ----------------------------------------------------------

def nk(text):
    """Comparison key: NFKC, whitespace removed, casefolded."""
    s = unicodedata.normalize("NFKC", str(text or ""))
    return "".join(s.split()).casefold()


def ws(text):
    return " ".join(str(text or "").split())


def ready(conn):
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' "
            "AND name='v_proper_noun';").fetchone() is not None
    except sqlite3.Error:
        return False


# -- the dictionary ---------------------------------------------------------

def surfaces(conn, include_gone=False):
    """[{kind, ref, surface, role, parent, status}] from v_proper_noun."""
    if not ready(conn):
        return []
    rows = conn.execute(
        "SELECT kind, ref_id, surface, surface_role, parent_ref, status "
        "FROM v_proper_noun WHERE surface IS NOT NULL;").fetchall()
    out = []
    for kind, ref, surf, role, parent, status in rows:
        if not include_gone and status != "active":
            continue
        if not ws(surf):
            continue
        out.append({"kind": kind, "ref": int(ref), "surface": ws(surf),
                    "role": role, "parent": parent, "status": status})
    return out


def display(conn, kind, ref):
    """The owner-facing name of one entity ('' when absent)."""
    q = {
        "person": "SELECT COALESCE(NULLIF(TRIM(display_name),''), name) "
                  "FROM persons WHERE id=?",
        "group": "SELECT COALESCE(NULLIF(TRIM(display_name),''), name) "
                 "FROM person_group WHERE id=?",
        "place": "SELECT name FROM place WHERE id=?",
        "sub_place": "SELECT name FROM sub_place WHERE id=?",
        "project": "SELECT title FROM projects WHERE id=?",
        "method": "SELECT name FROM pay_method WHERE id=?",
    }.get(kind)
    if q is None:
        return ""
    try:
        row = conn.execute(q, (int(ref),)).fetchone()
    except (sqlite3.Error, TypeError, ValueError):
        return ""
    return ws(row[0]) if row and row[0] else ""


def resolve(conn, text, kinds=None, include_gone=False, parent=None,
            all_refs=False):
    """Exact resolution of one surface.  -> [{kind, ref, display, match}]:
    the hits of the BEST precedence tier only, one per (kind, ref), lowest
    ref first inside a kind.  More than one kind in the list = a homonym the
    caller's field gate must split.  `parent` limits sub_place hits to the
    rooms of that place id.  `all_refs` keeps every same-kind tie (noun-context
    reports two persons sharing a name as AMBIGUOUS instead of picking one)."""
    key = nk(text)
    if not key:
        return []
    best, hits = None, {}
    for s in surfaces(conn, include_gone=include_gone):
        if kinds and s["kind"] not in kinds:
            continue
        if s["kind"] == "sub_place" and parent is not None \
                and s["parent"] != parent:
            continue
        if nk(s["surface"]) != key:
            continue
        rank = ROLE_RANK.get(s["role"], 9)
        if best is None or rank < best:
            best, hits = rank, {}
        if rank == best:
            k = (s["kind"], s["ref"])
            if k not in hits:
                hits[k] = s["role"]
    out = []
    for (kind, ref), role in sorted(hits.items(),
                                    key=lambda t: (KINDS.index(t[0][0]),
                                                   t[0][1])):
        if not all_refs and any(o["kind"] == kind for o in out):
            continue                    # tie inside a kind: lowest id wins
        out.append({"kind": kind, "ref": ref,
                    "display": display(conn, kind, ref), "match": role})
    return out


def _is_boundary(ch):
    if ch is None:
        return True
    cat = unicodedata.category(ch)
    return ch.isspace() or cat.startswith("P") or cat.startswith("S")


def scan(conn, utterance):
    """Every known name in `utterance` -> [{text, start, end, kind, ref,
    display, match, source:'scan'}] (homonyms: one row per kind).  Longest
    surface wins at a position; a match must start at a token start and end
    at a boundary or a josa followed by a boundary, so a name inside another
    word (a person's name inside a longer word) is not a match."""
    u = str(utterance or "")
    low = unicodedata.normalize("NFKC", u).casefold()
    if len(low) != len(u):
        low = u.casefold()
    surf = sorted({s["surface"] for s in surfaces(conn)},
                  key=lambda x: (-len(x), x))
    out, i, n = [], 0, len(u)
    while i < n:
        if not (i == 0 or _is_boundary(u[i - 1])) or _is_boundary(u[i]):
            i += 1
            continue
        hit = None
        for s in surf:
            sl = s.casefold()
            if not low.startswith(sl, i):
                continue
            j = i + len(sl)
            if j < n and not _is_boundary(u[j]):
                tail = u[j:]
                ok = False
                for jo in JOSA:
                    if tail.startswith(jo) and (j + len(jo) == n or
                                                _is_boundary(u[j + len(jo)])):
                        ok = True
                        break
                if not ok:
                    continue
            hit = (s, j)
            break
        if hit is None:
            i += 1
            continue
        s, j = hit
        for r in resolve(conn, s):
            out.append({"text": u[i:j], "start": i, "end": j,
                        "kind": r["kind"], "ref": r["ref"],
                        "display": r["display"], "match": r["match"],
                        "source": "scan"})
        i = j
    return out


# -- field gates ------------------------------------------------------------

def gate_kinds(conn, field):
    """Kinds the field accepts (field_kind_gate), in roster order.  An
    unknown field (or a pre-073 database) gates nothing: None."""
    try:
        rows = conn.execute("SELECT kind FROM field_kind_gate WHERE "
                            "field_token=?;", (field,)).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    have = set(r[0] for r in rows)
    return [k for k in KINDS if k in have]


def field_refusal_lines(K, conn, field, text, hit, eid=None, place_id=None):
    """The FIELD_KIND_REFUSED block for `hit` (a resolve() row) in `field`."""
    accepts = gate_kinds(conn, field) or []
    lines = ["FIELD_KIND_REFUSED\t%s\t%s\t%s:%d\taccepts=%s" % (
        field, ws(text), hit["kind"], hit["ref"], ",".join(accepts))]
    kind_label = _kind_label(K, hit["kind"])
    name = ws(text)
    josa = K._josa(name, "eun")
    if field == "appt.room":
        rooms = room_rows(conn, place_id) if place_id else []
        say = K._i18n("noun.field.refused.appt.room",
                      "'{name}' is registered as a {kind} ({display}), so it "
                      "cannot be a room. Which room is this booking?").format(
            name=name, eun=josa, kind=kind_label, display=hit["display"],
            ro=K._josa(hit["display"] or name, "ro"))
        if hit["kind"] in ("group", "person") and eid is not None:
            lines.append("NEXT\tparticipant\tkit_life.sh %s %s %s:%d" % (
                "appt-group" if hit["kind"] == "group" else "block-person",
                eid, hit["kind"], hit["ref"]))
        lines.append("SAY\t%s" % say)
        if rooms:
            choices = [("sub_place:%d" % r[0], r[1]) for r in rooms]
            lines.append("ROOM_ASK\t%s\t%s" % (
                eid if eid is not None else "-",
                ask_choices.choices_field(choices)))
            lines.append(ask_choices.options_line("ROOM_ASK",
                                                  eid if eid is not None
                                                  else "-", choices))
            lines.append(ask_choices.RELAY)
    else:
        say = K._i18n("noun.field.refused",
                      "'{name}' is registered as a {kind} ({display}); it "
                      "cannot fill this field.").format(
            name=name, eun=josa, kind=kind_label, display=hit["display"],
            ro=K._josa(hit["display"] or name, "ro"))
        lines.append("SAY\t%s" % say)
    lines.append(FIELD_RELAY)
    return lines


FIELD_RELAY = (
    "FIELD_RELAY Nothing was written. FIELD_KIND_REFUSED names a value of the "
    "wrong kind for that field. If the value was YOUR guess (read from a "
    "word, a screenshot, a suffix), move it to the field the NEXT line names "
    "and retry with the right value -- the owner sees no refusal. If the "
    "OWNER said it, relay the SAY line (and the ROOM_ASK buttons when "
    "printed). Never print these lines.")


def _kind_label(K, kind):
    fb = {"person": "person", "group": "group", "place": "place",
          "sub_place": "room", "project": "project", "method": "payment "
          "method", "none": "not a name"}
    return K._i18n("noun.kind.%s" % kind, fb.get(kind, kind))


# -- rooms ------------------------------------------------------------------

def room_rows(conn, place_id):
    try:
        return conn.execute("SELECT id, name, token FROM sub_place WHERE "
                            "place_id=? ORDER BY id;",
                            (int(place_id),)).fetchall()
    except (sqlite3.Error, TypeError, ValueError):
        return []


def _strip_room_noun(key):
    for rn in ROOM_NOUNS:
        if key.endswith(rn) and len(key) > len(rn):
            return key[:-len(rn)], rn
    return key, None


def room_signature(name):
    """'<ALNUM><room noun>' when the name is an ASCII/digit core + a trailing
    room noun (A2-room -> '<ALNUM>' + the noun); None otherwise (a Hangul-only
    word that merely ends in a room noun)."""
    core, noun = _strip_room_noun(nk(name))
    if noun is None or not core:
        return None
    if all(ch.isascii() and ch.isalnum() for ch in core):
        return "<ALNUM>" + noun
    return None


def room_match(conn, place_id, text):
    """The sub_place row of `place_id` that `text` names, or None: name /
    token equality, or the same core with the room noun dropped ("A2" ->
    A2-room).  The ASCII core keeps A2 != A3."""
    key = nk(text)
    if not key:
        return None
    core, _n = _strip_room_noun(key)
    for rid, name, tok in room_rows(conn, place_id):
        if key in (nk(name), nk(tok)):
            return (rid, name)
    for rid, name, tok in room_rows(conn, place_id):
        if core and _strip_room_noun(nk(name))[0] == core:
            return (rid, name)
    return None


def room_accrues(conn, place_id, text):
    """True when an unknown room may be registered without asking: the place
    already has a room and one sibling shares the new name's signature."""
    sig = room_signature(text)
    if sig is None:
        return False
    return any(room_signature(r[1]) == sig for r in room_rows(conn, place_id))


def block_room(conn, eid):
    """(sub_place id, name) of the booking's room, or None."""
    try:
        row = conn.execute(
            "SELECT sp.id, sp.name FROM block_attr ba JOIN sub_place sp ON "
            "CAST(sp.id AS TEXT) = ba.value WHERE ba.block_id=? AND "
            "ba.attr_token=? LIMIT 1;", (int(eid), ROOM_ATTR)).fetchone()
    except sqlite3.Error:
        return None
    return (row[0], row[1]) if row else None


def block_room_clear(conn, eid):
    """Remove the booking's room (caller commits).  -> the old name or None."""
    old = block_room(conn, eid)
    if old is not None:
        conn.execute("DELETE FROM block_attr WHERE block_id=? AND "
                     "attr_token=?;", (int(eid), ROOM_ATTR))
    return old[1] if old else None


def block_room_set(conn, eid, sub_id):
    """Write the booking's room (caller commits).  -> old name or None."""
    old = block_room(conn, eid)
    conn.execute("DELETE FROM block_attr WHERE block_id=? AND attr_token=?;",
                 (int(eid), ROOM_ATTR))
    conn.execute("INSERT INTO block_attr(block_id, attr_token, value, source) "
                 "VALUES (?,?,?,'user');", (int(eid), ROOM_ATTR,
                                            str(int(sub_id))))
    return old[1] if old else None


NEW_ROOM_IDS = set()      # rooms accrued by this process (card mark)


def apply_room(K, conn, eid, text):
    """The room= of appt-add / appt-upd.  Returns (lines, code): code 0 =
    written (or nothing to do), FIELD_KIND_CODE / PARENT_CODE = refused,
    nothing written.  The NEWEST owner evidence wins: an existing room is
    replaced without a question (REPLACED line), never kept."""
    lines = []
    text = ws(text)
    if not ready(conn):
        return ["ROOM_SKIPPED\tschema<73"], 0
    if not text:
        old = block_room_clear(conn, eid)
        conn.commit()
        if old:
            lines.append("ROOM_CLEARED\t%d\t%s" % (eid, old))
        return lines, 0
    row = conn.execute("SELECT place_id FROM block WHERE id=?;",
                       (int(eid),)).fetchone()
    place_id = row[0] if row else None
    # A value of another kind is refused BEFORE anything else: the founding
    # incident's band name never lands in a room.
    if place_id is not None and room_match(conn, place_id, text) is not None:
        other = []
    else:
        other = [h for h in resolve(conn, text)
                 if h["kind"] not in ("sub_place",)]
    if other:
        return field_refusal_lines(K, conn, "appt.room", text, other[0],
                                   eid=eid, place_id=place_id), \
            FIELD_KIND_CODE
    if place_id is None:
        return parent_ask_lines(K, conn, eid, text), PARENT_CODE
    hit = room_match(conn, place_id, text)
    if hit is None:
        if room_accrues(conn, place_id, text):
            sp = K.sub_place_add(conn, place_id, text)
            NEW_ROOM_IDS.add(int(sp["id"]))
            hit = (sp["id"], sp["name"])
            lines.append("ROOM_ADDED\t%d\t%d\t%s" % (place_id, sp["id"],
                                                     sp["name"]))
        else:
            ask = offer_ask(K, conn, text, "appt.room", place_id=place_id,
                            eid=eid)
            return lines + ask, 0
    old = block_room_set(conn, eid, hit[0])
    conn.commit()
    if old is not None and old != hit[1]:
        lines.append("REPLACED\troom\t%s\t%s" % (old, hit[1]))
    lines.append("ROOM_SET\t%d\t%d\t%s" % (eid, hit[0], hit[1]))
    return lines, 0


def parent_ask_lines(K, conn, eid, text):
    """PARENT_ASK: which place is room <text> in?  Buttons = places that
    already have rooms + the most recent appointment places, max 4, + 'another
    place'.  Nothing is written."""
    cands = []
    try:
        for (pid,) in conn.execute(
                "SELECT DISTINCT sp.place_id FROM sub_place sp JOIN place p "
                "ON p.id=sp.place_id WHERE p.status='active' "
                "ORDER BY sp.place_id;"):
            if pid not in cands:
                cands.append(pid)
        for (pid,) in conn.execute(
                "SELECT b.place_id FROM block b JOIN place p ON "
                "p.id=b.place_id WHERE p.status='active' AND p.is_base=0 "
                "AND b.place_id IS NOT NULL ORDER BY b.start_at DESC "
                "LIMIT 20;"):
            if pid not in cands:
                cands.append(pid)
    except sqlite3.Error:
        pass
    cands = cands[:4]
    choices = [("place:%d" % p, display(conn, "place", p)) for p in cands]
    choices.append(("place:other", K._i18n("noun.parent.other",
                                           "another place")))
    q = K._i18n("noun.parent.ask", "Which place is the room '{name}' in?"
                ).format(name=text)
    ref = "%s:%s" % (eid if eid is not None else "-", nk(text))
    return ["PARENT_ASK\t%s\t%s\t%s" % (ref, q,
                                        ask_choices.choices_field(choices)),
            ask_choices.options_line("PARENT_ASK", ref, choices),
            "NEXT\tplace:<id>\tkit_life.sh noun-type %s sub_place --parent "
            "<place id>; then appt-upd %s room=%s" % (
                ask_key(text), eid if eid is not None else "<id>", text),
            ask_choices.RELAY]


# -- the typing ask ---------------------------------------------------------

def ask_key(text):
    return nk(text)


def _local_today(K):
    try:
        tz = K._day_tz(None, K._card_tz_offset())
        return datetime.datetime.now(tz).date().isoformat()
    except Exception:
        return datetime.date.today().isoformat()


def daily_cap(conn):
    try:
        row = conn.execute("SELECT value FROM ledger_guard WHERE guard_key="
                           "'noun_ask_daily_cap' AND scope='global';"
                           ).fetchone()
    except sqlite3.Error:
        return 3
    if row is None or row[0] is None:
        return 3
    return int(row[0])


_DAY_KEY = "noun_ask_day"


def asks_today(K, conn):
    row = conn.execute("SELECT value FROM config WHERE key=?;",
                       (_DAY_KEY,)).fetchone()
    if not row or not row[0]:
        return 0
    day, _sep, n = str(row[0]).partition(" ")
    return int(n) if day == _local_today(K) and n.isdigit() else 0


def _spend_ask(K, conn):
    n = asks_today(K, conn) + 1
    conn.execute("INSERT INTO config(key, value) VALUES (?, ?) ON CONFLICT"
                 "(key) DO UPDATE SET value=excluded.value;",
                 (_DAY_KEY, "%s %d" % (_local_today(K), n)))


def ask_row(conn, text):
    try:
        return conn.execute(
            "SELECT query_key, query_text, state, offered_n, field_token, "
            "said, updated_at FROM noun_ask WHERE query_key=?;",
            (ask_key(text),)).fetchone()
    except sqlite3.Error:
        return None


def _recent(ts, minutes):
    try:
        t = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return False
    now = datetime.datetime.utcnow()
    return (now - t).total_seconds() < minutes * 60


def _now(K):
    return K.now_utc()


def type_options(K, conn, field, place_id=None):
    """[(key, label)] for TYPE_ASK: the kinds the field accepts first, then
    the rest of the roster, then not-a-name.  sub_place is offered only when
    a place is known (the room needs a parent); project only when the field
    takes one."""
    accepts = gate_kinds(conn, field) or []
    roster = ["person", "group", "place"]
    if place_id is not None or "sub_place" in accepts:
        roster.append("sub_place")
    if "project" in accepts:
        roster.append("project")
    order = [k for k in accepts if k in roster] + \
        [k for k in roster if k not in accepts]
    if "sub_place" in order and place_id is None:
        order.remove("sub_place")
    out = []
    pname = display(conn, "place", place_id) if place_id is not None else ""
    for k in order:
        if k == "sub_place":
            out.append(("sub_place", K._i18n(
                "noun.kind.sub_place.of", "a room of {place}").format(
                place=pname)))
        elif k == "place" and place_id is not None:
            out.append(("place", K._i18n("noun.kind.place.other",
                                         "another place")))
        else:
            out.append((k, _kind_label(K, k)))
    out.append(("not_a_name", _kind_label(K, "none")))
    return out


def offer_ask(K, conn, text, field=None, place_id=None, eid=None,
              commit=True):
    """The TYPE_ASK lines for an unknown name, honoring the life-long
    states, decay and the daily cap.  Writes only the noun_ask offer
    bookkeeping.  [] when the name must not be asked (not_a_name /
    auto_declined); NOUN_ASK_CAPPED when the day's asks are spent."""
    text = ws(text)
    key = ask_key(text)
    if not key or not ready(conn):
        return []
    row = ask_row(conn, text)
    if row is not None and row[2] in ("not_a_name", "auto_declined"):
        return ["NOUN_SKIP\t%s\t%s" % (row[2], text)]
    same_turn = row is not None and _recent(row[6], REOFFER_SAME_TURN_MIN)
    if row is not None and row[3] >= DECAY_K and not same_turn:
        conn.execute("UPDATE noun_ask SET state='auto_declined', "
                     "updated_at=? WHERE query_key=?;", (_now(K), key))
        if commit:
            conn.commit()
        return ["NOUN_SKIP\tauto_declined\t%s" % text]
    if not same_turn:
        if asks_today(K, conn) >= daily_cap(conn):
            return ["NOUN_ASK_CAPPED\t%s\t%s" % (text, K._i18n(
                "noun.ask.capped", "asked enough names today; kept as "
                "typed"))]
        _spend_ask(K, conn)
        if row is None:
            conn.execute("INSERT INTO noun_ask(query_key, query_text, state, "
                         "offered_n, field_token, updated_at) VALUES "
                         "(?,?,'offered',1,?,?);",
                         (key, text, field, _now(K)))
        else:
            conn.execute("UPDATE noun_ask SET offered_n=offered_n+1, "
                         "field_token=COALESCE(?, field_token), updated_at=? "
                         "WHERE query_key=?;", (field, _now(K), key))
        if commit:
            conn.commit()
    choices = type_options(K, conn, field, place_id=place_id)
    josa = K._josa(text, "eun")
    if place_id is not None and any(k == "sub_place" for k, _l in choices):
        q = K._i18n("noun.type.ask.room",
                    "'{name}' is a new name. Is it a room of {place}?"
                    ).format(name=text, eun=josa,
                             place=display(conn, "place", place_id))
    else:
        q = K._i18n("noun.type.ask", "'{name}' is a new name. What should "
                    "I remember it as?").format(name=text, eun=josa)
    lines = ["TYPE_ASK\t%s\t%s\t%s\t%s" % (
        key, text, field or "-", ask_choices.choices_field(choices)),
        "ASK\t%s" % q,
        ask_choices.options_line("TYPE_ASK", key, choices, parent=place_id)]
    for k, _l in choices:
        nxt = "kit_life.sh noun-type %s %s" % (key, k)
        if k == "sub_place" and place_id is not None:
            nxt += " --parent %d" % place_id
        lines.append("NEXT\t%s\t%s" % (k, nxt))
    lines.append(TYPE_RELAY)
    # TYPE_RELAY includes the menu contract; keep this block within 1 KB.
    return lines


TYPE_RELAY = (
    "TYPE_RELAY Search agent memory. Known -> no type menu; one-tap confirmation. "
    "Only 0 hits (done; unavailable!=0) -> ASK+ASK_OPTIONS verbatim. "
    "Answer: NEXT/retry; else no write. "
    "Menu: no PLACE_ASK or other question; await menu-free reply. Hide codes.")


def tag(K, conn, utterance, spans=(), field=None, place_id=None):
    """noun-tag: resolve-before-fill.  -> dict {spans, ask, unknown}."""
    out = {"spans": [], "ask": None, "unknown": [], "lines": []}
    covered = set()
    accepts = gate_kinds(conn, field) if field else None
    for s in scan(conn, utterance):
        out["spans"].append(s)
        covered.add(nk(s["text"]))
    asked = False
    for sp in spans:
        text = ws(sp)
        if not text:
            continue
        hits = resolve(conn, text,
                       parent=place_id if place_id is not None else None)
        if field == "appt.room" and place_id is not None:
            rm = room_match(conn, place_id, text)
            if rm is not None:
                hits = [{"kind": "sub_place", "ref": rm[0], "display": rm[1],
                         "match": "token"}]
        if hits:
            for h in hits:
                d = dict(h, text=text, start=None, end=None, source="agent")
                if accepts is not None:
                    d["field_ok"] = h["kind"] in accepts
                out["spans"].append(d)
            if accepts is not None:
                fit = [h for h in hits if h["kind"] in accepts]
                if len(fit) > 1 and not asked:
                    out["ask"] = ambiguous_ask(K, conn, text, fit)
                    asked = True
            elif len(hits) > 1 and not asked:
                out["ask"] = ambiguous_ask(K, conn, text, hits)
                asked = True
            continue
        near = []
        for dom in ("place", "person", "group"):
            try:
                c = K.near_candidates(conn, dom, text)
            except Exception:
                c = []
            near += [dict(x, domain=dom) for x in c]
        if near:
            out["spans"].append({"text": text, "kind": None, "ref": None,
                                 "display": None, "match": "near",
                                 "source": "agent",
                                 "near": [{"domain": n["domain"],
                                           "ref": n["ref"],
                                           "name": n["name"]}
                                          for n in near]})
            continue
        row = ask_row(conn, text)
        if row is not None and row[2] in ("not_a_name", "auto_declined"):
            out["spans"].append({"text": text, "kind": None, "ref": None,
                                 "display": None, "match": "skip",
                                 "source": "agent", "state": row[2]})
            out["lines"].append("NOUN_SKIP\t%s\t%s" % (row[2], text))
            continue
        out["unknown"].append(text)
        out["spans"].append({"text": text, "kind": None, "ref": None,
                             "display": None, "match": "unknown",
                             "source": "agent"})
        if field == "appt.room" and place_id is not None \
                and room_accrues(conn, place_id, text):
            out["spans"][-1]["room_accrues"] = True
            continue
        if not asked:
            lines = offer_ask(K, conn, text, field, place_id=place_id)
            if lines:
                out["lines"] += lines
                if lines[0].startswith("TYPE_ASK"):
                    f = lines[0].split("\t")
                    out["ask"] = {"type": "TYPE_ASK", "id": f[1],
                                  "text": f[2], "field": field,
                                  "question": lines[1].split("\t", 1)[1],
                                  "choices": f[4][len("choices="):],
                                  "options": lines[2].split("\t")[3]}
                    asked = True
    return out


def ambiguous_ask(K, conn, text, hits):
    choices = [("%s:%d" % (h["kind"], h["ref"]), "%s (%s)" % (
        h["display"], _kind_label(K, h["kind"]))) for h in hits[:3]]
    choices.append(("none", K._i18n("noun.kind.neither", "neither")))
    q = K._i18n("noun.ambiguous.ask", "'{name}' -- which one?").format(
        name=text)
    return {"type": "AMBIGUOUS_KIND_ASK", "id": ask_key(text), "text": text,
            "question": q, "choices": ask_choices.choices_field(
                choices)[len("choices="):],
            "options": ask_choices.options_marker(choices)}


# -- registration (the answer to TYPE_ASK) ----------------------------------

def cross_kind(conn, name, kind):
    """Exact hits of `name` in OTHER kinds (for a create path)."""
    return [h for h in resolve(conn, name) if h["kind"] != kind]


def group_add(conn, name, display_name=None, aliases=None):
    name = ws(name)
    if not name:
        raise ValueError("group name required")
    cur = conn.execute(
        "INSERT INTO person_group(name, display_name, aliases) VALUES "
        "(?,?,?);", (name, ws(display_name) or None,
                     json.dumps([ws(a) for a in aliases if ws(a)],
                                ensure_ascii=False) if aliases else None))
    return cur.lastrowid


def _json_aliases(blob):
    try:
        arr = json.loads(blob) if blob else []
    except (TypeError, ValueError):
        return []
    return [a for a in arr if isinstance(a, str)] if isinstance(arr, list) \
        else []


def add_alias(K, conn, kind, ref, alias):
    """Teach `alias` to an existing entity (caller commits)."""
    alias = ws(alias)
    if kind == "person":
        row = conn.execute("SELECT aliases FROM persons WHERE id=?;",
                           (ref,)).fetchone()
        if row is None:
            raise ValueError("no person %s" % ref)
        have = [a.strip() for a in (row[0] or "").split(",") if a.strip()]
        if alias not in have:
            have.append(alias)
        conn.execute("UPDATE persons SET aliases=? WHERE id=?;",
                     (",".join(have), ref))
    elif kind in ("group", "place", "method"):
        table = {"group": "person_group", "place": "place",
                 "method": "pay_method"}[kind]
        row = conn.execute("SELECT aliases FROM %s WHERE id=?;" % table,
                           (ref,)).fetchone()
        if row is None:
            raise ValueError("no %s %s" % (kind, ref))
        have = _json_aliases(row[0])
        if alias not in have:
            have.append(alias)
        conn.execute("UPDATE %s SET aliases=? WHERE id=?;" % table,
                     (json.dumps(have, ensure_ascii=False), ref))
    else:
        raise ValueError("kind %s takes no alias" % kind)


def parse_ref(token):
    """'group:3' -> ('group', 3); a bare id -> (None, id)."""
    s = str(token or "")
    if ":" in s:
        k, _c, v = s.partition(":")
        if k in KINDS and v.isdigit():
            return k, int(v)
    if s.isdigit():
        return None, int(s)
    return None, None


def resolve_group(conn, token, include_gone=False):
    """A group by 'group:<id>', id, name, display_name or alias -> row."""
    k, v = parse_ref(token)
    if v is not None and k in (None, "group"):
        row = conn.execute("SELECT id, name, display_name, aliases, status "
                           "FROM person_group WHERE id=?;", (v,)).fetchone()
        if row is not None:
            return row
    hits = resolve(conn, token, kinds=("group",), include_gone=include_gone)
    if not hits:
        return None
    return conn.execute("SELECT id, name, display_name, aliases, status "
                        "FROM person_group WHERE id=?;",
                        (hits[0]["ref"],)).fetchone()


def group_line(row):
    gid, name, disp, aliases, status = row
    return "GROUP\t%d\t%s\t%s\t%s\t%s" % (
        gid, name, disp or "", "|".join(_json_aliases(aliases)), status)


# -- participants -----------------------------------------------------------

def event_groups(conn, eid):
    """[(group_id, display)] of a block, by group id."""
    try:
        return [(g, ws(d)) for g, d in conn.execute(
            "SELECT g.id, COALESCE(NULLIF(TRIM(g.display_name),''), g.name) "
            "FROM event_group eg JOIN person_group g ON g.id=eg.group_id "
            "WHERE eg.event_id=? ORDER BY g.id;", (int(eid),)).fetchall()]
    except sqlite3.Error:
        return []


# -- context-first read (noun-context) --------------------------------------
# Owner incidents 2026-10-08: a capture naming two registered persons ->
# the agent asked "which area?" although a dated gathering with exactly them
# was on the calendar; "the housewarming gift again" -> the agent asked who it
# was for although today's housewarming was on the calendar.  The kit held the
# answer both times; nothing read it before the question.
#
# noun-context is that read, and ONLY a read: it is a sibling of noun-tag,
# not an extension of it, because noun-tag spends state (TYPE_ASK offers,
# noun_ask rows, the daily ask cap) and a context lookup must be free to run
# before every question without touching any of that.  Names come from the
# same scan() / resolve() noun-tag uses (073 registry), so the two never
# disagree about what a name is.
#
# Matching, all deterministic:
#   names      every registered surface in the text (scan) + exact --span
#              hits; a --span that is only NEAR a name is reported (NEAR),
#              not resolved, never asked here; its events carry via=near:.
#   title      an event title word (>= 2 chars) that occurs in the text, or
#              a text word (>= 2 chars, trailing particle stripped) that a
#              title word starts with.
#   events     appointments / stays not cancelled, overlapping [local today,
#              local today + days]; linked to a person (event_persons), a
#              group (event_group), a place (block place) or a title word.
#   persons    (2026-10-09, ag gift incident: memory said "expecting" two
#              weeks after the kit recorded the child's birth) every resolved
#              person's record fields + directional links (persons_relations,
#              role = what the OTHER person is) + recent births (own or a
#              child's full birth date 0..PERSON_RECENT_DAYS days before the
#              window start -- a date, never an anniversary).  Printed even
#              with no EVENT: no EVENT does not mean no facts.  A spoken name
#              two persons share is AMBIGUOUS: no facts until it is resolved.
CONTEXT_KINDS = ("appointment", "presence")
CONTEXT_DAYS = 14
CONTEXT_MIN_WORD = 2
NOUN_MEMORY_RELAY = (
    "NOUN_MEMORY_RELAY Before noun-tag / a typing ask, search agent memory for "
    "each unresolved span (including case variants). Kit absence is not a "
    "memory search. Known -> suppress the type menu; propose the remembered "
    "kind and facts for one-tap confirmation. After confirmation, resolve "
    "then group-add/person-add as needed; group-member <group> <person>... "
    "lands members, preserving existing groups; then retry noun-tag. Kit facts "
    "win conflicts. Only a completed 0-hit search permits a type ask; "
    "unavailable search is not 0 hits. Any [[OPTIONS]] -> only its question; "
    "defer other questions, with PLACE_ASK on a menu-free reply. Never print this line.")
PERSON_RECENT_DAYS = 60
PERSON_FIELDS = ("gender", "residence", "job", "contact")
PERSON_RELAY = (
    "PERSON_RELAY Kit person facts (PERSON / PERSON_REL / PERSON_EVENT) "
    "override agent memory. A remembered future expectation whose date has "
    "passed is stale: never assert it; read the kit record or ask once if "
    "still unknown. A recorded birth is past, never an upcoming delivery. "
    "Never print these lines.")


def _words(text):
    import re
    return [w for w in re.findall(r"[^\W_]+", str(text or ""))
            if len(w) >= CONTEXT_MIN_WORD]


def _strip_josa(word):
    for jo in JOSA:
        if word.endswith(jo) and len(word) - len(jo) >= CONTEXT_MIN_WORD:
            return word[:-len(jo)]
    return word


def _title_hits(text, title):
    """Title words of `title` the text points at, in title order."""
    key = nk(text)
    toks = {_strip_josa(w).casefold() for w in _words(text)}
    out = []
    for w in _words(title):
        wk = w.casefold()
        if wk in out:
            continue
        if nk(w) in key or any(wk.startswith(t) for t in toks):
            out.append(wk)
    return out


TITLE_ALIASES = "title_aliases"


def block_aliases(conn, eid):
    """Owner shorthand on the existing block attribute store; no new entity kind."""
    row = conn.execute(
        "SELECT value FROM block_attr WHERE block_id=? AND attr_token=? "
        "AND source='user';", (int(eid), TITLE_ALIASES)).fetchone()
    aliases = json.loads(row[0]) if row else []
    if not isinstance(aliases, list) or not all(
            isinstance(a, str) and ws(a) for a in aliases):
        raise ValueError("invalid title_aliases attribute")
    return aliases


def block_alias_add(K, conn, eid, aliases):
    """Merge aliases in the caller's transaction. Block identity survives edits."""
    row = conn.execute(
        "SELECT title FROM timeblock WHERE id=? AND kind IN "
        "('appointment','task','presence') AND block_class IS NULL;",
        (int(eid),)).fetchone()
    if row is None:
        raise ValueError(K._i18n("schedule.alias.block_required",
                                "A title alias needs an existing schedule item."))
    if not aliases or not all(isinstance(a, str) and ws(a) for a in aliases):
        raise ValueError(K._i18n("schedule.alias.empty",
                                "Enter the name you used for this item."))
    have = block_aliases(conn, eid)
    keys = {nk(a) for a in have}
    for alias in aliases:
        if nk(alias) not in keys:
            have.append(ws(alias))
            keys.add(nk(alias))
    conn.execute(
        "INSERT OR REPLACE INTO block_attr(block_id,attr_token,value,source) "
        "VALUES (?,?,?,'user');",
        (int(eid), TITLE_ALIASES, json.dumps(have, ensure_ascii=False)))
    return {"id": int(eid), "title": row[0], "aliases": have}


def _alias_hits(text, aliases, spans=()):
    """Exact surfaces with the registry's token/josa boundary rules."""
    low = unicodedata.normalize("NFKC", ws(text)).casefold()
    explicit = {nk(s) for s in spans}
    hits = []
    for alias in aliases:
        surface = unicodedata.normalize("NFKC", ws(alias)).casefold()
        if nk(alias) in explicit:
            hits.append(alias)
            continue
        pos = low.find(surface)
        while pos >= 0:
            end = pos + len(surface)
            tail = low[end:]
            if (pos == 0 or _is_boundary(low[pos - 1])) and (
                    not tail or _is_boundary(tail[0]) or any(
                        tail.startswith(jo) and (len(tail) == len(jo)
                        or _is_boundary(tail[len(jo)])) for jo in JOSA)):
                hits.append(alias)
                break
            pos = low.find(surface, pos + 1)
    return hits


def _context_window(K, start=None, days=CONTEXT_DAYS):
    """(from_date, to_date, from_utc, to_utc): local [start 00:00, start +
    days 24:00) as canonical UTC strings."""
    try:
        tz = K._day_tz(None, K._card_tz_offset())
    except Exception:
        tz = datetime.timezone.utc
    if start is None:
        d0 = datetime.datetime.now(tz).date()
    else:
        d0 = datetime.date.fromisoformat(start)
    d1 = d0 + datetime.timedelta(days=days)
    utc = datetime.timezone.utc

    def edge(d):
        return (datetime.datetime(d.year, d.month, d.day, tzinfo=tz)
                .astimezone(utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return (d0.isoformat(), d1.isoformat(), edge(d0),
            edge(d1 + datetime.timedelta(days=1)))


def _event_party(conn, eid):
    out = []
    try:
        out = [ws(d) for (d,) in conn.execute(
            "SELECT COALESCE(NULLIF(TRIM(p.display_name),''), p.name) FROM "
            "event_persons ep JOIN persons p ON p.id=ep.person_id WHERE "
            "ep.event_id=? ORDER BY p.id;", (eid,)).fetchall()]
    except sqlite3.Error:
        pass
    return out + [d for _g, d in event_groups(conn, eid)]


def _event_local(K, start_at, sched, tz):
    try:
        s = K._render_local_col(start_at, sched, tz or K.TRAVEL_DISPLAY_TZ)
    except Exception:
        return start_at or ""
    return s if sched == "all_day" else s[:16]


def context(K, conn, text, spans=(), start=None, days=CONTEXT_DAYS):
    """noun-context: names in `text` + the events they point at + the
    named persons' facts.  -> dict {names, near, ambiguous, events,
    persons, window}.  Read-only."""
    names, seen, ambiguous = [], set(), []

    def ambiguous_add(t, cands):
        if not any(a["text"] == t for a in ambiguous):
            ambiguous.append({"text": t, "candidates": cands})

    for s in scan(conn, text):
        if s["kind"] == "person":
            cands = resolve(conn, s["text"], kinds=("person",),
                            all_refs=True)
            if len(cands) > 1:
                ambiguous_add(s["text"], cands)
                continue
        if (s["kind"], s["ref"]) not in seen:
            seen.add((s["kind"], s["ref"]))
            names.append(dict(s))
    near = []
    for sp in spans:
        t = ws(sp)
        if not t:
            continue
        hits = resolve(conn, t)
        person_hits = resolve(conn, t, kinds=("person",), all_refs=True)
        if len(person_hits) > 1:
            ambiguous_add(t, person_hits)
            hits = [h for h in hits if h["kind"] != "person"]
        for h in hits:
            if (h["kind"], h["ref"]) not in seen:
                seen.add((h["kind"], h["ref"]))
                names.append(dict(h, text=t, source="agent"))
        if hits or len(person_hits) > 1:
            continue
        cands = []
        for dom in ("person", "group", "place"):
            try:
                cands += [(dom, c["ref"], c["name"])
                          for c in K.near_candidates(conn, dom, t)]
            except Exception:
                pass
        if cands:
            near.append({"text": t, "near": cands})
    d0, d1, lo, hi = _context_window(K, start, days)
    marks = ",".join("?" * len(CONTEXT_KINDS))
    rows = conn.execute(
        "SELECT id, kind, start_at, end_at, schedule_kind, display_tz, title, "
        "place_id, location FROM timeblock WHERE kind IN (" + marks + ") "
        "AND start_at IS NOT NULL AND start_at < ? "
        "AND COALESCE(end_at, start_at) >= ? "
        "AND (settled_outcome IS NULL OR settled_outcome <> 'abandoned') "
        "ORDER BY start_at, id;", CONTEXT_KINDS + (hi, lo)).fetchall()
    persons = {n["ref"] for n in names if n["kind"] == "person"}
    groups = {n["ref"] for n in names if n["kind"] == "group"}
    places = {n["ref"] for n in names if n["kind"] == "place"}
    near_refs = {(d, r) for n in near for d, r, _nm in n["near"]}
    aliases_by_id = {eid: json.loads(value) for eid, value in conn.execute(
        "SELECT block_id,value FROM block_attr WHERE attr_token=? "
        "AND source='user';", (TITLE_ALIASES,))}
    events = []
    for eid, kind, sa, _ea, sk, tz, title, pid, loc in rows:
        via = []
        try:
            ppl = [p for (p,) in conn.execute(
                "SELECT person_id FROM event_persons WHERE event_id=? "
                "ORDER BY person_id;", (eid,)).fetchall()]
        except sqlite3.Error:
            ppl = []
        grp = [g for g, _d in event_groups(conn, eid)]
        via += ["person:%d" % p for p in ppl if p in persons]
        via += ["group:%d" % g for g in grp if g in groups]
        if pid in places:
            via.append("place:%d" % pid)
        # a NEAR span is not resolved, but the event it would point at is
        # shown, marked near: -- the owner's "which one" is answered by the
        # calendar, not by a separate question
        via += ["near:person:%d" % p for p in ppl
                if ("person", p) in near_refs]
        via += ["near:group:%d" % g for g in grp if ("group", g) in near_refs]
        if ("place", pid) in near_refs:
            via.append("near:place:%d" % pid)
        via += ["title:%s" % w for w in _title_hits(text, title)]
        via += ["alias:%s" % a for a in _alias_hits(
            text, aliases_by_id.get(eid, []), spans)]
        if not via:
            continue
        where = (display(conn, "place", pid) if pid is not None else "") \
            or ws(loc)
        events.append({"id": eid, "kind": kind,
                       "when": _event_local(K, sa, sk, tz),
                       "title": ws(title), "place": where,
                       "party": _event_party(conn, eid), "via": via})
    facts = [f for f in (person_facts(K, conn, p, d0)
                         for p in sorted(persons)) if f]
    return {"names": names, "near": near, "ambiguous": ambiguous,
            "events": events, "persons": facts, "window": [d0, d1]}


def person_facts(K, conn, pid, as_of):
    """One person's record fields + directional links + recent births as of
    the local date `as_of`.  -> dict, or None when the row is gone."""
    row = conn.execute(
        "SELECT name, COALESCE(NULLIF(TRIM(display_name),''), name), "
        "relation, birthday, gender, residence, job, contact "
        "FROM persons WHERE id=?;", (int(pid),)).fetchone()
    if row is None:
        return None
    out = dict(zip(("name", "display", "relation", "birthday", "gender",
                    "residence", "job", "contact"), row))
    out.update(id=int(pid), relations=[], recent_events=[])
    today = datetime.date.fromisoformat(as_of)

    def birth(kind, oid, name, birthday):
        # Only a full birth date establishes a recent birth. Partial years
        # and annual month/day birthdays must never become newborn events.
        try:
            born = datetime.date.fromisoformat(str(birthday))
        except (TypeError, ValueError):
            return
        if 0 <= (today - born).days <= PERSON_RECENT_DAYS:
            out["recent_events"].append({"kind": kind,
                                         "date": born.isoformat(),
                                         "person_id": oid, "name": ws(name)})

    birth("born", int(pid), out["name"], out["birthday"])
    for rid, oid, oname, role, note in K.person_relations(conn, pid):
        b = conn.execute("SELECT birthday FROM persons WHERE id=?;",
                         (oid,)).fetchone()
        b = b[0] if b else None
        out["relations"].append({"id": rid, "person_id": oid,
                                 "name": ws(oname), "role": role,
                                 "note": note, "birthday": b})
        if role == "child":
            birth("child_born", oid, oname, b)
    return out


def person_lines(p):
    """Machine lines of one person_facts() dict (every line keyed by id)."""
    lines = ["PERSON\t%d\t%s\trelation=%s\tbirthday=%s" % (
        p["id"], ws(p["name"]), ws(p["relation"]), ws(p["birthday"]))]
    for f in PERSON_FIELDS:
        if p[f]:
            lines.append("PERSON_FACT\t%d\t%s\t%s" % (p["id"], f, ws(p[f])))
    for r in p["relations"]:
        lines.append("PERSON_REL\t%d\t%s\t%d\t%s\tbirthday=%s\tnote=%s" % (
            p["id"], r["role"], r["person_id"], r["name"],
            ws(r["birthday"]), ws(r["note"])))
    for e in p["recent_events"]:
        lines.append("PERSON_EVENT\t%d\t%s\t%s\t%d\t%s\t%s -> %s%s born %s"
                     % (p["id"], e["kind"], e["date"], e["person_id"],
                        e["name"], ws(p["name"]),
                        "child " if e["kind"] == "child_born" else "",
                        e["name"], e["date"]))
    return lines


# -- CLI --------------------------------------------------------------------

def _usage(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def _pop(argv, flag, has_value=True):
    out, val, i = [], None, 0
    found = False
    while i < len(argv):
        a = argv[i]
        if a == flag:
            found = True
            if has_value:
                if i + 1 >= len(argv):
                    _usage("%s needs a value" % flag)
                val = argv[i + 1]
                i += 2
                continue
            i += 1
            continue
        out.append(a)
        i += 1
    return out, (val if has_value else found)


def _pop_all(argv, flag):
    out, vals, i = [], [], 0
    while i < len(argv):
        if argv[i] == flag:
            if i + 1 >= len(argv):
                _usage("%s needs a value" % flag)
            vals.append(argv[i + 1])
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out, vals


def _place_ref(K, conn, token):
    if token is None:
        return None
    k, v = parse_ref(token)
    if v is not None and k in (None, "place"):
        if conn.execute("SELECT 1 FROM place WHERE id=?;", (v,)).fetchone():
            return v
    p = K._resolve_location_place(conn, token)
    if p is None:
        _usage("no place %r" % token)
    return p["id"]


def read_conn(K):
    """A connection that cannot write (noun-context, entity_facts): no
    get_conn() pragmas, no migration, query_only."""
    import os
    if not os.path.isfile(K.DB_PATH):
        print("kit_life.db missing (%s)" % K.DB_PATH, file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(K.DB_PATH, timeout=5.0)
    conn.execute("PRAGMA query_only=ON;")
    return conn


def cli(K, verb, argv):
    conn = read_conn(K) if verb == "noun-context" else K.get_conn()
    try:
        if not ready(conn):
            print("SCHEMA_NOT_READY\tthe proper-noun registry needs "
                  "user_version >= %d" % SCHEMA_VERSION, file=sys.stderr)
            sys.exit(1)
        fn = globals()["_cli_" + verb.replace("-", "_")]
        return fn(K, conn, list(argv))
    except Refused as e:
        if conn.in_transaction:
            conn.rollback()
        for line in e.lines:
            print(line)
        sys.exit(e.code)
    finally:
        conn.close()


def _cli_block_alias(K, conn, argv):
    if len(argv) < 2 or not str(argv[0]).isdigit():
        _usage("usage: kit_life.sh block-alias <block_id> <alias>...")
    try:
        with conn:
            result = block_alias_add(K, conn, int(argv[0]), argv[1:])
    except ValueError as exc:
        _usage(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _cli_noun_tag(K, conn, argv):
    argv, spans = _pop_all(argv, "--span")
    argv, field = _pop(argv, "--field")
    argv, place = _pop(argv, "--place")
    argv, as_json = _pop(argv, "--json", has_value=False)
    if len(argv) != 1:
        _usage("usage: kit_life.sh noun-tag \"<utterance>\" [--span <text>]... "
               "[--field <field_token>] [--place <place>] [--json]")
    place_id = _place_ref(K, conn, place) if place else None
    res = tag(K, conn, argv[0], spans=spans, field=field, place_id=place_id)
    if as_json:
        d = {"spans": res["spans"], "ask": res["ask"],
             "unknown": res["unknown"]}
        print(json.dumps(d, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")))
        return
    for s in res["spans"]:
        print("SPAN\t%s\t%s\t%s\t%s\t%s\t%s" % (
            s["text"], s["kind"] or "-",
            s["ref"] if s["ref"] is not None else "-", s["display"] or "",
            s["match"], s["source"]))
    for line in res["lines"]:
        print(line)
    a = res["ask"]
    if a and a["type"] == "AMBIGUOUS_KIND_ASK":
        print("AMBIGUOUS_KIND_ASK\t%s\t%s\tchoices=%s" % (a["id"], a["text"],
                                                        a["choices"]))
        print("ASK\t%s" % a["question"])
        pairs = [part.split('=',1) for part in a['choices'].split('|')]
        print(ask_choices.options_line('AMBIGUOUS_KIND_ASK', a['id'], pairs))
        print(ask_choices.RELAY)


def _cli_noun_context(K, conn, argv):
    argv, spans = _pop_all(argv, "--span")
    argv, days = _pop(argv, "--days")
    argv, start = _pop(argv, "--from")
    argv, as_json = _pop(argv, "--json", has_value=False)
    usage = ("usage: kit_life.sh noun-context \"<text>\" [--span <text>]..."
             " [--days <n>] [--from YYYY-MM-DD] [--json]")
    if len(argv) != 1 or (days is not None and not str(days).isdigit()):
        _usage(usage)
    if days is not None and int(days) > 3660:
        _usage(usage)
    try:
        _context_window(K, start, int(days) if days is not None
                        else CONTEXT_DAYS)
    except (ValueError, OverflowError):
        _usage(usage)
    res = context(K, conn, argv[0], spans=spans, start=start,
                  days=int(days) if days is not None else CONTEXT_DAYS)
    if as_json:
        print(json.dumps({"names": [{k: n.get(k) for k in (
            "kind", "ref", "display", "text", "match", "source")}
            for n in res["names"]], "near": res["near"],
            "ambiguous": res["ambiguous"], "events": res["events"],
            "persons": res["persons"], "window": res["window"]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return
    for n in res["names"]:
        print("NAME\t%s\t%d\t%s\t%s\t%s" % (
            n["kind"], n["ref"], n["display"] or "", ws(n["text"]),
            n["source"]))
    for a in res["ambiguous"]:
        print("AMBIGUOUS\tperson\t%s\t%s" % (ws(a["text"]), "|".join(
            "%d:%s" % (c["ref"], ws(c["display"])) for c in a["candidates"])))
    for p in res["persons"]:
        for line in person_lines(p):
            print(line)
    for n in res["near"]:
        print("NEAR\t%s\t%s" % (n["text"], "|".join(
            "%s:%d:%s" % (d, r, ws(nm)) for d, r, nm in n["near"])))
    for e in res["events"]:
        print("EVENT\t%d\t%s\t%s\t%s\t%s\t%s\t%s" % (
            e["id"], e["kind"], e["when"], e["title"], e["place"],
            "|".join(e["party"]), ",".join(e["via"])))
    print("CONTEXT\tnames=%d\tevents=%d\tfrom=%s\tto=%s\tpersons=%d" % (
        len(res["names"]), len(res["events"]), res["window"][0],
        res["window"][1], len(res["persons"])))
    print(NOUN_MEMORY_RELAY)
    if res["events"]:
        print(K.APPT_CONTEXT_RELAY)
    if res["persons"]:
        print(PERSON_RELAY)


def _cli_noun_type(K, conn, argv):
    argv, parent = _pop(argv, "--parent")
    argv, alias_of = _pop(argv, "--alias-of")
    argv, said = _pop(argv, "--said")
    if len(argv) != 2 or argv[1] not in ASK_KINDS + ("not_a_name",):
        _usage("usage: kit_life.sh noun-type <ask_id|text> <%s|not_a_name> "
               "[--parent <place>] [--alias-of <kind>:<ref>] "
               "[--said \"<owner words>\"]" % "|".join(ASK_KINDS))
    key, kind = argv
    row = conn.execute("SELECT query_text FROM noun_ask WHERE query_key=?;",
                       (ask_key(key),)).fetchone()
    text = ws(row[0]) if row else ws(key)
    now = K.now_utc()
    if kind == "not_a_name":
        conn.execute(
            "INSERT INTO noun_ask(query_key, query_text, state, offered_n, "
            "said, updated_at) VALUES (?,?,'not_a_name',0,?,?) ON CONFLICT"
            "(query_key) DO UPDATE SET state='not_a_name', said=excluded.said,"
            " updated_at=excluded.updated_at;", (ask_key(text), text, said,
                                                 now))
        conn.commit()
        print("NOT_A_NAME\t%s" % text)
        return
    if alias_of:
        ak, ref = parse_ref(alias_of)
        if ak is None or ref is None:
            _usage("--alias-of wants <kind>:<id>")
        if ak != kind:
            _usage("--alias-of %s does not match kind %s" % (alias_of, kind))
        try:
            add_alias(K, conn, ak, ref, text)
        except ValueError as e:
            _usage(str(e))
        conn.execute("DELETE FROM noun_ask WHERE query_key=?;",
                     (ask_key(text),))
        conn.commit()
        print("TYPED\t%s:%d\t%s\talias=%s" % (ak, ref,
                                              display(conn, ak, ref), text))
        return
    hits = resolve(conn, text, kinds=(kind,))
    if hits:
        conn.execute("DELETE FROM noun_ask WHERE query_key=?;",
                     (ask_key(text),))
        conn.commit()
        print("TYPED\t%s:%d\t%s\texisting" % (kind, hits[0]["ref"],
                                              hits[0]["display"]))
        return
    if kind == "sub_place":
        if not parent:
            for line in parent_ask_lines(K, conn, None, text):
                print(line)
            sys.exit(PARENT_CODE)
        pid = _place_ref(K, conn, parent)
        try:
            sp = K.sub_place_add(conn, pid, text)
        except sqlite3.IntegrityError as e:
            raise Refused(EXISTS_CODE, ["CROSS_KIND\tsub_place\t%s\t%s" % (
                text, str(e))])
        ref = sp["id"]
    elif kind == "person":
        cur = conn.execute("INSERT INTO persons(name) VALUES (?);", (text,))
        ref = cur.lastrowid
    elif kind == "group":
        ref = group_add(conn, text)
    elif kind == "place":
        conn.commit()
        try:
            ref = K.travel_place_add(conn, text)["id"]
        except ValueError as e:
            _usage(str(e))
    else:  # project
        ref = K.project_add(text, conn=conn)
    conn.execute("DELETE FROM noun_ask WHERE query_key=?;", (ask_key(text),))
    conn.commit()
    print("TYPED\t%s:%d\t%s" % (kind, ref, display(conn, kind, ref)))


def _cli_noun_ask_forget(K, conn, argv):
    if len(argv) != 1:
        _usage("usage: kit_life.sh noun-ask-forget <text>")
    n = conn.execute("DELETE FROM noun_ask WHERE query_key=?;",
                     (ask_key(argv[0]),)).rowcount
    conn.commit()
    print("FORGOT\t%s\t%d" % (ws(argv[0]), n))


def _cli_noun_ask_list(K, conn, argv):
    for row in conn.execute("SELECT query_key, query_text, state, offered_n, "
                            "COALESCE(field_token,''), COALESCE(said,''), "
                            "updated_at FROM noun_ask ORDER BY updated_at, "
                            "query_key;"):
        print("NOUN_ASK_ROW\t" + "\t".join(str(x) for x in row))
    print("NOUN_ASK_TODAY\t%d\t%d" % (asks_today(K, conn), daily_cap(conn)))


def _cli_noun_fields(K, conn, argv):
    argv, field = _pop(argv, "--field")
    rows = conn.execute("SELECT field_token, kind FROM field_kind_gate "
                        "ORDER BY field_token;").fetchall()
    by = {}
    for f, k in rows:
        by.setdefault(f, []).append(k)
    for f in sorted(by):
        if field and f != field:
            continue
        print("FIELD\t%s\t%s" % (f, ",".join(k for k in KINDS
                                             if k in by[f])))


def _cli_group_add(K, conn, argv):
    argv, confirmed = _pop(argv, "--kind-confirmed", has_value=False)
    argv, new_confirmed = _pop(argv, "--new-confirmed", has_value=False)
    if not argv:
        _usage("usage: kit_life.sh group-add <name> [display=<d>] "
               "[aliases=a|b] [--kind-confirmed] [--new-confirmed]")
    name, disp, aliases = ws(argv[0]), None, []
    for tok in argv[1:]:
        k, _e, v = tok.partition("=")
        if k == "display":
            disp = v
        elif k == "aliases":
            aliases = [a for a in v.split("|") if ws(a)]
        else:
            _usage("group-add: unknown field %r" % tok)
    hit = resolve(conn, name, kinds=("group",), include_gone=True)
    if hit:
        row = resolve_group(conn, "group:%d" % hit[0]["ref"])
        print("EXISTS 1")
        print(group_line(row))
        sys.exit(EXISTS_CODE)
    other = cross_kind(conn, name, "group")
    if other and not confirmed:
        raise Refused(EXISTS_CODE, ["CROSS_KIND\t%s:%d\t%s" % (
            other[0]["kind"], other[0]["ref"], other[0]["display"])])
    K._near_gate_new_confirmed(conn, "group", name, new_confirmed)
    gid = group_add(conn, name, disp, aliases)
    conn.execute("DELETE FROM noun_ask WHERE query_key=?;", (ask_key(name),))
    conn.commit()
    print(group_line(resolve_group(conn, "group:%d" % gid)))


def _cli_group_find(K, conn, argv):
    if len(argv) != 1:
        _usage("usage: kit_life.sh group-find <name|alias|display>")
    hits = resolve(conn, argv[0], kinds=("group",), include_gone=True)
    if not hits:
        print("ABSENT\t%s" % ws(argv[0]))
        return
    for h in hits:
        print(group_line(resolve_group(conn, "group:%d" % h["ref"],
                                       include_gone=True)))


def _members(conn, row):
    out = []
    for pid, name, groups in conn.execute(
            "SELECT id, name, groups FROM persons WHERE groups IS NOT NULL "
            "ORDER BY id;"):
        toks = [ws(g) for g in str(groups).split(",")]
        if row[1] in toks or (row[2] and ws(row[2]) in toks):
            out.append((pid, name))
    return out


def _cli_group_show(K, conn, argv):
    if len(argv) != 1:
        _usage("usage: kit_life.sh group-show <id|name>")
    row = resolve_group(conn, argv[0], include_gone=True)
    if row is None:
        _usage("no group %r" % argv[0])
    print(group_line(row))
    for pid, name in _members(conn, row):
        print("MEMBER\t%d\t%s" % (pid, name))


def _cli_group_member(K, conn, argv):
    """Add confirmed members using the existing persons.groups representation.

    Resolve the whole batch before writing; preserve other memberships and
    make repeat confirmation taps idempotent. No inferred person creation.
    """
    if len(argv) < 2:
        _usage("usage: kit_life.sh group-member <group> <person>...")
    row = resolve_group(conn, argv[0])
    if row is None:
        _usage("no group %r" % argv[0])
    if "," in row[1]:
        _usage("group-member: group name cannot contain a comma")
    people = [K._person_target(conn, p) for p in argv[1:]]
    with conn:
        for pid, _name, _rel, _aliases in people:
            raw = conn.execute("SELECT groups FROM persons WHERE id=?;",
                               (pid,)).fetchone()[0]
            groups = [ws(g) for g in (raw or "").split(",") if ws(g)]
            if row[1] not in groups and not (row[2] and ws(row[2]) in groups):
                groups.append(row[1])
                conn.execute("UPDATE persons SET groups=? WHERE id=?;",
                             (",".join(groups), pid))
    _cli_group_show(K, conn, [argv[0]])


def _cli_group_set(K, conn, argv):
    if len(argv) < 2:
        _usage("usage: kit_life.sh group-set <id|name> name=|display_name=|"
               "status=active|gone|emoji= ...")
    changes = []
    for tok in argv[1:]:
        k, eq, v = tok.partition("=")
        if not eq or k not in ("name", "display_name", "status", "emoji"):
            _usage("group-set: field %r (name|display_name|status|emoji)" % k)
        if k == "status" and v not in ("active", "gone"):
            _usage("group-set: status is active|gone")
        if k == "emoji" and v:
            import person_labels
            try:
                v = person_labels.normalize_emoji(v)
            except ValueError as e:
                _usage("group-set: %s" % e)
        changes.append((k, ws(v) or None))
    row = resolve_group(conn, argv[0], include_gone=True)
    known = None
    if row is None and any(k == "emoji" for k, _v in changes):
        # Only an existing membership token authorizes materializing a group.
        # Reuse the noun registry's identity and group_add; no emoji side store.
        key = ws(argv[0]).casefold()
        known = next((ws(token) for (raw,) in conn.execute(
            "SELECT groups FROM persons WHERE groups IS NOT NULL ORDER BY id")
            for token in raw.split(",") if ws(token).casefold() == key), None)
    if row is None and known is None:
        _usage("no group %r" % argv[0])
    try:
        with conn:
            if row is None:
                other = cross_kind(conn, known, "group")
                if other:
                    raise Refused(EXISTS_CODE, ["CROSS_KIND\t%s:%d\t%s" % (
                        other[0]["kind"], other[0]["ref"], other[0]["display"])])
                gid = group_add(conn, known)
                conn.execute("DELETE FROM noun_ask WHERE query_key=?", (ask_key(known),))
                row = resolve_group(conn, "group:%d" % gid)
            for k, v in changes:
                if k == "name":
                    other = cross_kind(conn, v, "group")
                    if other:
                        raise Refused(EXISTS_CODE, ["CROSS_KIND\t%s:%d\t%s" % (
                            other[0]["kind"], other[0]["ref"], other[0]["display"])])
                    add_alias(K, conn, "group", row[0], row[1])
                conn.execute("UPDATE person_group SET %s=? WHERE id=?" % k,
                             (v, row[0]))
    except sqlite3.IntegrityError as e:
        raise Refused(EXISTS_CODE, ["EXISTS\tgroup\t%s" % e])
    print(group_line(resolve_group(conn, "group:%d" % row[0], include_gone=True)))
    if any(k == "emoji" for k, _v in changes):
        emoji = conn.execute("SELECT emoji FROM person_group WHERE id=?", (row[0],)).fetchone()[0]
        print("EMOJI\t%d\t%s" % (row[0], emoji or "👥"))


def _cli_group_alias(K, conn, argv):
    if len(argv) < 2:
        _usage("usage: kit_life.sh group-alias <id|name> <alias>...")
    row = resolve_group(conn, argv[0], include_gone=True)
    if row is None:
        _usage("no group %r" % argv[0])
    for a in argv[1:]:
        other = [h for h in resolve(conn, a) if not (
            h["kind"] == "group" and h["ref"] == row[0])]
        if other:
            raise Refused(EXISTS_CODE, ["CROSS_KIND\t%s:%d\t%s" % (
                other[0]["kind"], other[0]["ref"], other[0]["display"])])
        add_alias(K, conn, "group", row[0], a)
    conn.commit()
    print(group_line(resolve_group(conn, "group:%d" % row[0],
                                   include_gone=True)))


def _cli_group_alias_rm(K, conn, argv):
    if len(argv) < 2:
        _usage("usage: kit_life.sh group-alias-rm <id|name> <alias>...")
    row = resolve_group(conn, argv[0], include_gone=True)
    if row is None:
        _usage("no group %r" % argv[0])
    drop = set(ws(a) for a in argv[1:])
    have = [a for a in _json_aliases(row[3]) if ws(a) not in drop]
    conn.execute("UPDATE person_group SET aliases=? WHERE id=?;",
                 (json.dumps(have, ensure_ascii=False) if have else None,
                  row[0]))
    conn.commit()
    print(group_line(resolve_group(conn, "group:%d" % row[0],
                                   include_gone=True)))


def _group_blocks(conn, gid):
    return conn.execute(
        "SELECT tb.id, tb.start_at, tb.title FROM event_group eg JOIN "
        "timeblock tb ON tb.id=eg.event_id WHERE eg.group_id=? "
        "ORDER BY tb.start_at, tb.id;", (gid,)).fetchall()


def _cli_group_blocks(K, conn, argv):
    if len(argv) != 1:
        _usage("usage: kit_life.sh group-blocks <id|name>")
    row = resolve_group(conn, argv[0], include_gone=True)
    if row is None:
        print("ABSENT\t%s" % ws(argv[0]))
        return
    blocks = _group_blocks(conn, row[0])
    print("GROUP\t%d\t%s\t%d" % (row[0], row[1], len(blocks)))
    for bid, sa, title in blocks:
        print("%d\t%s\t%s" % (bid, sa or "", title or ""))


def _cli_group_del(K, conn, argv):
    argv, into = _pop(argv, "--into")
    if len(argv) != 1:
        _usage("usage: kit_life.sh group-del <id|name> [--into <group>]")
    row = resolve_group(conn, argv[0], include_gone=True)
    if row is None:
        _usage("no group %r" % argv[0])
    blocks = _group_blocks(conn, row[0])
    members = _members(conn, row)
    if (blocks or members) and not into:
        lines = ["IN_USE\tgroup:%d\t%s\tblocks=%d\tmembers=%d" % (
            row[0], row[1], len(blocks), len(members))]
        lines += ["BLOCK\t%d\t%s\t%s" % b for b in blocks]
        lines += ["MEMBER\t%d\t%s" % m for m in members]
        lines.append("HINT\tretire keeps history: kit_life.sh group-set %d "
                     "status=gone; or --into <group> moves them" % row[0])
        raise Refused(IN_USE_CODE, lines)
    if into:
        dst = resolve_group(conn, into, include_gone=True)
        if dst is None or dst[0] == row[0]:
            _usage("--into: no other group %r" % into)
        conn.execute("INSERT OR IGNORE INTO event_group(event_id, group_id) "
                     "SELECT event_id, ? FROM event_group WHERE group_id=?;",
                     (dst[0], row[0]))
        conn.execute("DELETE FROM event_group WHERE group_id=?;", (row[0],))
        for pid, _n in members:
            g = conn.execute("SELECT groups FROM persons WHERE id=?;",
                             (pid,)).fetchone()[0]
            toks = [ws(t) for t in str(g).split(",") if ws(t)]
            toks = [dst[1] if t in (row[1], ws(row[2] or "")) else t
                    for t in toks]
            seen = []
            for t in toks:
                if t not in seen:
                    seen.append(t)
            conn.execute("UPDATE persons SET groups=? WHERE id=?;",
                         (",".join(seen), pid))
        add_alias(K, conn, "group", dst[0], row[1])
    import appointment_buffer
    buffer_changed = appointment_buffer.retire_target(
        K, conn, 'group', row[0], dst[0] if into else None)
    conn.execute("DELETE FROM person_group WHERE id=?;", (row[0],))
    conn.commit()
    if buffer_changed and into:
        appointment_buffer.refresh(K, conn)
    print("DELETED\tgroup:%d\t%s" % (row[0], row[1]))


def _block_target(K, conn, eid, verb):
    row = conn.execute("SELECT kind, block_class FROM timeblock WHERE id=?;",
                       (int(eid),)).fetchone()
    if row is None or row[0] not in K.BLOCK_PERSON_KINDS or row[1]:
        _usage("%s: no block that can carry participants: %s" % (verb, eid))
    return row[0]


def _cli_appt_group(K, conn, argv, verb="appt-group"):
    if len(argv) < 2 or not str(argv[0]).isdigit():
        _usage("usage: kit_life.sh %s <block_id> <group ref|name>..." % verb)
    eid = int(argv[0])
    kind = _block_target(K, conn, eid, verb)
    gids = []
    for tok in argv[1:]:
        k, _v = parse_ref(tok)
        if k not in (None, "group"):
            hit = {"kind": k, "ref": _v, "display": display(conn, k, _v)}
            raise Refused(FIELD_KIND_CODE, field_refusal_lines(
                K, conn, "block.participants", tok, hit, eid=eid))
        row = resolve_group(conn, tok)
        if row is None:
            other = resolve(conn, tok)
            if other:
                raise Refused(FIELD_KIND_CODE, ["FIELD_KIND_REFUSED\t"
                              "block.participants\t%s\t%s:%d\taccepts=person,"
                              "group\tuse block-person for a person" % (
                                  ws(tok), other[0]["kind"],
                                  other[0]["ref"])])
            _usage("%s: no group %r (group-add first)" % (verb, tok))
        if row[4] != "active":
            _usage("%s: group %s is retired (status gone)" % (verb, row[1]))
        gids.append(row[0])
    for g in gids:
        conn.execute("INSERT OR IGNORE INTO event_group(event_id, group_id) "
                     "VALUES (?,?);", (eid, g))
    conn.commit()
    if any(K._config_stored(conn, 'appointment_buffer.rule.group.%d' % g) for g in gids):
        import appointment_buffer
        appointment_buffer.refresh(K, conn, bid=eid)
    head = "appt" if verb.startswith("appt") else kind
    print("%s %d \uadf8\ub8f9 %d" % (head, eid, len(event_groups(conn, eid))))
    K._print_cards(conn, [eid])


def _cli_appt_group_rm(K, conn, argv, verb="appt-group-rm"):
    if len(argv) != 2 or not str(argv[0]).isdigit():
        _usage("usage: kit_life.sh %s <block_id> <group>" % verb)
    eid = int(argv[0])
    kind = _block_target(K, conn, eid, verb)
    row = resolve_group(conn, argv[1], include_gone=True)
    if row is None:
        _usage("%s: no group %r" % (verb, argv[1]))
    conn.execute("DELETE FROM event_group WHERE event_id=? AND group_id=?;",
                 (eid, row[0]))
    conn.commit()
    if K._config_stored(conn, 'appointment_buffer.rule.group.%d' % row[0]):
        import appointment_buffer
        appointment_buffer.refresh(K, conn, bid=eid)
    head = "appt" if verb.startswith("appt") else kind
    print("%s %d \uadf8\ub8f9 %d" % (head, eid, len(event_groups(conn, eid))))
    K._print_cards(conn, [eid])


def _cli_block_group(K, conn, argv):
    return _cli_appt_group(K, conn, argv, verb="block-group")


def _cli_block_group_rm(K, conn, argv):
    return _cli_appt_group_rm(K, conn, argv, verb="block-group-rm")


def _cli_person_del(K, conn, argv):
    argv, into = _pop(argv, "--into")
    if len(argv) != 1:
        _usage("usage: kit_life.sh person-del <id|name> [--into <person>]")
    k, v = parse_ref(argv[0])
    pid = v if v is not None and k in (None, "person") else None
    if pid is None:
        hits = resolve(conn, argv[0], kinds=("person",))
        pid = hits[0]["ref"] if hits else None
    row = conn.execute("SELECT id, name FROM persons WHERE id=?;",
                       (pid,)).fetchone() if pid is not None else None
    if row is None:
        _usage("no person %r" % argv[0])
    blocks = conn.execute(
        "SELECT tb.id, tb.start_at, tb.title FROM event_persons ep JOIN "
        "timeblock tb ON tb.id=ep.event_id WHERE ep.person_id=? ORDER BY "
        "tb.start_at, tb.id;", (pid,)).fetchall()
    experiences = conn.execute(
        "SELECT e.id,e.title FROM experience_person ep JOIN event e "
        "ON e.id=ep.event_id WHERE ep.person_id=? ORDER BY e.id;", (pid,)).fetchall()
    try:
        rels = conn.execute(
            "SELECT id FROM persons_relations WHERE person_a=? OR "
            "person_b=?;", (pid, pid)).fetchall()
    except sqlite3.Error:
        rels = []
    if (blocks or rels or experiences) and not into:
        lines = ["IN_USE\tperson:%d\t%s\tblocks=%d\trelations=%d" % (
            pid, row[1], len(blocks), len(rels))]
        lines += ["BLOCK\t%d\t%s\t%s" % b for b in blocks]
        lines += ["EXPERIENCE\t%d\t%s" % e for e in experiences]
        raise Refused(IN_USE_CODE, lines)
    if into:
        dk, dv = parse_ref(into)
        dst = dv if dv is not None and dk in (None, "person") else None
        if dst is None:
            h = resolve(conn, into, kinds=("person",))
            dst = h[0]["ref"] if h else None
        if dst is None or dst == pid:
            _usage("--into: no other person %r" % into)
        conn.execute("INSERT OR IGNORE INTO event_persons(event_id, "
                     "person_id) SELECT event_id, ? FROM event_persons "
                     "WHERE person_id=?;", (dst, pid))
        conn.execute("DELETE FROM event_persons WHERE person_id=?;", (pid,))
        conn.execute("INSERT OR IGNORE INTO experience_person(event_id,person_id,added_at) "
                     "SELECT event_id,?,added_at FROM experience_person WHERE person_id=?;",
                     (dst, pid))
        conn.execute("DELETE FROM experience_person WHERE person_id=?;", (pid,))
        # persons_relations keeps person_a < person_b: re-seat each pair.
        for rid, a, b, rtype, note in conn.execute(
                "SELECT id, person_a, person_b, relation_type, note FROM "
                "persons_relations WHERE person_a=? OR person_b=?;",
                (pid, pid)).fetchall():
            other = b if a == pid else a
            if other != dst:
                lo, hi = min(other, dst), max(other, dst)
                conn.execute("INSERT OR IGNORE INTO persons_relations("
                             "person_a, person_b, relation_type, note) "
                             "VALUES (?,?,?,?);", (lo, hi, rtype, note))
            conn.execute("DELETE FROM persons_relations WHERE id=?;", (rid,))
        add_alias(K, conn, "person", dst, row[1])
    import appointment_buffer
    buffer_changed = appointment_buffer.retire_target(
        K, conn, 'person', pid, dst if into else None)
    conn.execute("DELETE FROM persons WHERE id=?;", (pid,))
    conn.commit()
    if buffer_changed and into:
        appointment_buffer.refresh(K, conn)
    print("DELETED\tperson:%d\t%s" % (pid, row[1]))


def _cli_near_match_forget(K, conn, argv):
    if len(argv) != 3:
        _usage("usage: kit_life.sh near-match-forget <domain> <ref> <query>")
    nm = K._near_mod()
    key = nm.compact(argv[2], argv[0]) if nm else nk(argv[2])
    n = conn.execute("DELETE FROM near_match_refusal WHERE domain=? AND "
                     "existing_ref=? AND query_key=?;",
                     (argv[0], str(argv[1]), key)).rowcount
    conn.commit()
    print("FORGOT\t%s\t%s\t%s\t%d" % (argv[0], argv[1], ws(argv[2]), n))


# -- near-match rows for the two new domains --------------------------------

def near_rows(conn, domain):
    if domain == "group":
        return [(gid, name, ([d] if d else []) + _json_aliases(a))
                for gid, name, d, a in conn.execute(
                    "SELECT id, name, display_name, aliases FROM person_group "
                    "WHERE status='active' ORDER BY id;").fetchall()]
    if domain == "sub_place":
        return [(sid, name, [tok]) for sid, name, tok in conn.execute(
            "SELECT sp.id, sp.name, sp.token FROM sub_place sp JOIN place p "
            "ON p.id=sp.place_id WHERE p.status='active' ORDER BY sp.id;"
        ).fetchall()]
    return []
