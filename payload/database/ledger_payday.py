"""Salary calendar shared by stream reconciliation and read-only account cards.

Use the largest payroll credit per month: bonuses do not move payday or widen
its regular salary range. Owner rules are account-scoped ledger_payday facts
(item = account:<canonical pay_method id>, detail = day/shift/amount_note, an optional ordered list of reasons).
"""
import calendar
import datetime
import json
from zoneinfo import ZoneInfo

from holiday_source import is_holiday


def business_day(day, shift):
    step = -1 if shift == 'prev' else 1
    if shift in ('prev', 'next'):
        while day.weekday() >= 5 or is_holiday(day):
            day += datetime.timedelta(days=step)
    return day


def nominal(year, month, day):
    return datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def regular(payments):
    months = {}
    for row in payments:
        if row['amount'] > 0:
            key = row['date'][:7]
            if key not in months or row['amount'] > months[key]['amount']:
                months[key] = row
    return [months[key] for key in sorted(months)]


def infer(payments):
    dates = [datetime.date.fromisoformat(r['date']) for r in regular(payments)[-6:]]
    if not dates:
        return None, None
    if len(dates)==1:
        return dates[0].day, None
    candidates = []
    for day in range(1, 32):
        for shift in ('same', 'prev', 'next'):
            if all(business_day(nominal(d.year, d.month, day), shift) == d for d in dates):
                exact = sum(nominal(d.year, d.month, day) == d for d in dates)
                candidates.append((exact, shift == 'same', day, shift))
    if candidates:
        # A business-day anchor disambiguates adjacent nominal days. Without
        # one, only a unique holiday-shift explanation is a confirmed rule.
        best = max(candidates)
        tied = [c for c in candidates if c[:2] == best[:2]]
        if len(tied) == 1:
            return best[2], best[3]
    # Preserve the established reader's schedule-change handling, rather than
    # interpreting an employer's changed payday as a holiday adjustment.
    from ledger_profile import _payday
    day, shift, _, _ = _payday(regular(payments))
    return day, shift


def next_date(day, shift, after):
    if isinstance(after, str):
        after = datetime.datetime.fromisoformat(after.replace('Z','+00:00')) if 'T' in after else datetime.date.fromisoformat(after)
    if isinstance(after, datetime.datetime):
        after = after.astimezone(ZoneInfo('Asia/Seoul')).date() if after.tzinfo else after.date()
    for offset in range(14):
        index = after.year * 12 + after.month - 1 + offset
        year, month = divmod(index, 12)
        due = business_day(nominal(year, month + 1, day), shift)
        if due > after:
            return due.isoformat()
    raise ValueError('no next payday in calendar horizon')


def amount_notes(value):
    """Read the original scalar amount_note as one reason, without rewriting it."""
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))


def rule(conn, item):
    fact = conn.execute("SELECT detail,polarity FROM ledger_preference WHERE topic='ledger_payday' "
                        "AND item=? AND status='active' AND source IN ('owner','chat') "
                        "ORDER BY recorded_at DESC,id DESC LIMIT 1", (item,)).fetchone()
    return (json.loads(fact[0]) if fact and fact[0] else {}, fact[1] if fact else 'negotiable')


def notes_for(conn, items):
    return list(dict.fromkeys(note for item in dict.fromkeys(items)
                             for note in amount_notes(rule(conn, item)[0].get('amount_note'))))


def edit_notes(K, conn, account, fixed, reasons, remove=False):
    """Extend the existing account/fixed ledger_payday JSON; no schema change."""
    from ledger_v6 import _existing_method
    from consult_anchor import preference_add
    if account is not None:
        method = _existing_method(conn, account)
        if method['kind'] != 'account':
            raise ValueError('salary reasons require an account')
        item = 'account:' + str(method['id'])
    else:
        if not conn.execute('SELECT 1 FROM fixed_cost WHERE id=?', (fixed,)).fetchone():
            raise ValueError('unknown fixed-cost stream: %s' % fixed)
        item = 'fixed:' + str(fixed)
    if any(not r.strip() for r in reasons):
        raise ValueError('reason must not be blank')
    reasons = amount_notes(reasons)
    detail, polarity = rule(conn, item)
    before = amount_notes(detail.get('amount_note'))
    after = ([r for r in before if r not in reasons] if remove else
             list(dict.fromkeys(before + reasons)))
    changed = before != after or ('amount_note' in detail and detail['amount_note'] != after)
    if changed:
        detail['amount_note'] = after
        preference_add(K, conn, 'ledger_payday', item, polarity=polarity, detail=detail)
    return dict(item=item, amount_note=after, changed=changed)


def read(conn, payments, after, method_id=None):
    pays = regular(payments)
    day, shift = infer(pays)
    if day is None:
        return None
    item = 'account:'+str(method_id) if method_id is not None else None
    schedule = rule(conn, item)[0] if item else {}
    if type(schedule.get('day')) is int and 1 <= schedule['day'] <= 31:
        day = schedule['day']
    if schedule.get('shift') in ('prev', 'next', 'same'):
        shift = schedule['shift']
    items = ([item] if item else []) + ['fixed:'+str(r['stream_id']) for r in payments if r.get('stream_id')]
    amounts = [r['amount'] for r in pays[-3:]]
    return dict(salary_day=day, shift=shift, next_payday=next_date(day, shift, after),
                lo=min(amounts), hi=max(amounts), variable=min(amounts) != max(amounts),
                amount_note=notes_for(conn, items))
