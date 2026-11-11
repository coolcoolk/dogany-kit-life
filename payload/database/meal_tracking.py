"""Optional meal-card rows in kit config; intake facts stay in health_regimen.

No seeded settings, schema changes, or daily goals inferred from nutrition advice.
Supplement names appear only when explicitly tracked on the meal card.
"""
import argparse
import datetime
import json
import math

METRICS_KEY = 'meal_tracked_metrics'
REGIMENS_KEY = 'meal_tracked_regimens'
FIXED = frozenset(('kcal', 'protein', 'carb', 'fat', 'caffeine'))


def load(K, conn, key):
    raw = K._config_stored(conn, key)
    return json.loads(raw) if raw else []


def known_metrics(conn):
    """Use the registry, including future registered diet amount metrics."""
    return dict(conn.execute("SELECT token,unit_token FROM metric_def "
        "WHERE owner_module='diet' AND scope_kind='block' AND accrual='accrued' "
        "AND status='active' AND unit_token IS NOT NULL ORDER BY token"))


def amount_rows(K, conn, eaten_ids, planned_ids):
    tracked = load(K, conn, METRICS_KEY)
    if not tracked:
        return []
    known = known_metrics(conn)
    def totals(ids):
        if not ids:
            return {}
        return dict(conn.execute('SELECT metric,SUM(value) FROM measure '
            'WHERE block_id IN (%s) GROUP BY metric' % ','.join('?' * len(ids)), ids))
    eaten, planned = totals(eaten_ids), totals(planned_ids)
    return [dict(metric=r['metric'], unit=known[r['metric']], goal=r.get('goal'),
                 eaten=float(eaten.get(r['metric']) or 0),
                 planned=float(planned.get(r['metric']) or 0))
            for r in tracked if r['metric'] in known and r['metric'] not in FIXED]


def regimen_flags(K, conn, date):
    """One owner supplement flag per tracked name, on the actual intake day.

    Multiple schedules/doses of a name still yield one flag. Reuse the same
    occurrence snapshot and local-day definition as the existing count line.
    The meal card renders these opt-in names; the day pin keeps its count line.
    """
    import health_regimen as H
    from zoneinfo import ZoneInfo
    names = load(K, conn, REGIMENS_KEY)
    taken = set()
    if names and H.ready(conn):
        for name, at, tz in conn.execute(
                'SELECT r.name,t.taken_at,g.timezone FROM health_regimen_taken t '
                'JOIN health_regimen_reminder g ON g.id=t.reminder_id '
                'JOIN health_regimen r ON r.id=t.regimen_id '
                "WHERE g.member_id='owner' AND r.kind='supplement' AND t.taken_at IS NOT NULL"):
            if str(H.instant(at).astimezone(ZoneInfo(tz)).date()) == str(date):
                taken.add(name)
    return [dict(name=name, taken=name in taken) for name in names]


def cli(K, cmd, argv):
    ap = argparse.ArgumentParser(prog='kit_life.sh ' + cmd, description=__doc__)
    if cmd == 'meal-track-list':
        ap.add_argument('date', nargs='?', default=datetime.date.today().isoformat())
    else:
        ap.add_argument('name', help='metric token, or exact supplement name with --regimen')
        ap.add_argument('--regimen', action='store_true')
        if cmd == 'meal-track-add':
            ap.add_argument('--goal', type=float, help='owner-stated daily goal, in registry units')
    args = ap.parse_args(argv)
    conn = K.get_conn()
    try:
        if cmd == 'meal-track-list':
            if datetime.date.fromisoformat(args.date).isoformat() != args.date:
                raise ValueError('Use YYYY-MM-DD')
            out = dict(metrics=load(K, conn, METRICS_KEY),
                       regimens=regimen_flags(K, conn, args.date), date=args.date)
        else:
            if cmd == 'meal-track-add' and args.goal is not None:
                if args.regimen or not math.isfinite(args.goal) or args.goal <= 0:
                    raise ValueError('--goal must be a finite positive nutrient amount; omit it for intake-only tracking')
            conn.execute('BEGIN IMMEDIATE')
            key = REGIMENS_KEY if args.regimen else METRICS_KEY
            tracked = load(K, conn, key)
            if args.regimen:
                import health_regimen as H
                known = [r[0] for r in conn.execute(
                    "SELECT DISTINCT name FROM health_regimen WHERE member_id='owner' "
                    "AND active=1 AND kind='supplement' ORDER BY name")] if H.ready(conn) else []
                # An inactive/retired name can still be removed from tracking.
                if cmd == 'meal-track-add' and args.name not in known:
                    raise ValueError('Unknown supplement; register the owner-stated regimen first. Known supplements: ' + ', '.join(known))
                if cmd == 'meal-track-del':
                    tracked = [n for n in tracked if n != args.name]
                elif args.name not in tracked:
                    tracked.append(args.name)
            else:
                known = known_metrics(conn)
                if args.name not in known:
                    raise ValueError('Unknown metric. Known metrics: ' + ', '.join(known))
                if args.name in FIXED:
                    raise ValueError('This metric already has a fixed or automatic row; caffeine behavior is unchanged. Extra metrics: ' + ', '.join(k for k in known if k not in FIXED))
                # Updating a goal preserves the owner's row order.
                entry = dict(metric=args.name, goal=getattr(args, 'goal', None))
                index = next((i for i, r in enumerate(tracked) if r['metric'] == args.name), None)
                if index is not None:
                    if cmd == 'meal-track-add':
                        tracked[index] = entry
                    else:
                        tracked.pop(index)
                elif cmd == 'meal-track-add':
                    tracked.append(entry)
            K._config_put(conn, key, json.dumps(tracked, ensure_ascii=False))
            conn.commit()
            out = dict(status='TRACKED' if cmd == 'meal-track-add' else 'UNTRACKED',
                       kind='regimen' if args.regimen else 'metric', name=args.name)
        print(json.dumps(out, ensure_ascii=False))
    except (ValueError, TypeError) as error:
        conn.rollback()
        ap.error(str(error))
    finally:
        conn.close()
