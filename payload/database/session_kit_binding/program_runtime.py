"""Slot-pinned /2 execution, using installed data-only calculator hooks.

No v1 algorithm is inferred here. Pack code must implement the declared hook
and program projection; facts, approved policy sources and native features are
independently checked. Incomplete facts refuse before any persistent install.
"""
import copy
import hashlib
import importlib.util
import json
import sqlite3
import time
import sys
from contextlib import closing
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import session_program as p
from session_command import (Writer, TARGET_FIELDS, canonical, digest, grams,
    load_policy, require, row, rows, snapshot, update)
from session_policy import plates
from session_v2_contract.policy import EquipmentSnapshot
from session_kit_binding import KitRuntime, load_pack


def preflight(config):
    p.guard_configuration(config)
    p.need('installed_root' in config and 'pack_v2_path' in config, '$',
           'installed execution unavailable: pinned pack paths required')
    trainer = load_pack(config['pack_v2_path'])
    p.need(set(config['required_features']) <= set(getattr(trainer, 'PROGRAM_FEATURES', ())) and
           getattr(trainer, 'PROGRAM_COMMAND_VERSION', None) == 1 and
           getattr(trainer, 'PROGRAM_PROJECTION_VERSION', None) == 1 and
           callable(getattr(trainer, 'build_prepared_program', None)), '$',
           'installed execution unavailable: pack must implement /2 features and projection')
    now = time.time_ns() // 1_000_000
    for ref, eq in config['equipment'].items():
        v = eq['verification']
        if p.inherited(eq):
            continue
        p.need(v['confirmed_at'] <= now and (v['valid_until'] is None or now <= v['valid_until']),
               ref, 'physical observation is future or expired')
    root = Path(config['installed_root']).resolve()
    pinned = {a['path']: a['digest'] for a in config['files']}
    pack_files = set(config['artifact_files']['pack'])
    hooks = {}
    for ref, calc in config['calculators'].items():
        path = root / calc['artifact_path']
        p.need(path.parent.resolve() == Path(config['pack_v2_path']).resolve() and path.suffix == '.py' and
               path.resolve().is_relative_to(root) and str(path) in pack_files and
               pinned.get(str(path)) == calc['code_digest'], ref, 'calculator artifact is not pinned')
        actual = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
        p.need(actual == calc['code_digest'], ref, 'calculator artifact digest changed')
        name = trainer.__package__ + '.' + path.stem
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        p.need(callable(getattr(module, 'prescribe', None)) and
               getattr(module, 'CALCULATOR_VERSION', None) == calc['version'] and
               getattr(module, 'RULE_DIGEST', None) == calc['rule_digest'], ref,
               'installed calculator version/rule differs from declaration')
        hooks[ref] = module.prescribe
    return trainer, hooks


