"""Explicit, reversible consolidation of shared goal identities.

Targets and references stay on their original rows. Closing the superseded
spines removes them from current judgement while retaining historical evidence.
The newest row and all of its targets remain byte-for-byte unchanged.
"""
import argparse
import hashlib
import json


def _rows(c, table, where, args):
    cur = c.execute('SELECT * FROM ' + table + ' WHERE ' + where + ' ORDER BY id', args)
    keys = [d[0] for d in cur.description]
    return [dict(zip(keys, r)) for r in cur]


def snapshot(c, ids):
    marks = ','.join('?' for _ in ids)
    goals = _rows(c, 'goal', 'id IN (' + marks + ')', ids)
    if len(goals) != len(ids):
        raise ValueError('every explicit goal id must exist')
    ulids = [g['ulid'] for g in goals]
    return dict(goal=goals,
                ledger_goal=_rows(c, 'ledger_goal', 'ulid IN (' + marks + ')', ulids),
                goal_target=_rows(c, 'goal_target', 'goal_id IN (' + marks + ')', ids),
                commitment=_rows(c, 'commitment', 'goal_id IN (' + marks + ')', ids))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def plan(c, ids, reason):
    if len(ids) < 2 or len(set(ids)) != len(ids) or not reason.strip():
        raise ValueError('at least two distinct explicit goal ids and a reason required')
    before = snapshot(c, ids)
    newest = max(before['goal'], key=lambda g: (g['created_at'].replace(' ', 'T'), g['id']))
    if newest['closed_at'] or newest['lifecycle'] in ('achieved', 'failed', 'stopped', 'archived'):
        raise ValueError('newest goal is closed; refuse to silently reopen it')
    keep = newest['id']
    live_writers = [g for g in before['ledger_goal'] if g['status'] == 'active']
    if live_writers and not any(g['ulid'] == newest['ulid'] for g in live_writers):
        raise ValueError('newest goal has no active ledger writer; reconcile its identity before consolidating')
    result = dict(keep_goal_id=keep, close_goal_ids=[i for i in ids if i != keep],
                  reason='superseded by goal %d: %s' % (keep, reason),
                  goals=[dict(id=g['id'], title=g['title']) for g in before['goal']],
                  preserve_targets=[t['id'] for t in before['goal_target']],
                  before=before)
    result['expect'] = digest(result)
    return result


def apply(c, p, at):
    losers = set(p['close_goal_ids'])
    loser_ulids = {g['ulid'] for g in p['before']['goal'] if g['id'] in losers}
    for gid in losers:
        c.execute("UPDATE goal SET lifecycle='archived',closed_at=?,close_reason=?,"
                  "stage_token='done',focus_state='dormant' WHERE id=?",
                  (at, p['reason'], gid))
    for g in p['before']['ledger_goal']:
        if g['ulid'] in loser_ulids:
            detail = json.loads(g['detail'] or '{}')
            detail['retire_reason'] = p['reason']
            c.execute("UPDATE ledger_goal SET status='archived',rung_state='retired',"
                      "focus_state='dormant',updated_at=?,version=version+1,detail=? WHERE id=?",
                      (at, json.dumps(detail, ensure_ascii=False), g['id']))
    ids = [g['id'] for g in p['before']['goal']]
    record = dict(before=p['before'], after=snapshot(c, ids), reason=p['reason'])
    audit_id = c.execute("INSERT INTO ledger_audit(ts,kind,detail) VALUES (?,'goal_consolidate',?)",
                         (at, json.dumps(record, ensure_ascii=False))).lastrowid
    return dict(applied=True, keep_goal_id=p['keep_goal_id'],
                closed_goal_ids=p['close_goal_ids'], undo_audit_id=audit_id)


def undo(c, audit_id, at):
    r = c.execute("SELECT detail FROM ledger_audit WHERE id=? AND kind='goal_consolidate'",
                  (audit_id,)).fetchone()
    if not r:
        raise ValueError('consolidation audit id not found')
    if c.execute("SELECT 1 FROM ledger_audit WHERE kind='goal_consolidate_undo' AND detail=?",
                 (str(audit_id),)).fetchone():
        raise ValueError('consolidation already undone')
    record = json.loads(r[0])
    ids = [g['id'] for g in record['after']['goal']]
    if snapshot(c, ids) != record['after']:
        raise ValueError('goals or targets changed since consolidation; undo refused')
    for table, fields in (
            ('goal', ('lifecycle', 'closed_at', 'close_reason', 'stage_token', 'focus_state')),
            ('ledger_goal', ('status', 'rung_state', 'focus_state', 'updated_at', 'version', 'detail'))):
        for row in record['before'][table]:
            c.execute('UPDATE ' + table + ' SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?',
                      [row[k] for k in fields] + [row['id']])
    c.execute("INSERT INTO ledger_audit(ts,kind,detail) VALUES (?,'goal_consolidate_undo',?)",
              (at, str(audit_id)))
    return dict(undone=True, audit_id=audit_id)


def cli(K, argv):
    parser = argparse.ArgumentParser(prog='kit_life.sh goal-consolidate')
    parser.add_argument('--ids', help='comma-separated shared goal ids; newest is kept')
    parser.add_argument('--reason')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--undo', type=int, metavar='AUDIT_ID')
    parser.add_argument('--expect', help='exact dry-run digest, required for apply')
    args = parser.parse_args(argv)
    c = K.get_conn()
    try:
        c.execute('BEGIN IMMEDIATE' if not args.dry_run else 'BEGIN')
        if args.undo is not None:
            if args.ids or args.reason or args.expect:
                raise ValueError('--undo takes only the audit id')
            result = undo(c, args.undo, K._utc_now_str())
        else:
            ids = [int(i) for i in (args.ids or '').split(',') if i]
            p = plan(c, ids, args.reason or '')
            if args.apply:
                if args.expect != p['expect']:
                    raise ValueError('plan changed or --expect missing; run --dry-run again')
                result = apply(c, p, K._utc_now_str())
            else:
                result = {k: v for k, v in p.items() if k != 'before'}
                result['dry_run'] = True
        c.commit()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (ValueError, TypeError) as e:
        c.rollback()
        K._err('goal-consolidate: ' + str(e))
    finally:
        c.close()
