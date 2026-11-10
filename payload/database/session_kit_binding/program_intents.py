"""Reversible /2 operations on explicit pinned occurrences.

All mutations run inside Writer's transaction. Evidence and command receipts
are append-only; undo restores projections, never deletes allocated identities.
"""
import copy
import json

import session_program as p
from session_command import TARGET_FIELDS, digest, require, row, rows, snapshot, update

REVERSIBLE = frozenset(('change_load', 'change_reps', 'select_load', 'skip_lift',
    'close_lift', 'next_lift', 'skip_warmup', 'skip_block', 'complete_block',
    'reorder', 'substitute', 'free_lift', 'choose_split', 'choose_post',
    'choose_implement', 'choose_machine', 'photo_result', 'missing_weight',
    'claim_split', 'pain', 'skip_post', 'acknowledge_condition',
    'activity_start', 'acknowledge_activity'))
IDENTITY = frozenset(('substitute', 'free_lift', 'choose_split', 'choose_post',
    'choose_implement', 'choose_machine', 'photo_result', 'claim_split', 'missing_weight'))
WORKOUT_FIELDS = ('session_type_id', 'phase', 'active_lift_id', 'active_set_id', 'input_stage', 'action_kind',
    'rest_started_at', 'rest_deadline_at', 'rest_duration_ms')


def execute(runtime, writer, conn, c, state, policy, now):
    affected = operate(runtime, writer, conn, c, state, policy, now)
    after = snapshot(conn, c['session_id'])
    if c['event_type']=='undo' and after['workout']['phase']!='closed' and any(
            s['id']==c['args']['row_id'] and s['set_kind']=='post' for s in state['sets']):
        update(conn, 'workouts', c['session_id'], dict(phase='post',
            input_stage='activity_wait', action_kind='activity_start'))
        after = snapshot(conn, c['session_id'])
    # The final post dose (or explicit omission of the remaining post work)
    # closes through the existing canonical/legacy close door, in this commit.
    if (after['workout']['phase'] == 'lift_done' and
            any(s['set_kind']=='post' and s['row_status'] in ('planned','void') for s in state['sets']) and
            not any(s['row_status']=='planned' for s in after['sets'])):
        close = dict(c, event_type='stop_session', args=dict(reason=c['args'].get('reason', 'post_complete')))
        affected += writer.native_intent(conn, close, after, policy, now)
    return affected


def activity(runtime, writer, conn, c, state, now):
    event = c['event_type']; a = c['args']; w = state['workout']
    runtime.guard_intent(c, state)
    if event == 'acknowledge_condition':
        require(w['phase']=='entry' and w['input_stage']=='condition', 'STAGE', 'condition is not waiting')
        runtime.record(c, 'program_condition/1', dict(revision=c['expected_revision']+1, **a))
        update(conn, 'workouts', c['session_id'], dict(input_stage=None))
        writer.advance(conn, c['session_id'])
        return []
    current = next((s for s in state['sets'] if s['id']==a['row_id']), None)
    require(current and w['phase']=='post' and w['active_set_id']==current['id'] and
            current['set_kind']=='post' and current['row_status'] in ('planned','void'),
            'MOVED', 'activity must address the current post row')
    if event in ('activity_start', 'acknowledge_activity'):
        require(w['input_stage']=='activity_wait', 'STAGE', 'activity is not waiting for Start')
        require(current['target_unit']=='seconds' and current['target_hold_sec'],
                'UNIT', 'activity timer requires a seconds prescription')
        duration = current['target_hold_sec']*1000
        runtime.record(c, 'program_activity_timer/1', dict(timer_id=c['command_id'],
            revision=c['expected_revision']+1, row_id=current['id'], started_at=now,
            deadline=now+duration, duration_ms=duration, rest_epoch=w['rest_epoch']))
        update(conn, 'workouts', c['session_id'], dict(input_stage='activity_running', action_kind='activity_timer_end'))
        return [current['id']]
    if event == 'activity_timer_end':
        timer = runtime.activity_timer(state)
        require(timer and timer['timer_id']==a['timer_id'] and timer['deadline']==a['deadline'] and
                now>=a['deadline'], 'MOVED', 'activity timer is early, canceled or already completed')
        completed_at = a['deadline']
    else:
        require(w['input_stage'] in ('activity_wait','activity_running'), 'STAGE', 'activity cannot complete here')
        completed_at = a['completed_at']
    return writer.actual(conn, c, state, dict(unit=current['target_unit'],
        count=current['target_hold_sec'] if current['target_unit']=='seconds' else
              current['target_reps'] if current['target_unit']=='reps' else current['target_count'],
        load_g=current['target_load_g']), completed_at, 'typed')


