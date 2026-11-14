#!/usr/bin/env python3
"""dining_decide.py -- dine-choose: the restaurant the owner picked becomes
the appointment's place.

  kit_life.sh dine-choose <appointment id | ulid> --facts FILE [--new-confirmed]

(the ulid is the id= field of dining-session's context_events.py EVENT line)

WHY THIS EXISTS. On 2026-10-15 the owner picked a restaurant for the
"dalryeok" gathering, but the appointment kept its meeting point (a station)
as the place and the restaurant lived only in the retro text -- so
dining-history had no venue for the visit and the party's taste could not
build up. Owner rule 2026-10-09: when a restaurant is chosen, put it in as
the appointment's place and recompute the travel.

FILE is the dining-session facts step's own output (venue_facts.py JSON:
place_id, name, address, link, lat/lng/naver_ref, category, kind). No fetch
here: the kit registers what the facts step already read.

Steps, each through the kit's existing path:
  1. the venue as a kit place, find-first: a place already carrying this
     Naver id, else the exact name (adopted when it carries no other Naver
     id), else the near-match gate (exit 5, one question; --new-confirmed =
     the owner said "a different place"; the appointment's current place and
     places with another Naver id are not candidates) and travel_place_add. What the
     place lacks is taught, nothing it has is overwritten: road address
     (primary), the Naver place link (place_address map_url), the position
     (place_coord, the Naver directions end "lng,lat,name,id,PLACE_POI").
  2. the meeting point: when the appointment's place / location differs
     from the venue, it is kept in the appointment's NOTE (timeblock note,
     a META_COLS_COMMON column, version CAS) as one line "meet at <place>"
     (i18n dining.meet_note). Not the origin: origin_place_id is where the
     owner LEAVES from and drives the travel leg (the 10-15 row holds origin
     = home); the meeting point is where the party gathers before walking
     over, a fact for the owner, not a travel end. Written once: a later
     re-decide to another venue does not record the first venue as a
     meeting point.
  3. appt-upd <id> location=<venue> -- the one place-change path: the room
     of the old place is cleared, travel re-derives to the venue
     (appt_rederive), the card prints, the mirror is enqueued.
  4. the decided venue's kinds + category on the visit (block_attr
     dine_venue {"naver_id","category","kinds","meet_place_id"}): what food-kind block:<id>
     and dining-history read afterwards (target kit_life:food:naver_<id>).

Output: appt-upd's own lines and card, then
  DINE_CHOSEN <TAB> <appt id> <TAB> <place id> <TAB> <name> <TAB> naver_<id> <TAB> minted|existing
  MEET_KEPT <TAB> <appt id> <TAB> note <TAB> <meeting point>     (when kept)
Exit: 0 done; 2 usage / bad facts; 3 VENUE_CONFLICT (the name is a kit
place carrying ANOTHER Naver id, or the home base); 5 near match (nothing
written); appt-upd's own refusals pass through.

Functions take the kit_life module as K (errand.py convention).
"""
import json
import re
import sys

import food_pref as FP

USAGE = ("usage: kit_life.sh dine-choose <appointment id | ulid> --facts FILE "
         "[--new-confirmed]")
MEET_KEY = "dining.meet_note"
MEET_FALLBACK = "Meet at {place}"
CONFLICT_CODE = 3
CAS_TRIES = 3


def load_facts(path):
    """The fields dine-choose needs from a venue_facts.py JSON; ValueError
    when the file is not one (an exit-5 error object included)."""
    with open(path, encoding="utf-8") as fh:
        f = json.load(fh)
    if not isinstance(f, dict) or f.get("error"):
        raise ValueError("facts file is not a readable venue_facts result")
    nid = str(f.get("place_id") or "").strip()
    name = " ".join(str(f.get("name") or "").split())
    if not FP.PLACE_ID_RE.match(nid) or not name:
        raise ValueError("facts need place_id (naver digits) and name")
    return {"naver_id": nid, "name": name,
            "address": (f.get("address") or "").strip() or None,
            "link": "https://map.naver.com/p/entry/place/%s" % nid,
            "lat": f.get("lat"), "lng": f.get("lng"),
            "naver_ref": f.get("naver_ref") or None,
            "category": (f.get("category") or {}).get("value"),
            "kinds": list((f.get("kind") or {}).get("tokens") or [])}


