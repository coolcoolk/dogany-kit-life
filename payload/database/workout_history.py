"""History projection shared by kit readers across the negotiated schema floor.

Pre-078 rows are all historical actuals. On 078+, all readers use the canonical
logged-only view, including pending RIR input. No target is promoted to actual.
Deletion censuses intentionally read the base table (planned rows are dependents).
"""
import os
import re
import datetime as dt
import sys
from zoneinfo import ZoneInfo

_SOURCE = re.compile(r'\b(FROM|JOIN)\s+workout_sets\b', re.IGNORECASE)


def history_sql(conn, sql):
    if _SOURCE.search(sql) and conn.execute('PRAGMA user_version').fetchone()[0] >= 78:
        return _SOURCE.sub(lambda m: m[1]+' v2_logged_workout_sets', sql)
    return sql


def execute(conn, sql, parameters=()):
    return conn.execute(history_sql(conn, sql), parameters)


def begin_legacy_write(conn):
    """Serialize guard and mutation, including callers with a deferred txn."""
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    else:
        conn.execute('UPDATE workouts SET id=id WHERE 0')


def reject_legacy_admission(conn, categories):
    # Historical cardio remains independent of the retiring strength engine.
    if conn.execute('PRAGMA user_version').fetchone()[0] >= 83:
        if '\uadfc\ub825' in categories and conn.execute(
                'SELECT 1 FROM session_pack_fences LIMIT 1').fetchone():
            raise ValueError('FENCE_STALE: legacy strength admission is closed')


def reject_legacy_write(conn, workout_id):
    started = not conn.in_transaction
    begin_legacy_write(conn)
    try:
        if conn.execute('PRAGMA user_version').fetchone()[0] >= 78:
            row = conn.execute('SELECT session_version,session_type_id,EXISTS(SELECT 1 FROM workout_sets WHERE session_id=workouts.id) FROM workouts WHERE id=?',
                               (workout_id,)).fetchone()
            if row and row[0] == 2:
                raise ValueError('v2 session writes require service/kit_life/session-command')
            categories = [r[0] for r in conn.execute(
                'SELECT wt.category FROM workout_classifications wc '
                'JOIN workout_types wt ON wt.id=wc.type_id WHERE wc.workout_id=?',
                (workout_id,))]
            if row and (row[1] is not None or row[2]):
                categories.append('\uadfc\ub825')
            reject_legacy_admission(conn, categories)
    except Exception:
        if started:
            conn.rollback()
        raise


SESSION_NEXT = (
    "A workout session is still open. Continue workout-session; for an image, "
    "inspect it and handle machine/stack registration, food or form "
    "in the current session. Do not run photo completion, offer done buttons, "
    "create a done activity block, or reply with a task completion line. "
    "Keep the workout task open until the session is closed.")

PHOTO_TURN_PREFIX = 'workout_photo_turn.'


def route_photo(K, conn, agent):
    """Remember only the bridge turn identity, never the image or its facts.

    A capture in this turn may next supply --record-evidence or --said rather
    than --photo-evidence. Those shortcuts must follow the same session route.
    The next owner turn and a closed session both release this hold.
    """
    turn = K._inbound_request_id()
    if turn:
        K._config_put(conn, PHOTO_TURN_PREFIX + agent, turn)
        conn.commit()


def photo_sessions(K, conn):
    turn = K._inbound_request_id()
    agent = K._pin_agent()
    if turn and K._config_stored(conn, PHOTO_TURN_PREFIX + agent) == turn:
        return open_sessions(K, conn, agent)
    return []