def operate(runtime, writer, conn, c, state, policy, now):
    event = c['event_type']
    if event == 'undo_command':
        return undo(runtime, writer, conn, c, state)
    if event in ('activity_complete', 'activity_timer_end'):
        return activity(runtime, writer, conn, c, state, now)
    if event == 'skip_post' and not any(s['set_kind']=='post' and s['row_status']=='planned' for s in state['sets']):
        return writer.native_intent(conn,c,state,policy,now)
    if event not in REVERSIBLE:
        return writer.native_intent(conn, c, state, policy, now)
    runtime.guard_intent(c, state)
    require(state['workout']['input_stage'] != 'rir', 'STAGE', 'finish pending actual input first')
    before_input = row(conn, 'SELECT * FROM session_input_state WHERE session_id=?', (c['session_id'],))
    before_bindings = runtime.row_bindings(state)
    if event in ('acknowledge_condition', 'activity_start', 'acknowledge_activity'):
        affected = activity(runtime, writer, conn, c, state, now)
    elif event in IDENTITY:
        affected = identity(runtime, writer, conn, c, state, policy, now)
    elif event == 'pain' and c['args']['alternative_exercise_id'] is not None:
        a = c['args']
        require(a.get('alternative_slot_id'), 'PROGRAM', 'pain alternative requires an explicit pinned slot')
        runtime.spec(a['alternative_slot_id'])
        native = copy.deepcopy(c); native['args']['alternative_exercise_id'] = None
        affected = writer.native_intent(conn, native, state, policy, now)
        replacement = dict(c, event_type='substitute', args=dict(lift_id=a['lift_id'],
            exercise_id=a['alternative_exercise_id'],
            variant_id=runtime.spec(a['alternative_slot_id'])['variant_id'],
            slot_id=a['alternative_slot_id'], reason='pain'))
        affected += append_slot(runtime, writer, conn, replacement, state, policy, close=False)
    else:
        affected = writer.native_intent(conn, c, state, policy, now)
    after = snapshot(conn, c['session_id'])
    old_sets = {s['id']: s for s in state['sets']}
    old_lifts = {l['id']: l for l in state['lifts']}
    changed_sets = [s for s in after['sets'] if old_sets.get(s['id']) != s]
    changed_lifts = [l for l in after['lifts'] if old_lifts.get(l['id']) != l]
    # Native skip-warmup changes a target without invoking a calculator. Keep
    # the program projection bound to that accepted target as well.
    bound = {b['set_id']: copy.deepcopy(b) for b in runtime.row_bindings(after)}
    for s in changed_sets:
        b = bound[s['id']]['row']
        if s['row_status'] != 'logged':
            b.update(target_reps=s['target_reps'], load_g=s['target_load_g'],
                target_count=s['target_count'], target_hold_sec=s['target_hold_sec'])
            if s['target_load_g'] is not None:
                b['load_state'] = 'known'
            previous = old_sets.get(s['id'],s)
            local = event in ('change_load','change_reps','select_load') and (s['set_kind']=='ramp' or b['block_id'] is not None)
            if (local or event in ('skip_warmup','missing_weight')) and any(previous[k]!=s[k] for k in TARGET_FIELDS):
                b['basis'] = dict(decision='manual_target', reason=c['args'].get('reason',event), evidence_digest=digest(c))
            p.check_row(b, '$.operation.rows')
    value = dict(schema='program_operation/1', revision=c['expected_revision']+1,
        event=event, before=dict(workout={k:state['workout'][k] for k in WORKOUT_FIELDS},
            sets=[old_sets[s['id']] for s in changed_sets if s['id'] in old_sets],
            lifts=[old_lifts[l['id']] for l in changed_lifts if l['id'] in old_lifts],
            input=before_input, bindings=before_bindings),
        added_sets=[s['id'] for s in changed_sets if s['id'] not in old_sets],
        added_lifts=[l['id'] for l in changed_lifts if l['id'] not in old_lifts],
        rows=[bound[s['id']] for s in changed_sets])
    runtime.record(c, 'program_operation/1', value)
    return sorted(set(affected) | {s['id'] for s in changed_sets})


