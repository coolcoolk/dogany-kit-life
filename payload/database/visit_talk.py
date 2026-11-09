#!/usr/bin/env python3
"""visit_talk.py -- evening-retro small talk about a shared visit.

WHY THIS EXISTS. The owner wants group and venue taste to accumulate without
being questioned (owner 2026-10-16 05:51-05:59; rule 2026-10-10: the
conversation the user wants to have, not information extraction; don't drag
it out; no buttons). The retro item of a visit with people therefore opens
with ONE line of small talk, picked in this order:

  1 person   someone in the party worth a word: they JOINED (the retro line /
             note names them with a join word), it had been a long while
             (person module last meeting, gap >= max(60 days, their
             intimacy cycle)), a baby was born recently, a life fact they
             told before (relationship.db person_facts, life word, <= 180
             days), a birthday within 30 days
  2 event    what the retro line / note says happened: a later round or a
             move ending somewhere ("1st round ..., 2nd round at my place")
  3 venue    only when 1 and 2 have nothing: the restaurant by name and one
             thing the kit knows about it (its food kind, else a venue trait)

At most one question, a tail is optional. No material -> no talk, and the
item keeps its ordinary question. The rendered shapes are i18n
(retro.talk.*), owner-reviewed.

The agent reads each owner reply and supplies typed verdicts, traits and
person notes via visit-talk-reply --data. Raw reply words are inert evidence.
visit-talk-context --data supplies joined people and event destination facts;
retro-line/note words never choose the opener. Existing typed meeting dates,
birthdays and venue records may still provide its ordinary context.
Every capture keeps the owner's quote and a generated visit reply ref.
Empty captures write nothing and never request particular words.

Storage: block_attr visit_talk on the visit block (no table):
  {"v":1,"replies":[{"n","text","at","angle"}],
   "verdicts":[{"holder","verdict","said_by","quote","ref"}],
   "traits":[{"target","quote","ref"}],
   "notes":[{"holder","quote","ref"}]}
dining-history returns the verdicts (with quote); venue-traits the traits.

venue-traits <venue-id | place:<id> | block:<id>> [--facts F] --json
  {"v":1,"available":true,"query","food":[food-kind chains],
   "venue":["kit_life:venue:<trait>[:<value>]",...],
   "sources":[{"target","from":"observation","quote","ref","at"} |
              {"target","from":"facts","src"}]}
  read-only, no outbound call; owner words beat the place page per trait,
  the later observation wins; exit 2 usage / 3 not-found / 1 internal.

Functions take the kit_life module as K (errand.py convention).
"""
import datetime
import json
import os
import re
import sqlite3

import food_pref as FP

V = 1
ATTR = "visit_talk"
WORDS_FILE = "visit_talk_words.tsv"
RELDB_PATH = None                     # test seam; default beside the kit DB
LONG_GAP_DAYS = 60                    # floor of "a long while"
RECENT_GAP_DAYS = 30                  # joined but met within -> no "long while?"
FACT_DAYS = 180
BIRTHDAY_DAYS = 30
VENUE_NS = "kit_life:venue:"
TRAIT_ORDER = ("mood", "portion", "wait", "seating", "drinks_friendly",
               "price", "service")
PRICE_LOW_WON, PRICE_HIGH_WON = 12000, 25000
HANGUL_FULL_NAME = re.compile(u"^[\uac00-\ud7a3]{3}$")
CONTEXT_ATTR = 'visit_talk_context'


# -- data ---------------------------------------------------------------------

def words(K):
    out = {}
    path = os.path.join(K.SCRIPT_DIR, WORDS_FILE)
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip() or ln.startswith("#"):
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3:
                out.setdefault(p[0], []).append((p[1], p[2]))
    for cls in out:
        out[cls].sort(key=lambda r: -len(r[0]))
    return out


def reldb_path(K):
    return RELDB_PATH or os.path.join(os.path.dirname(os.path.abspath(K.DB_PATH)),
                                      "relationship.db")


def _rel_ro(K):
    path = reldb_path(K)
    if not os.path.isfile(path):
        return None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        conn.execute("SELECT 1 FROM person_facts LIMIT 1;")
        return conn
    except sqlite3.Error:
        return None