def open_sessions(K, conn, agent):
    """Open rows owned by ONE agent, including sessions crossing midnight.

    V2 owns lifecycle on workouts. Legacy engine state selects a workout row
    through the instance-local active marker; its closeout tombstone wins.
    An ordinary workout log alone is never treated as an open session.
    """
    cols = {r[1] for r in conn.execute('PRAGMA table_info(workouts)')}
    if not cols:
        return []
    result = []
    if {'session_version', 'phase'} <= cols:
        result = [dict(id=sid, engine='v2', phase=phase) for sid, phase in
                  conn.execute("SELECT id,phase FROM workouts WHERE owning_agent=? "
                               "AND session_version=2 AND phase!='closed' ORDER BY id",
                               (agent,))]
    sd = K._workout_state_dir()
    if not sd:
        return result
    try:
        with open(os.path.join(sd, '.active-session'), encoding='utf-8') as fh:
            sid = int(fh.read().strip())
    except (OSError, ValueError):
        return result
    # The closeout tombstone wins over a stale legacy active marker.
    try:
        with open(os.path.join(sd, '.session-closed'), encoding='utf-8') as fh:
            closed = int(fh.read().strip())
        if closed == sid:
            return result
    except (OSError, ValueError):
        pass
    row = conn.execute('SELECT owning_agent%s FROM workouts WHERE id=?' %
                       (',session_version' if 'session_version' in cols else ''),
                       (sid,)).fetchone()
    if row and row[0] == agent and (len(row) == 1 or row[1] == 1):
        result.append(dict(id=sid, engine='legacy', phase='open'))
    return result


def task_sessions(K, conn, block_id):
    """A workout action or the established legacy workout title is guarded.

    Read the task's owner, never the caller's broad task-read scope. Non-workout
    tasks keep their ordinary chat completion while another task is in progress.
    """
    row = conn.execute(
        "SELECT e.title,e.owning_agent,COALESCE((SELECT a.value FROM block_attr a "
        "WHERE a.block_id=e.id AND a.attr_token='action_kind' "
        "ORDER BY (a.source='user') DESC LIMIT 1),'check') "
        "FROM timeblock e WHERE e.id=? AND e.kind='task' "
        "AND e.block_class IS NULL AND e.settled_at IS NULL", (block_id,)).fetchone()
    if row and (row[2] == 'workout' or K._wd_is_session_title(row[0] or '')):
        return open_sessions(K, conn, row[1])
    return []


