#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""place_merge.py -- fold duplicate `place` rows into one, by a PINNED plan.

WHY THIS IS NOT IN MIGRATION 050
--------------------------------
050 splits the MODEL (place / place_address / sub_place).  Folding a specific
owner's duplicate rows is a different act on a different clock: it is keyed on
row IDs, and a row id means something different in every database that ever
mounted this kit.  A migration that merges `place 6 into place 19` is correct
for exactly one file on earth and a silent data-loss weapon for every other,
which is why it ships here instead -- as a tool that must be aimed, that
refuses to fire at anything it was not aimed at, and that shows its work before
it writes.

WHAT A MERGE IS
---------------
Merging A into B moves every reference to A onto B and retires A.  Concretely:

  travel_rule.place_id   A -> B
  block.place_id         A -> B            (the appointment's own place FK)
  ax_machine.gym_id      A -> B            (when that table exists)
  place_link             each edge of A is repointed; if the repointed PAIR
                         already exists, A's edge is FOLDED into it and its
                         measures are carried, never duplicated;  an edge that
                         becomes a self-pair (A->A after mapping) is DELETED
                         -- a trip from a place to itself is not knowledge
  measure                carried with the edge; if both edges hold an in-force
                         value for one metric, the SURVIVOR's stays in force
                         and the carried one is expired (valid_until stamped),
                         never deleted.  History is not the thing being merged
  aliases                A's name and A's aliases are taught to B, so every
                         string that used to resolve to A resolves to B
  place_address          A's address rows move to B as NON-primary (B's own
                         primary keeps the wheel; a merge does not re-answer
                         "which string does the map get")
  place A                status='gone' -- v3.2 disappearance, never DELETE
                         (036: past blocks and ax_machine.gym_id must keep
                         resolving)

Optionally the same entry declares the SUB-PLACE the merged row actually was:
merging "<studio> D룸" into "<studio>" with sub_place "D룸" is what turns a
duplicate place into a room of the survivor -- and, from that moment, 050's
trigger refuses to let that string become a place ever again.

THE OWNER'S ANSWER IS A FIELD, NOT A TONE OF VOICE
-------------------------------------------------
Which two rows are "the same place" -- and which of them SURVIVES -- is the
owner's answer and nothing else's.  `양화로 147 == 그라운드 본점` was true only
because he said so; no string comparison, no edge geometry and no model
produces it.  So it is not advice in a skill file, it is a required field:

    "decision": { "keep": <the surviving place id>,
                  "said": "<the owner's own words>" }

Every merge entry carries one.  An entry with no `decision`, a blank `said`,
or a `keep` that is not one of the two rows is REFUSED before anything opens a
transaction.  The entry may name its rows either way round --

    {"pair": [6, 19], "decision": {"keep": 19, "said": "..."}}
    {"from": 6, "into": 19, "decision": {"keep": 19, "said": "..."}}

-- and `pair` is the form that matters, because `pair` is exactly what the
DETECTOR (`kit_life.sh place-dupes`) emits: two ids and no direction.  There
is no path from a detector candidate to an applied merge that does not pass
through a `keep` the detector never supplies.  That absence is the mechanism;
the rest is bookkeeping.

`keep` is also pinned.  The fingerprint fields are keyed by ROLE (from_ulid /
into_ulid), so flipping `keep` after a plan was pinned points from_ulid at the
other row and the guard refuses -- the survivor cannot be quietly reversed
between the conversation and the write.

THE CONVERSATIONAL ENTRY POINT (--propose)
------------------------------------------
The owner must never hand-author JSON.  `--propose` builds the plan from the
answer he just gave, resolving each side through the SAME lookup
`place-resolve` uses, and pins it in the same read:

    place_merge.py --db <db> --plan <path> --propose \
        --pair "<one place>" "<the other>" \
        --keep "<the one he said survives>" \
        --said "<what he actually said>" [--sub-place "D룸"]

One pair per call; repeat to add entries to the same plan.  It refuses without
`--keep` and without `--said`, and it refuses a `--keep` that is not one of
the pair.  Then the ordinary two steps: `--dry-run` (show him), `--apply`.