def installation_preflight(config):
    """Read policy/evidence on the installed DB before writing private config."""
    preflight(config)
    with closing(sqlite3.connect(Path(config['db_path']).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        require(conn.execute('PRAGMA user_version').fetchone()[0] >= 84,
                'CAPABILITY', 'program execution requires schema 84')
        from kit_life import _health_agent_resolve_full
        owner = _health_agent_resolve_full(conn)[0]
        require(all(eq['verification']['owning_agent'] == owner for eq in config['equipment'].values()),
                'EQUIPMENT', 'equipment observation belongs to another owning agent')
        for ref, calc in config['calculators'].items():
            bundle = load_policy(conn, calc['policy_ref'])
            require(all(r['approval_status'] == 'approved' for r in bundle['rules']) and
                    any(r['scope'] == 'owner' and r['source_ref'] == 'calculator:' + ref + ':' + calc['rule_digest']
                        for r in bundle['rules']), 'POLICY', 'calculator rule lacks explicit owner approval: ' + ref)
        validate_legacy_coverage(conn, config, owner)


def validate_legacy_coverage(conn, config, owner):
    p.validate_history_bindings(conn, {k: config[k] for k in p.FACT_KEYS})
    covered = {sid for b in config['history_bindings'].values() if b['owning_agent'] == owner
               for sid in b['legacy_session_ids']}
    missing = {r[0] for r in conn.execute(
        'SELECT id FROM workouts WHERE session_version=1 AND member_id IS NULL AND owning_agent=?',
        (owner,))} - covered
    require(not missing, 'HISTORY', 'unattributed legacy workouts: ' + ','.join(map(str, sorted(missing))))
    for b in config['history_bindings'].values():
        null_ids = {r[0] for r in conn.execute(
            'SELECT id FROM workouts WHERE session_version=1 AND member_id IS NULL AND owning_agent=?',
            (b['owning_agent'],))} & set(b['legacy_session_ids'])
        require(not null_ids or 'instance_roster' in b, 'HISTORY',
                'null legacy attribution requires captured instance roster provenance')
        if 'instance_roster' in b:
            from session_instance_members import read_instance_roster
            require(read_instance_roster(config['installed_root']) == b['instance_roster'],
                    'HISTORY', 'instance member roster changed since attribution')


def target_tuple(r, equipment_id):
    return dict(target_reps=r['target_reps'], target_load_g=r['load_g'],
        target_rir_min=r['rir_band'][0] if r['rir_band'] else None,
        target_rir_max=r['rir_band'][1] if r['rir_band'] else None,
        target_rest_ms=r['target_rest_ms'], target_unit=r['unit'],
        target_count=r['target_count'], target_hold_sec=r['target_hold_sec'],
        equipment_snapshot_id=equipment_id)


class TargetChanges(dict):
    """Target updates plus native IDs changed by the typed topology event."""
    def __init__(self, values, affected):
        super().__init__(values)
        self.topology_affected=tuple(affected)


class SlotRuntime(KitRuntime):
    rest_all_doses = True
    recalculate_after_actual = True
    nullable_reps = True

    def __init__(self, config, principal, trainer, *, clock=None):
        # Preload and verify executable bytes before Writer begins a transaction.
        installed, self.hooks = preflight(config)
        super().__init__(config, principal, installed, clock=clock)
        self.pending = None
        self.last_exchange = {}

    def spec(self, slot_id):
        require(slot_id in self.config['program_slots'], 'PROGRAM', 'an explicit slot identity is required')
        return self.config['program_slots'][slot_id]

    def target(self, *args, **kwargs):
        self.missing('command-bound slot prescription hook request')

    def make_plan(self, *args, **kwargs):
        self.missing('slot topology changes require a newly pinned plan')

    def resolve(self, *args, **kwargs):
        self.missing('slot-aware identity/equipment reconciliation requires a pinned program command')

    def program_intent(self, writer, conn, command, state, policy, now):
        from .program_intents import execute
        return execute(self, writer, conn, command, state, policy, now)

    def capability(self, conn, command):
        require(conn.execute('PRAGMA user_version').fetchone()[0] >= 84,
                'CAPABILITY', 'program execution requires schema 84')
        super().capability(conn, command)

    def admission(self, conn, command, prior=None):
        require(conn.execute('PRAGMA user_version').fetchone()[0] >= 84,
                'CAPABILITY', 'program execution requires schema 84')
        return super().admission(conn, command, prior)

    def pinned_plan(self, state):
        records = [json.loads(e['value']) for e in state['evidence'] if e['kind'] == 'program_plan/1']
        require(len(records) == 1, 'PROGRAM', 'session must have one pinned program plan')
        plan = records[0]
        require(plan['configuration_digest'] == self.config['config_digest'] and
                plan['program_digest'] == digest(self.facts()), 'PROGRAM', 'pinned program changed')
        return plan

    def effective_plan(self, state):
        plan = copy.deepcopy(self.pinned_plan(state))
        definitions = {b['block_id']: b for b in self.blocks(plan['split_id'])}
        for b in plan['blocks']:
            b['definition'] = definitions[b['block_id']]
        for e in state['evidence']:
            if e['kind'] == 'program_extension/1':
                extension = json.loads(e['value'])
                plan['slots'].extend(extension['slots'])
                plan['rows'].extend(extension['rows'])
                for b in extension['blocks']:
                    existing = next((old for old in plan['blocks'] if old['block_id'] == b['block_id']), None)
                    if existing:
                        existing['set_ids'].extend(b['set_ids'])
                    else:
                        plan['blocks'].append(b)
        contexts = [json.loads(e['value']) for e in state['evidence']
                    if e['kind'] in ('program_extension/1','program_undo/1')]
        for context in sorted(contexts, key=lambda e:e['revision']):
            plan['split_id'] = context['split_id']
        for e in sorted((json.loads(e['value']) for e in state['evidence']
                         if e['kind'] == 'prescription_basis/2'), key=lambda e:e.get('revision', 0)):
            t = e.get('topology')
            if t:
                pin = next(s for s in plan['slots'] if s['lift_id'] == t['lift_id'])
                pin['set_ids'].extend(t['added_set_ids'])
        return plan

    def facts(self):
        return {k: self.config[k] for k in p.FACT_KEYS}

    def slot_for(self, state, lift):
        pin = next((s for s in self.effective_plan(state)['slots'] if s['lift_id'] == lift['id']), None)
        require(pin, 'PROGRAM', 'lift is not a program slot')
        return pin['slot_id'], self.config['program_slots'][pin['slot_id']]

    def equipment_fact(self, state, lift):
        plan = self.effective_plan(state)
        pin = next((s for s in plan['slots'] if s['lift_id'] == lift['id']), None)
        if pin:
            ref = pin['equipment_ref']
        else:
            b = next((b for b in plan['blocks'] if any(s['lift_id'] == lift['id'] and
                      s['id'] in b['set_ids'] for s in state['sets'])), None)
            require(b, 'PROGRAM', 'unbound block lift')
            ids = {s['id'] for s in state['sets'] if s['lift_id'] == lift['id']}
            refs = {item['row']['equipment_ref'] for item in self.row_bindings(state)
                    if item['set_id'] in ids}
            require(len(refs) == 1, 'PROGRAM', 'block equipment binding is ambiguous')
            ref = refs.pop()
        fact = copy.deepcopy(self.config['equipment'][ref])
        undone = {json.loads(e['value'])['command_id'] for e in state['evidence'] if e['kind']=='program_undo/1'}
        for e in sorted(state['evidence'], key=lambda e:json.loads(e['value']).get('revision',0) if e['kind']=='program_grid/1' else 0):
            if e['kind']=='program_grid/1' and e['event_id'] not in undone:
                grid = json.loads(e['value'])
                if grid['lift_id']==lift['id']:
                    fact['available_loads_g'] = grid['available_loads_g']
        return fact

    def grid(self, state, lift):
        return list(self.equipment_fact(state, lift)['available_loads_g'])

    def plate_facts(self, state, nxt, eq):
        if self.catalog(eq['variant_id'])['implement_kind'] != 'barbell' or nxt['target_load_g'] is None:
            return {}
        lift = next(l for l in state['lifts'] if l['id'] == nxt['lift_id'])
        fact = self.equipment_fact(state, lift)
        if p.inherited(fact):
            return {}  # Counted-plate cues require measured inventory.
        physical = fact['verification']['implement_ref'] or fact['machine_ref']
        bindings = {b['set_id']: b['row'] for b in self.row_bindings(state)}
        prior = []
        for s in state['sets']:
            if s['row_status'] != 'logged' or s['weight_kg'] is None:
                continue
            used = self.config['equipment'][bindings[s['id']]['equipment_ref']]
            used_physical = used['verification']['implement_ref'] or used['machine_ref']
            if physical is not None and used_physical == physical:
                prior.append(s)
        mounted = grams(max(prior, key=lambda s: (s['recorded_revision'] or 0, s['id']))['weight_kg']) if prior else eq['mounted_load_g']
        require(mounted is not None, 'EQUIPMENT', 'mounted bar load unknown')
        return plates(from_load_g=mounted, to_load_g=nxt['target_load_g'], bar_g=fact['bar_g'],
                      inventory_per_side={int(k): v for k, v in fact['inventory_per_side'].items()})

    def equipment_snapshot(self, sid, ref, suffix):
        fact = self.config['equipment'][ref]
        c = self.catalog(fact['variant_id'])
        verification = fact['verification']
        from kit_life import _health_agent_resolve_full
        require(verification['owning_agent'] == _health_agent_resolve_full(self.conn)[0],
                'EQUIPMENT', 'equipment observation belongs to another owning agent')
        require(p.inherited(fact) or (verification['confirmed_at'] <= self.clock() and
                (verification['valid_until'] is None or self.clock() <= verification['valid_until'])),
                'EQUIPMENT', 'physical observation is future or expired: ' + ref)
        if fact['machine_ref'] is not None and not p.inherited(fact):
            require(row(self.conn, "SELECT ulid FROM ax_machine WHERE ulid=? AND status='active' AND owning_agent=?",
                        (fact['machine_ref'], verification['owning_agent'])), 'MACHINE', 'machine registration is inactive')
        eq = EquipmentSnapshot(dict(id=f'{sid}:{suffix}:{ref}', session_id=sid,
            catalog_version=c['catalog_version'], variant_id=c['variant_id'], machine_ref=fact['machine_ref'],
            grid_version=fact['grid_version'], inventory_version=fact['inventory_version'],
            load_basis=c['load_basis'], implement_count=c['implement_count'],
            mounted_load_g=fact['mounted_load_g'], evidence_id=None)).to_dict()
        self.conn.execute('INSERT INTO session_equipment_snapshots('+','.join(eq)+') VALUES('+','.join('?' for _ in eq)+')', tuple(eq.values()))
        return eq

    def history_for(self, slot_id, session_id):
        """Only member-authorized, occurrence-pinned history is eligible.

        Bound legacy streams keep their row identities for the pinned source
        reader's exercise/machine/session filters. New history stays slot-pinned.
        """
        member = self.principal['member_id']
        binding = self.config['history_bindings'].get(member)
        from kit_life import _health_agent_resolve_full
        owner = _health_agent_resolve_full(self.conn)[0]
        validate_legacy_coverage(self.conn, self.config, owner)
        result = []
        self.history_occurrences = {}
        if binding:
            catalog = {c['exercise_id']: c for c in self.config['catalog']}
            # Preserve all original streams for the pinned v1 reader to filter.
            # Never pre-pool them by variant or discard ramps/zero sequences.
            for sid in binding['legacy_session_ids']:
                if sid == session_id:
                    continue
                w = row(self.conn, 'SELECT * FROM workouts WHERE id=?', (sid,))
                for s in rows(self.conn, 'SELECT * FROM workout_sets WHERE session_id=? ORDER BY id', (sid,)):
                    c = catalog.get(s['exercise_name'])
                    require(c is not None, 'HISTORY', 'legacy movement requires pinned source semantics: ' + s['exercise_name'])
                    result.append(dict(session_id=sid, row_id=s['id'], routine_id=None,
                        split_id=None, slot_id=None, owning_agent=w['owning_agent'], member_id=member,
                        machine_ref=s['machine_ref'], recorded_at=s['recorded_at'],
                        row_status=s['row_status'] or 'logged', set_kind=s['set_kind'] or 'working',
                        unit='seconds' if s['hold_sec'] else 'reps', load_basis=c['load_basis'],
                        load_g=grams(s['weight_kg']), reps=s['reps'], count=None,
                        hold_sec=s['hold_sec'], rir=s['rir'], rir_is_lower_bound=bool(s['rir_is_lower_bound']),
                        session_date=w['date'], condition=w['note'], stop_reason=None,
                        attribution_digest=binding['attribution_digest']))
        for e in rows(self.conn, "SELECT e.value,w.id,w.member_id,w.owning_agent,w.date,w.created_at,w.note "
                     "FROM session_evidence e JOIN workouts w ON w.id=e.session_id "
                     "WHERE e.kind='program_plan/1' AND w.member_id=? AND w.owning_agent=? "
                     "AND w.session_version=2 AND w.id<>? ORDER BY w.id",
                     (member, owner, session_id)):
            plan = self.effective_plan(snapshot(self.conn,e['id']))
            require(plan['program_digest'] == digest(self.facts()), 'HISTORY',
                    'prior occurrence needs reviewed configuration/history reconciliation')
            # V1 fatigue, rotation and anchors read other authorized occurrences.
            # Their immutable source bindings, not the queried slot or row order,
            # identify each stream. Applied rows retain correction/replan pins.
            prior_state = snapshot(self.conn, e['id'])
            bound = {b['set_id']: b['row'] for b in self.row_bindings(prior_state)}
            for pin in plan['slots']:
                slot = self.config['program_slots'][pin['slot_id']]
                fact = self.config['equipment'][slot['equipment_ref']]
                require(e['owning_agent'] == fact['verification']['owning_agent'],
                        'HISTORY', 'native history belongs to another owning agent')
                for s in rows(self.conn, 'SELECT * FROM workout_sets WHERE lift_id=? ORDER BY id', (pin['lift_id'],)):
                    require(s['id'] in bound, 'HISTORY', 'native row lacks an immutable binding')
                    basis = bound[s['id']]['basis']['reason']
                    try:
                        projection = json.loads(basis).get('v1_projection')
                    except (ValueError, AttributeError):
                        projection = None
                    if projection is not None:
                        self.history_occurrences[str(s['id'])] = dict(row_id=s['id'], session_id=e['id'],
                            projection=projection, session_created_at=e['created_at'])
                    result.append(dict(session_id=s['session_id'], row_id=s['id'],
                        routine_id=slot['source_identity']['routine_id'], split_id=slot['source_identity']['split_id'],
                        slot_id=pin['slot_id'], owning_agent=e['owning_agent'], member_id=member,
                        machine_ref=s['machine_ref'], recorded_at=s['recorded_at'], row_status=s['row_status'],
                        set_kind=s['set_kind'], unit=s['actual_unit'] or s['target_unit'],
                        load_basis=slot['working']['load_basis'], load_g=grams(s['weight_kg']),
                        reps=s['reps'], count=s['reps'] if s['target_unit'] != 'reps' else None,
                        hold_sec=s['actual_hold_sec'], rir=s['rir'], rir_is_lower_bound=bool(s['rir_is_lower_bound']),
                        session_date=e['date'], condition=e['note'], stop_reason=None,
                        attribution_digest=digest(dict(member_id=member, session_id=s['session_id']))))
        return result

    def calculate(self, command, slot_id, policy, current_rows=(), reason='start', *, lift_id=None):
        slot = copy.deepcopy(self.config['program_slots'][slot_id])
        calculation_facts = copy.deepcopy(self.facts())
        fact = calculation_facts['equipment'][slot['equipment_ref']]
        state = snapshot(self.conn, command['session_id'])
        pin = next((s for s in self.effective_plan(state)['slots'] if s['slot_id']==slot_id and
                    s['lift_id']==(lift_id or state['workout']['active_lift_id'])), None) if any(e['kind']=='program_plan/1' for e in state['evidence']) else None
        if pin:
            lift = next(l for l in state['lifts'] if l['id']==pin['lift_id'])
            fact.update(self.equipment_fact(state,lift))
        require('effort_semantics' in slot['working'] or 'effort.floor' in policy,
                'POLICY', 'explicit approved effort floor required')
        bundle_id = (row(self.conn, 'SELECT policy_bundle_id FROM workouts WHERE id=?',
                         (command['session_id'],)) or {}).get('policy_bundle_id')
        bundle = load_policy(self.conn, bundle_id)
        require(bundle['owner_id'] == self.principal['member_id'], 'POLICY', 'policy belongs to another member')
        history = self.history_for(slot_id, command['session_id'])
        output = None
        for key in ('progression_ref', 'ramp_ref'):
            ref = slot[key]; calc = self.config['calculators'][ref]
            require(calc['policy_ref'] == bundle_id and any(
                r['approval_status'] == 'approved' and r['scope'] == 'owner' and r['source_ref'] == 'calculator:' + ref + ':' + calc['rule_digest']
                for r in bundle['rules']), 'POLICY', 'calculator rule lacks explicit owner approval: ' + ref)
            inputs = dict(today=datetime.fromtimestamp(self.clock()/1000, ZoneInfo(self.config['timezone'])).date().isoformat(),
                          command_args=command['args'], source_identity=slot['source_identity'],
                          calculator_phase=key, history_occurrences=self.history_occurrences,
                          current_session={k: v for k, v in row(self.conn,
                              'SELECT created_at,date,note FROM workouts WHERE id=?',
                              (command['session_id'],)).items()})
            q = dict(schema='prescription_request/1', member_id=self.principal['member_id'],
                session_id=command['session_id'], split_id=slot['source_identity']['split_id'], slot_id=slot_id,
                template=slot['working'], calculator_ref=ref, code_digest=calc['code_digest'],
                rule_digest=calc['rule_digest'], policy_bundle_id=bundle_id, policy_digest=digest(bundle),
                equipment=fact, history=history, current_rows=list(current_rows), inputs=inputs, reason=reason,
                dependencies_digest=digest(dict(history=history, current_rows=list(current_rows), inputs=inputs,
                                                equipment=fact, policy=bundle)))
            p.validate_request(q, calculation_facts)
            out = self.hooks[ref](copy.deepcopy(q))
            p.validate_response(out, q, calculation_facts, approved_rir_floor=policy.get('effort.floor', 0))
            if output is not None:
                before = [r for r in output['rows'] if r['set_kind'] != 'ramp']
                after = [r for r in out['rows'] if r['set_kind'] != 'ramp']
                # Ramp calculation can prepend rows, never rewrite the work dose.
                clean = lambda rs: [{k:v for k,v in r.items() if k not in ('step_order', 'row_ref')} for r in rs]
                require(clean(before) == clean(after), 'PROGRAM', 'ramp hook changed working prescription')
            self.record(command, 'prescription_basis/2', dict(schema='prescription_basis/2', slot_id=slot_id,
                calculator_ref=ref, request_digest=digest(q), response_digest=digest(out),
                dependencies_digest=q['dependencies_digest'], basis=canonical([r['basis'] for r in out['rows']]),
                evidence_digest=digest(out), request=q, response=out), scope='lift', suffix=slot_id+':'+key)
            self.last_exchange[(command['session_id'], command['command_id'], slot_id)] = (q, out)
            output = out
            current_rows = out['rows']
        return output['rows']

    def record(self, command, kind, value, *, scope='session', suffix=''):
        source = command.get('source_ref', {}) if kind not in ('program_plan/1','prescription_basis/2') else {}
        source_kind = source.get('kind', 'service' if kind == 'program_plan/1' else 'calculator')
        Writer.evidence(self.conn, dict(id=f'{kind}:{command["session_id"]}:{command["command_id"]}:{suffix}',
            session_id=command['session_id'], event_id=command['command_id'], kind=kind, value=value,
            unit=None, scope=scope, observed_at=self.clock(), source_kind=source_kind,
            source_ref=source.get('ref', self.config['config_digest'])))

    def blocks(self, split):
        return self.config['splits'][split]['blocks'] if split is not None else self.config['free_blocks']

    def plan(self, conn, command, policy, roster=None):
        self.conn = conn
        split = row(conn, 'SELECT split_id FROM session_input_state WHERE session_id=?', (command['session_id'],))['split_id']
        roster = roster if roster is not None else (self.config['splits'].get(split) if split else dict(slot_ids=self.config['free_start'], blocks=self.config['free_blocks']))
        require(roster is not None, 'SPLIT', 'split has no pinned program')
        parts = []
        def block_items(anchor, slot_id=None):
            for b in roster['blocks']:
                if b['anchor'] == anchor and b['anchor_slot_id'] == slot_id:
                    for stage in b['stages']:
                        for round_index in range(stage['rounds'] or 1):
                            for index, source in enumerate(stage['rows']):
                                r = copy.deepcopy(source)
                                r['row_ref'] += ':round:' + str(round_index)
                                r['stage'] = dict(stage_id=stage['stage_id'], round_index=round_index,
                                    source_row_ref=source['row_ref'], duration_ms=stage['duration_ms'],
                                    rest_after_ms=stage['rest_ms'] if index == len(stage['rows'])-1 else 0)
                                parts.append(dict(block_id=b['block_id'], row=r))
        block_items('session_start')
        for slot_id in roster['slot_ids']:
            block_items('before_slot', slot_id)
            parts.append(dict(slot_id=slot_id))
            block_items('after_slot', slot_id)
        block_items('after_lifts')
        plan = []; meta = []
        for i, item in enumerate(parts):
            if 'slot_id' in item:
                slot = self.config['program_slots'][item['slot_id']]
                ref = slot['equipment_ref']; c = self.catalog(slot['variant_id'])
                rx = self.calculate(command, item['slot_id'], policy)
                name = c['display_name']
            else:
                r = item['row']; ref = r['equipment_ref']
                c = self.catalog(self.config['equipment'][ref]['variant_id'])
                require((r['load_state'] != 'calibration_required' or p.inherited(self.config['equipment'][ref])) and
                        p.load_allowed(self.config['equipment'][ref], r['load_g']),
                        'EQUIPMENT', 'block row is unresolved or infeasible')
                require(r['laterality']['recording_mode'] != 'separate_sides', 'PROGRAM', 'separate side block expansion unavailable')
                rx = [r]; name = c['display_name']
            eq = self.equipment_snapshot(command['session_id'], ref, command['command_id']+':'+str(i))
            offset = sum(len(item['sets']) for item in plan)
            sets = [dict(target_tuple(r, eq['id']), set_kind=r['set_kind'], step_order=offset+n) for n, r in enumerate(rx)]
            plan.append(dict(lift=dict(exercise_id=c['exercise_id'], exercise_name=name,
                variant_id=c['variant_id'], order_index=i, slot_num=i+1,
                machine_ref=eq['machine_ref'], implement_resolution_id=eq['id']), sets=sets))
            meta.append(dict(item, equipment_ref=ref, rows=rx))
        self.pending = dict(split=split, meta=meta, definitions={b['block_id']:b for b in roster['blocks']},
            policy_digest=digest(load_policy(conn, row(conn, 'SELECT policy_bundle_id FROM workouts WHERE id=?', (command['session_id'],))['policy_bundle_id'])))
        return plan

    def plan_inserted(self, conn, sid, plan, allocated, revision, cid):
        require(self.pending and len(self.pending['meta']) == len(allocated), 'PROGRAM', 'plan allocation binding missing')
        slots = []; blocks = {}; row_bindings = []
        for meta, ids in zip(self.pending['meta'], allocated):
            if 'slot_id' in meta:
                slots.append(dict(slot_id=meta['slot_id'], equipment_ref=meta['equipment_ref'], **ids))
            else:
                blocks.setdefault(meta['block_id'], []).extend(ids['set_ids'])
            row_bindings.extend(dict(set_id=sid, row=r) for sid, r in zip(ids['set_ids'], meta['rows']))
        evidence = dict(schema='program_plan/1', configuration_digest=self.config['config_digest'],
            program_digest=digest(self.facts()), policy_digest=self.pending['policy_digest'], split_id=self.pending['split'],
            slots=slots, blocks=[dict(block_id=b, set_ids=ids) for b, ids in blocks.items()],
            calculators={ref:{k:c[k] for k in ('version','code_digest','rule_digest')} for ref,c in self.config['calculators'].items()},
            rows=row_bindings)
        c = dict(session_id=sid, command_id=cid)
        if conn.execute("SELECT 1 FROM session_evidence WHERE session_id=? AND kind='program_plan/1'", (sid,)).fetchone():
            evidence['schema'] = 'program_extension/1'
            evidence['revision'] = revision
            for b in evidence['blocks']:
                b['definition'] = self.pending['definitions'][b['block_id']]
            check = copy.deepcopy(evidence); check['schema'] = 'program_plan/1'; check.pop('revision')
            for b in check['blocks']:
                b.pop('definition')
            p.validate_evidence(conn, dict(kind='program_plan/1', scope='session', value=check, session_id=sid))
            self.record(c, 'program_extension/1', evidence)
        else:
            self.record(c, 'program_plan/1', evidence)
        self.pending = None
        self.last_exchange.clear()

    def advance_program(self, conn, sid):
        w = row(conn, 'SELECT * FROM workouts WHERE id=?', (sid,))
        if w['phase'] == 'plan' or w['input_stage'] == 'condition':
            update(conn, 'workouts', sid, dict(phase='entry', input_stage='condition',
                action_kind='acknowledge_condition', active_lift_id=None, active_set_id=None))
            return
        # Close exhausted prep/hold/post lifts, then follow the pinned order.
        for l in rows(conn, "SELECT * FROM workout_lifts WHERE session_id=? AND status='open' ORDER BY order_index", (sid,)):
            if not conn.execute("SELECT 1 FROM workout_sets WHERE lift_id=? AND row_status='planned'", (l['id'],)).fetchone():
                conn.execute('UPDATE workouts SET active_lift_id=NULL,active_set_id=NULL WHERE id=?', (sid,))
                logged = conn.execute("SELECT 1 FROM workout_sets WHERE lift_id=? AND row_status='logged'", (l['id'],)).fetchone()
                update(conn, 'workout_lifts', l['id'], dict(status='closed' if logged else 'skipped', closed_reason='planned_complete'))
        nxt = row(conn, "SELECT s.* FROM workout_sets s JOIN workout_lifts l ON l.id=s.lift_id "
                  "WHERE s.session_id=? AND s.row_status='planned' AND l.status='open' ORDER BY l.order_index,s.step_order,s.id LIMIT 1", (sid,))
        update(conn, 'workouts', sid, dict(active_lift_id=nxt['lift_id'] if nxt else None,
            active_set_id=nxt['id'] if nxt else None,
            phase=('prep' if nxt['set_kind']=='prep' else 'post' if nxt['set_kind']=='post' else 'ready') if nxt else 'lift_done',
            input_stage=('activity_wait' if nxt['set_kind']=='post' else 'reps') if nxt else None,
            action_kind=('activity_start' if nxt['set_kind']=='post' else 'set') if nxt else None))

    def activity_timer(self, state):
        w = state['workout']
        if w['input_stage'] != 'activity_running':
            return None
        undone = {json.loads(e['value'])['command_id'] for e in state['evidence'] if e['kind']=='program_undo/1'}
        timers = [json.loads(e['value']) for e in state['evidence']
                  if e['kind']=='program_activity_timer/1' and e['event_id'] not in undone]
        timer = max(timers, key=lambda t:t['revision'], default=None)
        return timer if timer and timer['row_id']==w['active_set_id'] and timer['rest_epoch']==w['rest_epoch'] else None

    def rest_duration(self, state, current):
        if current['set_kind'] == 'post':
            return 0  # Each post row waits for its own Start; activity time is not rest.
        binding = next(b['row'] for b in self.row_bindings(state) if b['set_id'] == current['id'])
        return binding['target_rest_ms'] + binding.get('stage', {}).get('rest_after_ms', 0)

    def requires_rir(self, state, current):
        if current['set_kind']=='working':return True
        if current['set_kind']!='ramp':return False
        binding=next(b['row'] for b in self.row_bindings(state) if b['set_id']==current['id'])
        try:return json.loads(binding['basis']['reason']).get('v1_probe_rir') is True
        except (ValueError,AttributeError):return False

    def validate_actual(self, state, actual):
        undone = {json.loads(e['value'])['command_id'] for e in state['evidence'] if e['kind']=='program_undo/1'}
        state = dict(state, evidence=[e for e in state['evidence'] if e['kind']!='pain' or e['event_id'] not in undone])
        current = next(s for s in state['sets'] if s['id'] == state['workout']['active_set_id'])
        prescribed = next(b['row'] for b in self.row_bindings(state) if b['set_id'] == current['id'])
        require(prescribed['load_state'] != 'calibration_required' or actual.get('load_g') is not None,
                'CALIBRATION', 'enter a starting load with the first set')
        fact = self.config['equipment'][prescribed['equipment_ref']]
        if not p.inherited(fact):
            return super().validate_actual(state, actual)
        require(actual['unit'] == current['target_unit'], 'UNIT', 'actual dose unit mismatch')
        require(not any(e['kind'] == 'pain' and
                json.loads(e['value']).get('lift_id') == current['lift_id'] for e in state['evidence']),
                'PAIN', 'pain-constrained lift cannot be logged')
        require(p.load_allowed(fact, actual['load_g']) and
                ((actual['load_g'] is None and prescribed['load_state'] in ('unloaded', 'unprescribed')) or
                 (actual['load_g'] is not None and prescribed['load_state'] != 'unloaded')),
                'EQUIPMENT', 'actual load differs from inherited source grid')

    def block_intent(self, writer, conn, c, state):
        plan = self.effective_plan(state)
        b = next((b for b in plan['blocks'] if b['block_id'] == c['args']['block_id']), None)
        require(b, 'STAGE', 'unknown pinned block')
        if c['event_type'] == 'skip_block':
            require(state['workout']['active_set_id'] in b['set_ids'], 'STAGE', 'only the active block can skip')
        mine = [s for s in state['sets'] if s['id'] in b['set_ids']]
        require(c['event_type'] == 'skip_block' or all(s['row_status'] == 'logged' for s in mine),
                'STAGE', 'block completion cannot fabricate actuals')
        affected = []
        if c['event_type'] == 'skip_block':
            for lid in dict.fromkeys(s['lift_id'] for s in mine if s['row_status'] == 'planned'):
                affected += writer.close_lift(conn, c['session_id'], lid, c['args']['reason'], c['expected_revision']+1)
        self.record(c, 'block_event/1', dict(block_id=b['block_id'], event=c['event_type'], reason=c['args']['reason']))
        writer.cancel_rest(conn, c['session_id'])
        writer.advance(conn, c['session_id'])
        return affected

    def guard_intent(self, c, state):
        from .program_intents import order_guard
        event = c['event_type']; args = c['args']; w = state['workout']
        require(w['input_stage'] != 'condition' or event in
                ('acknowledge_condition', 'stop_session', 'approve_step', 'measure_kcal'),
                'STAGE', 'acknowledge condition before starting the program')
        plan = self.effective_plan(state)
        active = next((s for s in state['sets'] if s['id'] == w['active_set_id']), None)
        if event in ('change_load','change_reps','select_load'):
            target = next((s for s in state['sets'] if s['id']==args.get('row_id',w['active_set_id'])), None)
            require(target and target['row_status']=='planned', 'PROGRAM', 'no planned target')
            if target['id'] != w['active_set_id']:
                same_block = any(target['id'] in b['set_ids'] and w['active_set_id'] in b['set_ids'] for b in plan['blocks'])
                require(event != 'select_load' and ((target['set_kind']=='ramp' and args.get('slot_id')) or same_block),
                        'MOVED', 'target row is not active')
            if args.get('slot_id'):
                lift = next(l for l in state['lifts'] if l['id']==target['lift_id'])
                require(self.slot_for(state,lift)[0] == args['slot_id'], 'PROGRAM', 'target belongs to another slot')
            if event == 'change_reps':
                require(target['target_unit'] == 'reps', 'UNIT', 'counted/time dose cannot use change_reps')
        if event == 'stop_session':
            pending = {sid for b in plan['blocks'] if b['definition']['required'] for sid in b['set_ids']}
            require(not any(s['id'] in pending and s['row_status']=='planned' for s in state['sets']),
                    'PROGRAM', 'required blocks need completion or block-local skip before closeout')
        block_set_ids = {sid for b in plan['blocks'] for sid in b['set_ids']}
        if event == 'skip_warmup':
            require(c['source_ref']['kind']=='text', 'PROGRAM', 'warm-up skip requires chat')
            require(active and active['id'] not in block_set_ids, 'PROGRAM', 'warm-up skip requires an active slot')
        if event == 'reorder':
            order_guard(self,state,args['lift_ids'])
        if event == 'next_lift' and args.get('target_lift_id'):
            target = next((l for l in state['lifts'] if l['id']==args['target_lift_id'] and l['status']=='open'), None)
            require(target, 'LIFT', 'target lift is not open')
            require(not any(l['status']=='open' and l['id'] not in (w['active_lift_id'],target['id']) and
                    l['order_index'] < target['order_index'] for l in state['lifts']),
                    'STAGE', 'slot jump would bypass ordered work')
        if event == 'pain':
            require(args['lift_id']==w['active_lift_id'], 'LIFT', 'pain action requires the active slot')
        if event == 'reclassify':
            require(args['row_id'] not in block_set_ids, 'PROGRAM', 'block row kind is pinned')

    def target_scope(self, state, command):
        return command['args'].get('row_id', state['workout']['active_set_id'])

    def recalculate(self, state, command, policy):
        event = command['event_type']; args = command['args']
        if event in ('change_load','change_reps','select_load'):
            active = next(s for s in state['sets'] if s['id']==args.get('row_id',state['workout']['active_set_id']))
            plan = self.effective_plan(state)
            if active['set_kind']=='ramp' or any(active['id'] in b['set_ids'] for b in plan['blocks']):
                from .program_intents import local_targets
                return local_targets(self,state,command)
        recorded = next((s for s in state['sets'] if s['id'] == args.get('row_id')), None)
        lid = recorded['lift_id'] if event in ('undo','correct','reclassify','set_logged','select_rir','select_reps','select_reps_nullable') and recorded else state['workout']['active_lift_id']
        lift = next((l for l in state['lifts'] if l['id'] == lid), None)
        require(lift is not None, 'PROGRAM', 'no slot for recalculation')
        plan = self.effective_plan(state)
        if not any(s['lift_id'] == lid for s in plan['slots']):
            return {}  # Undo/correct a block actual without changing prescribed rows.
        slot_id, slot = self.slot_for(state, lift)
        bindings = self.row_bindings(state)
        pin = next(s for s in plan['slots'] if s['lift_id'] == lid)
        current = [b['row'] for b in bindings if b['set_id'] in pin['set_ids'] and
                   next(s for s in state['sets'] if s['id']==b['set_id'])['row_status'] != 'omitted']
        reasons = dict(change_load='load_change', select_load='load_change', change_reps='reps_change',
                       undo='undo', correct='correction', reclassify='reclassification',
                       set_logged='remeasurement', select_rir='remeasurement', select_reps='remeasurement', select_reps_nullable='remeasurement')
        # Actuals are passed explicitly; they never overwrite the immutable plan.
        qcommand = copy.deepcopy(command)
        bound = {b['set_id']: b['row'] for b in bindings}
        qcommand['args']['current_actuals'] = [dict(row_id=s['id'], row_ref=bound[s['id']]['row_ref'],
            row_status=s['row_status'], set_kind=s['set_kind'], unit=s['actual_unit'] or s['target_unit'],
            load_g=grams(s['weight_kg']), count=s['actual_hold_sec'] if s['target_unit']=='seconds' else s['reps'],
            rir=s['rir'], rir_is_lower_bound=bool(s['rir_is_lower_bound']), recorded_revision=s['recorded_revision'],
            recorded_at=s['recorded_at'], machine_ref=s['machine_ref']) for s in state['sets'] if s['lift_id'] == lid and s['row_status'] != 'omitted']
        rx = self.calculate(qcommand, slot_id, policy, current, reasons[event], lift_id=lid)
        q, response = self.last_exchange.pop((command['session_id'], command['command_id'], slot_id))
        old_by_ref = {bound[rid]['row_ref']:rid for rid in pin['set_ids']}
        require(len(old_by_ref)==len(pin['set_ids']), 'PROGRAM', 'duplicate pinned row reference')
        actual_ids = {r['id'] for r in state['sets'] if r['lift_id']==lid and r['row_status']=='logged'}
        require(actual_ids <= {old_by_ref[r['row_ref']] for r in rx if r['row_ref'] in old_by_ref},
                'IMMUTABLE', 'source transition dropped performed actuals')
        topology_changed = ([r['row_ref'] for r in rx] != [r['row_ref'] for r in current] or
                            [r['set_kind'] for r in rx] != [r['set_kind'] for r in current])
        require(not topology_changed or 'transition' in response, 'PROGRAM', 'explicit source transition required')
        result = {}; added = []; omitted = []; kinds = []; applied = []; effective = []
        revision = command['expected_revision']+1
        for index, r in enumerate(rx):
            rid = old_by_ref.get(r['row_ref'])
            if rid is None:
                require('transition' in response, 'PROGRAM', 'new row lacks source transition')
                # Allocate stable native identities, never re-use omitted/void IDs.
                fields = target_tuple(r, lift['implement_resolution_id'])
                seq = self.conn.execute('SELECT coalesce(max(set_seq),0)+1 FROM workout_sets WHERE session_id=? AND exercise_name=?',
                                        (command['session_id'], lift['exercise_name'])).fetchone()[0]
                fields.update(session_id=command['session_id'], lift_id=lid, exercise_name=lift['exercise_name'],
                    slot_num=lift['slot_num'], set_seq=max(1,seq), machine_ref=lift['machine_ref'], set_kind=r['set_kind'],
                    step_order=index, row_status='planned', actual_source=None, prescribed_revision=revision,
                    policy_bundle_id=state['workout']['policy_bundle_id'], basis_event_id=command['command_id'],
                    recorded_at=datetime.fromtimestamp(self.clock()/1000,ZoneInfo(self.config['timezone'])).strftime('%Y-%m-%d %H:%M:%S'))
                cursor=self.conn.execute('INSERT INTO workout_sets('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')', tuple(fields.values()))
                rid=cursor.lastrowid; added.append(rid)
                old = dict(fields,id=rid)
            else:
                old = next(s for s in state['sets'] if s['id']==rid)
            effective.append(dict(set_id=rid,row=r))
            if old['set_kind'] != r['set_kind']:
                require('transition' in response and old['set_kind'] in ('working','ramp') and
                        r['set_kind'] in ('working','ramp'), 'PROGRAM', 'invalid source kind transition')
                kinds.append(dict(set_id=rid,before=old['set_kind'],after=r['set_kind']))
                update(self.conn,'workout_sets',rid,dict(set_kind=r['set_kind']))
            if old['row_status']=='logged':
                # The actual and its original target tuple are immutable.
                if old['set_kind'] != r['set_kind']:
                    applied.append(dict(set_id=rid,row=r))
                continue
            if old['row_status']!='planned' or (args.get('scope')=='current_only' and rid!=state['workout']['active_set_id']):
                continue
            if r['set_kind'] in ('working','hold'):
                if event in ('change_load','select_load'):
                    require(r['load_g']==args['load_g'], 'PROGRAM', 'calculator ignored selected load')
                if event=='change_reps':
                    require(r['target_reps']==args['target_reps'], 'PROGRAM', 'calculator ignored selected reps')
            result[rid] = target_tuple(r,lift['implement_resolution_id'])
            applied.append(dict(set_id=rid,row=r))
            update(self.conn,'workout_sets',rid,dict(step_order=index))
        retained={b['set_id'] for b in effective}
        for old in state['sets']:
            if old['id'] not in pin['set_ids'] or old['id'] in retained:
                continue
            require(old['row_status']!='logged', 'IMMUTABLE', 'cannot omit performed actual')
            omitted.append(old['id'])
            if old['row_status']=='planned':
                if old['id']==state['workout']['active_set_id']:
                    update(self.conn,'workouts',command['session_id'],dict(active_set_id=None))
                update(self.conn,'workout_sets',old['id'],dict(row_status='omitted'))
        value=dict(schema='prescription_basis/2',slot_id=slot_id,lift_id=lid,calculator_ref=q['calculator_ref'],
            request_digest=digest(q), response_digest=digest(response), dependencies_digest=q['dependencies_digest'],
            basis='applied_to_planned_rows',evidence_digest=digest(response),request=q,response=response,
            revision=revision,applied_rows=applied)
        if topology_changed:
            value['topology']=dict(lift_id=lid,added_set_ids=added,omitted_set_ids=omitted,
                                   kind_changes=kinds,rows=effective)
        self.record(command,'prescription_basis/2',value,scope='lift',suffix=slot_id+':applied')
        return TargetChanges(result,added+omitted+[k['set_id'] for k in kinds])

    def import_resolution(self, *args):
        self.missing('slot-aware legacy reconciliation')


    def row_bindings(self, state):
        bound = {}
        plan = self.effective_plan(state)
        require('rows' in plan, 'PROGRAM', 'immutable row bindings required')
        for item in plan['rows']:
            bound[item['set_id']] = item
        updates = [(e['kind'],json.loads(e['value'])) for e in state['evidence']
                   if e['kind'] in ('prescription_basis/2','program_operation/1','program_undo/1')]
        for kind, event in sorted(updates, key=lambda item:item[1].get('revision',0)):
            for item in event.get('topology', {}).get('rows', []):
                bound[item['set_id']] = item
            applied = event.get('applied_rows', []) if kind=='prescription_basis/2' else event['rows']
            for item in applied:
                require(item['set_id'] in bound, 'PROGRAM', 'row update is outside the pinned plan')
                bound[item['set_id']] = item
        return list(bound.values())

    def project_program(self, state):
        """Project source structure and committed rows, without recalculation."""
        plan = self.effective_plan(state)
        by_id = {s['id']: s for s in state['sets']}
        bindings = {b['set_id']: b['row'] for b in self.row_bindings(state)}
        result = dict(schema='program_projection/1', plan=plan, blocks=[], rows=[], slots=[],
            input_stage=state['workout']['input_stage'], activity_timer=self.activity_timer(state),
            equipment_evidence={ref: dict(evidence_class=eq['verification']['status'],
                provenance_ref=eq['provenance_ref'], digest=eq['verification']['digest'])
                for ref, eq in self.config['equipment'].items()})
        for pin in plan['slots']:
            slot = self.config['program_slots'][pin['slot_id']]
            result['slots'].append(dict(pin, source_range=slot['working']['range'], role=slot['role']))
        for pin in plan['blocks']:
            b = pin['definition']
            status = [by_id[sid]['row_status'] for sid in pin['set_ids']]
            value = dict(b, set_ids=pin['set_ids'], status=('pending' if 'planned' in status else
                         'skipped' if 'omitted' in status else 'complete'))
            result['blocks'].append(value)
        for s in state['sets']:
            require(s['id'] in bindings, 'PROGRAM', 'uncovered committed row')
            result['rows'].append(dict(set_id=s['id'], prescription=bindings[s['id']],
                                       committed={k:s[k] for k in TARGET_FIELDS}, status=s['row_status']))
        return result
