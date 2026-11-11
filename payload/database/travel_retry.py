"""Durable per-anchor, per-direction work. Success means a block read-back."""
import os
import subprocess
import sqlite3


def ready(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='travel_leg_retry'").fetchone() is not None


def enqueue(k, conn, anchor, origin, dest):
    if not ready(conn) or not origin or not dest or anchor['start_at'] <= k.now_utc():
        return
    legs = [('before',origin,dest)]
    base = k._travel_base_place_id(conn)
    if base and base != dest and not k._travel_has_following_external(conn,anchor):
        legs.append(('after',dest,base))
    rule = k._travel_rule_for_anchor_txn(conn, anchor['ulid'], None)
    for role,a,b in legs:
        if rule and rule.get(role+'_src') == 'explicit':
            continue
        if a==b or k._travel_edge_get(conn,a,b) is not None:
            continue
        place = k.travel_place_get(conn, dest)
        legacy = place.get('to_min' if role=='before' else 'from_min')
        if legacy is not None and (role=='after' or a==base):
            continue
        conn.execute('INSERT OR IGNORE INTO travel_leg_retry(anchor_ulid,role,origin_id,dest_id,next_at) VALUES(?,?,?,?,?)',
                     (anchor['ulid'],role,a,b,k.now_utc()))
    conn.commit()


def run(k, conn, limit=2, emit=print):
    if not ready(conn):
        return
    now = k.now_utc()
    rows = conn.execute("SELECT id,anchor_ulid,role,origin_id,dest_id,attempts FROM travel_leg_retry WHERE state='queued' AND next_at<=? ORDER BY next_at,id LIMIT ?", (now,limit)).fetchall()
    for rid,ulid,role,origin,dest,attempts in rows:
        anchor = k._travel_row(conn,'SELECT * FROM timeblock WHERE ulid=?',(ulid,))
        rule = k._travel_rule_for_anchor_txn(conn,ulid,None)
        if (not anchor or anchor.get('settled_at') or anchor['start_at']<=now
                or not rule or rule['status']!='active'
                or rule.get(role+'_src') == 'explicit'
                or (role=='before' and (anchor.get('place_id') != dest or k._travel_origin_place_id(conn,anchor)!=origin))
                or (role=='after' and (anchor.get('place_id') != origin or k._travel_has_following_external(conn,anchor)))):
            conn.execute("UPDATE travel_leg_retry SET state='cancelled' WHERE id=?",(rid,))
            conn.commit()
            continue
        # Claim the attempt before the external call. Concurrent cadence runs
        # cannot drive the same leg; a crashed worker becomes due again.
        claim = conn.execute("UPDATE travel_leg_retry SET attempts=attempts+1,next_at=? WHERE id=? AND state='queued' AND next_at<=?",(k._iso_shift_min(now,10),rid,now))
        conn.commit()
        if not claim.rowcount:
            continue
        error = 'lookup failed'
        try:
            edge = k._travel_edge_get(conn,origin,dest)
            if edge is None:
                script = os.environ.get('TRAVEL_RESOLVE_SH') or os.path.join(k._kit_root(),'routines','bundle','travel-resolve.sh')
                env = dict(os.environ,TRAVEL_DB=os.path.abspath(k.DB_PATH),LIFEKIT_SH=os.path.join(k.SCRIPT_DIR,'kit_life.sh'))
                result = subprocess.run(['bash',script,'--lookup-only',k._place_name(conn,origin),k._place_name(conn,dest)],
                                        env=env,capture_output=True,text=True,timeout=45)
                values = [x.split('\t') for x in result.stdout.splitlines() if x.startswith(('RESOLVED\t','NEGLIGIBLE\t'))]
                if result.returncode or not values:
                    raise ValueError((result.stdout or result.stderr or 'lookup failed').strip()[:300])
                minutes = int(values[0][3])
                if minutes<0:
                    raise ValueError('negative route duration')
                edge = k.travel_edge_add(conn,origin,dest,minutes)
            minutes = int(edge['minutes'])
            rule = k._travel_rule_for_anchor_txn(conn,ulid,None)
            if role=='before' and rule.get('before_src')=='provisional':
                if not k._travel_provisional_upgrade(conn,rule,dest,minutes):
                    raise ValueError('provisional upgrade failed')
            elif role=='after' and rule.get('after_src')=='provisional':
                res = k.travel_rule_upd(conn,rule['id'],{'after_src':'place'},owner_edit=False)
                if res.get('result')!='applied':
                    raise ValueError('return upgrade failed')
            k.travel_refresh(conn,None,ulid)
            # Read the actual stored move block, not the refresh receipt. Zero
            # minutes legitimately removes it. Pins/started blocks never pass
            # as repaired merely because the edge is present.
            blocks = conn.execute("SELECT start_at,end_at FROM timeblock WHERE derived_from=? AND derived_role=? AND block_class='travel' AND settled_at IS NULL",(ulid,role)).fetchall()
            durations = [k._iso_diff_min(b,a) for a,b in blocks]
            if (minutes==0 and blocks) or (minutes>0 and durations != [minutes]):
                raise ValueError('block read-back mismatch: expected %s, got %s' % (minutes,durations))
            conn.execute("UPDATE travel_leg_retry SET state='done',error=NULL WHERE id=?",(rid,))
            conn.commit()
            emit('TRAVEL_RETRY_VERIFIED\t%s\t%s\t%s' % (rid,role,minutes))
            continue
        except (OSError,ValueError,IndexError,TypeError,sqlite3.Error,subprocess.TimeoutExpired) as exc:
            conn.rollback()
            error = str(exc)[:300]
        exhausted = attempts+1>=4
        notice = k._i18n('travel.retry.failed','Route lookup failed repeatedly: {origin} → {dest}.').format(origin=k._place_name(conn,origin),dest=k._place_name(conn,dest)) if exhausted else None
        conn.execute("UPDATE travel_leg_retry SET state=?,error=?,notice=? WHERE id=?",('failed' if exhausted else 'queued',error,notice,rid))
        conn.commit()
        emit('TRAVEL_RETRY\t%s\t%s\t%s' % (rid,'failed' if exhausted else 'queued',role))


def cli(k, verb, argv):
    conn = k.event_conn()
    try:
        if verb=='travel-retry-run':
            if argv and (len(argv)!=2 or argv[0]!='--max' or not argv[1].isdigit() or int(argv[1])<1):
                k._err('usage: travel-retry-run [--max N]')
            run(k,conn,int(argv[1]) if argv else 2)
        elif verb=='travel-retry-notices':
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute('SELECT id,notice FROM travel_leg_retry WHERE notice IS NOT NULL AND notice_read=0').fetchall()
            for rid,notice in rows:
                print('TRAVEL_RETRY_NOTICE\t%s\t%s' % (rid,notice))
                conn.execute('UPDATE travel_leg_retry SET notice_read=1 WHERE id=?',(rid,))
            conn.commit()
        else:
            for row in conn.execute('SELECT id,anchor_ulid,role,origin_id,dest_id,state,attempts,error FROM travel_leg_retry ORDER BY id'):
                print('TRAVEL_RETRY_LEG\t'+'\t'.join('' if v is None else str(v) for v in row))
    finally:
        conn.close()