THE FINGERPRINT GUARD
---------------------
Every plan entry carries the ULID of each row it names.  A ULID is opaque,
globally unique, and never reused, so a plan pinned against one database
cannot fire against another: `--apply` refuses unless every id still resolves
to the ulid the plan recorded.  A plan that has never been pinned cannot be
applied at all.

    place_merge.py --db <db> --plan <plan.json> --propose ...  # write + pin it
    place_merge.py --db <db> --plan <plan.json> --pin       # READ + stamp ulids
    place_merge.py --db <db> --plan <plan.json> --dry-run   # show every write
    place_merge.py --db <db> --plan <plan.json> --apply     # one transaction

`--pin` is the only step that decides anything a human should see: it prints
the NAME of every row the plan is about to fold, so the owner confirms the
aim before anything is written.  `--dry-run` is the default -- passing no mode
flag shows the plan and writes nothing.

REGRESSION CENSUS
-----------------
Every mode prints a census of live future travel blocks (count + the multiset
of start/end stamps) before, and `--apply` prints it again after.  A merge must
not move a single minute: it repoints FKs and folds edges, and every derived
block keeps its own stamps.  If the two censuses differ, the merge did
something it was not asked to do and the run says so on stderr.

Exit: 0 = done (or dry run clean); 2 = refused (guard, bad plan, bad db);
      3 = applied but the block census MOVED (the run is reported, not hidden).
"""
import sys as _sys, os as _os; _sys.dont_write_bytecode = True; _os.environ["PYTHONDONTWRITEBYTECODE"] = "1"   # C4b: residue under payload/ makes pack_install refuse the pack
import argparse
import collections
import json
import os
import sqlite3
import sys

MIN_USER_VERSION = 50          # place_address / sub_place must exist


def _err(msg, code=2):
    sys.stderr.write("place-merge: %s\n" % msg)
    sys.exit(code)


def _now(conn):
    return conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now');").fetchone()[0]


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
        (name,)).fetchone() is not None


def _place(conn, pid):
    return conn.execute(
        "SELECT id, ulid, name, status FROM place WHERE id=?;",
        (pid,)).fetchone()


# ── the census ────────────────────────────────────────────────────────────

def census(conn):
    """Live FUTURE travel blocks: how many, and at exactly which instants.

    The multiset of (start_at, end_at) is the thing that must not move.  A
    count alone would pass a merge that swapped two blocks' times."""
    # Read through the compat view, which is where `block_class` is a column
    # rather than a block_attr row -- the same surface the deriver and every
    # travel test read.  Asking the base tables here would mean restating the
    # view's own attr join, i.e. a second definition of "is this a travel
    # block" that can drift from the first.
    rows = conn.execute(
        "SELECT start_at, end_at FROM timeblock "
        "WHERE block_class='travel' AND settled_at IS NULL "
        "AND start_at IS NOT NULL;").fetchall()
    return collections.Counter((a, b) for a, b in rows)


def census_line(c):
    return "blocks=%d distinct_spans=%d" % (sum(c.values()), len(c))


# ── plan loading + the fingerprint guard ─────────────────────────────────

def _entry_ids(m):
    """The two rows a merge entry is about, in the form it was written.

    `pair` is the DETECTOR's form -- two ids and nothing else, because the
    detector has no business having an opinion about direction.  `from`/`into`
    is the hand-authored form and already states one.  Both are accepted; both
    then have to survive normalize_merge, which is where the direction is
    required to have come from the owner either way."""
    if "pair" in m:
        ids = m["pair"]
        if (not isinstance(ids, list) or len(ids) != 2
                or not all(isinstance(i, int) for i in ids)):
            _err("'pair' must be exactly two integer place ids: %r" % m)
        return [ids[0], ids[1]]
    for k in ("from", "into"):
        if not isinstance(m.get(k), int):
            _err("every merge entry needs 'pair': [a, b] or integer 'from' "
                 "and 'into': %r" % m)
    return [m["from"], m["into"]]