def undo(runtime, writer, conn, c, state):
    target = row(conn, 'SELECT * FROM session_commands WHERE session_id=? AND command_id=?',
                 (c['session_id'], c['args']['command_id']))
    require(target and target['committed_revision'] == c['expected_revision'],
            'MOVED', 'only the latest command can be undone')
    evidence = next((json.loads(e['value']) for e in state['evidence']
                     if e['event_id'] == target['command_id'] and e['kind'] == 'program_operation/1'), None)
    require(evidence, 'COMMAND', 'command is not a reversible program operation')
    before = evidence['before']; sid = c['session_id']
    update(conn, 'workouts', sid, dict(active_set_id=None, active_lift_id=None))
    # Move all lift orders out of the way before restoring their unique indices.
    lifts = {l['id']: l for l in state['lifts']}
    base = max((l['order_index'] for l in state['lifts']), default=0)+1
    for n, l in enumerate(before['lifts']):
        update(conn, 'workout_lifts', l['id'], dict(order_index=base+n))
    for n, lid in enumerate(evidence['added_lifts']):
        update(conn, 'workout_lifts', lid, dict(status='skipped', closed_reason='undo_command',
            closed_revision=c['expected_revision']+1, order_index=base+len(lifts)+n))
    for l in before['lifts']:
        update(conn, 'workout_lifts', l['id'], {k:v for k,v in l.items() if k != 'id'})
    for s in before['sets']:
        update(conn, 'workout_sets', s['id'], {k:v for k,v in s.items() if k != 'id'})
    for rid in evidence['added_sets']:
        update(conn, 'workout_sets', rid, dict(row_status='omitted'))
    meta = before['input']
    conn.execute('UPDATE session_input_state SET split_id=?,claimed_split_id=?,pending_completed_at=? WHERE session_id=?',
                 (meta['split_id'], meta['claimed_split_id'], meta['pending_completed_at'], sid))
    writer.cancel_rest(conn, sid)  # epochs never rewind; old timer jobs stay fenced.
    restored = dict(before['workout'])
    if restored['input_stage'] == 'activity_running':
        # Undo never resurrects a dispatched timer. The restored row needs its
        # own new Start, with a new timer identity and deadline.
        restored.update(input_stage='activity_wait', action_kind='activity_start')
    update(conn, 'workouts', sid, restored)
    runtime.record(c, 'program_undo/1', dict(schema='program_undo/1',
        command_id=target['command_id'], revision=c['expected_revision']+1, split_id=meta['split_id'],
        rows=before['bindings']))
    return [s['id'] for s in before['sets']]+evidence['added_sets']


def local_targets(runtime, state, c):
    """A ramp or one block drill is edited directly, without work progression."""
    active = next(s for s in state['sets'] if s['id'] == c['args'].get('row_id',state['workout']['active_set_id']))
    binding = next(b['row'] for b in runtime.row_bindings(state) if b['set_id'] == active['id'])
    require(c['args'].get('scope', 'current_only') == 'current_only', 'SCOPE',
            'ramp and block edits address one issued row')
    lift = next(l for l in state['lifts'] if l['id']==active['lift_id'])
    fact = runtime.equipment_fact(state,lift)
    r = copy.deepcopy(binding); args = c['args']
    if 'load_g' in args:
        require(r['load_state'] != 'unloaded' and p.load_allowed(fact, args['load_g']),
                'EQUIPMENT', 'selected load is outside the pinned drill grid')
        r.update(load_g=args['load_g'], load_state='known')
    if 'target_reps' in args:
        require(r['unit'] == 'reps', 'UNIT', 'change_reps requires reps')
        r['target_reps'] = args['target_reps']
    r['basis'] = dict(decision='manual_target', reason=args.get('reason', 'selected load'),
                      evidence_digest=digest(c))
    p.check_row(r, '$.manual_target')
    from session_kit_binding.program_runtime import target_tuple
    return {active['id']:target_tuple(r, active['equipment_snapshot_id'])}