def _copy(K, key, fallback, **kw):
    val = K._i18n(key, fallback)
    try:
        return val.format(**kw)
    except (KeyError, IndexError, ValueError):
        return val


COPY = {
    "retro.talk.when.today": "today",
    "retro.talk.when.yesterday": "yesterday",
    "retro.talk.when.date": "on {m}/{d}",
    "retro.talk.person.joined": "So {name} joined {when} too -- had it been a while?",
    "retro.talk.person.joined_recent": "So {name} joined {when} too -- nice to have "
                                       "everyone together.",
    "retro.talk.person.long_gap": "{when}: first time seeing {name} in {gap} -- how are "
                                  "they doing?",
    "retro.talk.person.child_born": "{name} just had a baby -- did you hear about the "
                                    "baby {when}?",
    "retro.talk.person.fact": "{name} mentioned \"{fact}\" before -- did you catch up on "
                              "that {when}?",
    "retro.talk.person.birthday": "{name}'s birthday is {date} -- did that come up {when}?",
    "retro.talk.gap.months": "{n} months",
    "retro.talk.gap.years": "{n} years",
    "retro.talk.event.after": "After {first} you went on to {where} -- must have been a "
                              "long one?",
    "retro.talk.event.round": "round {n}",
    "retro.talk.event.home": "your place",
    "retro.talk.event.home_of": "{who}'s place",
    "retro.talk.venue.kind": "{venue} is a {kind} place -- did it suit {who}?",
    "retro.talk.venue.trait": "{venue} is a {trait} place -- was it OK for {who}?",
    "retro.talk.who.all": "everyone",
    "retro.talk.who.pair": "both of you",
    "retro.talk.date": "{m}/{d}",
    "retro.talk.trait.mood.quiet": "quiet",
    "retro.talk.trait.mood.lively": "lively",
    "retro.talk.trait.portion.large": "big-portion",
    "retro.talk.trait.portion.small": "small-portion",
    "retro.talk.trait.wait.long": "queue-up",
    "retro.talk.trait.wait.short": "walk-in",
    "retro.talk.trait.seating.room": "private-room",
    "retro.talk.trait.seating.bar": "bar-seat",
    "retro.talk.trait.seating.table": "table",
    "retro.talk.trait.drinks_friendly": "good-for-drinks",
    "retro.talk.trait.price.low": "good-value",
    "retro.talk.trait.price.mid": "mid-priced",
    "retro.talk.trait.price.high": "pricey",
    "retro.talk.trait.service.attentive": "friendly",
    "retro.talk.trait.service.slow": "slow-service",
}


def copy(K, key, **kw):
    return _copy(K, key, COPY.get(key, ""), **kw)


# -- shared reads ---------------------------------------------------------------

def _local_day(K, instant, tz):
    return K._local_date_of(instant, tz or "Asia/Seoul")


def talk_attr(conn, bid):
    row = conn.execute("SELECT value FROM block_attr WHERE block_id=? AND "
                       "attr_token=? ORDER BY source LIMIT 1;", (bid, ATTR)).fetchone()
    try:
        v = json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _note_attr(conn, bid):
    row = conn.execute("SELECT value FROM block_attr WHERE block_id=? AND "
                       "attr_token='note' LIMIT 1;", (bid,)).fetchone()
    return (row[0] or "") if row else ""


def party_ids(conn, members):
    out = []
    for m in members:
        for (pid,) in conn.execute("SELECT person_id FROM event_persons WHERE "
                                   "event_id=? ORDER BY person_id;", (m["id"],)):
            if pid not in out:
                out.append(pid)
    return out


def short_name(conn, pid, surfaces):
    """What the owner calls a person: the display name, else a three-syllable
    Hangul name's given name when it is unambiguous in the party, else the name."""
    r = conn.execute("SELECT name, display_name FROM persons WHERE id=?;", (pid,)).fetchone()
    if not r:
        return ""
    if r[1] and r[1].strip():
        return r[1].strip()
    if HANGUL_FULL_NAME.match(r[0] or "") and r[0][1:] in surfaces:
        return r[0][1:]
    return r[0] or ""


def venue_short(name):
    """The venue as said: a trailing branch word ("... jikyeongjeom") dropped."""
    parts = (name or "").split()
    if len(parts) > 1 and parts[-1].endswith(u"\uc810"):
        parts = parts[:-1]
    return " ".join(parts)