def normalize_merge(m):
    """Resolve one entry to from/into -- and REFUSE it if nobody answered.

    This is the D1 guard, and it is a guard rather than a sentence in a skill
    file on purpose.  Two rows being one place, and which of them survives, is
    knowledge only the owner has: the detector can notice that `양화로 147`
    and `그라운드 본점` look related, and it still cannot know they are the
    same building.  So the survivor is a REQUIRED field, and the tool has no
    default for it.  `--apply` never sees an entry that got this far without
    one.

    Refused, before any transaction opens:
      * no `decision` block at all          -- nobody was asked
      * `decision.keep` not one of the pair  -- the merge was re-aimed at a row
                                                the owner was never shown
      * blank `decision.said`                -- an unattributed merge is a
                                                guess with a transaction
                                                behind it
      * the two ids are the same row
    """
    ids = _entry_ids(m)
    if ids[0] == ids[1]:
        _err("a place cannot be merged into itself: %r" % m)
    d = m.get("decision")
    if not isinstance(d, dict):
        _err("REFUSED: merge entry for places %r carries no `decision`. "
             "Which two rows are the same place -- and which one SURVIVES -- "
             "is the owner's answer, not this tool's and not the agent's. Ask "
             "him, then record what he said:  --propose --pair A B --keep "
             "<the survivor> --said \"<his words>\"" % (ids,))
    keep = d.get("keep")
    if keep not in ids:
        _err("REFUSED: decision.keep=%r is not one of the pair %r. The "
             "survivor has to be one of the two rows the owner was shown."
             % (keep, ids))
    said = " ".join(str(d.get("said") or "").split())
    if not said:
        _err("REFUSED: decision.said is empty for pair %r. Record the owner's "
             "own words -- a merge nobody can be shown to have authorized is "
             "a guess with a transaction behind it." % (ids,))
    d["said"] = said
    # from/into are DERIVED from the answer, so the `pair` form and the
    # hand-authored form reach the rest of this file as one shape.  For the
    # hand-authored form this is also a cross-check: a `decision` that
    # disagrees with `into` re-keys the pins and the fingerprint guard refuses.
    m["into"] = keep
    m["from"] = [i for i in ids if i != keep][0]
    return m


def load_plan(path):
    try:
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, ValueError) as e:
        _err("cannot read plan %s: %s" % (path, e))
    if not isinstance(plan, dict):
        _err("plan must be a JSON object")
    plan.setdefault("merges", [])
    plan.setdefault("sub_places", [])
    for m in plan["merges"]:
        normalize_merge(m)
    for sp in plan["sub_places"]:
        if not isinstance(sp.get("place"), int) or not sp.get("name"):
            _err("every sub_places entry needs 'place' (int) and 'name': %r" % sp)
    return plan


def write_plan(plan, path):
    """The plan as it should live ON DISK.

    `from`/`into` are DERIVED from `pair` + `decision.keep` every time the plan
    is loaded, so an entry written in the `pair` form must not ALSO carry them
    in the file: two spellings of one direction is the drift this estate keeps
    removing (place.address vs place_address, place.to_min vs measure).  The
    hand-authored form, which has no `pair`, keeps its own columns -- there
    they are the source, not a copy."""
    out = dict(plan)
    out["merges"] = [
        {k: v for k, v in m.items()
         if not ("pair" in m and k in ("from", "into"))}
        for m in plan.get("merges", [])]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _pin_pairs(plan):
    """(id, ulid_field, entry) for every row the plan names."""
    out = []
    for m in plan["merges"]:
        out.append((m["from"], "from_ulid", m))
        out.append((m["into"], "into_ulid", m))
    for sp in plan["sub_places"]:
        out.append((sp["place"], "place_ulid", sp))
    return out


def do_pin(conn, plan, path):
    """READ the database, stamp each named id's current ULID into the plan, and
    print the NAME beside it.  This is the step a human reads: the ids came off
    a decision record, and the names are how the owner checks the tool is aimed where
    he thinks it is."""
    seen = {}
    for pid, field, entry in _pin_pairs(plan):
        row = _place(conn, pid)
        if row is None:
            _err("--pin: place id %d does not exist in this database" % pid)
        entry[field] = row[1]
        if pid not in seen:
            seen[pid] = row
            print("pin\t%d\t%s\t%s\t%s" % (row[0], row[1], row[3], row[2]))
    write_plan(plan, path)
    print("pinned\t%d place rows\t%s" % (len(seen), path))