def reconcile_completed(K, conn, day, session_id=None):
    """Settle only the task explicitly linked by session-ensure, in this tx.

    No title/category matching, task creation, filesystem state, or commit.
    Legacy closeout calls this through close_task.sh. Retro repairs old closes
    whose task handoff did nothing. V2 calls it inside its writer transaction.
    """
    def instant(value):
        if not value or 'T' not in str(value):
            return None
        try:
            stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            return stamp if stamp.tzinfo else None
        except ValueError:
            return None

    def utc(stamp):
        return stamp.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    if not conn.in_transaction:
        raise ValueError('workout completion requires caller transaction')
    completed = []
    workouts = list(conn.execute('SELECT id,owning_agent,closed_at,session_version,phase '
                                 'FROM workouts WHERE date=?' + (' AND id=?' if session_id is not None else ''),
                                 (day, session_id) if session_id is not None else (day,)))
    for sid, owner, closed, version, phase in workouts:
        if not closed or (version == 2 and phase != 'closed'):
            continue
        tasks = list(conn.execute(
            "SELECT id,title,settled_at,start_at,end_at,display_tz FROM timeblock WHERE kind='task' "
            "AND note=? AND owning_agent IS ? AND (settled_at IS NULL OR "
            "(settled_outcome='done' AND settled_by='workout-session')) ORDER BY id",
            ('session-ensure sid=%d' % sid, owner)))
        if not tasks:
            continue
        if len(tasks) != 1:
            print('WORKOUT_TASK_AMBIGUOUS sid=%d' % sid, file=sys.stderr)
            continue
        bid, title, settled, old_start, old_end, zone = tasks[0]
        shadow = conn.execute("SELECT id,start_at,settled_at FROM block WHERE ulid=? "
                              "AND settled_by='workout-record'", ('x-workout-%d' % sid,)).fetchone()
        meta = conn.execute('SELECT started_at FROM session_input_state WHERE session_id=?', (sid,)).fetchone()
        start = (dt.datetime.fromtimestamp(meta[0] / 1000, dt.timezone.utc)
                 if meta else instant(shadow[1] or shadow[2]) if shadow else None)
        end = (dt.datetime.fromtimestamp(closed / 1000, dt.timezone.utc)
               if isinstance(closed, (int, float)) else instant(closed))
        # Legacy re-entry can append actual sets after an earlier durable close.
        # Those performed timestamps outrank the stale close, not minutes or
        # a later edit/retro time. Naive set stamps require a declared zone.
        if zone and end:
            stamps = []
            for value, in execute(conn, 'SELECT recorded_at FROM workout_sets WHERE session_id=?', (sid,)):
                try:
                    stamp = dt.datetime.fromisoformat(value)
                    stamps.append(stamp if stamp.tzinfo else stamp.replace(tzinfo=ZoneInfo(zone)))
                except (TypeError, ValueError):
                    continue
            if stamps and max(stamps) > end:
                end = max(stamps)
                print('WORKOUT_CLOSE_PRECEDES_RECORD sid=%d' % sid, file=sys.stderr)
        if not title or not start or not end or end <= start:
            print('WORKOUT_TIME_MISSING sid=%d' % sid, file=sys.stderr)
            continue
        if not settled or (old_start, old_end, settled) != (utc(start), utc(end), utc(end)):
            K._tb_update(conn, bid, dict(start_at=utc(start), end_at=utc(end),
                schedule_kind='timed', time_mode='anchored', open_ended=0,
                settled_at=utc(end), settled_outcome='done', settled_by='workout-session',
                settled_verdict='machine', updated_at=K.now_utc()), bump_version=True)
            K._store_status(conn, bid, 'done')
        linked = conn.execute('SELECT event_id FROM event_timeblock WHERE timeblock_id=? ORDER BY event_id LIMIT 1', (bid,)).fetchone()
        if not linked and shadow:
            linked = conn.execute("SELECT re.event_id FROM record_event re JOIN record r ON r.id=re.record_id "
                                  "WHERE r.source_table='block' AND r.source_id=? ORDER BY re.event_id LIMIT 1", (shadow[0],)).fetchone()
        # Two explicitly distinct sessions with the same title/day are still
        # distinct occurrences. Skip the general title/day capture dedup.
        if linked:
            eid = linked[0]
        else:
            eid = conn.execute(
                'INSERT INTO event(id,ulid,title,owning_agent,occurred_on,occurred_at) VALUES(?,?,?,?,?,?)',
                (K.next_event_id(conn), K.new_ulid(), title, owner, day, utc(start))).lastrowid
            conn.execute('INSERT INTO experience_capture_key VALUES(?,?)', ('created:' + str(eid), eid))
        K.experience_ensure(conn, title, event_id=eid, block_id=bid, occurred_on=day,
                            occurred_at=utc(start),
                            evidence=[('block', shadow[0])] if shadow else [],
                            _savepoint=False)
        completed.append(bid)
    # Deleted workout rows can leave nullable-title shadows. They are not proof
    # of a second session. Diagnose them without inventing another visible row.
    if session_id is not None:
        return completed
    for bid, uid in conn.execute("SELECT b.id,b.ulid FROM block b WHERE b.date=? "
            "AND b.source_module='training' AND b.settled_by='workout-record' "
            "AND b.ulid GLOB 'x-workout-[0-9]*' AND NOT EXISTS "
            "(SELECT 1 FROM workouts w WHERE b.ulid='x-workout-'||w.id)", (day,)):
        print('WORKOUT_RECORD_ORPHAN block=%s source=%s' % (bid, uid), file=sys.stderr)
    return completed
