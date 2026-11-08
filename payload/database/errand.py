"""errand.py -- place-bound errands: a TASK with a place and a deadline.

Owner decision 2026-10-06/07 (binding). An errand with no booked time that
only needs a visit before the place closes -- pick up a document, return,
get something issued, drop off ("dentist: pick up the treatment
certificate") -- is NOT an appointment. It is a task with a place and a
deadline: the place's closing time on that date (place_hours, 067). Only
the deadline time is shown. No arrival-buffer block is attached: the
15-minute "arrive and catch your breath" buffer stays ONE principle, for
real appointments only (_travel_appt_buffer_min), with no per-errand
exception. Travel is computed on its own and never shrunk to fit.

Live case: block 15701 "dentist (pick up certificate)" (2026-10-07) was
captured as an appointment with travel + buffer; it should have been a
place task due before the clinic closes.

WHAT THE ENGINE DECIDES
  classify()   errand | appointment | ask | task | other, from typed facts:
               booked time, place, a clock time, and an agent-supplied verb
               enum pickup|return|issue|drop_off. Title words are display
               evidence and never choose the class or veto a write verb.
  plan()       for a live all-day task at a place (that IS the errand
               shape): deadline = closing time on the date; arrival =
               deadline - margin (LIFEKIT_ERRAND_MARGIN_MIN, default 10);
               latest departure = arrival - travel (origin -> place, the
               stored edge; unknown -> no departure, never guessed);
               slack = latest departure - the moment the owner is free
               (the end of the stay / appointment they leave from, or now
               on the day itself). tight = slack below
               LIFEKIT_ERRAND_TIGHT_MIN (default 30); the slack is shown
               only then.
  alert_lines() the departure alert, bullets in the house alert format:
               "* **Leave by 17:40**" / "* 30 min left" /
               "* <title> closes 18:30" / route / directions.

Called as module(K, ...) with K = the running kit_life module (the shop.py /
intake.py convention), so this file never imports kit_life itself.
English/ASCII only.
"""
import datetime
import os
import sqlite3
from datetime import timezone
from zoneinfo import ZoneInfo

import agent_conf

VERB_CLASSES = ("pickup", "return", "issue", "drop_off")
LEXICON_KEY = "errand.verbs.%s"

MARGIN_MIN = agent_conf.conf_int("LIFEKIT_ERRAND_MARGIN_MIN", 10)
TIGHT_MIN = agent_conf.conf_int("LIFEKIT_ERRAND_TIGHT_MIN", 30)

ERRAND_CODE = 7          # kit exit codes: 3 EXISTS, 4 CONFLICT, 5 NEAR, 6 PLACE

CLASS_ERRAND = "errand"
CLASS_APPOINTMENT = "appointment"
CLASS_ASK = "ask"
CLASS_TASK = "task"
CLASS_OTHER = "other"

_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Copy fallbacks (the i18n bundles override each key).
FALLBACK = {
    "errand.ask": u"{title}: is there a booked time, or does it only need "
                  u"a visit before {place} closes?",
    "errand.ask.booked": u"Booked time",
    "errand.ask.before_close": u"Before closing",
    "day.card.deadline": u"Closes {hhmm}",
    "day.card.errand_tight": u" \u2190 leave by {depart} ({n} min to spare)",
    "day.card.errand_late": u" \u2190 leave by {depart} ({n} min short)",
    "remind.errand": u"Errand",
    "remind.errand_lead": u"Errand alert \u00b7 {time}",
    "remind.errand_start": u"Errand alert",
    "remind.deadline": u"{what} closes {hhmm}",
    "remind.latest_depart": u"**Leave by {hhmm}**",
    "remind.slack": u"{n} min to spare",
    "remind.slack_short": u"{n} min short",
}


def _t(K, key):
    return K._i18n(key, FALLBACK.get(key, ""))


# -- classification ----------------------------------------------------------