# ── --propose: the door a CONVERSATION comes through ──────────────────────

_KIT_LIFE = []


def _kit_life():
    """kit_life, imported once, or None.  Imported LAZILY rather than at module
    load: every other mode of this tool is deliberately self-contained stdlib,
    and only the name-resolving half of --propose needs the kit's own lookup."""
    if not _KIT_LIFE:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        try:
            import kit_life
        except Exception:
            kit_life = None
        _KIT_LIFE.append(kit_life)
    return _KIT_LIFE[0]


def _resolve_place(conn, token):
    """A place id / ulid / exact name / taught alias -> the place row.

    Routed through kit_life's `travel_place_get` -- the SAME lookup
    `place-resolve` answers with -- rather than restated here.  A second
    resolver in this file could land a name on a different row than the one
    the skill just showed the owner, and the whole point of --propose is that
    what he confirmed is what gets folded.  If kit_life cannot be imported,
    only integer ids are accepted; a name is refused rather than guessed."""
    kit_life = _kit_life()
    if kit_life is not None:
        row = kit_life.travel_place_get(conn, token)
        if row is not None:
            return _place(conn, row["id"])
        return None
    try:
        return _place(conn, int(token))
    except (TypeError, ValueError):
        _err("cannot resolve %r: kit_life.py is not importable from %s, so "
             "only integer place ids can be named here"
             % (token, os.path.dirname(os.path.abspath(__file__))))


def do_propose(conn, path, args):
    """Append ONE merge entry, built from the answer the owner just gave.

    The owner never hand-authors JSON and never types an id.  What he supplies
    is the part nothing else can: which two places are one, which one stays,
    and the sentence he said it in.  This function's whole job is to turn that
    into the pinned plan the rest of the file already knows how to refuse.

    It is deliberately one pair per call.  A merge is a question with one
    answer, and batching two of them behind a single confirmation is how a
    second, unasked fold rides in on the first."""
    if not args.pair or len(args.pair) != 2:
        _err("--propose needs --pair <place> <place>")
    if not args.keep:
        _err("--propose REFUSED: no --keep. Which of the two rows SURVIVES is "
             "the owner's answer -- there is no default for it here and there "
             "is not going to be one. Ask him, then pass the place he named.")
    if not " ".join(str(args.said or "").split()):
        _err("--propose REFUSED: no --said. Record the owner's own words; a "
             "merge nobody can be shown to have authorized is a guess with a "
             "transaction behind it.")
    rows = []
    for tok in args.pair:
        row = _resolve_place(conn, tok)
        if row is None:
            _err("--propose: no place resolves to %r in this database. Run "
                 "`kit_life.sh place-resolve %s` and use the name it echoes."
                 % (tok, tok))
        rows.append(row)
    if rows[0][0] == rows[1][0]:
        _err("--propose: both sides of --pair resolve to place %d (%s). That "
             "is one row, not two." % (rows[0][0], rows[0][2]))
    keep = _resolve_place(conn, args.keep)
    if keep is None:
        _err("--propose: no place resolves to --keep %r" % (args.keep,))
    if keep[0] not in (rows[0][0], rows[1][0]):
        _err("--propose REFUSED: --keep %r resolves to place %d (%s), which is "
             "not one of the pair (%d %s / %d %s). The survivor has to be one "
             "of the two rows the owner was shown."
             % (args.keep, keep[0], keep[2], rows[0][0], rows[0][2],
                rows[1][0], rows[1][2]))
    for row in rows:
        if row[3] != "active":
            _err("--propose: place %d (%s) is already '%s'. A retired row is "
                 "somebody's answered question; nothing folds into or out of "
                 "it." % (row[0], row[2], row[3]))

    plan = load_plan(path) if os.path.exists(path) else {
        "_note": [
            "Built by place_merge.py --propose from a conversation. Every",
            "entry carries the owner's own words in decision.said and the id",
            "he named as the survivor in decision.keep; the tool refuses an",
            "entry that carries neither. The ulid fields pin this plan to ONE",
            "database -- it cannot fire against any other.",
        ],
        "merges": [], "sub_places": []}
    entry = {
        "pair": [rows[0][0], rows[1][0]],
        "from_ulid": "", "into_ulid": "",
        "sub_place": (" ".join(str(args.sub_place).split())
                      if args.sub_place else None),
        "decision": {"keep": keep[0],
                     "said": " ".join(str(args.said).split()),
                     "asked_at": _now(conn)},
    }
    normalize_merge(entry)
    for have in plan["merges"]:
        if sorted(_entry_ids(have)) == sorted(entry["pair"]):
            _err("--propose: this plan already carries an entry for places "
                 "%r. Edit it or start a new plan file -- silently replacing "
                 "one answer with another is exactly what this tool does not "
                 "do." % (entry["pair"],))
    plan["merges"].append(entry)
    write_plan(plan, path)
    fold = rows[0] if rows[0][0] == entry["from"] else rows[1]
    print("propose\t%d (%s)\t->\t%d (%s)%s"
          % (fold[0], fold[2], keep[0], keep[2],
             ("\tsub-place " + entry["sub_place"]) if entry["sub_place"]
             else ""))
    print("because\t%s" % entry["decision"]["said"])
    return plan