def order_guard(runtime, state, ordered):
    """Explicit reordering may move slots while retaining their block anchors."""
    plan = runtime.effective_plan(state)
    positions = {lid:n for n,lid in enumerate(ordered)}
    slots = {s['slot_id']:s['lift_id'] for s in plan['slots'] if s['lift_id'] in positions}
    for b in plan['blocks']:
        definition = b['definition']
        lids = {s['lift_id'] for s in state['sets'] if s['id'] in b['set_ids'] and s['lift_id'] in positions}
        if not lids:
            continue
        bp = [positions[lid] for lid in lids]
        anchor = definition['anchor']; slot = slots.get(definition['anchor_slot_id'])
        if anchor == 'session_start':
            require(all(max(bp) < positions[lid] for lid in slots.values()), 'STAGE', 'entry block must precede slots')
        elif anchor == 'after_lifts':
            require(all(min(bp) > positions[lid] for lid in slots.values()), 'STAGE', 'post block must follow slots')
        elif slot is not None:
            require((max(bp) < positions[slot]) if anchor == 'before_slot' else (min(bp) > positions[slot]),
                    'STAGE', 'slot jump would bypass its ordered block')
        original = [l['id'] for l in state['lifts'] if l['id'] in lids]
        require(sorted(original, key=positions.get) == original, 'STAGE', 'block drill order is pinned')


def append_plan(runtime, writer, conn, c, policy, roster, index):
    sid = c['session_id']
    plan = runtime.plan(conn, c, policy, roster=roster)
    # plan() creates fresh immutable equipment snapshots for every allocation.
    existing = rows(conn, 'SELECT * FROM workout_lifts WHERE session_id=? ORDER BY order_index DESC', (sid,))
    for lift in existing:
        if lift['order_index'] >= index:
            update(conn, 'workout_lifts', lift['id'], dict(order_index=lift['order_index']+len(plan)))
    for n, item in enumerate(plan):
        item['lift']['order_index'] = index+n
    writer.insert_plan(conn, sid, plan, c['expected_revision']+1, c['command_id'])
    writer.cancel_rest(conn, sid); writer.advance(conn, sid)
    return [s['id'] for s in snapshot(conn,sid)['sets'] if s['basis_event_id'] == c['command_id']]


