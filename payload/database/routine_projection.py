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
             | "M:" DD ("," DD)*        -- day(s) of month, DD 1..31 or L=last;
                                           a day the month lacks (29/30/31)
                                           falls on that month's last day
             | "MW:" N ":" DAY          -- Nth weekday of the month, N 1..4 or
                                           L=last (MW:2:MON = the 2nd Monday)
             | "Y:" MM-DD               -- yearly, solar date (02-29 -> 02-28
                                           in a non-leap year)
             | "YL:" MM-DD              -- yearly, LUNAR date (eumryeok, lunar_kr:
                                           normal month, never the leap month;
                                           day 30 of a 29-day month -> day 29)
             | "IM:" N "@" YYYY-MM-DD   -- every N months from the anchor's
                                           day of month (clamped), N>=2
                                           (IM:12 = yearly by interval)

KST-single (no DST) scope inherited from DGN-179; the tz math still goes
through zoneinfo (no fixed offsets) so the module stays correct if
display_tz ever changes.
"""

import datetime
import re
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import agent_conf
import calendar
import lunar_kr

DAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_DAY_INDEX = {name: i for i, name in enumerate(DAY_NAMES)}  # Mon=0 .. Sun=6

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_INTERVAL_RE = re.compile(r"^I:(\d+)@(\d{4}-\d{2}-\d{2})$")
_MINTERVAL_RE = re.compile(r"^IM:(\d+)@(\d{4}-\d{2}-\d{2})$")
_MWEEK_RE = re.compile(r"^MW:([1-4L]):([A-Z]{3})$")
_MD_RE = re.compile(r"^(\d{2})-(\d{2})$")
LAST = 0     # "L" in M:/MW: is stored as 0

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
        ("M", frozenset_of_days)             -- 1..31, 0 = last day
        ("MW", n, weekday_int)               -- n 1..4, 0 = last
        ("Y", month, day)                    -- solar
        ("YL", month, day)                   -- lunar (month 1..12, day 1..30)
        ("IM", n, anchor_date)               -- every n months, n >= 2
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
    if base.startswith("M:"):
        toks = base[2:].split(",")
        days = set()
        for t in toks:
            if t == "L":
                days.add(LAST)
            elif t.isdigit() and 1 <= int(t) <= 31 and len(t) <= 2:
                days.add(int(t))
            else:
                raise ValueError("bad monthly cadence: %r" % cadence)
        return ("M", frozenset(days))
    m = _MWEEK_RE.match(base)
    if m:
        if m.group(2) not in _DAY_INDEX:
            raise ValueError("bad nth-weekday cadence: %r" % cadence)
        n = LAST if m.group(1) == "L" else int(m.group(1))
        return ("MW", n, _DAY_INDEX[m.group(2)])
    if base.startswith("Y:") or base.startswith("YL:"):
        kind, _sep, md = base.partition(":")
        m = _MD_RE.match(md)
        if not m:
            raise ValueError("bad yearly cadence: %r" % cadence)
        mm, dd = int(m.group(1)), int(m.group(2))
        if kind == "Y":
            try:
                datetime.date(2024, mm, dd)       # leap year: 02-29 is valid
            except ValueError:
                raise ValueError("bad yearly date: %r" % cadence)
        elif not (1 <= mm <= 12 and 1 <= dd <= 30):
            raise ValueError("bad lunar date (month 1..12, day 1..30): %r"
                             % cadence)
        return (kind, mm, dd)
    m = _MINTERVAL_RE.match(base)
    if m:
        n = int(m.group(1))
        if n < 2:
            raise ValueError("month-interval cadence needs N>=2: %r" % cadence)
        try:
            anchor = datetime.date.fromisoformat(m.group(2))
        except ValueError:
            raise ValueError("bad month-interval anchor date: %r" % cadence)
        return ("IM", n, anchor)
    raise ValueError("bad cadence: %r" % cadence)


def _month_len(d):
    return calendar.monthrange(d.year, d.month)[1]


def _fires_base(parsed, d):
    kind = parsed[0]
    if kind == "D":
        return True
    if kind == "W":
        return d.weekday() in parsed[1]
    if kind == "M":
        last = _month_len(d)
        return d.day in parsed[1] or (
            LAST in parsed[1] and d.day == last) or (
            d.day == last and any(x > last for x in parsed[1]))
    if kind == "MW":
        if d.weekday() != parsed[2]:
            return False
        if parsed[1] == LAST:
            return d.day + 7 > _month_len(d)
        return (d.day - 1) // 7 + 1 == parsed[1]
    if kind == "Y":
        if (d.month, d.day) == (parsed[1], parsed[2]):
            return True
        return (parsed[1], parsed[2]) == (2, 29) and (d.month, d.day) == (2, 28) \
            and not calendar.isleap(d.year)
    if kind == "YL":
        return lunar_kr.fires_on(d, parsed[1], parsed[2])
    if kind == "IM":
        _t, n, anchor = parsed
        if d < anchor:
            return False
        months = (d.year - anchor.year) * 12 + d.month - anchor.month
        return months % n == 0 and d.day == min(anchor.day, _month_len(d))
    _t, n, anchor = parsed
    return d >= anchor and (d - anchor).days % n == 0


def occurs(cadence, d):
    """Spec 1.2 occurs(): does cadence fire on local date d?
    d: datetime.date or 'YYYY-MM-DD' string."""
    if isinstance(d, str):
        d = datetime.date.fromisoformat(d)
    fire = _fires_base(parse_cadence(cadence), d)
    if not fire:
        return False
    # exclusion suffixes subtract otherwise-firing dates
    excl = cadence_exclusions(cadence)
    if "hol" in excl and _holiday_src is not None and _holiday_src.is_holiday(d):
        return False
    if "lastfri" in excl and _is_last_friday(d):
        return False
    return True


CADENCE_KEYS = (
    "routine.cadence.daily", "routine.cadence.weekly", "routine.cadence.sep",
    "routine.cadence.interval_days", "routine.cadence.month_days",
    "routine.cadence.month_day", "routine.cadence.month_last",
    "routine.cadence.nth_weekday", "routine.cadence.last_weekday",
    "routine.cadence.yearly", "routine.cadence.yearly_lunar",
    "routine.cadence.interval_months",
    "routine.cadence.nth.1", "routine.cadence.nth.2", "routine.cadence.nth.3",
    "routine.cadence.nth.4",
) + tuple("routine.cadence.dow.%d" % i for i in range(7))


def describe_cadence(cadence, t):
    """Human label of a cadence (monthly on the 12th, 2nd Monday ...). t(key)
    returns the template for one of CADENCE_KEYS from the kit i18n bundle
    (the copy lives there, not in code). Exclusion suffixes are not
    described. ValueError on a bad cadence (same gate as parse_cadence)."""
    p = parse_cadence(cadence)
    k = p[0]
    dow = lambda i: t("routine.cadence.dow.%d" % i)
    if k == "D":
        return t("routine.cadence.daily")
    if k == "W":
        return t("routine.cadence.weekly").format(days=t("routine.cadence.sep")
                                                  .join(dow(i) for i in sorted(p[1])))
    if k == "I":
        return t("routine.cadence.interval_days").format(n=p[1], date=p[2].isoformat())
    if k == "M":
        parts = [t("routine.cadence.month_day").format(n=x)
                 for x in sorted(x for x in p[1] if x != LAST)]
        if LAST in p[1]:
            parts.append(t("routine.cadence.month_last"))
        return t("routine.cadence.month_days").format(days=", ".join(parts))
    if k == "MW":
        if p[1] == LAST:
            return t("routine.cadence.last_weekday").format(day=dow(p[2]))
        return t("routine.cadence.nth_weekday").format(
            nth=t("routine.cadence.nth.%d" % p[1]), day=dow(p[2]))
    if k == "Y":
        return t("routine.cadence.yearly").format(m=p[1], d=p[2])
    if k == "YL":
        return t("routine.cadence.yearly_lunar").format(m=p[1], d=p[2])
    return t("routine.cadence.interval_months").format(n=p[1], date=p[2].isoformat())


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
    if is_alternatives(defn):
        raise ValueError('placement alternatives require live placement context')
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


def project_virtual(defs, from_date, to_date, placement_context=None):
    """Spec 3.1: [(recurrence_id, title, start_at, end_at, exclusive)] for
    every occurrence of the given defs in [from_date, to_date] (inclusive).
    Input = def rows + date bounds only; deterministic, no clock, no DB."""
    hits = []
    for defn in defs:
        if defn["cadence"] is None:
            continue
        for d in occurrence_dates(defn, from_date, to_date):
            if is_alternatives(defn):
                context = (placement_context or {}).get((defn['recurrence_id'], d.isoformat()))
                if context is None:
                    raise ValueError('placement alternatives require live placement context')
                choice = placement_candidate(defn, d, **context)
                sa, ea = choice['start_at'], choice['end_at']
            else:
                sa, ea, _sk = rule_instants(defn, d)
            hits.append((defn["recurrence_id"], defn["title"], sa, ea,
                         defn["exclusive"]))
    return hits


# ---------------------------------------------------------------------------
# 070 ride_travel placement (owner 2026-10-07): an anywhere-routine rides the
# day's first move leg that fits it, else sits at its fallback time. Still
# PURE: the caller reads the day's legs from the DB and passes them in, so
# materialize, the ride pass and the remind check all place a ride routine
# through this one function.
# ---------------------------------------------------------------------------

RIDE_TRAVEL = "ride_travel"
RIDE_PREFER = ("morning", "evening")
RIDE_NOON = "12:00"        # morning / evening split (local leg start)
RIDE_DEFAULT_NOT_BEFORE = "08:00"
RIDE_LONG_LEG_MIN = 30


def is_ride(defn):
    """True iff the def places itself on the day's travel (070)."""
    return (defn.get("placement") if hasattr(defn, "get")
            else defn["placement"]) == RIDE_TRAVEL