def check_pins(conn, plan):
    """The guard.  Refuses a plan that was never pinned, and refuses a plan
    whose pins no longer match -- which is what happens when it is pointed at a
    different database, or at the same one after the ids moved."""
    for pid, field, entry in _pin_pairs(plan):
        want = entry.get(field)
        if not want:
            _err("plan is NOT PINNED (%s missing for place %d). Run --pin "
                 "first and read what it prints." % (field, pid))
        row = _place(conn, pid)
        if row is None:
            _err("refused: place id %d does not exist in this database" % pid)
        if row[1] != want:
            _err("refused: place %d is ulid %s here, but the plan was pinned "
                 "against %s. This plan was written for a DIFFERENT database "
                 "(or these rows have moved). Nothing changed."
                 % (pid, row[1], want))


# ── the merge itself ─────────────────────────────────────────────────────

def _survivor(mapping, pid):
    """Follow the merge chain so `A -> B, B -> C` lands A on C.  Bounded by the
    number of entries, so a cycle in a hand-written plan stops rather than
    hangs."""
    seen = set()
    while pid in mapping and pid not in seen:
        seen.add(pid)
        pid = mapping[pid]
    return pid


def plan_actions(conn, plan):
    """Everything the merge would write, as printable lines.  Pure read."""
    mapping = {m["from"]: m["into"] for m in plan["merges"]}
    acts = []
    for m in plan["merges"]:
        src, dst = m["from"], _survivor(mapping, m["into"])
        s, d = _place(conn, src), _place(conn, dst)
        acts.append("merge\t%d (%s)\t->\t%d (%s)" % (src, s[2], dst, d[2]))
        # The authorization, printed with the act it authorizes.  A dry run the
        # owner reads should show him his own sentence beside the row it is
        # about to retire -- that is the only way he can catch a merge aimed by
        # a misheard answer before it fires.
        acts.append("  because\t%s" % m["decision"]["said"])
        if m.get("sub_place"):
            acts.append("  sub-place\t%d\t%s" % (dst, m["sub_place"]))
        for tbl, col in (("travel_rule", "place_id"), ("block", "place_id"),
                         ("ax_machine", "gym_id")):
            if not _table_exists(conn, tbl):
                continue
            n = conn.execute("SELECT COUNT(*) FROM %s WHERE %s=?;" % (tbl, col),
                             (src,)).fetchone()[0]
            if n:
                acts.append("  repoint\t%s.%s\t%d row(s)" % (tbl, col, n))
        for lid, f, t in conn.execute(
                "SELECT id, from_place_id, to_place_id FROM place_link "
                "WHERE from_place_id=? OR to_place_id=?;", (src, src)):
            nf, nt = _survivor(mapping, f), _survivor(mapping, t)
            nmeas = conn.execute(
                "SELECT COUNT(*) FROM measure WHERE place_link_id=?;",
                (lid,)).fetchone()[0]
            if nf == nt:
                acts.append("  edge\t%d (%d->%d)\tDELETE (self-pair after "
                            "merge), %d measure row(s)" % (lid, f, t, nmeas))
                continue
            keep = conn.execute(
                "SELECT id FROM place_link WHERE from_place_id=? "
                "AND to_place_id=? AND id!=?;", (nf, nt, lid)).fetchone()
            if keep is None:
                acts.append("  edge\t%d (%d->%d)\t-> (%d->%d)"
                            % (lid, f, t, nf, nt))
            else:
                acts.append("  edge\t%d (%d->%d)\tFOLD into %d (%d->%d), "
                            "%d measure row(s) carried"
                            % (lid, f, t, keep[0], nf, nt, nmeas))
        na = conn.execute("SELECT COUNT(*) FROM place_address WHERE place_id=?;",
                          (src,)).fetchone()[0]
        if na:
            acts.append("  address\t%d row(s) move to %d as non-primary"
                        % (na, dst))
        acts.append("  retire\tplace %d status -> gone" % src)
    for sp in plan["sub_places"]:
        p = _place(conn, sp["place"])
        acts.append("sub-place\t%d (%s)\t%s" % (sp["place"], p[2], sp["name"]))
        if sp.get("primary_address"):
            acts.append("  primary-address\t%s\t%s"
                        % (sp["primary_address"].get("kind", "?"),
                           sp["primary_address"].get("value", "")))
    return acts


