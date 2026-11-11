"""Payment timing evidence for one recurring stream; never schedules or sends.

Only the last six positive, personal payments count. Missing clocks never
count toward the 80% window. Explicit owner values always survive refreshes.
"""
import collections
import datetime
import re


def infer(payments):
    payments = list(payments)[:6]  # caller supplies newest first
    result = dict(pay_mode=None, payments=len(payments), time_hits=0, day_hits=0,
                  same_method=False, modal_day=None)
    if len(payments) < 3:
        return result
    times, days, methods = [], [], []
    for row in payments:
        clock = row.get('at_time') or ''
        if re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?', clock):
            times.append(int(clock[:2])*60 + int(clock[3:5]))
        days.append(datetime.date.fromisoformat(row['date']).day)
        methods.append(row.get('method'))
    if not times:
        return result
    # A clock window can cross midnight. Inclusive endpoints: exactly 90m
    # apart still qualifies; integer arithmetic preserves the 80% boundary.
    time_hits = max(sum((other-start) % 1440 <= 90 for other in times) for start in times)
    counts = collections.Counter(days)
    # Deterministic tie: the most recent of equally frequent observed days.
    modal_day = next(day for day in days if counts[day] == max(counts.values()))
    day_hits = sum(abs(day-modal_day) <= 3 for day in days)
    same_method = bool(methods[0]) and len(set(methods)) == 1
    mode = 'auto' if time_hits*5 >= len(payments)*4 and day_hits*3 >= len(days)*2 and same_method else 'manual'
    return dict(result, pay_mode=mode, time_hits=time_hits, day_hits=day_hits,
                same_method=same_method, modal_day=modal_day)


def refresh(conn):
    """Persist inference in the caller's transaction, without touching source
    (which describes stream identity, independently of payment-mode evidence).
    """
    import ledger_diag as D
    results = []
    landed = [row for row in D._landed(conn) if row['kind'] == 'expense']
    partner = D._partner_methods(conn)
    keys = collections.defaultdict(set)
    for fid, key in conn.execute('SELECT fixed_id,key FROM fixed_cost_key'):
        keys[fid].add(key)
    for stream in D.rows(conn, "SELECT * FROM fixed_cost WHERE direction='expense' ORDER BY id"):
        fid, name, mode, source = (stream[key] for key in ('id','name','pay_mode','pay_mode_source'))
        # A historical review reconciles attachments only through that month.
        # Newer actual payments still belong to an established, unambiguous
        # stream key (e.g. a utility now paid through a different wallet).
        # Reuse registration's identity and compatibility rules without moving
        # any spend row or inferring membership from a similar amount alone.
        payments = [row for row in landed if row['stream_id'] == fid or
                    (row['stream_id'] is None and D.stream_key(row, partner) in keys[fid]
                     and D.stream_compatible(stream, row))]
        payments.sort(key=lambda row: (row['date'], row['at_time'] or '', row['id']), reverse=True)
        payments = payments[:6]
        verdict = infer(payments)
        # An existing value without provenance predates inference: preserve it
        # just like an owner correction rather than claiming it as our guess.
        if source == 'owner' or (mode is not None and source is None):
            stored, origin = mode, 'owner'
        else:
            stored, origin = verdict['pay_mode'], 'inferred'
        conn.execute('UPDATE fixed_cost SET pay_mode=?,pay_mode_source=? WHERE id=?', (stored, origin, fid))
        results.append(dict(verdict, id=fid, name=name, inferred=verdict['pay_mode'],
                            pay_mode=stored, pay_mode_source=origin))
    return results


def owner_set(conn, fid, value):
    if value not in ('auto', 'manual', '', None):
        raise ValueError('pay_mode must be auto, manual or empty (unknown)')
    conn.execute("UPDATE fixed_cost SET pay_mode=?,pay_mode_source='owner' WHERE id=?", (value or None, fid))