# -- venue ----------------------------------------------------------------------

def _book(K, conn):
    try:
        return FP.Book(K, conn)
    except Exception:                                 # noqa: BLE001
        return None


def observed_traits(conn, place_id):
    """[(target, quote, ref, at)] the owner said about a place, oldest first."""
    out = []
    for bid, ulid, sa, date in conn.execute(
            "SELECT id, ulid, start_at, date FROM block WHERE place_id=? "
            "ORDER BY COALESCE(start_at, date), id;", (place_id,)):
        att = talk_attr(conn, bid)
        if not att:
            continue
        at = {r.get("n"): r.get("at") for r in att.get("replies") or []}
        for t in att.get("traits") or []:
            if t.get("target") and t.get("ref"):
                n = t["ref"].rsplit("#", 1)[-1]
                out.append((t["target"], t.get("quote") or "", t["ref"],
                            at.get(int(n) if n.isdigit() else n) or sa or date or ""))
    return out


def latest_traits(obs):
    """One target per trait, the later observation winning."""
    keep = {}
    for target, _q, _r, _at in obs:
        keep[target[len(VENUE_NS):].split(":")[0]] = target
    return keep


def venue_of_visit(K, conn, members):
    """{bid, place_id, name, kind, trait} for the restaurant of a visit, or
    None (module_dining off, no non-home place with a food kind / trait)."""
    if FP.module_off(K):
        return None
    book = _book(K, conn)
    for m in members:
        if m["kind"] == "meal" or not m["place_id"] or m["place_is_base"]:
            continue
        kind = None
        if book is not None:
            _key, nodes, cz = FP.venue_of(book, conn, m["place_id"], m["place_name"], m["id"])
            node = nodes[0] if nodes else cz
            if node and node in book.tree:
                kind = book.tree[node]["label"]
        traits = latest_traits(observed_traits(conn, m["place_id"]))
        trait = next((traits[t] for t in TRAIT_ORDER if t in traits), None)
        if kind or trait:
            return dict(bid=m["id"], place_id=m["place_id"], name=m["place_name"],
                        kind=kind, trait=trait)
    return None


# -- the opener ----------------------------------------------------------------

def _when(K, visit_day, today):
    gap = (today - visit_day).days
    if gap <= 0:
        return copy(K, "retro.talk.when.today")
    if gap == 1:
        return copy(K, "retro.talk.when.yesterday")
    return copy(K, "retro.talk.when.date", m=visit_day.month, d=visit_day.day)


def last_meeting_before(K, conn, rel, pid, visit_start, visit_day):
    """Local date of the person's latest meeting before the visit day:
    appointments together (kit) and recalled meetings (relationship.db
    touches kind meet), or None."""
    best = None
    for sa, tz in conn.execute(
            "SELECT b.start_at, (SELECT value FROM block_attr x WHERE "
            "x.block_id=b.id AND x.attr_token='display_tz' LIMIT 1) "
            "FROM block b JOIN event_persons ep ON ep.event_id=b.id "
            "WHERE ep.person_id=? AND b.kind_token='appointment' "
            "AND b.start_at IS NOT NULL AND b.start_at < ? "
            "AND (b.settled_outcome IS NULL OR b.settled_outcome <> 'abandoned') "
            "ORDER BY b.start_at DESC LIMIT 5;", (pid, visit_start)):
        d = _local_day(K, sa, tz)
        if d < visit_day:
            best = d
            break
    if rel is not None:
        try:
            r = rel.execute("SELECT MAX(touched_on) FROM touches WHERE person_id=? "
                            "AND kind='meet' AND tombstone=0 AND touched_on<?;",
                            (pid, visit_day.isoformat())).fetchone()
        except sqlite3.Error:
            r = None
        if r and r[0]:
            d = datetime.date.fromisoformat(r[0][:10])
            if best is None or d > best:
                best = d
    return best


def _cycle_days(conn, pid):
    try:
        r = conn.execute("SELECT il.cycle_months FROM persons p JOIN intimacy_levels il "
                         "ON il.id=p.intimacy_id WHERE p.id=?;", (pid,)).fetchone()
    except sqlite3.Error:
        r = None
    return int(round(float(r[0]) * 30)) if r and r[0] else 0