def _slug(name):
    import unicodedata
    out = ''.join(c if c.isascii() and (c.isalnum() or c in '_-') else '_'
                  for c in unicodedata.normalize('NFKD', name or ''))
    return '_'.join(filter(None, out.lower().split('_')))


def _add_sub_place(conn, pid, name, now):
    name = " ".join(str(name).split())
    if conn.execute("SELECT 1 FROM sub_place WHERE place_id=? AND name=?;",
                    (pid, name)).fetchone():
        return
    tok = _slug(name)
    if not tok or conn.execute(
            "SELECT 1 FROM sub_place WHERE place_id=? AND token=?;",
            (pid, tok)).fetchone():
        tok = "sub_%d_%d" % (pid, conn.execute(
            "SELECT COUNT(*) FROM sub_place WHERE place_id=?;",
            (pid,)).fetchone()[0] + 1)
    conn.execute("INSERT INTO sub_place(place_id, name, token, created_at, "
                 "updated_at) VALUES(?,?,?,?,?);", (pid, name, tok, now, now))


def _teach_aliases(conn, src, dst):
    """Every string that used to reach A must still reach B.  A's NAME is the
    first of those strings and the one most likely to be typed again."""
    keys = []
    for pid in (src,):
        row = conn.execute("SELECT name, aliases FROM place WHERE id=?;",
                           (pid,)).fetchone()
        if row[0]:
            keys.append(" ".join(str(row[0]).split()))
        try:
            arr = json.loads(row[1]) if row[1] else []
        except ValueError:
            arr = []
        keys += [a for a in arr if isinstance(a, str)]
    cur = conn.execute("SELECT aliases FROM place WHERE id=?;", (dst,)).fetchone()
    try:
        have = json.loads(cur[0]) if cur[0] else []
    except ValueError:
        have = []
    have = [a for a in have if isinstance(a, str)]
    dstname = conn.execute("SELECT name FROM place WHERE id=?;",
                           (dst,)).fetchone()[0]
    for k in keys:
        if k and k != dstname and k not in have:
            have.append(k)
    conn.execute("UPDATE place SET aliases=? WHERE id=?;",
                 (json.dumps(have, ensure_ascii=False) if have else None, dst))