def place_by_naver_id(K, conn, nid):
    """An active kit place already carrying Naver id `nid`, or None."""
    like = "%," + nid + ",%"
    cands = [r[0] for r in conn.execute(
        "SELECT place_id FROM place_coord WHERE naver_ref LIKE ?;", (like,))]
    try:
        cands += [r[0] for r in conn.execute(
            "SELECT place_id FROM place_address WHERE kind='map_url' AND value LIKE ?;",
            ("%/place/" + nid + "%",))]
    except Exception:                                 # noqa: BLE001  (pre-050)
        pass
    for pid in sorted(set(cands)):
        row = conn.execute("SELECT COALESCE(status,'active') FROM place WHERE id=?;",
                           (pid,)).fetchone()
        if row and row[0] == "active" and FP.place_naver_id(conn, pid) == nid:
            return pid
    return None


def near_gate(K, conn, facts, not_venue, new_confirmed):
    """The kit's near-match gate, minus two rows that cannot be this venue:
    the appointment's current place and its recorded meeting point (what the
    pick replaces -- "<venue> sinnonhyeon branch" is not the station) and a
    place carrying another Naver id. Anything left asks the one question (exit 5)."""
    cands = [c for c in K.near_candidates(conn, "place", facts["name"])
             if str(c["ref"]) not in not_venue
             and FP.place_naver_id(conn, int(c["ref"])) in (None, facts["naver_id"])]
    if not cands:
        return
    if new_confirmed:
        K.near_refuse(conn, "place", facts["name"], [c["ref"] for c in cands],
                      said="--new-confirmed")
        return
    K._near_exit("place", facts["name"], cands)


def find_or_mint(K, conn, facts, not_venue=(), new_confirmed=False):
    """(place id, minted). Exits CONFLICT_CODE / near-match (5) itself."""
    pid = place_by_naver_id(K, conn, facts["naver_id"])
    if pid is not None:
        return pid, False
    hit = K._resolve_location_place(conn, facts["name"])
    if hit is not None:
        other = FP.place_naver_id(conn, hit["id"])
        if hit["id"] == K._travel_base_place_id(conn) or (
                other and other != facts["naver_id"]):
            print("VENUE_CONFLICT\t%d\t%s\tnaver=%s" % (hit["id"], hit["name"], other or "-"))
            sys.exit(CONFLICT_CODE)
        return hit["id"], False
    near_gate(K, conn, facts, not_venue, new_confirmed)
    return K.travel_place_add(conn, facts["name"])["id"], True


def teach(K, conn, pid, facts):
    """Fill what the place lacks from the facts; never overwrite."""
    if facts["address"] and K._place_split_ready(conn) \
            and not K.place_address_primary(conn, pid):
        K._place_address_write(conn, pid, facts["address"])
    if K._place_split_ready(conn) and not K._place_map_url(conn, pid):
        K.place_address_add(conn, pid, facts["link"], kind="map_url", primary=False)
    have = K.place_coord_get(conn, pid)
    if (have is None or not FP.place_naver_id(conn, pid)) and \
            facts["lat"] is not None and facts["lng"] is not None:
        K._place_coord_from_kv(conn, pid, {
            "lat": str(facts["lat"]), "lng": str(facts["lng"]),
            "naver_ref": facts["naver_ref"], "coord_src": "naver_place"})


def _set_note(K, conn, eid, note):
    for _ in range(CAS_TRIES):
        ver = conn.execute("SELECT version FROM timeblock WHERE id=?;", (eid,)).fetchone()[0]
        if K.timeblock_set_meta(conn, eid, ver, {"note": note}) == K.MutationResult.APPLIED:
            return True
    return False


