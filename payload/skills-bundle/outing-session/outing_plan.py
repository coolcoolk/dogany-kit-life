#!/usr/bin/env python3
"""outing_plan.py -- the time arithmetic + gate of an outing course.

usage: outing_plan.py <plan.json>
plan.json:
  {"date": "YYYY-MM-DD", "start": "HH:MM", "origin": "<name>",
   "end_by": "HH:MM" | null,
   "busy": [{"start": "HH:MM", "end": "HH:MM", "title": "..."}],
   "stops": [{"name": "...", "kind": "meal|cafe|sight|walk|activity",
              "stay_min": 60, "leg_min": 75 | null, "leg_src": "route" | null,
              "fixed_at": "HH:MM" | null,
              "hours": {"open": "HH:MM", "close": "HH:MM",
                        "break": ["15:00", "17:00"] | null,
                        "last_order": "HH:MM" | null, "closed": false} | null,
              "booking": "<how, from the page>" | null,
              "link": "<official or booking page>" | null,
              "src": "<page the facts came from>" | null}],
   "return": {"leg_min": 70 | null, "leg_src": "route" | null}}

leg_min = minutes from the previous stop (origin for the first), timed by the
kit route lookup (place-view/route.sh, car or transit); null = not timed.
busy = calendar blocks of that day the course must not overlap
(schedule-log event-day).

output (stdout, one line each, tab separated):
  STOP<TAB>n<TAB>name<TAB>depart<TAB>arrive<TAB>leave<TAB>wait_min
  END<TAB>HH:MM<TAB>home arrival
  UNRESOLVED<TAB>what          (a fact the sources could not give)
  REFUSE<TAB>what              (the course is wrong: fix it, run again)
exit 0 = clean, 3 = any REFUSE. More than ONE unresolved fact is a REFUSE:
the first reply names at most one and never hands the check to the owner.
"""
import json
import math
import sys