def append_slot(runtime, writer, conn, c, state, policy, close=True):
    a = c['args']; event = c['event_type']; sid = c['session_id']
    require(a.get('slot_id'), 'PROGRAM', 'an explicit replacement slot_id is required')
    spec = runtime.spec(a['slot_id']); cat = runtime.catalog(spec['variant_id'])
    require(cat['exercise_id'] == a.get('exercise_id', cat['exercise_id']) and
            spec['variant_id'] == a.get('variant_id', spec['variant_id']),
            'PROGRAM', 'replacement slot identity differs from the selected movement')
    lift = next((l for l in state['lifts'] if l['id'] == a.get('lift_id')), None)
    if event != 'free_lift':
        require(lift and lift['id'] == state['workout']['active_lift_id'] and lift['status'] == 'open',
                'LIFT', 'replacement requires the active slot')
        runtime.slot_for(state, lift)
        require(not any(pin['slot_id']==a['slot_id'] and pin['lift_id']!=lift['id'] and
                any(other['id']==pin['lift_id'] and other['status']=='open' for other in state['lifts'])
                for pin in runtime.effective_plan(state)['slots']),
                'PROGRAM', 'replacement slot is already open')
        if event != 'substitute':
            require(cat['exercise_id'] == lift['exercise_id'], 'PROGRAM', 'equipment change must retain movement identity')
        index = lift['order_index']
        # Retire the old station's unperformed anchored drills together. Their
        # actuals and IDs remain; the replacement carries its own pinned drills.
        old_slot = runtime.slot_for(state,lift)[0]
        for b in runtime.effective_plan(state)['blocks']:
            if b['definition']['anchor_slot_id']==old_slot:
                for old in state['lifts']:
                    if old['status']=='open' and any(s['lift_id']==old['id'] and s['id'] in b['set_ids'] and s['row_status']=='planned' for s in state['sets']):
                        writer.close_lift(conn,sid,old['id'],'slot_replaced',c['expected_revision']+1)
        if close:
            writer.close_lift(conn,sid,lift['id'],a.get('reason',event),c['expected_revision']+1)
    else:
        require(not any(pin['slot_id']==a['slot_id'] and any(l['id']==pin['lift_id'] and l['status']=='open' for l in state['lifts']) for pin in runtime.effective_plan(state)['slots']),
                'PROGRAM','slot is already open')
        post_ids={rid for b in runtime.effective_plan(state)['blocks'] if b['definition']['anchor']=='after_lifts' for rid in b['set_ids']}
        post_orders=[l['order_index'] for l in state['lifts'] if l['status']=='open' and any(s['lift_id']==l['id'] and s['id'] in post_ids for s in state['sets'])]
        index = min(post_orders) if post_orders else max((l['order_index'] for l in state['lifts']),default=-1)+1
    source = runtime.config['splits'][spec['source_identity']['split_id']]
    roster = dict(slot_ids=[a['slot_id']], blocks=[b for b in source['blocks'] if b['anchor_slot_id']==a['slot_id']])
    return append_plan(runtime,writer,conn,c,policy,roster,index)


