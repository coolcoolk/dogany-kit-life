"""LOG projection of one committed snapshot; no prescription recomputation.

The pack's frozen builder supplies the dependency/slot compiler. Catalog facts,
grids and plate arithmetic come from the pinned binding, never legacy flow JSON.
"""
import json
import copy

from session_command import digest, grams, load_policy, require, row, snapshot


def receipt_for(state, command, *, delivery_state='waiting_trainer'):
    w = state['workout']
    return dict(schema=2, session_id=w['id'], command_id=command['command_id'],
                receipt_id=f"{w['id']}:{w['revision']}:{command['command_id']}",
                revision=w['revision'], view_digest=digest(state), affected_row_ids=[],
                outbox_id=None, delivery_state=delivery_state, commit_state='committed')


def project(conn, runtime, state, receipt):
    w = state['workout']
    require(receipt['revision'] == w['revision'] and receipt['view_digest'] == digest(state),
            'MOVED', 'projection must use the receipt snapshot')
    nxt = next((s for s in state['sets'] if s['id'] == w['active_set_id']), None)
    lift = next((l for l in state['lifts'] if nxt and l['id'] == nxt['lift_id']), None)
    catalog = runtime.catalog(lift['variant_id']) if lift else None
    equipment = next((e for e in state['equipment'] if nxt and e['id'] == nxt['equipment_snapshot_id']), None)
    require(not nxt or catalog and equipment, 'EQUIPMENT', 'next row lacks catalog/equipment resolution')
    performed = [s for s in state['sets'] if s['row_status'] == 'logged' and
                 (not lift or s['lift_id'] == lift['id'])]
    efforts = [s for s in performed if s['recorded_revision'] is not None]
    last = max(efforts, key=lambda s: (s['recorded_revision'], s['id'])) if efforts else None
    rx = None
    if nxt:
        rx = {k: nxt[k] for k in ('target_unit', 'target_reps', 'target_count', 'target_hold_sec',
              'target_rir_min', 'target_rir_max', 'target_rest_ms', 'prescribed_revision',
              'basis_event_id', 'policy_bundle_id', 'equipment_snapshot_id')}
        rx.update(row_id=nxt['id'], lift_id=nxt['lift_id'], lift_label=catalog['display_name'],
                  variant_id=lift['variant_id'], set_kind=nxt['set_kind'], load_g=nxt['target_load_g'],
                  set_ordinal=1 + sum(s['step_order'] < nxt['step_order'] and
                      s['set_kind'] == nxt['set_kind'] and s['row_status'] not in ('void', 'omitted')
                      for s in state['sets'] if s['lift_id'] == nxt['lift_id']))
    elapsed = 0
    if w['rest_started_at'] is not None:
        end = None
        if w['phase'] != 'rest':
            end = row(conn, "SELECT accepted_at FROM session_commands WHERE session_id=? "
                      "AND event_type='rest_end' AND accepted_at>=? ORDER BY committed_revision DESC LIMIT 1",
                      (w['id'], w['rest_started_at']))
        elapsed = max(0, (end['accepted_at'] if end else runtime.clock()) - w['rest_started_at'])
    evidence = [dict(e, value=json.loads(e['value'])) for e in state['evidence']]
    bundle = load_policy(conn, w['policy_bundle_id'])
    plates = runtime.plate_facts(state, nxt, equipment) if nxt else {}
    deps = dict(next_row_id=nxt['id'] if nxt else None, prescription_digest=digest(rx),
                actual_effort_digest=digest(last), variant_id=lift['variant_id'] if lift else None,
                policy_bundle_id=w['policy_bundle_id'], catalog_version=w['catalog_version'],
                cue_index=len(performed), session_epoch=w['session_epoch'], rest_epoch=w['rest_epoch'])
    value = dict(schema=2, session_id=w['id'], base_revision=w['revision'],
        receipt_id=receipt['receipt_id'], view_digest=receipt['view_digest'],
        dependency_digest=digest(deps), dependencies=deps, phase=w['phase'],
        row_ids=[s['id'] for s in state['sets']], next=rx,
        last_effort=(dict(row_id=last['id'], load_g=grams(last['weight_kg']), actual_reps=last['reps'],
                         rir=last['rir'], rir_is_lower_bound=bool(last['rir_is_lower_bound']),
                         recorded_revision=last['recorded_revision']) if last else None),
        available_loads_g=runtime.grid(state, lift) if lift else [],
        plate_total_per_side_g=plates.get('plate_total_per_side_g'),
        plate_delta_per_side_g=plates.get('plate_delta_per_side_g'), rest={'elapsed_ms': elapsed},
        rest_deadline_at=w['rest_deadline_at'],
        lifts=[{k: l[k] for k in ('id', 'order_index', 'status')} for l in state['lifts']],
        equipment=equipment, catalog=catalog, cue_candidates=[], cue_cadence='full',
        policy_bundle_id=w['policy_bundle_id'],
        policy_provenance=runtime.policy_provenance(conn, bundle['id']), evidence=evidence, slots={})
    if hasattr(runtime, 'project_program'):
        program = runtime.project_program(state)
        result = runtime.trainer.build_prepared_program(receipt, copy.deepcopy(value), runtime.templates, copy.deepcopy(program))
        from session_v2_contract.trainer import PreparedNext
        core = {k:v for k,v in result.items() if k != 'program'}
        PreparedNext(core)
        require(result.get('program') == program, 'PROGRAM', 'projection changed pinned program structure')
        require(all(core[k] == v for k,v in value.items() if k not in ('dependencies','dependency_digest','slots','cue_candidates','cue_cadence')),
                'PROGRAM', 'projection changed committed session facts')
        require(core['dependency_digest']==digest(core['dependencies']), 'PROGRAM', 'derived dependency digest differs')
        return result
    return runtime.trainer.build_prepared(receipt, value, runtime.templates)


def prepare_next(conn, runtime, session_id, revision):
    """Read receipt and domain rows in one read transaction, never mixed revisions."""
    require(not conn.in_transaction, 'TRANSACTION', 'prepare_next owns its read snapshot')
    conn.execute('BEGIN')
    try:
        state = snapshot(conn, session_id)
        require(state['workout'] and state['workout']['revision'] == revision,
                'MOVED', 'requested revision is no longer current')
        result = row(conn, 'SELECT receipt_json FROM session_commands WHERE session_id=? AND committed_revision=?',
                     (session_id, revision))
        require(result, 'RECEIPT', 'snapshot has no command receipt')
        return project(conn, runtime, state, json.loads(result['receipt_json']))
    finally:
        conn.rollback()