def _gap_text(K, days):
    if days >= 365:
        return copy(K, "retro.talk.gap.years", n=days // 365)
    return copy(K, "retro.talk.gap.months", n=max(2, days // 30))


def _person_angle(K, conn, outer, party, surfaces, context, visit_day, today, when):
    holders = {"person:kit_life:%d" % p: p for p in party}
    rel = _rel_ro(K)
    try:
        def name_of(pid):
            return short_name(conn, pid, sum(surfaces.values(), []))

        def gap_of(pid):
            last = last_meeting_before(K, conn, rel, pid, outer["start_at"] or "", visit_day)
            return (visit_day - last).days if last else None

        for pid in context.get("joined", []):
            h = "person:kit_life:%d" % pid
            if h not in holders:
                continue
            gap = gap_of(pid)
            kind = "joined_recent" if gap is not None and gap < RECENT_GAP_DAYS else "joined"
            return dict(kind=kind, holder=h, line=copy(
                K, "retro.talk.person." + kind, when=when, name=name_of(pid)), gap_days=gap)
        for pid in party:
            gap = gap_of(pid)
            if gap is not None and gap >= max(LONG_GAP_DAYS, _cycle_days(conn, pid)):
                return dict(kind="long_gap", holder="person:kit_life:%d" % pid, gap_days=gap,
                            line=copy(K, "retro.talk.person.long_gap", when=when,
                                      name=name_of(pid), gap=_gap_text(K, gap)))
        N = K._noun_mod()
        for pid in party:
            try:
                p = N.person_facts(K, conn, pid, today.isoformat()) or {}
            except Exception:                         # noqa: BLE001
                p = {}
            if any(e.get("kind") == "child_born" for e in p.get("recent_events") or []):
                return dict(kind="child_born", holder="person:kit_life:%d" % pid,
                            line=copy(K, "retro.talk.person.child_born", when=when,
                                      name=name_of(pid)))
        for choice in context.get('life_facts', []):
            pid = choice['person_id']
            if pid not in party or rel is None:
                continue
            row = rel.execute('SELECT fact FROM person_facts WHERE id=? AND person_id=? AND tombstone=0',
                              (choice['fact_id'], pid)).fetchone()
            if row:
                return dict(kind='fact', holder='person:kit_life:%d' % pid, fact=row[0],
                            line=copy(K, 'retro.talk.person.fact', when=when,
                                      name=name_of(pid), fact=_brief(row[0])))
        import relmod
        for pid in party:
            r = conn.execute("SELECT birthday FROM persons WHERE id=?;", (pid,)).fetchone()
            soon = relmod.birthday_soon(r[0] if r else None, today.isoformat())
            if soon and soon[1] <= BIRTHDAY_DAYS:
                d = datetime.date.fromisoformat(soon[0])
                return dict(kind="birthday", holder="person:kit_life:%d" % pid,
                            line=copy(K, "retro.talk.person.birthday", when=when,
                                      name=name_of(pid),
                                      date=copy(K, "retro.talk.date", m=d.month, d=d.day)))
    finally:
        if rel is not None:
            rel.close()
    return None


def _brief(text, limit=30):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _event_angle(K, event, title):
    if not event:
        return None
    kind = event['destination_kind']
    where = (copy(K, 'retro.talk.event.home') if kind == 'home' else
             copy(K, 'retro.talk.event.home_of', who=event['who']) if kind == 'person_home' else
             event['where'])
    first = copy(K, 'retro.talk.event.round', n=event['first_round']) if event.get('first_round') else title
    return dict(kind='after', line=copy(K, 'retro.talk.event.after', first=first, where=where))


def set_context(K, conn, token, data, now=None):
    """Store typed opener facts. The original retro line is never parsed."""
    if not isinstance(data, dict) or set(data) - {'joined', 'event', 'life_facts'}:
        raise FP.Usage('context fields are joined, event and life_facts')
    got = _group(K, conn, token, now or K.now_utc())
    if got is None:
        raise FP.NotFound('no retro item %s' % token)
    outer, children = got
    party = party_ids(conn, [outer] + children)
    joined = data.get('joined', [])
    if not isinstance(joined, list) or any(type(p) is not int or p not in party for p in joined):
        raise FP.Usage('joined must list person ids in this visit')
    facts = data.get('life_facts', [])
    if not isinstance(facts, list) or any(not isinstance(f, dict) or type(f.get('person_id')) is not int or f['person_id'] not in party or type(f.get('fact_id')) is not int for f in facts):
        raise FP.Usage('life_facts must list typed person_id and fact_id')
    event = data.get('event')
    if event is not None:
        if not isinstance(event, dict) or event.get('destination_kind') not in ('home', 'person_home', 'venue'):
            raise FP.Usage('event destination_kind is home|person_home|venue')
        if event.get('first_round') is not None and (type(event['first_round']) is not int or event['first_round'] <= 0):
            raise FP.Usage('first_round must be a positive integer')
        field = {'person_home': 'who', 'venue': 'where'}.get(event['destination_kind'])
        if field and (not isinstance(event.get(field), str) or not event[field].strip()):
            raise FP.Usage('event requires a typed %s' % field)
    conn.execute("INSERT OR REPLACE INTO block_attr(block_id,attr_token,value,source) VALUES(?,?,?,'user')",
                 (outer['id'], CONTEXT_ATTR, json.dumps(data, ensure_ascii=False)))
    conn.commit()
    return dict(item=outer['id'], context=data)


def _venue_angle(K, conn, members, party_n):
    v = venue_of_visit(K, conn, members)
    if v is None:
        return None
    who = copy(K, "retro.talk.who.pair" if party_n == 1 else "retro.talk.who.all")
    name = venue_short(v["name"])
    if v["kind"]:
        line = copy(K, "retro.talk.venue.kind", venue=name, kind=v["kind"], who=who)
    else:
        tr = v["trait"][len(VENUE_NS):].replace(":", ".")
        line = copy(K, "retro.talk.venue.trait", venue=name, who=who,
                    trait=copy(K, "retro.talk.trait." + tr) or tr)
    return dict(kind="kind" if v["kind"] else "trait", line=line, venue=dict(
        block=v["bid"], place_id=v["place_id"], name=v["name"], kind=v["kind"],
        trait=v["trait"]))


def mark_asked(conn, bid, talk, now):
    """Inside the caller's transaction: the talk line was asked as the
    item's question (retro-item-set). Kept as {"asked": talk} in the visit's
    visit_talk attr; no verdict, no reply."""
    stored = talk_attr(conn, bid) or {"v": V}
    stored["asked"] = dict(talk, at=now)
    conn.execute("INSERT OR REPLACE INTO block_attr(block_id, attr_token, value, source) "
                 "VALUES(?,?,?,'user');", (bid, ATTR, json.dumps(stored, ensure_ascii=False)))


def asked_talk(conn, members):
    for m in members:
        att = talk_attr(conn, m["id"]) if m["kind"] != "meal" else None
        if att and att.get("asked"):
            return att["asked"]
    return None


def talked(conn, members):
    return any(talk_attr(conn, m["id"]) for m in members if m["kind"] != "meal")


def opener(K, conn, outer, children, now=None):
    """The small-talk line for a retro item, or None (no people, talked
    already, no material). {angle, kind, line, holder?, venue?}."""
    members = [outer] + [m for m in children]
    party = party_ids(conn, [m for m in members if m["kind"] != "meal"])
    if not party or talked(conn, members) or not outer["start_at"]:
        return None
    now = now or K.now_utc()
    tz = outer["tz"] or "Asia/Seoul"
    visit_day = _local_day(K, outer["start_at"], tz)
    today = _local_day(K, now, tz)
    when = _when(K, visit_day, today)
    surfaces = FP.party_surfaces(conn, party)
    row = conn.execute("SELECT value FROM block_attr WHERE block_id=? AND attr_token=?", (outer["id"], CONTEXT_ATTR)).fetchone()
    context = json.loads(row[0]) if row else {}
    got = _person_angle(K, conn, outer, party, surfaces, context, visit_day, today, when)
    if got:
        return dict(got, angle="person")
    got = _event_angle(K, context.get("event"), outer["title"])
    if got:
        return dict(got, angle="event")
    got = _venue_angle(K, conn, members, len(party))
    if got:
        return dict(got, angle="venue")
    return None


# -- the reply ------------------------------------------------------------------

def _group(K, conn, token, now):
    col = "id" if str(token).isdigit() else "ulid"
    row = conn.execute("SELECT id, display_tz FROM timeblock WHERE %s=?;" % col,
                       (int(token) if col == "id" else str(token),)).fetchone()
    if row is None:
        return None
    day = str(_local_day(K, now, row[1] or "Asia/Seoul"))
    got = K._retro_group(conn, row[0], day, now, K._retro_visit_ends(conn))
    if got is None:
        return None
    outer, children, _t, _w = got
    if row[0] != outer["id"]:
        outer, children = K._retro_member(conn, row[0]), []
    return outer, children


def read_reply(K, conn, outer, children, text, talk, ref, data=None):
    """Validate agent-supplied captures; owner text is quote evidence only."""
    data = {} if data is None else data
    if not isinstance(data, dict) or set(data) - {'verdicts', 'traits', 'notes'}:
        raise FP.Usage('supply typed verdicts, traits and notes lists')
    members = [outer] + children
    party = party_ids(conn, [m for m in members if m['kind'] != 'meal'])
    holders = {'owner'} | {'person:kit_life:%d' % p for p in party}
    group = FP.auto_group(sorted(holders))
    venue = venue_of_visit(K, conn, members)
    allowed_traits = {VENUE_NS + val for _word, val in words(K).get('trait', [])}
    captures = {}
    for kind in ('verdicts', 'traits', 'notes'):
        rows = data.get(kind, [])
        if not isinstance(rows, list):
            raise FP.Usage('%s must be a typed list' % kind)
        captures[kind] = []
        for row in rows:
            if not isinstance(row, dict):
                raise FP.Usage('capture must be an object')
            quote = row.get('quote', text)
            if not isinstance(quote, str) or not quote.strip() or quote not in text:
                raise FP.Usage('quote must be verbatim evidence from this reply')
            if kind == 'traits':
                if not venue or row.get('target') not in allowed_traits:
                    raise FP.Usage('choose an existing venue trait target')
                capture = dict(target=row['target'])
            else:
                holder = row.get('holder')
                allowed = holders | {group} if kind == 'verdicts' else holders - {'owner'}
                if holder not in allowed:
                    raise FP.Usage('choose a holder from this visit party')
                capture = dict(holder=holder)
                if kind == 'verdicts':
                    if not venue or row.get('verdict') not in ('liked', 'neutral', 'disliked'):
                        raise FP.Usage('choose liked|neutral|disliked for the venue')
                    capture.update(verdict=row['verdict'], said_by='owner')
            capture.update(quote=quote, ref=ref)
            captures[kind].append(capture)
    last = {v['holder']: v for v in captures['verdicts']}
    return list(last.values()), captures['traits'], captures['notes'], venue


def _rel_write(K, notes, day):
    """Person notes -> relationship.db person_facts (source retro, verbatim,
    ref). Returns the new fact ids."""
    import relmod
    path = reldb_path(K)
    relmod.ensure_schema(path)
    rel = relmod.rel_conn(path)
    ids = []
    try:
        for n in notes:
            pid = int(n["holder"].rsplit(":", 1)[1])
            cur = rel.execute("INSERT INTO person_facts (person_id, fact, source, noted_on, "
                              "ref) VALUES (?,?,?,?,?);",
                              (pid, n["quote"], "retro", day, n["ref"]))
            ids.append(cur.lastrowid)
        rel.commit()
    finally:
        rel.close()
    return ids


def reply(K, conn, token, text, now=None, dry_run=False, *, data=None):
    """Capture typed verdicts/traits/notes with the owner reply as evidence."""
    now = now or K.now_utc()
    value = " ".join(str(text or "").split())
    got = _group(K, conn, token, now)
    if got is None:
        raise FP.NotFound("no retro item %s" % token)
    outer, children = got
    members = [outer] + children
    talk = opener(K, conn, outer, children, now) or asked_talk(conn, members)
    venue = venue_of_visit(K, conn, members)
    target_bid = venue["bid"] if venue else outer["id"]
    row = conn.execute("SELECT ulid FROM block WHERE id=?;", (target_bid,)).fetchone()
    prior = talk_attr(conn, target_bid) or {}
    n = len(prior.get("replies") or []) + 1
    ref = "kit_life:block:%s:%s#%d" % (row[0], ATTR, n)
    verdicts, traits, notes, _v = read_reply(K, conn, outer, children, value, talk, ref, data)
    out = {"v": V, "visit": "kit_life:block:%s" % row[0], "item": outer["id"],
           "angle": talk["angle"] if talk else None,
           "verdicts": verdicts, "traits": traits, "notes": notes,
           "written": False, "follow_up": None}
    if venue:
        book = _book(K, conn)
        if book is not None:
            key, _nodes, _cz = FP.venue_of(book, conn, venue["place_id"], venue["name"],
                                           venue["bid"])
            out["target"] = FP.NS + key
    if dry_run or not (verdicts or traits or notes):
        out["dry_run"] = bool(dry_run)
        return out
    stored = dict(prior)
    stored["v"] = V
    stored["replies"] = list(prior.get("replies") or []) + [
        {"n": n, "text": value, "at": now, "angle": out["angle"]}]
    keep = [x for x in prior.get("verdicts") or []
            if x.get("holder") not in {v["holder"] for v in verdicts}]
    stored["verdicts"] = keep + verdicts
    stored["traits"] = list(prior.get("traits") or []) + traits
    stored["notes"] = list(prior.get("notes") or []) + notes
    conn.execute("BEGIN IMMEDIATE;")
    try:
        conn.execute("INSERT OR REPLACE INTO block_attr(block_id, attr_token, value, source) "
                     "VALUES(?,?,?,'user');",
                     (target_bid, ATTR, json.dumps(stored, ensure_ascii=False)))
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    if notes:
        day = str(_local_day(K, now, outer["tz"] or "Asia/Seoul"))
        for nt, fid in zip(notes, _rel_write(K, notes, day)):
            nt["fact_id"] = fid
    out["written"] = True
    return out


def history_verdicts(conn, bid):
    """(verdicts with quote, reply texts) stored on a visit, for dining-history."""
    att = talk_attr(conn, bid)
    if not att:
        return [], set()
    vs = [{"holder": x["holder"], "verdict": x["verdict"],
           "said_by": x.get("said_by") or "owner", "ref": x["ref"],
           "quote": x.get("quote") or ""}
          for x in att.get("verdicts") or []
          if x.get("holder") and x.get("verdict") and x.get("ref")]
    return vs, {r.get("text") for r in att.get("replies") or [] if r.get("text")}


# -- venue-traits ----------------------------------------------------------------

def _facts_traits(K, facts):
    """[(target, src)] a place page states: price band from the per-person
    price, drinks_friendly from its category."""
    out = []
    pp = (facts or {}).get("per_person") or {}
    won = pp.get("won")
    if isinstance(won, (int, float)) and won > 0:
        band = "low" if won < PRICE_LOW_WON else ("mid" if won < PRICE_HIGH_WON else "high")
        out.append((VENUE_NS + "price:" + band, pp.get("src") or "venue facts per_person"))
    cat = ((facts or {}).get("category") or {}).get("value") or ""
    if cat and any(w in cat for w, _v in words(K).get("drinks", [])):
        out.append((VENUE_NS + "drinks_friendly",
                    ((facts or {}).get("category") or {}).get("src") or "venue facts category"))
    return out


def _place_of_naver(conn, nid):
    for (pid,) in conn.execute("SELECT id FROM place ORDER BY id;"):
        if FP.place_naver_id(conn, pid) == nid:
            return pid
    return None


def venue_traits(K, conn_open, query, facts=None):
    q = (query or "").strip()
    if not q:
        raise FP.Usage("empty query")
    conn = conn_open()
    try:
        pid = None
        if FP.PLACE_ID_RE.match(q):
            pid = _place_of_naver(conn, q)
            if pid is None and facts is None:
                raise FP.NotFound("no kit place and no --facts for naver place %s" % q)
            if facts is not None and str(facts.get("place_id") or q) != q:
                raise FP.NotFound("--facts is for place %s, not %s" % (facts.get("place_id"), q))
        elif FP.KIT_PLACE_RE.match(q):
            pid = int(FP.KIT_PLACE_RE.match(q).group(1))
            if not conn.execute("SELECT 1 FROM place WHERE id=?;", (pid,)).fetchone():
                raise FP.NotFound("no kit place %s" % q)
        elif FP.BLOCK_RE.match(q) or FP.ULID_RE.match(q):
            row = FP._block_row(conn, q)
            if row is None:
                raise FP.NotFound("no kit block %s" % q)
            pid = row[4]
            if pid is None:
                raise FP.NotFound("block %d has no recorded place" % row[0])
        else:
            raise FP.NotFound("not a venue id (naver id, place:<id>, block:<id>)")
        if pid is not None and conn.execute("SELECT COALESCE(is_base,0) FROM place "
                                            "WHERE id=?;", (pid,)).fetchone()[0]:
            raise FP.NotFound("place:%d is the home base, not a venue" % pid)
        obs = observed_traits(conn, pid) if pid is not None else []
    finally:
        conn.close()
    try:
        food = FP.food_kind(K, conn_open, q, facts=facts)["chains"]
    except FP.NotFound:
        food = []
    keep = latest_traits(obs)
    sources = [{"target": t, "from": "observation", "quote": quote, "ref": ref, "at": at}
               for t, quote, ref, at in obs if keep.get(t[len(VENUE_NS):].split(":")[0]) == t]
    for t, src in _facts_traits(K, facts):
        k = t[len(VENUE_NS):].split(":")[0]
        if k not in keep:
            keep[k] = t
            sources.append({"target": t, "from": "facts", "src": src})
    venue = [keep[k] for k in sorted(keep, key=lambda k: (
        TRAIT_ORDER.index(k) if k in TRAIT_ORDER else 99, k))]
    return {"v": V, "available": True, "query": q, "food": food, "venue": venue,
            "sources": sources}


# -- CLI ------------------------------------------------------------------------

TRAITS_USAGE = ("usage: kit_life.sh venue-traits <naver place id | place:<id> | "
                "block:<id>> [--facts FILE] --json")
REPLY_USAGE = ("usage: kit_life.sh visit-talk-reply <item id|ulid> \"<owner words>\" "
               "[--data JSON] [--dry-run] --json")


def cli_venue_traits(K, argv):
    argv = FP._common(K, argv, TRAITS_USAGE)
    argv, facts_path = K._pop_opt(argv, "--facts")
    if len(argv) != 1 or not argv[0].strip():
        FP._usage(None, TRAITS_USAGE)
    query = argv[0].strip()

    def body():
        if not os.path.isfile(K.DB_PATH) or FP.module_off(K):
            return FP._emit({"v": V, "available": False})
        try:
            FP.Book(K)
        except RuntimeError:
            return FP._emit({"v": V, "available": False})
        facts = None
        if facts_path:
            with open(facts_path, encoding="utf-8") as fh:
                facts = json.load(fh)
        FP._emit(venue_traits(K, lambda: FP._ro(K), query, facts=facts))
    FP._run(K, body, None, TRAITS_USAGE)


def cli_visit_talk_context(K, argv):
    usage = 'usage: kit_life.sh visit-talk-context ITEM --data JSON --json'
    argv = FP._common(K, argv, usage)
    argv, raw = K._pop_opt(argv, '--data')
    if len(argv) != 1 or raw is None:
        FP._usage(None, usage)
    try:
        data = json.loads(raw)
    except ValueError:
        FP._usage('invalid context JSON', usage)
    def body():
        conn = K.event_conn()
        try:
            FP._emit(set_context(K, conn, argv[0], data))
        finally:
            conn.close()
    FP._run(K, body, None, usage)


def cli_visit_talk_reply(K, argv):
    argv = FP._common(K, argv, REPLY_USAGE)
    argv, raw = K._pop_opt(argv, '--data')
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        FP._usage('invalid capture JSON', REPLY_USAGE)
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    if len(argv) != 2 or not argv[0].strip():
        FP._usage(None, REPLY_USAGE)

    def body():
        conn = K.event_conn()
        try:
            FP._emit(reply(K, conn, argv[0].strip(), argv[1], dry_run=dry, data=data))
        finally:
            conn.close()
    FP._run(K, body, None, REPLY_USAGE)


if __name__ == "__main__":
    import sys
    sys.exit("visit_talk.py is driven by kit_life.sh (retro-items / visit-talk-reply / "
             "venue-traits)")