def _langs(K):
    """Every language the kit ships a bundle for (the lexicon is matched in
    all of them: an owner may write in either)."""
    root = os.path.dirname(K.SCRIPT_DIR)
    out = []
    for d in (os.path.join(root, "config", "i18n"),
              os.path.join(root, "service", "kit_life", "i18n")):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in sorted(names):
            if n.endswith(".json"):
                lang = n.split(".")[0]
                if lang and lang not in out:
                    out.append(lang)
    return out


def lexicon(K):
    """{verb_class: [token, ...]} merged over every shipped language."""
    root = os.path.dirname(K.SCRIPT_DIR)
    lex = {c: [] for c in VERB_CLASSES}
    for lang in _langs(K):
        bundle = K._kit_bundle(root, lang)
        for c in VERB_CLASSES:
            raw = bundle.get(LEXICON_KEY % c)
            if not isinstance(raw, str):
                continue
            for tok in raw.split("|"):
                tok = tok.strip().lower()
                if tok and tok not in lex[c]:
                    lex[c].append(tok)
    return lex


def classify(K, title, has_place, at=None, booked=False, *, verb=None):
    """Classify typed booking/place/time/verb facts; title is display only."""
    if verb is not None and verb not in VERB_CLASSES:
        raise ValueError('verb class must be pickup|return|issue|drop_off')
    if booked:
        return {"class": CLASS_APPOINTMENT, "verb_class": verb,
                "reason": "booked_time"}
    if verb is None:
        return {"class": CLASS_OTHER, "verb_class": None,
                "reason": "no_errand_verb"}
    if not has_place:
        return {"class": CLASS_TASK, "verb_class": verb, "reason": "no_place"}
    if at:
        return {"class": CLASS_ASK, "verb_class": verb,
                "reason": "time_not_booked"}
    return {"class": CLASS_ERRAND, "verb_class": verb,
            "reason": "no_booked_time"}


def ask_text(K, title, place):
    return _t(K, "errand.ask").format(title=title, place=place or "")


# -- the plan ----------------------------------------------------------------

def _hm(K, ts):
    return _local_k(K, ts).strftime("%H:%M")


def _minutes(K, conn, origin_id, dest_id):
    """Travel minutes origin -> dest from what is on file (the stored edge;
    for a trip from home, the place's own to_min), 0 when already there,
    None when unknown -- never guessed."""
    if origin_id is None or dest_id is None:
        return None
    if origin_id == dest_id:
        return 0
    try:
        edge = K._travel_edge_get(conn, origin_id, dest_id)
    except sqlite3.Error:
        edge = None
    if edge is not None and edge.get("minutes") is not None:
        return int(edge["minutes"])
    if origin_id == K._travel_base_place_id(conn):
        dest = K.travel_place_get(conn, dest_id) or {}
        if dest.get("to_min") is not None:
            return int(dest["to_min"])
    return None


def _busy_before(K, conn, at, day_start, exclude_id):
    """The live timed stay / appointment the owner is at or has just left at
    `at`: the one covering `at`, else the latest one ending in
    [at - chain window, at] on the same day. Derived travel rows and home
    rows are never 'busy'. dict {id, place_id, end_at, covers} or None."""
    try:
        rows = conn.execute(
            "SELECT id, place_id, start_at, end_at FROM timeblock "
            "WHERE settled_at IS NULL AND schedule_kind='timed' "
            "AND kind IN ('presence','appointment') AND block_class IS NULL "
            "AND id != ? AND start_at < ? AND end_at > ? "
            "ORDER BY end_at DESC;",
            (exclude_id or -1, at, day_start)).fetchall()
    except sqlite3.Error:
        return None
    base = K._travel_base_place_id(conn)
    lo = K._iso_shift_min(at, -K.TRAVEL_CHAIN_WINDOW_MIN)
    for bid, pid, sa, ea in rows:
        if pid is not None and pid == base:
            continue
        if ea > at:
            return {"id": bid, "place_id": pid, "end_at": ea, "covers": True}
        if ea >= lo:
            return {"id": bid, "place_id": pid, "end_at": ea,
                    "covers": False}
        return None
    return None


