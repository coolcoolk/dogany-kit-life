"""Validated placement unions and one pure candidate selector.

The JSON aggregate is atomic: alternatives, explicit priority, and fallback
cannot be observed at different revisions. No IO, DB, or wall clock here.
"""
import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

DAYS = ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN')
FMT = '%Y-%m-%dT%H:%M:%SZ'


class PlacementInvalid(ValueError):
    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


from routine_projection import PlacementUnavailable


def _time(value, end=False):
    if end and value == '24:00':
        return 1440
    if not isinstance(value, str) or not re.fullmatch(r'[0-2][0-9]:[0-5][0-9]', value):
        raise PlacementInvalid('time', 'time must be HH:MM')
    minute = int(value[:2]) * 60 + int(value[3:])
    if minute >= 1440:
        raise PlacementInvalid('time', 'time must be before 24:00')
    return minute


def validate(raw, duration=30):
    """Return a canonical JSON-compatible spec; reject unexecutable fields."""
    try:
        spec = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError) as exc:
        raise PlacementInvalid('json', 'alternatives must be valid JSON') from exc
    if not isinstance(spec, dict) or set(spec) - {'alternatives', 'priority', 'fallback_at'}:
        raise PlacementInvalid('conditions', 'expected alternatives, optional priority and fallback_at')
    alts = spec.get('alternatives')
    if not isinstance(alts, list) or not alts:
        raise PlacementInvalid('conditions', 'alternatives must be a nonempty list')
    duration = 30 if duration is None else duration
    if type(duration) is not int or duration <= 0:
        raise PlacementInvalid('duration', 'duration must be a positive integer')
    out = {'alternatives': []}
    possible = False
    for alt in alts:
        if not isinstance(alt, dict):
            raise PlacementInvalid('conditions', 'each alternative must be an object')
        kind = alt.get('type')
        allowed = {'type', 'weekdays'} | ({'start_not_before', 'end_not_after'}
                   if kind == 'window' else {'min_container_minutes'}
                   if kind == 'travel' else set())
        if kind not in ('window', 'travel') or set(alt) - allowed:
            raise PlacementInvalid('conditions', 'unknown placement type or condition')
        a = dict(alt)
        if 'weekdays' in alt:
            days = alt['weekdays']
            if (not isinstance(days, list) or not days
                    or any(type(d) is not str or d not in DAYS for d in days)
                    or len(set(days)) != len(days)):
                raise PlacementInvalid('weekdays', 'weekdays must be distinct MON..SUN names')
            a['weekdays'] = sorted(days, key=DAYS.index)
        if kind == 'window':
            lo = _time(alt.get('start_not_before'))
            hi = _time(alt.get('end_not_after', '24:00'), end=True)
            if hi <= lo:
                raise PlacementInvalid('interval', 'window end must be after start on the same day')
            possible |= 'end_not_after' not in alt or hi - lo >= duration
        else:
            minimum = alt.get('min_container_minutes')
            if type(minimum) is not int or minimum < 1:
                raise PlacementInvalid('container_minimum', 'min_container_minutes must be a positive integer')
            possible = True  # This is a minimum, never a maximum container size.
        out['alternatives'].append(a)
    if 'priority' in spec:
        priority = spec['priority']
        if (not isinstance(priority, list)
                or any(type(i) is not int for i in priority)
                or sorted(priority) != list(range(len(alts)))):
            raise PlacementInvalid('priority', 'priority must list every alternative index exactly once')
        out['priority'] = list(priority)
    if 'fallback_at' in spec:
        _time(spec['fallback_at'])
        out['fallback_at'] = spec['fallback_at']
        possible = True
    if not possible:
        raise PlacementInvalid('no_fit', 'duration does not fit any stated interval; widen a window or shorten duration')
    return out


def _stamp(value):
    return dt.datetime.strptime(value, FMT).replace(tzinfo=dt.timezone.utc)


def _utc(value):
    return value.astimezone(dt.timezone.utc).strftime(FMT)


def select(spec, date, duration, tz_name, legs=(), busy=(), now=None):
    """Earliest fitting union candidate, or owner-specified priority order.

    legs and busy: (start_at, end_at, container/block ID). Only the chosen
    container is exempt from collision checking. Fallback is explicit and
    fixed; it never slides outside the owner's alternatives.
    """
    duration = 30 if duration is None else duration
    spec = validate(spec, duration)
    day = dt.date.fromisoformat(date) if isinstance(date, str) else date
    midnight = dt.datetime.combine(day, dt.time(), ZoneInfo(tz_name or 'Asia/Seoul'))
    end_day = midnight + dt.timedelta(days=1)
    delta = dt.timedelta(minutes=duration)
    last_finish = end_day + delta - dt.timedelta(seconds=1)
    floor = _stamp(now) + dt.timedelta(seconds=1) if now else None
    blockers = [(_stamp(s), _stamp(e), bid) for s, e, bid in busy if s and e]
    reasons = set()

    def local(minute):
        return (midnight + dt.timedelta(minutes=minute)).astimezone(dt.timezone.utc)

    def fit(start, end, container=None, fixed=False):
        if floor and start < floor:
            if fixed:
                reasons.add('interval_elapsed')
                return None
            start = floor
        for bs, be, bid in sorted(blockers, key=lambda b: (b[0], b[1], str(b[2]))):
            if (container is not None and bid == container) or be <= start or bs >= end:
                continue
            if start < be and bs < start + delta:
                reasons.add('overlap_fixed')
                if fixed:
                    return None
                start = be
        if start + delta > end:
            reasons.add('insufficient_remaining')
            return None
        return start

    choices = []
    for index, alt in enumerate(spec['alternatives']):
        if DAYS[day.weekday()] not in alt.get('weekdays', DAYS):
            continue
        if alt['type'] == 'window':
            sa = fit(local(_time(alt['start_not_before'])),
                     local(_time(alt['end_not_after'], end=True))
                     if 'end_not_after' in alt else last_finish)
            if sa is not None:
                choices.append((index, sa, None, 'window'))
        else:
            available = False
            for leg in legs:
                st, en = _stamp(leg[0]), _stamp(leg[1])
                if en <= st or en <= midnight or st >= end_day:
                    continue
                if (en - st).total_seconds() < alt['min_container_minutes'] * 60:
                    reasons.add('container_too_short')
                    continue
                available = True
                sa = fit(max(st, midnight), min(en, last_finish), leg[2])
                if sa is not None:
                    choices.append((index, sa, leg, 'travel'))
            if not available:
                reasons.add('container_unavailable')
    if not any(DAYS[day.weekday()] in a.get('weekdays', DAYS) for a in spec['alternatives']):
        reasons.add('scope_unavailable')
    if choices:
        priority = spec.get('priority')
        key = ((lambda c: (priority.index(c[0]), c[1], str(c[2]))) if priority
               else (lambda c: (c[1], c[3], str(c[2]))))
        _, start, leg, kind = min(choices, key=key)
    elif 'fallback_at' in spec:
        start = local(_time(spec['fallback_at']))
        start = fit(start, start + delta, fixed=True)
        if start is None:
            raise PlacementUnavailable(day, reasons)
        leg, kind = None, 'fallback'
    else:
        raise PlacementUnavailable(day, reasons)
    return {'start_at': _utc(start), 'end_at': _utc(start + delta),
            'schedule_kind': 'timed', 'leg': leg, 'kind': kind,
            'reasons': sorted(reasons & {'overlap_fixed'})}