def keep_meeting_point(K, conn, eid, venue_pid, prior):
    """Write the meeting-point line into the appointment note. Returns
    (name written now or None, its place id or None, the note before)."""
    pid, loc, note = conn.execute(
        "SELECT place_id, location, note FROM timeblock WHERE id=?;", (eid,)).fetchone()
    if prior is not None or pid == venue_pid:
        return None, None, note            # already decided once / same place
    name = K._place_name(conn, pid) if pid is not None else " ".join((loc or "").split())
    if not name:
        return None, None, note
    line = K._i18n(MEET_KEY, MEET_FALLBACK).format(place=name)
    lines = [ln for ln in (note or "").splitlines() if ln.strip()]
    if line in lines:
        return None, pid, note
    if not _set_note(K, conn, eid, "\n".join(lines + [line])):
        K._err("dine-choose: the appointment changed while writing its note, retry")
    return name, pid, note


def store_choice(conn, eid, facts, meet_pid):
    kinds = [k for k in facts["kinds"] if re.match(r"^[a-z][a-z0-9_]*$", str(k))]
    conn.execute("BEGIN IMMEDIATE;")
    try:
        conn.execute("INSERT OR REPLACE INTO block_attr(block_id, attr_token, value, source) "
                     "VALUES(?,?,?,'user');",
                     (eid, FP.DINE_ATTR, json.dumps(
                         {"naver_id": facts["naver_id"], "category": facts["category"],
                          "kinds": kinds, "meet_place_id": meet_pid},
                         ensure_ascii=False)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def cli(K, argv):
    argv, new_confirmed = K._pop_flag(list(argv), "--new-confirmed")
    argv, path = K._pop_opt(argv, "--facts")
    if len(argv) != 1 or not str(argv[0]).strip() or not path:
        K._err(USAGE, code=2)
    try:
        facts = load_facts(path)
    except (OSError, ValueError) as e:
        K._err("dine-choose: %s\n%s" % (e, USAGE), code=2)
    conn = K.event_conn()
    try:
        tok = str(argv[0]).strip()
        row = conn.execute("SELECT id FROM timeblock WHERE kind='appointment' AND "
                           "(id=? OR ulid=?);",
                           (int(tok) if tok.isdigit() else -1, tok)).fetchone()
        if row is None:
            K._err("dine-choose: no appointment %s" % tok)
        eid = row[0]
        prior = FP.dine_attr(conn, eid)
        current = conn.execute("SELECT place_id FROM timeblock WHERE id=?;",
                               (eid,)).fetchone()[0]
        not_venue = {str(x) for x in (current, (prior or {}).get("meet_place_id"))
                     if x is not None}
        pid, minted = find_or_mint(K, conn, facts, not_venue, new_confirmed)
        teach(K, conn, pid, facts)
        venue = K._place_name(conn, pid)
        meet, meet_pid, note_before = keep_meeting_point(K, conn, eid, pid, prior)
        if prior is not None:
            meet_pid = prior.get("meet_place_id")
    finally:
        conn.close()
    try:
        K.cli_appt_upd([str(eid), "location=" + venue])
    except SystemExit as e:
        if e.code:
            if meet is not None:           # the move did not land: undo the note
                conn = K.event_conn()
                try:
                    _set_note(K, conn, eid, note_before)
                finally:
                    conn.close()
            raise
    conn = K.event_conn()
    try:
        store_choice(conn, eid, facts, meet_pid)
        now_pid = conn.execute("SELECT place_id FROM timeblock WHERE id=?;",
                               (eid,)).fetchone()[0]
    finally:
        conn.close()
    if now_pid != pid:
        K._err("dine-choose: appointment %d did not take place %d (now %s)"
               % (eid, pid, now_pid))
    print("DINE_CHOSEN\t%d\t%d\t%s\tnaver_%s\t%s" % (
        eid, pid, venue, facts["naver_id"], "minted" if minted else "existing"))
    if meet is not None:
        print("MEET_KEPT\t%d\tnote\t%s" % (eid, meet))