def parse_fallback(fallback_at):
    """fallback_at grammar (070):
        spec  := item (";" item)*
        item  := TIME | DAY ("," DAY)* "=" TIME        TIME = HH:MM
    A bare TIME is the default for days no item names (at most one).
    Returns {weekday_int: 'HH:MM'} plus key None for the default.
    Raises ValueError on anything else, or a weekday named twice."""
    out = {}
    if fallback_at is None or not str(fallback_at).strip():
        raise ValueError("fallback_at required (HH:MM or DAY,DAY=HH:MM;...)")
    for item in str(fallback_at).split(";"):
        item = item.strip()
        if not item:
            continue
        days, sep, tod = item.rpartition("=")
        tod = tod.strip()
        if not _TIME_RE.match(tod) or int(tod[:2]) > 23 or int(tod[3:]) > 59:
            raise ValueError("bad fallback time %r in %r" % (tod, fallback_at))
        keys = [None]
        if sep:
            names = [p.strip().upper() for p in days.split(",")]
            if not names or any(p not in _DAY_INDEX for p in names):
                raise ValueError("bad fallback days %r in %r"
                                 % (days, fallback_at))
            keys = [_DAY_INDEX[p] for p in names]
        for k in keys:
            if k in out:
                raise ValueError("fallback day named twice in %r"
                                 % (fallback_at,))
            out[k] = tod
    if not out:
        raise ValueError("fallback_at required (HH:MM or DAY,DAY=HH:MM;...)")
    return out