def _fold_edges(conn, src, mapping, now):
    for lid, f, t in conn.execute(
            "SELECT id, from_place_id, to_place_id FROM place_link "
            "WHERE from_place_id=? OR to_place_id=?;", (src, src)).fetchall():
        nf, nt = _survivor(mapping, f), _survivor(mapping, t)
        if nf == nt:
            # A trip from a place to itself is not knowledge. The measures die
            # with the edge (FK ON DELETE CASCADE) because they were about a
            # pair that, after the merge, never existed.
            conn.execute("DELETE FROM place_link WHERE id=?;", (lid,))
            continue
        keep = conn.execute(
            "SELECT id FROM place_link WHERE from_place_id=? AND to_place_id=? "
            "AND id!=?;", (nf, nt, lid)).fetchone()
        if keep is None:
            conn.execute("UPDATE place_link SET from_place_id=?, "
                         "to_place_id=? WHERE id=?;", (nf, nt, lid))
            continue
        survivor = keep[0]
        # Carry the history, but never seat two answers. For each metric the
        # SURVIVOR already answers, the carried rows arrive already expired;
        # for a metric it does not answer, the carried value takes the seat.
        for (metric,) in conn.execute(
                "SELECT DISTINCT metric FROM measure WHERE place_link_id=?;",
                (lid,)).fetchall():
            in_force = conn.execute(
                "SELECT COUNT(*) FROM measure WHERE place_link_id=? "
                "AND metric=? AND basis='actual' "
                "AND (valid_until IS NULL OR valid_until > ?);",
                (survivor, metric, now)).fetchone()[0]
            if in_force:
                conn.execute(
                    "UPDATE measure SET valid_until=? WHERE place_link_id=? "
                    "AND metric=? AND (valid_until IS NULL OR valid_until > ?);",
                    (now, lid, metric, now))
        # ux_measure_row is (link, metric, seq, basis, observed_at): a carried
        # row that would collide with one already on the survivor is a literal
        # duplicate of it, so drop it rather than inventing a second.
        conn.execute(
            "DELETE FROM measure WHERE place_link_id=? AND EXISTS("
            "  SELECT 1 FROM measure m2 WHERE m2.place_link_id=? "
            "   AND m2.metric = measure.metric AND m2.basis = measure.basis "
            "   AND COALESCE(m2.seq,-1) = COALESCE(measure.seq,-1) "
            "   AND m2.observed_at = measure.observed_at);", (lid, survivor))
        conn.execute("UPDATE measure SET place_link_id=? WHERE place_link_id=?;",
                     (survivor, lid))
        conn.execute("DELETE FROM place_link WHERE id=?;", (lid,))


