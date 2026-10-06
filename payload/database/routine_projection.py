"""
DGN-240 shared instant module (spec v3 section 3.1, T11 front half).

SINGLE OWNER of every recurrence instant computation. materialize (roller),
rule-conformance (roller), and the advisory conflict probe all import THIS
module -- no other instant computation implementation may exist in the
codebase (M-A: kills the systematic-delta root of conformance oscillation).

PURE functions only: no DB access, no clock access (now()/today()), no
file IO. Same input = same output (H-INST-PURE / H-INST-VECTORS gates).

Cadence DSL v1 (closed grammar, spec 1.2):
    cadence := "D"                      -- daily
             | "W:" DAY ("," DAY)*      -- weekly day-set, DAY in MON..SUN
             | "I:" N "@" YYYY-MM-DD    -- every N days from anchor, N>=2

KST-single (no DST) scope inherited from DGN-179; the tz math still goes
through zoneinfo (no fixed offsets) so the module stays correct if
display_tz ever changes.
"""

import datetime
import re
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import agent_conf

DAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_DAY_INDEX = {name: i for i, name in enumerate(DAY_NAMES)}  # Mon=0 .. Sun=6

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_INTERVAL_RE = re.compile(r"^I:(\d+)@(\d{4}-\d{2}-\d{2})$")

# Per-instance tunable (config/agent.conf: LIFEKIT_DEFAULT_DURATION_MIN);
# blank/absent -> 30 (zero-delta).
DEFAULT_DURATION_MIN = agent_conf.conf_int("LIFEKIT_DEFAULT_DURATION_MIN", 30)
UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Exclusion suffixes: a cadence may carry "!<excl>" suffixes that SUBTRACT dates
# the base grammar would otherwise fire (no schema change -- rides the cadence
# string). e.g. "W:MON,TUE,WED,THU,FRI!hol!lastfri".
#   hol      -- skip KR public holidays (static table, holidays_kr, no runtime dep)
#   lastfri  -- skip the last Friday of the month
_VALID_EXCL = frozenset(("hol", "lastfri"))

try:
    import holiday_source as _holiday_src
except Exception:   # module absent -> 'hol' exclusion safely no-ops
    _holiday_src = None


def cadence_exclusions(cadence):
    """Exclusion tokens after the base (parts joined by '!'). Empty if none."""
    return frozenset((cadence or "").split("!")[1:])


def _is_last_friday(d):
    """True iff d is a Friday AND the next Friday is in a later month
    (= the last Friday of d's month)."""
    return d.weekday() == 4 and (d + timedelta(days=7)).month != d.month


def parse_cadence(cadence):
    """Validate + decompose a cadence string.
    Returns one of:
        ("D",)
        ("W", frozenset_of_weekday_ints)     -- Mon=0 .. Sun=6
        ("I", n, anchor_date)                -- n >= 2, anchor datetime.date
    An optional '!'-joined exclusion suffix (hol|lastfri) is validated and
    stripped before the base is parsed (e.g. 'W:MON,FRI!hol!lastfri').
    Raises ValueError on anything outside the closed grammar."""
    parts = (cadence or "").split("!")
    base = parts[0]
    for ex in parts[1:]:
        if ex not in _VALID_EXCL:
            raise ValueError("unknown cadence exclusion %r in %r" % (ex, cadence))
    if base == "D":
        return ("D",)
    if base.startswith("W:"):
        days = base[2:].split(",")
        if not days or any(p not in _DAY_INDEX for p in days):
            raise ValueError("bad weekly cadence: %r" % cadence)
        return ("W", frozenset(_DAY_INDEX[p] for p in days))
    m = _INTERVAL_RE.match(base)
    if m:
        n = int(m.group(1))
        if n < 2:
            raise ValueError("interval cadence needs N>=2: %r" % cadence)
        try:
            anchor = datetime.date.fromisoformat(m.group(2))
        except ValueError:
            raise ValueError("bad interval anchor date: %r" % cadence)
        return ("I", n, anchor)
    raise ValueError("bad cadence: %r" % cadence)