def identity(runtime, writer, conn, c, state, policy, now):
    a=c['args']; event=c['event_type']; sid=c['session_id']; plan=runtime.effective_plan(state)
    if event == 'choose_split':
        require(a['split_id'] in runtime.config['splits'], 'SPLIT', 'unknown pinned split')
        require(not any(s['row_status']=='logged' for s in state['sets']), 'STAGE', 'split cannot change after recording')
        for lift in state['lifts']:
            if lift['status']=='open':
                writer.close_lift(conn,sid,lift['id'],'choose_split',c['expected_revision']+1)
        conn.execute('UPDATE session_input_state SET split_id=? WHERE session_id=?',(a['split_id'],sid))
        writer.split(conn,sid,a['split_id'])
        return append_plan(runtime,writer,conn,c,policy,runtime.config['splits'][a['split_id']],0)
    if event == 'claim_split':
        meta=row(conn,'SELECT * FROM session_input_state WHERE session_id=?',(sid,))
        require(meta['mode']=='free' and meta['claimed_split_id'] is None,'CLAIM','split claim requires unclaimed free session')
        roster=runtime.config['splits'].get(a['split_id'])
        require(roster,'SPLIT','unknown pinned split')
        covered={pin['slot_id'] for pin in plan['slots'] if any(s['lift_id']==pin['lift_id'] and s['row_status']=='logged' for s in state['sets'])}
        require(set(roster['slot_ids']) <= covered,'CLAIM','logged occurrences do not cover the claimed split')
        writer.split(conn,sid,a['split_id'])
        conn.execute('UPDATE session_input_state SET claimed_split_id=? WHERE session_id=?',(a['split_id'],sid))
        return []
    if event == 'choose_post':
        require(state['workout']['phase'] in ('lift_done','post') and
                not any(s['row_status']=='planned' for s in state['sets']), 'STAGE', 'finish the current plan first')
        kinds={'cardio':'cardio','interval':'circuit','stretch':'cooldown'}
        choices=[b for b in runtime.config['free_blocks'] if b['kind']==kinds[a['kind']] and
                 b['anchor']=='after_lifts' and (not a.get('block_id') or b['block_id']==a['block_id'])]
        require(len(choices)==1,'PROGRAM','post selection needs one explicit pinned block')
        block=choices[0]
        require(all(r['set_kind']=='post' for stage in block['stages'] for r in stage['rows']), 'PROGRAM','selected block is not post work')
        require(sum((stage['duration_ms'] or 0)*(stage['rounds'] or 1) for stage in block['stages'])==a['duration_ms'],
                'PROGRAM','post duration differs from pinned block')
        return append_plan(runtime,writer,conn,c,policy,dict(slot_ids=[],blocks=[block]),
                           max((l['order_index'] for l in state['lifts']),default=-1)+1)
    if event == 'missing_weight':
        lift=next((l for l in state['lifts'] if l['id']==a['lift_id']),None)
        require(lift and lift['id']==state['workout']['active_lift_id'],'LIFT','grid observation requires active slot')
        _, spec=runtime.slot_for(state,lift)
        base=runtime.config['equipment'][spec['equipment_ref']]
        require(a['unavailable_load_g'] not in a['available_loads_g'] and
                len(a['available_loads_g'])==len(set(a['available_loads_g'])) and
                set(a['available_loads_g']) <= set(base['available_loads_g']), 'EQUIPMENT','grid must narrow pinned equipment')
        runtime.record(c,'program_grid/1',dict(lift_id=lift['id'],available_loads_g=a['available_loads_g'],
            evidence_id=a['evidence_id'],revision=c['expected_revision']+1))
        # Pick only a measured feasible rung, with the full target tuple retained.
        affected=[]
        for s in state['sets']:
            if s['lift_id']==lift['id'] and s['row_status']=='planned' and s['target_load_g'] is not None:
                grid=[g for g in a['available_loads_g'] if g<=s['target_load_g']]
                require(grid,'EQUIPMENT','no feasible load for remaining dose')
                update(conn,'workout_sets',s['id'],dict(target_load_g=max(grid),
                    prescribed_revision=c['expected_revision']+1,basis_event_id=c['command_id']))
                affected.append(s['id'])
        return affected
    if event in ('choose_implement','choose_machine','photo_result'):
        require(a.get('slot_id'), 'PROGRAM', 'equipment resolution requires an explicit pinned slot')
        spec=runtime.spec(a['slot_id']); fact=runtime.config['equipment'][spec['equipment_ref']]
        cat=runtime.catalog(spec['variant_id'])
        if event=='photo_result' and a['machine_ref'] is None:
            lift=next((l for l in state['lifts'] if l['id']==a['lift_id']),None)
            require(lift and lift['id']==state['workout']['active_lift_id'], 'LIFT','photo requires the active slot')
            require(runtime.slot_for(state,lift)[0]==a['slot_id'], 'PROGRAM','photo belongs to another slot')
            require(a['observed_at']<=now,'CLOCK','future photo evidence')
            runtime.record(c,'program_photo/1',dict(evidence_id=a['evidence_id'],lift_id=a['lift_id'],
                slot_id=a['slot_id'],machine_ref=None,observed_at=a['observed_at']))
            update(conn,'workouts',sid,dict(phase='entry',input_stage='setup',action_kind='choose_machine'))
            return []
        if event=='choose_implement':
            require(all(a[k]==cat[k] for k in ('variant_id','implement_count','load_basis')),'EQUIPMENT','implement semantics differ from pinned slot')
        else:
            require(a['machine_ref']==fact['machine_ref'] and fact['machine_ref'] is not None,
                    'MACHINE','machine observation must resolve to pinned equipment')
            require(a['machine_ref'] in runtime.config['machines'] and
                    set(cat['compatible_machine_capabilities']) <= set(runtime.config['machines'][a['machine_ref']]['capabilities']),
                    'MACHINE','machine lacks pinned capabilities')
            if event=='photo_result':
                require(a['observed_at']<=now,'CLOCK','future photo evidence')
                runtime.record(c,'program_photo/1',dict(evidence_id=a['evidence_id'],lift_id=a['lift_id'],
                    slot_id=a['slot_id'],machine_ref=a['machine_ref'],observed_at=a['observed_at']))
    return append_slot(runtime,writer,conn,c,state,policy)