def mins(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def hhmm(n):
    n = int(round(n))
    return "%02d:%02d" % ((n // 60) % 24, n % 60)


def run(plan):
    out, refuse, unresolved = [], [], []
    t = mins(plan["start"])
    busy = [(mins(b["start"]), mins(b["end"]), b.get("title", "")) for b in plan.get("busy", [])]
    stops = plan.get("stops", [])
    if not stops:
        refuse.append("no stops")
    for i, s in enumerate(stops, 1):
        name = s.get("name") or "?"
        leg = s.get("leg_min")
        if leg is None:
            unresolved.append("leg to %s not timed" % name)
            refuse.append("%s: route measurement required" % name)
            leg = 0
        elif not isinstance(leg, (int, float)) or isinstance(leg, bool) or not math.isfinite(leg) or leg < 0:
            refuse.append("%s: invalid leg minutes" % name)
            leg = 0
        elif s.get("leg_src") != "route":
            refuse.append("%s: leg minutes not from the kit route lookup" % name)
        depart = t
        arrive = t + leg
        wait = 0
        fx = s.get("fixed_at")
        if fx:
            if arrive > mins(fx):
                refuse.append("%s: arrive %s after fixed %s" % (name, hhmm(arrive), fx))
            else:
                wait = mins(fx) - arrive
                arrive = mins(fx)
        h = s.get("hours")
        stay = int(s.get("stay_min", 0))
        if stay <= 0:
            refuse.append("%s: stay must be positive" % name)
        if not h:
            unresolved.append("%s: hours not found" % name)
        else:
            if h.get("closed"):
                refuse.append("%s: closed that day" % name)
            op = mins(h["open"]) if h.get("open") else None
            if op is not None and arrive < op:
                wait += op - arrive
                arrive = op
            if fx and arrive != mins(fx):
                refuse.append("%s: fixed time before opening" % name)
            cutoff = h.get("entry_cutoff")
            if cutoff and arrive > mins(cutoff):
                refuse.append("%s: after entry cutoff %s" % (name, cutoff))
            br = h.get("break")
            if br and mins(br[0]) <= arrive < mins(br[1]):
                refuse.append("%s: arrive %s inside break %s-%s" % (name, hhmm(arrive), br[0], br[1]))
            if br and arrive < mins(br[0]) < arrive + stay:
                refuse.append("%s: stay runs into break %s" % (name, br[0]))
            lo = h.get("last_order")
            if lo and arrive > mins(lo):
                refuse.append("%s: arrive %s after last order %s" % (name, hhmm(arrive), lo))
            if h.get("close") and arrive + stay > mins(h["close"]) + (24 * 60 if mins(h["close"]) < (op or 0) else 0):
                refuse.append("%s: leave %s after close %s" % (name, hhmm(arrive + stay), h["close"]))
        for w in s.get("windows", []):
            if arrive < mins(w["start"]) and not fx:
                wait += mins(w["start"]) - arrive
                arrive = mins(w["start"])
            if arrive < mins(w["start"]) or arrive + stay > mins(w["end"]):
                refuse.append("%s: outside visit window" % name)
        if h and s.get("windows"):
            if h.get("entry_cutoff") and arrive > mins(h["entry_cutoff"]):
                refuse.append("%s: after entry cutoff" % name)
            if h.get("last_order") and arrive > mins(h["last_order"]):
                refuse.append("%s: after last order" % name)
            if h.get("close") and arrive + stay > mins(h["close"]) + (1440 if mins(h["close"]) < (op or 0) else 0):
                refuse.append("%s: after close" % name)
            if h.get("break") and arrive < mins(h["break"][1]) and arrive + stay > mins(h["break"][0]):
                refuse.append("%s: visit window overlaps break" % name)
        reservation = s.get("reservation") or {}
        if reservation.get("required") and reservation.get("status") not in ("confirmed", "available"):
            refuse.append("%s: required reservation unavailable" % name)
        if reservation.get("vehicle_required") and reservation.get("vehicle_status") not in ("confirmed", "available"):
            refuse.append("%s: required vehicle reservation unavailable" % name)
        leave = arrive + stay
        if s.get("kind") == "meal" and not s.get("booking"):
            unresolved.append("%s: booking not stated on the page" % name)
        if not (s.get("link") and s.get("src")):
            refuse.append("%s: no official/booking page behind the facts" % name)
        for b0, b1, title in busy:
            if arrive < b1 and leave > b0:
                refuse.append("%s %s-%s overlaps calendar '%s' %s-%s" % (
                    name, hhmm(arrive), hhmm(leave), title, hhmm(b0), hhmm(b1)))
        out.append("STOP\t%d\t%s\t%s\t%s\t%s\t%d" % (i, name, hhmm(depart), hhmm(arrive), hhmm(leave), wait))
        t = leave
    rt = plan.get("return") or {}
    if rt.get("leg_min") is None:
        unresolved.append("return leg not timed")
        refuse.append("return route measurement required")
    else:
        if rt.get("leg_src") != "route":
            refuse.append("return leg minutes not from the kit route lookup")
        leg = rt["leg_min"]
        if not isinstance(leg, (int, float)) or isinstance(leg, bool) or not math.isfinite(leg) or leg < 0:
            refuse.append("invalid return leg minutes")
        else:
            t += int(leg)
    if t > 1440:
        refuse.append("course extends beyond this day")
    out.append("END\t%s" % hhmm(t))
    if plan.get("end_by") and t > mins(plan["end_by"]):
        refuse.append("home %s after end_by %s" % (hhmm(t), plan["end_by"]))
    for b0, b1, title in busy:
        if mins(plan["start"]) < b1 and t > b0:
            refuse.append("course overlaps calendar %s" % title)
    # de-duplicate, keep order
    seen = set()
    unresolved = [u for u in unresolved if not (u in seen or seen.add(u))]
    if len(unresolved) > 1:
        refuse.append("%d unresolved facts (max 1): %s" % (len(unresolved), "; ".join(unresolved)))
    out += ["UNRESOLVED\t%s" % u for u in unresolved]
    out += ["REFUSE\t%s" % r for r in refuse]
    return out, bool(refuse)


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        plan = json.load(f)
    out, bad = run(plan)
    print("\n".join(out))
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