def fallback_time(fallback_at, d):
    """'HH:MM' the ride routine falls back to on local date d, or None when
    no item covers d's weekday and there is no default."""
    table = parse_fallback(fallback_at)
    return table.get(_as_date(d).weekday(), table.get(None))


def fallback_default(fallback_at):
    """The time_of_day a ride def stores (display / legacy readers): the
    default item, else the first item written."""
    table = parse_fallback(fallback_at)
    if None in table:
        return table[None]
    first = str(fallback_at).split(";")[0]
    return first.rpartition("=")[2].strip()


def fallback_covers(fallback_at, cadence):
    """Weekday names the cadence can fire on that fallback_at leaves without
    a time ([] = every firing day has one)."""
    table = parse_fallback(fallback_at)
    if None in table:
        return []
    parsed = parse_cadence(cadence)
    days = parsed[1] if parsed[0] == "W" else range(7)
    return [DAY_NAMES[i] for i in sorted(days) if i not in table]


def _local_hhmm(ts_utc, tz):
    return (datetime.datetime.strptime(ts_utc, UTC_FMT)
            .replace(tzinfo=timezone.utc).astimezone(tz).strftime("%H:%M"))


def _minutes(a, b):
    return int((datetime.datetime.strptime(b, UTC_FMT)
                - datetime.datetime.strptime(a, UTC_FMT)).total_seconds()
               // 60)


def ride_leg(defn, legs):
    """The leg the ride routine takes, or None. legs = the day's live move
    legs as (start_at, end_at, ...) tuples in start order. A leg qualifies
    when it is at least duration_min long and (ride_not_before set) starts
    at/after it locally; ride_prefer tries that half of the day first
    (split at local noon), the other half still before the fallback.
    The FIRST qualifying leg wins -- deterministic, no scoring."""
    dur = defn["duration_min"]
    dur = DEFAULT_DURATION_MIN if dur is None else int(dur)
    tz = ZoneInfo(defn["display_tz"] or "Asia/Seoul")
    nb = defn.get("ride_not_before") if hasattr(defn, "get") else None
    fits = [leg for leg in legs
            if leg[0] and leg[1] and _minutes(leg[0], leg[1]) >= dur
            # Owner rule (2026-10-23): a ride belongs only on a leg that
            # starts after 08:00 local or is itself at least 30 minutes.
            # The duration fit remains independent: long routines never
            # overflow a short, late leg.
            and (_local_hhmm(leg[0], tz) > RIDE_DEFAULT_NOT_BEFORE
                 or _minutes(leg[0], leg[1]) >= RIDE_LONG_LEG_MIN)
            and (not nb or _local_hhmm(leg[0], tz) >= nb)]
    prefer = defn.get("ride_prefer") if hasattr(defn, "get") else None
    if prefer in RIDE_PREFER:
        want_am = prefer == "morning"
        fits = ([l for l in fits if (_local_hhmm(l[0], tz) < RIDE_NOON)
                 == want_am]
                + [l for l in fits if (_local_hhmm(l[0], tz) < RIDE_NOON)
                   != want_am])
    return fits[0] if fits else None


def ride_instants(defn, d, legs):
    """(start_at, end_at, schedule_kind, leg) of a ride_travel def on local
    date d. leg = the chosen legs[] item (the block starts at the leg's
    start and lasts duration_min) or None (the block sits at the day's
    fallback time). Raises ValueError when no fallback covers d -- the
    registration gate (fallback_covers) keeps that from being stored."""
    d = _as_date(d)
    leg = ride_leg(defn, legs)
    dur = defn["duration_min"]
    dur = DEFAULT_DURATION_MIN if dur is None else int(dur)
    if leg is not None:
        start = datetime.datetime.strptime(leg[0], UTC_FMT).replace(
            tzinfo=timezone.utc)
        return (leg[0], _utc(start + timedelta(minutes=dur)), "timed", leg)
    tod = fallback_time(defn["fallback_at"], d)
    if tod is None:
        raise ValueError("ride routine has no fallback time for %s"
                         % d.isoformat())
    fixed = dict(defn)
    fixed.update(schedule_kind="timed", time_of_day=tod)
    sa, ea, sk = rule_instants(fixed, d)
    return sa, ea, sk, None


class PlacementUnavailable(ValueError):
    """No executable candidate; available without the optional 092 module."""
    def __init__(self, date, reasons):
        self.date = str(date)
        self.reasons = sorted(set(reasons)) or ['container_unavailable']
        super().__init__('placement unavailable on %s: %s' %
                         (self.date, ','.join(self.reasons)))


def is_alternatives(defn):
    return dict(defn).get('placement_alternatives') is not None


def is_flexible(defn):
    return is_alternatives(defn) or is_ride(defn)


def placement_candidate(defn, d, legs=(), busy=(), now=None):
    """Shared pure selection entry. Legacy ride semantics are unchanged."""
    if is_alternatives(defn):
        import placement_alternatives as pa
        duration = DEFAULT_DURATION_MIN if defn['duration_min'] is None else defn['duration_min']
        return pa.select(defn['placement_alternatives'], d, duration,
                         defn['display_tz'], legs, busy, now)
    sa, ea, sk, leg = (ride_instants(defn, d, legs) if is_ride(defn)
                       else (*rule_instants(defn, d), None))
    return dict(start_at=sa, end_at=ea, schedule_kind=sk, leg=leg,
                kind='travel' if leg else 'fallback', reasons=[])
