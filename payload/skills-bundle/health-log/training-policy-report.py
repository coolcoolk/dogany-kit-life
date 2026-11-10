#!/usr/bin/env python3
"""Read a session's prescribed/actual doses with their pinned policy provenance.

Health-log owns this historical review projection, not policy mutation. Planned
rows never enter the history output. No fallback to current program defaults.
"""
import argparse
import json
from pathlib import Path
import sqlite3


def report(conn, session_id):
    conn.row_factory = sqlite3.Row
    workout = conn.execute('SELECT id,session_version,policy_bundle_id FROM workouts WHERE id=?',
                           (session_id,)).fetchone()
    if workout is None:
        raise ValueError('session does not exist')
    rules = [dict(r) for r in conn.execute(
        'SELECT key,value_type,value_json,scope,source_ref,source_date,approval_status '
        'FROM training_policy_rules WHERE bundle_id=? ORDER BY key,scope', (workout['policy_bundle_id'],))]
    for rule in rules:
        rule['value'] = json.loads(rule.pop('value_json'))
    actuals = [dict(r) for r in conn.execute(
        "SELECT id,exercise_name,set_kind,target_reps,reps,target_load_g,weight_kg,actual_unit,"
        "target_count,actual_hold_sec,policy_bundle_id FROM workout_sets "
        "WHERE session_id=? AND row_status='logged' ORDER BY recorded_at,id", (session_id,))]
    return dict(session=dict(workout), policy_rules=rules, logged_sets=actuals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--session', required=True, type=int)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        conn.execute('BEGIN')
        print(json.dumps(report(conn, args.session), ensure_ascii=False))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