def _origin_at(K, conn, at, day_start, exclude_id):
    busy = _busy_before(K, conn, at, day_start, exclude_id)
    if busy is not None and busy["place_id"] is not None:
        return busy["place_id"], busy
    try:
        _kind, pid, _rid = K.presence_at(conn, at, exclude_event_id=exclude_id)
    except Exception:
        pid = None
    if pid is None:
        pid = K._travel_base_place_id(conn)
    return pid, busy


def _row(conn, eid):
    try:
        cur = conn.execute(
            "SELECT id, ulid, kind, title, start_at, schedule_kind, "
            "place_id, settled_at, block_class FROM timeblock WHERE id=?;",
            (int(eid),))
    except (sqlite3.Error, TypeError, ValueError):
        return None
    r = cur.fetchone()
    if r is None:
        return None
    return dict(zip(("id", "ulid", "kind", "title", "start_at",
                     "schedule_kind", "place_id", "settled_at",
                     "block_class"), r))


def is_errand_row(K, conn, row):
    """The errand SHAPE: a live, dated, no-time task at a place that is not
    home."""
    if not row or row.get("kind") != "task" or row.get("place_id") is None:
        return False
    if row.get("settled_at") is not None or row.get("block_class"):
        return False
    if row.get("schedule_kind") != "all_day" or not row.get("start_at"):
        return False
    place = K.travel_place_get(conn, row["place_id"]) or {}
    return bool(place) and not place.get("is_base") \
        and place.get("status") != "gone"


def plan(K, conn, eid, now=None):
    """The errand plan of block `eid`, or None when it is not an errand.

    {"id", "ulid", "title", "place_id", "place", "date",
     "state": open|closed|unknown|none,
     "deadline_at", "deadline_hm", "margin_min", "arrive_at",
     "travel_min", "origin_id", "depart_at", "depart_hm",
     "free_at", "slack_min", "tight", "late"}
    Times are canonical UTC; *_hm are display-local. No buffer anywhere."""
    row = _row(conn, eid)
    if not is_errand_row(K, conn, row):
        return None
    place = K.travel_place_get(conn, row["place_id"]) or {}
    date = K._local_date_of(row["start_at"], K.TRAVEL_DISPLAY_TZ)
    out = {"id": row["id"], "ulid": row["ulid"], "title": row["title"] or "",
           "place_id": row["place_id"], "place": place.get("name") or "",
           "date": date.isoformat(), "state": "unknown",
           "deadline_at": None, "deadline_hm": None,
           "margin_min": max(0, MARGIN_MIN), "arrive_at": None,
           "travel_min": None, "origin_id": None, "depart_at": None,
           "depart_hm": None, "free_at": None, "slack_min": None,
           "tight": False, "late": False}
    try:
        day = K.place_hours_on(conn, row["place_id"], date, now=now)
    except Exception:
        return out
    out["state"] = day.get("state", "unknown")
    if out["state"] != "open":
        return out
    deadline = K._to_canonical_utc("%s %s" % (date.isoformat(), day["close"]))
    out.update(deadline_at=deadline, deadline_hm=day["close"])
    arrive = K._iso_shift_min(deadline, -out["margin_min"])
    out["arrive_at"] = arrive
    day_start = K.all_day_instants(date.isoformat())[0]
    origin, busy = _origin_at(K, conn, arrive, day_start, row["id"])
    mins = _minutes(K, conn, origin, row["place_id"])
    if mins is not None:
        depart = K._iso_shift_min(arrive, -mins)
        o2, b2 = _origin_at(K, conn, depart, day_start, row["id"])
        if o2 != origin:
            m2 = _minutes(K, conn, o2, row["place_id"])
            if m2 is not None:
                origin, mins = o2, m2
                depart = K._iso_shift_min(arrive, -mins)
                o2, b2 = _origin_at(K, conn, depart, day_start, row["id"])
        busy = b2
    out["origin_id"] = origin
    out["travel_min"] = mins
    if mins is None:
        return out
    out.update(depart_at=depart, depart_hm=_hm(K, depart))
    # When is the owner free to leave? The end of the stay / appointment
    # they leave from, and never before now on the day itself.
    now_c = K.canonical(now or K.now_utc())
    free = busy["end_at"] if busy is not None else None
    if K._local_date_of(now_c, K.TRAVEL_DISPLAY_TZ) == date:
        free = max(free, now_c) if free else now_c
    if free is not None:
        out["free_at"] = free
        out["slack_min"] = K._iso_diff_min(depart, free)
        out["tight"] = out["slack_min"] < TIGHT_MIN
        out["late"] = out["slack_min"] < 0
    return out


