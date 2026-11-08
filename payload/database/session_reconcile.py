"""Reviewed, digest-bound legacy import inside the normal start transaction.

The importer consumes an immutable reconciliation export supplied by the trusted
binding. It never reads flow/override files itself or resolves disagreements by
timestamp. A legacy CLOSED source is never reopened.
"""
from session_command import digest, require, rows, update, TARGET_FIELDS

SOURCE_KINDS = {'kit', 'flow', 'program', 'overrides', 'equipment', 'post', 'delivery'}


def preflight(conn, command, state, resolution):
    sid = command['session_id']
    require(command['source_ref']['kind']=='import', 'IMPORT', 'legacy start requires explicit reconciliation')
    require(resolution and resolution['session_id']==sid, 'IMPORT', 'no reviewed import for this session')
    require(resolution['legacy_state']=='open', 'IMPORT_CLOSED', 'closed legacy history cannot be resumed')
    sources = resolution['sources']
    require(set(s['kind'] for s in sources)==SOURCE_KINDS and
            len(sources)==len(SOURCE_KINDS) and all(s['digest'].startswith('sha256:') for s in sources),
            'IMPORT', 'complete hashed source inventory required')
    body = {k:v for k,v in resolution.items() if k!='source_digest'}
    require(digest(body)==resolution['source_digest'], 'IMPORT', 'reconciliation digest mismatch')
    require(resolution['reviewed_by'] and resolution['review_ref'], 'IMPORT', 'recorded operator/trainer review required')
    require(type(resolution['started_at']) is int and resolution['started_at']>=0, 'IMPORT', 'original session start required')
    require(resolution['actual_digest']==digest(state['sets']), 'IMPORT_CONFLICT', 'kit actuals changed since reconciliation')
    require(resolution['workout_digest']==digest(state['workout']), 'IMPORT_CONFLICT', 'kit workout changed since reconciliation')
    for conflict in resolution['conflicts']:
        require(conflict['field'] and len(conflict['provenances'])>=2 and conflict.get('resolution') is not None and
                conflict.get('resolved_by') and conflict.get('review_ref'), 'IMPORT_CONFLICT',
                'unresolved legacy disagreement: '+str(conflict.get('field')))
    actual_ids = [rid for l in resolution['lifts'] for rid in l['actual_row_ids']]
    require(len(actual_ids)==len(set(actual_ids)) and set(actual_ids)=={s['id'] for s in state['sets']},
            'IMPORT_CONFLICT', 'each kit actual needs exactly one reviewed lift mapping')
    for l in resolution['lifts']:
        require(l['status'] in ('open','closed','skipped') and (l['status']=='open' or l['closed_reason']),
                'IMPORT_CONFLICT', 'lift closure requires explicit resolution')
        require(l['status']=='open' or not l['plan']['sets'], 'IMPORT_CONFLICT', 'terminal lift has future prescriptions')
        require(not (l['status']=='skipped' and l['actual_row_ids']), 'IMPORT_CONFLICT', 'performed lift cannot be skipped')
        for s in state['sets']:
            if s['id'] in l['actual_row_ids']:
                require(s['exercise_name']==l['plan']['lift']['exercise_name'], 'IMPORT_CONFLICT', 'actual exercise identity differs')
        for planned in l['plan']['sets']:
            require(TARGET_FIELDS <= set(planned), 'IMPORT', 'future prescription tuple is incomplete')
    return resolution


def link_history(conn, resolution):
    """Attach old rows while still v1; strict new-row v2 validation stays intact."""
    sid=resolution['session_id']; linked=[]
    for item in resolution['lifts']:
        fields=dict(item['plan']['lift'],session_id=sid,status=item['status'],
                    closed_reason=item['closed_reason'],closed_revision=1 if item['status']!='open' else None)
        allowed={'exercise_id','exercise_name','slot_num','order_index','variant_id','machine_ref',
                 'implement_resolution_id','session_id','status','closed_reason','closed_revision'}
        require(set(fields)<=allowed,'IMPORT','unknown lift fields')
        lid=conn.execute('INSERT INTO workout_lifts('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values())).lastrowid
        linked.append(lid)
        for rid in item['actual_row_ids']:
            # Only the new relationship column changes. Original timestamps,
            # NULL actuals, kinds, targets, loads and IDs stay byte-for-byte.
            update(conn, 'workout_sets', rid, {'lift_id':lid})
    return linked


def materialize(writer, conn, command, resolution, linked):
    sid=command['session_id']
    require(resolution['started_at']<=writer.clock(),'IMPORT_CONFLICT','session start is in the future')
    writer.insert_plan(conn,sid,[item['plan'] for item in resolution['lifts']],1,command['command_id'],existing_lift_ids=linked)
    writer.advance(conn,sid)
    if resolution['rest'] is not None:
        rest=resolution['rest']
        require(all(type(rest[k]) is int for k in ('started_at','deadline_at','duration_ms','epoch')) and
                resolution['started_at']<=rest['started_at']<=writer.clock() and
                rest['duration_ms']>=0 and rest['deadline_at']==rest['started_at']+rest['duration_ms'],
                'IMPORT_CONFLICT', 'reviewed rest clock is inconsistent')
        update(conn,'workouts',sid,dict(phase='rest',rest_started_at=rest['started_at'],
               rest_deadline_at=rest['deadline_at'],rest_duration_ms=rest['duration_ms'],rest_epoch=rest['epoch']+1))
    conn.execute('INSERT INTO session_import_audit VALUES(?,?,?,?)',
                 (resolution['source_digest'],sid,__import__('json').dumps(resolution,sort_keys=True),writer.clock()))