def occurs(cadence, d):
    """Spec 1.2 occurs(): does cadence fire on local date d?
    d: datetime.date or 'YYYY-MM-DD' string."""
    if isinstance(d, str):
        d = datetime.date.fromisoformat(d)
    parsed = parse_cadence(cadence)
    if parsed[0] == "D":
        fire = True
    elif parsed[0] == "W":
        fire = d.weekday() in parsed[1]
    else:
        _tag, n, anchor = parsed
        fire = d >= anchor and (d - anchor).days % n == 0
    if not fire:
        return False
    # exclusion suffixes subtract otherwise-firing dates
    excl = cadence_exclusions(cadence)
    if "hol" in excl and _holiday_src is not None and _holiday_src.is_holiday(d):
        return False
    if "lastfri" in excl and _is_last_friday(d):
        return False
    return True


def _as_date(d):
    return datetime.date.fromisoformat(d) if isinstance(d, str) else d


def _utc(dt_local):
    return dt_local.astimezone(timezone.utc).strftime(UTC_FMT)


def rule_instants(defn, d):
    """(start_at, end_at, schedule_kind) for def occurrence on local date d.

    defn: mapping with keys schedule_kind, time_of_day, duration_min,
    display_tz (db row object or plain dict). d: date or 'YYYY-MM-DD'.

    timed:   start = d + time_of_day in display_tz; end = start + duration
             (duration_min NULL -> 30, spec 1.3 default).
    all_day: day-block [d 00:00 local, d+1 00:00 local) -- byte-identical
             convention to kit_life.all_day_instants (H-INST-EQ gate).
    """
    d = _as_date(d)
    sk = defn["schedule_kind"]
    tz = ZoneInfo(defn["display_tz"] or "Asia/Seoul")
    if sk == "timed":
        tod = defn["time_of_day"]
        if tod is None or not _TIME_RE.match(tod):
            raise ValueError("timed def needs time_of_day HH:MM, got %r" % tod)
        hh, mm = int(tod[:2]), int(tod[3:])
        dur = defn["duration_min"]
        dur = DEFAULT_DURATION_MIN if dur is None else int(dur)
        start_local = datetime.datetime(d.year, d.month, d.day, hh, mm, 0,
                                        tzinfo=tz)
        end_local = start_local + timedelta(minutes=dur)
        return _utc(start_local), _utc(end_local), "timed"
    if sk == "all_day":
        start_local = datetime.datetime(d.year, d.month, d.day, 0, 0, 0,
                                        tzinfo=tz)
        end_local = start_local + timedelta(days=1)
        return _utc(start_local), _utc(end_local), "all_day"
    raise ValueError("routine def schedule_kind must be timed|all_day, got %r"
                     % sk)


def occurrence_dates(defn, from_date, to_date):
    """All local dates in [from_date, to_date] (INCLUSIVE) where the def's
    cadence fires, clamped to [def.start_date, def.end_date]. Pure expansion
    helper shared by project_virtual and the roller's materialize loop."""
    from_date = _as_date(from_date)
    to_date = _as_date(to_date)
    lo = max(from_date, _as_date(defn["start_date"]))
    hi = to_date
    if defn["end_date"] is not None:
        hi = min(hi, _as_date(defn["end_date"]))
    out = []
    d = lo
    while d <= hi:
        if occurs(defn["cadence"], d):
            out.append(d)
        d += timedelta(days=1)
    return out


def project_virtual(defs, from_date, to_date):
    """Spec 3.1: [(recurrence_id, title, start_at, end_at, exclusive)] for
    every occurrence of the given defs in [from_date, to_date] (inclusive).
    Input = def rows + date bounds only; deterministic, no clock, no DB."""
    hits = []
    for defn in defs:
        if defn["cadence"] is None:
            continue
        for d in occurrence_dates(defn, from_date, to_date):
            sa, ea, _sk = rule_instants(defn, d)
            hits.append((defn["recurrence_id"], defn["title"], sa, ea,
                         defn["exclusive"]))
    return hits