def plan_line(p):
    """ERRAND<TAB><id><TAB><deadline|-><TAB><depart|-><TAB><travel|->
    <TAB><slack|-><TAB>ok|tight|late|no_deadline|travel_unknown."""
    if p["deadline_hm"] is None:
        verdict = "no_deadline"
    elif p["depart_at"] is None:
        verdict = "travel_unknown"
    else:
        verdict = ("late" if p["late"] else "tight" if p["tight"] else "ok")

    def f(v):
        return "-" if v is None else str(v)
    return "ERRAND\t%s\t%s\t%s\t%s\t%s\t%s" % (
        p["id"], f(p["deadline_hm"]), f(p["depart_hm"]), f(p["travel_min"]),
        f(p["slack_min"]), verdict)


RELAY = (
    "ERRAND_RELAY An ERRAND line is a task with a place and a deadline (the "
    "place's closing time), not an appointment: no visit time is picked, no "
    "arrival buffer is attached. The reply names the deadline only "
    "(\"by <deadline>\"); the latest departure and the time to spare are "
    "said only when the line ends in tight or late. Never print ERRAND "
    "lines or this one.")


def card_bits(K, conn, eid, now=None):
    """(head, suffix) for the day card row of an errand: head replaces the
    all-day label with the deadline; suffix is the tight / late note
    ('' when there is spare time). None when not an errand / no deadline."""
    p = plan(K, conn, eid, now=now)
    if p is None or p["deadline_hm"] is None:
        return None
    head = _t(K, "day.card.deadline").format(hhmm=p["deadline_hm"])
    suffix = ""
    if p["slack_min"] is not None and p["tight"]:
        key = "day.card.errand_late" if p["late"] else "day.card.errand_tight"
        suffix = _t(K, key).format(depart=p["depart_hm"],
                                   n=abs(p["slack_min"]))
    return head, suffix


# -- write-side surfaces -----------------------------------------------------

def emit(K, conn, ids, now=None):
    """Print ERRAND lines (+ relay) for the touched errands; nothing else."""
    out = []
    for raw in ids or ():
        p = plan(K, conn, raw, now=now)
        if p is not None:
            out.append(plan_line(p))
    if out:
        for line in out:
            print(line)
        print(RELAY)


def gate_lines(K, verdict, title, place, date, at):
    """The lines a write verb prints when the engine re-files the item:
    ERRAND_CLASS, then ERRAND_FILE (the verb to run) or ERRAND_ASK (one
    question) + ERRAND_ANSWER lines."""
    lines = ["ERRAND_CLASS\t%s\t%s\t%s" % (
        verdict["class"], verdict["verb_class"] or "-", verdict["reason"])]
    task_cmd = "task-add %s %s --place %s" % (
        _q(title), date or "<YYYY-MM-DD>", _q(place))
    if verdict["class"] == CLASS_ERRAND:
        lines.append("ERRAND_FILE\t" + task_cmd)
    elif verdict["class"] == CLASS_ASK:
        # 073 ask-choices: the two answers are the errand classes; the token
        # line carries them and the exact [[OPTIONS]] marker follows.
        ac = K._ask_choices()
        choices = [("booked", _t(K, "errand.ask.booked")),
                   ("before_close", _t(K, "errand.ask.before_close"))]
        lines.append("ERRAND_ASK\t" + ask_text(K, title, place) + "\t" +
                     ac.choices_field(choices))
        lines.append("ERRAND_ANSWER\t%s\t--booked" % _t(K, "errand.ask.booked"))
        lines.append("ERRAND_ANSWER\t%s\t%s" % (
            _t(K, "errand.ask.before_close"), task_cmd))
        lines.append(ac.options_line("ERRAND_ASK", "-", choices, task_argv=["task-add", title, date or "<YYYY-MM-DD>", "--place", place]))
        lines.append(ac.RELAY)
    lines.append(GATE_RELAY)
    return lines