def apply_plan(conn, plan):
    now = _now(conn)
    mapping = {m["from"]: m["into"] for m in plan["merges"]}
    conn.execute("BEGIN IMMEDIATE;")
    try:
        for m in plan["merges"]:
            src, dst = m["from"], _survivor(mapping, m["into"])
            _teach_aliases(conn, src, dst)
            if m.get("sub_place"):
                _add_sub_place(conn, dst, m["sub_place"], now)
            _fold_edges(conn, src, mapping, now)
            for tbl, col in (("travel_rule", "place_id"),
                             ("block", "place_id"), ("ax_machine", "gym_id")):
                if _table_exists(conn, tbl):
                    conn.execute("UPDATE %s SET %s=? WHERE %s=?;"
                                 % (tbl, col, col), (dst, src))
            # Addresses move as NON-primary: which string the map gets is the
            # owner's answer, and a merge is not that question being asked.
            conn.execute(
                "DELETE FROM place_address WHERE place_id=? AND value IN "
                "(SELECT value FROM place_address WHERE place_id=?);",
                (src, dst))
            conn.execute("UPDATE place_address SET place_id=?, is_primary=0, "
                         "updated_at=? WHERE place_id=?;", (dst, now, src))
            conn.execute("UPDATE place SET status='gone', updated_at=? "
                         "WHERE id=?;", (now, src))
        for sp in plan["sub_places"]:
            _add_sub_place(conn, sp["place"], sp["name"], now)
            pa = sp.get("primary_address")
            if pa and pa.get("value"):
                conn.execute("UPDATE place_address SET is_primary=0, "
                             "updated_at=? WHERE place_id=? AND is_primary=1;",
                             (now, sp["place"]))
                conn.execute(
                    "INSERT INTO place_address(place_id, kind, value, "
                    "is_primary, created_at, updated_at) VALUES(?,?,?,1,?,?) "
                    "ON CONFLICT(place_id, value) DO UPDATE SET kind=excluded."
                    "kind, is_primary=1, updated_at=excluded.updated_at;",
                    (sp["place"], pa.get("kind", "legacy"),
                     " ".join(str(pa["value"]).split()), now, now))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.split("\n")[0])
    # --db defaults to the kit's own database, the same fixed
    # <script dir>/kit_life.db that kit_life.py opens.  A skill driving this
    # tool from a conversation should not have to know a path the rest of the
    # CLI never asks for; a test pointing at a throwaway copy still passes one.
    ap.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "kit_life.db"))
    ap.add_argument("--plan", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--propose", action="store_true",
                   help="append ONE merge entry from the owner's answer, and pin it")
    g.add_argument("--pin", action="store_true",
                   help="READ the db and stamp each named id's ulid into the plan")
    g.add_argument("--dry-run", action="store_true", help="(default) show, write nothing")
    g.add_argument("--apply", action="store_true", help="write, in one transaction")
    # --propose's inputs. --keep and --said are the OWNER's; there is no
    # default for either, and their absence is a refusal, not a prompt.
    ap.add_argument("--pair", nargs=2, metavar=("PLACE", "PLACE"),
                    help="the two places (id / ulid / name / taught alias)")
    ap.add_argument("--keep", metavar="PLACE",
                    help="the one the OWNER said survives -- must be one of --pair")
    ap.add_argument("--said", metavar="TEXT",
                    help="the owner's own words, recorded with the merge")
    ap.add_argument("--sub-place", dest="sub_place", metavar="NAME",
                    help="what the folded row actually was: a room / floor / hall")
    args = ap.parse_args(argv)
    if not args.propose:
        for flag, val in (("--pair", args.pair), ("--keep", args.keep),
                          ("--said", args.said),
                          ("--sub-place", args.sub_place)):
            if val:
                _err("%s is an argument of --propose; it decides nothing in "
                     "any other mode." % flag)

    if not os.path.exists(args.db):
        _err("no such database: %s" % args.db)
    # --propose is the one mode that may be handed a path that does not exist
    # yet: it is what CREATES a plan.  Every other mode needs one already.
    plan = None if args.propose else load_plan(args.plan)
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        uv = conn.execute("PRAGMA user_version;").fetchone()[0]
        if uv < MIN_USER_VERSION:
            _err("refused: this database is at user_version %d; the place/"
                 "address/sub-place model lands at %d. Migrate first."
                 % (uv, MIN_USER_VERSION))
        if args.propose:
            # Propose, then PIN in the same read: --propose has just resolved
            # every row it names, so stamping the fingerprint now costs nothing
            # and a plan that was never pinned is a plan that cannot be
            # applied.  do_pin prints the NAME of every row, which is the line
            # the owner reads back before the dry run.
            plan = do_propose(conn, args.plan, args)
            do_pin(conn, plan, args.plan)
            print("next\t--dry-run (show him), then --apply")
            return 0
        if args.pin:
            do_pin(conn, plan, args.plan)
            return 0
        check_pins(conn, plan)
        before = census(conn)
        print("census-before\t%s" % census_line(before))
        for line in plan_actions(conn, plan):
            print(line)
        if not args.apply:
            print("dry-run\tnothing written")
            return 0
        apply_plan(conn, plan)
        after = census(conn)
        print("census-after\t%s" % census_line(after))
        if after != before:
            sys.stderr.write(
                "place-merge: BLOCK CENSUS MOVED -- a merge must repoint FKs "
                "and fold edges, never change a derived block's time. "
                "before=%s after=%s\n" % (census_line(before), census_line(after)))
            return 3
        print("applied\tmerges=%d sub_places=%d\tblock census unchanged"
              % (len(plan["merges"]), len(plan["sub_places"])))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
