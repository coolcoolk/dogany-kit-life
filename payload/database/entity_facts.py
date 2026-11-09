"""entity_facts.py -- the kit's entity-facts provider (framework DGN-1962).

The framework's memory-engine recall hook (memory.py, ENTITY_FACTS_MODULE)
finds this file on its kit discovery ladder (LIFEKIT_DIR -> PROJECT_ROOT/
database -> ../database) and calls

    get_entity_facts(prompt, today) -> [{"entity", "aliases", "facts": [
        {"text", "date"?, "as_of"?, "source"?, "topics"?}]}]

on a daemon thread under a 1.0 s budget; a raise, timeout or malformed
return drops the lane for that turn, never the turn.  The facts render as
structured state AHEAD of recalled memory and withhold recalled lines on a
subject a fact covers (founding incident 2026-10-09 09:54: a two-month-old
memory line "due after the holiday" drove gift advice a week after the kit
recorded the child's birth).

What this returns is exactly the noun-context person facts (noun_registry:
scan / resolve / person_facts) -- one reader, so the hook and the skills
never disagree:
  entities  persons NAMED in the prompt (073 dictionary: name, display name,
            alias, particle-aware).  A name two persons share is ambiguous:
            no entity.  At most MAX_ENTITIES, in order of first mention.
  facts     at most MAX_FACTS per person, most decisive first: recent births
            (own / a child's, 0..60 days before `today`), relation to the
            owner, birthday, family / social links, residence, job, gender.
            Contact details are never returned.

Read-only: query_only connection, no get_conn(), no migration, no writes.
Fact text wording matters to the framework: a fact text that contains a
recall_subjects.json group term covers that subject (withholds recalled
memory on it).  The own-birthday fact therefore says "\\uc0dd\\uc77c"
(not "birthday": it contains "birth" and would cover the birth subject);
birth facts say "born" on purpose.
"""
import datetime
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_ENTITIES = 5
MAX_FACTS = 8
BIRTHDAY_WORD = u"\uc0dd\uc77c"          # "birthday" without the "birth" term
LINK_ORDER = ("spouse", "partner", "child", "parent", "sibling", "family")
PROFILE_FIELDS = ("residence", "job", "gender")


def _kit():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import kit_life
    import noun_registry
    return kit_life, noun_registry


def _day(text):
    try:
        return datetime.date.fromisoformat(str(text or "")[:10]).isoformat()
    except ValueError:
        return None


def _ref(pid):
    return "person#%d" % pid


def _created(conn, pid):
    row = conn.execute("SELECT created_at FROM persons WHERE id=?;",
                       (pid,)).fetchone()
    return _day(row[0]) if row else None


def _facts(conn, p):
    """person_facts() dict -> contract facts, most decisive first."""
    out = []
    for e in p["recent_events"]:
        if e["kind"] == "child_born":
            text = "child %s (%s) born %s" % (e["name"], _ref(e["person_id"]),
                                              e["date"])
        else:
            text = "born %s" % e["date"]
        f = {"text": text, "date": e["date"], "source": _ref(e["person_id"]),
             "topics": ["birth"]}
        as_of = _created(conn, e["person_id"])
        if as_of:
            f["as_of"] = as_of
        out.append(f)
    if p["relation"] == "pet" or (p["relation"] or "").startswith(("pet_", "pet:")):
        species = (p["relation"][4:] if p["relation"].startswith(("pet_", "pet:"))
                   else {"cat": "cat", "dog": "dog"}.get(p["relation"], "unknown"))
        out.append({"text": "pet species: %s" % species,
                    "source": _ref(p["id"]),
                    "topics": ["species", "cat", "dog", "\uace0\uc591\uc774",
                               "\uac15\uc544\uc9c0", "\ubc18\ub824\uacac", "\ubc18\ub824\ubb18"]})
    if p["relation"]:
        out.append({"text": "relation to owner: %s" % p["relation"],
                    "source": _ref(p["id"])})
    born = _day(p["birthday"])
    if born and not any(e["kind"] == "born" for e in p["recent_events"]):
        out.append({"text": "%s %s" % (BIRTHDAY_WORD, born[5:]),
                    "date": born, "source": _ref(p["id"]),
                    "topics": ["birthday"]})
    elif not born:
        K, _ = _kit()
        if K.birthday_parts(p["birthday"]):
            # The framework date field requires a complete date. Never
            # invent a year/day merely to attach a timestamp to a fact.
            out.append({"text": K._person_value_label("birthday", p["birthday"]),
                        "source": _ref(p["id"]), "topics": ["birthday"]})
    rels = sorted(p["relations"], key=lambda r: (
        LINK_ORDER.index(r["role"]) if r["role"] in LINK_ORDER
        else len(LINK_ORDER), r["name"], r["person_id"]))
    told = {e["person_id"] for e in p["recent_events"]
            if e["kind"] == "child_born"}
    for r in rels:
        if r["role"] == "child" and r["person_id"] in told:
            continue                    # the birth fact already names them
        out.append({"text": "%s %s (%s)" % (r["role"], r["name"],
                                            _ref(r["person_id"])),
                    "source": _ref(p["id"])})
    for field in PROFILE_FIELDS:
        v = " ".join(str(p[field] or "").split())
        if v:
            out.append({"text": "%s: %s" % (field, v),
                        "source": _ref(p["id"])})
    return out[:MAX_FACTS]


def entity_facts(K, N, conn, prompt, today):
    """The provider body on an open connection (tests call this)."""
    if not N.ready(conn):
        return []
    res = N.context(K, conn, prompt, start=today, days=0)
    by_id = {p["id"]: p for p in res["persons"]}
    out = []
    for n in res["names"]:
        if n["kind"] != "person" or n["ref"] not in by_id:
            continue
        p = by_id.pop(n["ref"])
        aliases = []
        for s in N.surfaces(conn):
            if s["kind"] == "person" and s["ref"] == p["id"] \
                    and s["surface"] != p["display"] \
                    and s["surface"] not in aliases:
                aliases.append(s["surface"])
        facts = _facts(conn, p)
        if facts:
            out.append({"entity": p["display"], "aliases": aliases,
                        "facts": facts})
        if len(out) >= MAX_ENTITIES:
            break
    return out


def get_entity_facts(prompt, today):
    """Framework entry point.  prompt: the user turn; today: local
    YYYY-MM-DD.  [] when no person is named or the kit has no database."""
    if not isinstance(prompt, str) or not prompt.strip():
        return []
    today = datetime.date.fromisoformat(str(today)).isoformat()
    K, N = _kit()
    if not os.path.isfile(K.DB_PATH):
        return []
    conn = sqlite3.connect(K.DB_PATH, timeout=0.5)
    try:
        conn.execute("PRAGMA query_only=ON;")
        return entity_facts(K, N, conn, prompt, today)
    finally:
        conn.close()