GATE_RELAY = (
    "ERRAND_GATE_RELAY Nothing was written. ERRAND_FILE: run that verb "
    "instead, silently (an errand is a task with a place and a deadline). "
    "ERRAND_ASK: ask that one question verbatim; the first ERRAND_ANSWER "
    "means re-run the same verb with --booked, the second means run its "
    "verb. Never print these lines or this one.")


def _q(s):
    s = str(s or "")
    return "'" + s.replace("'", "'\\''") + "'"


# -- the alert ---------------------------------------------------------------

def due_rows(K, conn, now_ts):
    """Live all-day tasks at a place dated today or tomorrow (local) -- the
    errands that may alert near now_ts."""
    now_c = datetime.datetime.fromtimestamp(now_ts, tz=timezone.utc) \
        .strftime(_FMT)
    today = K._local_date_of(now_c, K.TRAVEL_DISPLAY_TZ)
    lo = K.all_day_instants(today.isoformat())[0]
    hi = K.all_day_instants(
        (today + datetime.timedelta(days=1)).isoformat())[1]
    try:
        return [r[0] for r in conn.execute(
            "SELECT id FROM timeblock WHERE kind='task' "
            "AND schedule_kind='all_day' AND settled_at IS NULL "
            "AND place_id IS NOT NULL AND start_at >= ? AND start_at < ? "
            "ORDER BY start_at, id;", (lo, hi))]
    except sqlite3.Error:
        return []


def alert_lines(K, conn, p, alert_ts, bullet, dur):
    """Bullets of one errand alert (house format, one fact per line):
         "* **Leave by 17:40**" + "* 30 min left"   (or "* **Leave now**")
         "* <title> closes 18:30"
         "* N min to spare" / "* N min short"   (tight, before departure)
         "* [origin](url) -> [place](url)" / "* [directions](url)"
    `bullet` / `dur` are remind_select's own formatters."""
    depart = _local_k(K, p["depart_at"])
    left = (depart.timestamp() - alert_ts) // 60
    if left > 0:
        lines = [bullet(_t(K, "remind.latest_depart").format(
            hhmm=p["depart_hm"])),
            bullet(K._i18n("remind.left", fallback="{dur} left").format(
                dur=dur(left)))]
    else:
        lines = [bullet(K._i18n("remind.depart_now",
                                fallback="**Leave now**"))]
    lines.append(bullet(_t(K, "remind.deadline").format(
        what=p["title"], hhmm=p["deadline_hm"])))
    if left > 0 and p["tight"] and p["slack_min"] is not None:
        # the time to spare is planning news; at the departure itself
        # "leave now" already says it
        key = "remind.slack_short" if p["late"] else "remind.slack"
        lines.append(bullet(_t(K, key).format(n=abs(p["slack_min"]))))
    links = []
    if p["origin_id"] is not None and p["origin_id"] != p["place_id"]:
        try:
            links = K.travel_map_links(conn, p["origin_id"], p["place_id"])
            r = K._travel_route_links(conn, p["origin_id"], p["place_id"],
                                      links)
        except Exception:
            r = None
        if r:
            if r.get("route_md"):
                lines.append(bullet(r["route_md"]))
            if r.get("directions"):
                d = r["directions"]
                lines.append(bullet(K._md_link(d["text"], d["url"])))
            links = r.get("buttons", links)
    return lines, links


def _local_k(K, ts):
    return (datetime.datetime.strptime(ts, _FMT).replace(tzinfo=timezone.utc)
            .astimezone(ZoneInfo(K.TRAVEL_DISPLAY_TZ)))
