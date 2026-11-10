#!/usr/bin/env python3
"""context_events.py -- step 0 of dining-session, as code (not prose).

Ported unchanged in behaviour from the life agent's local restaurant-find skill (retired
by the kit, service/kit_life/local-skill-retire.tsv); the only change is the
root: $PROJECT_ROOT first, then three levels up from the skill dir.

Lists appointments / stays from local today to +14 days that the given words
point at (an attendee name or a title / place word). The 14-day window and
the canonical UTC arguments are computed here so the agent never picks a
narrower window by hand (live 2026-10-08: a 2-day window missed the event).

usage: context_events.py <word> [<word> ...]
output: one line per hit
  EVENT<TAB><local start><TAB><title><TAB><place><TAB><people><TAB>via=<word><TAB>id=<ulid>
then (id = what `kit_life.sh dine-choose` takes when the owner picks a venue)
  CONTEXT<TAB>events=<n><TAB>from=<date><TAB>to=<date>
exit 0 always (no hit = events=0; the agent then asks for the area).
"""
import datetime as dt
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("PROJECT_ROOT") or os.path.abspath(
    os.path.join(HERE, "..", "..", ".."))
KIT = os.path.join(ROOT, "database", "kit_life.sh")
DAYS = 14
TZ_HOURS = 9
KINDS = ("appointment", "presence")


def main(words):
    words = [w.strip() for w in words if w.strip()]
    tz = dt.timezone(dt.timedelta(hours=TZ_HOURS))
    today = dt.datetime.now(tz).date()
    start = dt.datetime.combine(today, dt.time(0, 0), tz)
    end = start + dt.timedelta(days=DAYS + 1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    a = start.astimezone(dt.timezone.utc).strftime(fmt)
    b = end.astimezone(dt.timezone.utc).strftime(fmt)
    out = subprocess.run([KIT, "event-window", a, b], capture_output=True,
                         text=True).stdout
    n = 0
    for line in out.splitlines():
        col = line.split("\t")
        if len(col) < 10 or col[1] not in KINDS or col[5] != "open":
            continue
        title, place, people = col[2], col[8], col[9]
        hay = " ".join((title, place, people))
        hit = next((w for w in words if w in hay), None)
        if hit is None:
            continue
        try:
            t = dt.datetime.strptime(col[3], fmt).replace(
                tzinfo=dt.timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            t = col[3]
        print("EVENT\t%s\t%s\t%s\t%s\tvia=%s\tid=%s" % (t, title, place, people, hit,
                                                          col[0]))
        n += 1
    print("CONTEXT\tevents=%d\tfrom=%s\tto=%s"
          % (n, today.isoformat(), (today + dt.timedelta(days=DAYS)).isoformat()))


if __name__ == "__main__":
    main(sys.argv[1:])
