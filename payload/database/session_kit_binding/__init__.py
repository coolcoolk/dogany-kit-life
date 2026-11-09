"""Authenticated kit binding to pinned pack facts and the WP2 compiler.

Deployment supplies a private configuration, signed caller context and native
capability receipt. Nothing here installs a route or claims a bridge is present.
All files are verified/read before Writer begins its transaction.
"""
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

from session_command import (Runtime, Writer, CommandError, SCHEMA_VERSION, TARGET_FIELDS, canonical,
                             digest, grams, require, row, rows, update)
from session_policy import effective_range, working_target, taper, plates
from session_projection import project, receipt_for
from session_v2_contract.policy import CatalogEntry, EquipmentSnapshot
from session_program import capability_response as _program_capability_response


def validate_capability_response(challenge, receipt):
    if 'program' not in challenge and 'program' not in receipt:
        from session_v2_contract.transport import validate_capability_response as native_response
        return native_response(challenge, receipt)
    return _program_capability_response(challenge, receipt)

HEAVY = frozenset(('deadlift', 'sumo_deadlift', 'back_squat', 'bench_press',
                   'incline_barbell_press', 'pendlay_row'))
CONFIG_RECEIPT_FIELDS = frozenset(('service_key_hex', 'config_digest', 'challenge',
    'capability_receipt', 'capability_signature', 'legacy_retirement', 'installation_closure'))


def configuration_digest(config):
    """Hash facts/paths/manifests, including any receipt-supplied installer digest."""
    facts = {k:v for k,v in config.items() if k not in CONFIG_RECEIPT_FIELDS}
    if 'activation' in facts:
        # Generation rotates admission, not the pinned planning facts of an
        # already-open workout. F and closure authenticate it separately.
        facts['activation'] = {k:v for k,v in facts['activation'].items() if k != 'generation'}
    return digest(facts)


def load_pack(path):
    path = Path(path)
    version = hashlib.sha256(str(path.resolve()).encode())
    for source in sorted(path.iterdir()):
        if source.suffix in ('.py', '.json'):
            version.update(source.name.encode()); version.update(source.read_bytes())
    name = '_lifekit_pinned_session_pack_' + version.hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(name, path/'__init__.py',
                                                 submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return __import__(name + '.trainer_runtime', fromlist=['trainer_runtime'])


class KitRuntime(Runtime):
    def __init__(self, config, principal, trainer, *, clock=None):
        from session_program import guard_configuration
        guard_configuration(config)
        if config.get('schema') == 'session_program_facts/2':
            from session_program import need
            need(hasattr(self, 'hooks'), '$', 'installed execution unavailable: use slot runtime factory')
        self.config = config
        self.principal = principal
        self.trainer = trainer
        self.templates = trainer.load_templates(config['templates_path'])
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        self.catalogs = {c['variant_id']: CatalogEntry(c).to_dict() for c in config['catalog']}
        self.conn = None

    def authorize(self, command, state):
        p = self.principal
        require(p['actor'] == command['actor'] and p['session_id'] == command['session_id'],
                'AUTH', 'caller role/session differs from signed context')
        require(p['command_digest'] == digest(command), 'AUTH', 'request differs from authenticated bytes')
        source = command['source_ref']
        require(source['kind'] in p['source_kinds'], 'AUTH', 'caller cannot use this source')
        for key in ('member_id', 'chat_id'):
            if key in source:
                require(source[key] == p[key], 'AUTH', 'caller '+key+' mismatch')
        if state['workout'] and state['workout']['session_version']==1:
            resolution=self.config.get('imports',{}).get(str(command['session_id']))
            require(command['event_type']=='start' and source['kind']=='import' and resolution and
                    resolution['member_id']==p['member_id'], 'AUTH', 'legacy session requires reviewed member mapping')
        elif state['workout']:
            require(state['workout']['member_id'] == p['member_id'], 'AUTH', 'session belongs to another member')
            meta = row(self.conn, 'SELECT chat_id FROM session_input_state WHERE session_id=?', (command['session_id'],))
            require(meta and meta['chat_id'] == p['chat_id'], 'AUTH', 'session belongs to another chat')
            pin=next((e for e in state['evidence'] if e['kind']=='runtime_binding'),None)
            require(pin and json.loads(pin['value'])['config_digest']==self.config['config_digest'],
                    'ARTIFACT','session was opened with a different facts/configuration bundle')
        require(p['expires_at'] >= self.clock(), 'AUTH', 'caller context expired')

    def admission(self, conn, command, prior=None):
        require(conn.execute('PRAGMA user_version').fetchone()[0] >= 83,
                'CAPABILITY', 'installed K1 writer requires schema 83')
        authority = getattr(self, 'activation_authority', None)
        require(authority is not None, 'CAPABILITY', 'installed K1 authority is required')
        installed = self.config['activation']
        stored = row(conn, 'SELECT closure FROM session_pack_fences WHERE pack=?', (installed['pack'],))
        require(stored and stored['closure'] == canonical(self.config.get('installation_closure')),
                'CAPABILITY', 'missing or unverified installation closure')
        authority.check_attempt(conn, command, self.principal, installed, prior)

    def delivery_context(self, command):
        authority = getattr(self, 'delivery_authority', None)
        if authority is None:
            return None
        matches = [e for e in authority.envelopes if e['envelope_id'] == command['source_ref']['ref']]
        require(len(matches) == 1, 'AUTH', 'verified delivery envelope reference required')
        scope = matches[0]['S']
        principal = dict(publisher_id=authority.publisher_id, actor=command['actor'], member_id=self.principal['member_id'])
        mapped = authority.scope(scope, principal)
        require(mapped['session_id'] == command['session_id'] and str(scope['chat_id']) == self.principal['chat_id'], 'AUTH', 'delivery identity mismatch')
        authority.command_envelope(dict(command), scope, principal)
        return authority, scope

    def capability(self, conn, command):
        c = self.config
        require(c.get('installation_closure') is not None and getattr(self, 'activation_authority', None) is not None,
                'CAPABILITY', 'authenticated installation absence attestation required')
        receipt = self.runtime_capability()
        require(c['capability_receipt']['receipt_id'] == command['args']['capability_receipt_id'] and
                receipt['artifacts'] == c['artifact_digests'] and
                receipt['config_digest'] == c['config_digest'] and
                receipt['installed_root'] == c['installed_root'] and
                receipt['capabilities']['kit_schema'] >= SCHEMA_VERSION,
                'CAPABILITY', 'receipt does not identify this installed bundle')
        retirement = c['legacy_retirement']
        require(retirement['capability_receipt_id'] == receipt['install_receipt_id'] and
                retirement['routes_revoked'] is True and retirement['workers_fenced'] is True and
                all(s['markup_state'] == 'clear' and s['acknowledged'] is True for s in retirement['surfaces']),
                'CAPABILITY', 'legacy callbacks/keyboards have not been verifiably retired')
        require(command['args']['catalog_version'] == c['catalog_version'], 'CATALOG', 'catalog version mismatch')

    def runtime_capability(self):
        """Verify the framework's fresh session-open measurement, not install age."""
        c = self.config
        try:
            envelope = getattr(self, 'runtime_receipt', None)
            require(envelope is not None, 'CAPABILITY', 'session-open runtime receipt missing')
            require(type(envelope) is dict and set(envelope) == {
                'schema', 'capability_receipt', 'capability_signature'} and
                envelope['schema'] == 'session_runtime_receipt/1',
                'CAPABILITY', 'invalid session-open runtime receipt envelope')
            receipt = envelope['capability_receipt']
            require(type(receipt) is dict and type(envelope['capability_signature']) is str,
                    'CAPABILITY', 'invalid session-open runtime receipt')
            key = bytes.fromhex(c['service_key_hex'])
            require(len(key) >= 32 and hmac.compare_digest(
                hmac.new(key, canonical(receipt).encode(), hashlib.sha256).hexdigest(),
                envelope['capability_signature']), 'CAPABILITY',
                'invalid session-open runtime receipt signature')
            install = c['capability_receipt']
            require(receipt.get('purpose') == 'session_open' and
                    receipt.get('install_receipt_id') == install['receipt_id'] and
                    receipt.get('config_digest') == c['config_digest'] == install['config_digest'],
                    'CAPABILITY', 'session-open runtime receipt purpose/install/config mismatch')
            nonce = receipt.get('nonce')
            require(type(nonce) is str and nonce != install['nonce'] and
                    receipt.get('receipt_id') == 'session_open:'+nonce,
                    'CAPABILITY', 'session-open runtime receipt requires a fresh nonce and receipt id')
            # Reuse the installed capability requirements with the runtime nonce.
            # The frozen native receipt contract excludes the two runtime fields.
            challenge = dict(c['challenge'], nonce=nonce)
            native = {k:v for k,v in receipt.items() if k not in ('purpose', 'install_receipt_id')}
            _program_capability_response(challenge, native, c.get('required_features', ()))
            require(0 <= self.clock() - receipt['checked_at'] <= c['capability_max_age_ms'],
                    'CAPABILITY', 'session-open runtime receipt expired or checked in the future')
            return receipt
        except CommandError as error:
            if error.code == 'CAPABILITY':
                raise
            raise CommandError('CAPABILITY', 'invalid session-open runtime receipt: '+str(error)) from error
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise CommandError('CAPABILITY', 'invalid session-open runtime receipt: '+str(error)) from error

    def pin_context(self, conn, command):
        sid=command['session_id']
        Writer.evidence(conn,dict(id=f'runtime:{sid}',session_id=sid,event_id=command['command_id'],
            kind='runtime_binding',value={'config_digest':self.config['config_digest']},unit=None,scope='session',
            observed_at=self.clock(),source_kind='service',source_ref=self.config['capability_receipt']['receipt_id']))

    def catalog(self, variant):
        require(variant in self.catalogs, 'CATALOG', 'unknown variant')
        c = self.catalogs[variant]
        require(c['review_status'] == 'approved' and c['catalog_version'] == self.config['catalog_version'],
                'CATALOG', 'catalog is not reviewed/pinned')
        return c

    def policy_provenance(self, conn, bundle_id):
        # Trainer explanations consume the same immutable policy rows as LOG.
        return [r[0] for r in conn.execute(
            'SELECT DISTINCT source_ref FROM training_policy_rules WHERE bundle_id=? ORDER BY source_ref',
            (bundle_id,))]

    def spec(self, variant):
        require(variant in self.config['program'], 'PROGRAM', 'variant has no pinned program')
        return self.config['program'][variant]

    def equipment(self, sid, variant, suffix, machine_ref=None):
        c = self.catalog(variant)
        fact = self.config['equipment'][variant]
        if machine_ref is None:
            machine_ref = fact['machine_ref']
        if machine_ref is not None:
            machine = self.config['machines'].get(machine_ref)
            require(machine and set(c['compatible_machine_capabilities']) <= set(machine['capabilities']),
                    'MACHINE', 'machine lacks variant capabilities')
        return EquipmentSnapshot(dict(id=f'{sid}:{suffix}:{variant}', session_id=sid,
            catalog_version=c['catalog_version'], variant_id=variant, machine_ref=machine_ref,
            grid_version=fact['grid_version'], inventory_version=fact['inventory_version'],
            load_basis=c['load_basis'], implement_count=c['implement_count'],
            mounted_load_g=fact['mounted_load_g'], evidence_id=fact.get('evidence_id'))).to_dict()

    def grid(self, state, lift):
        variant = lift['variant_id']
        grid = self.config['equipment'][variant]['available_loads_g']
        if lift.get('machine_ref'):
            grid = self.config['machines'][lift['machine_ref']]['available_loads_g']
        learned = []
        if self.conn is not None:
            learned = rows(self.conn, "SELECT e.* FROM session_evidence e JOIN workouts w ON w.id=e.session_id "
                           "WHERE w.member_id=? AND e.kind='available_loads' ORDER BY e.observed_at,e.id",
                           (self.principal['member_id'],))
        for e in learned + state['evidence']:
            if e['kind'] == 'available_loads':
                v = json.loads(e['value'])
                if v['variant_id'] == variant and v['machine_ref'] == lift.get('machine_ref'):
                    grid = v['available_loads_g']
        require(grid and all(type(n) is int and n >= 0 for n in grid), 'EQUIPMENT', 'invalid feasible load grid')
        return sorted(set(grid))

    def plate_facts(self, state, nxt, eq):
        c = self.catalog(eq['variant_id'])
        if c['implement_kind'] != 'barbell' or nxt['target_load_g'] is None:
            return {}
        fact = self.config['equipment'][eq['variant_id']]
        require(eq['mounted_load_g'] is not None, 'EQUIPMENT', 'mounted bar load is unknown')
        # Performed ramp/working actual is newer mounted-load evidence.
        prior = [s for s in state['sets'] if s['lift_id'] == nxt['lift_id'] and
                 s['row_status'] == 'logged' and s['weight_kg'] is not None]
        mounted = grams(max(prior, key=lambda s: (s['recorded_revision'] or 0, s['id']))['weight_kg']) if prior else eq['mounted_load_g']
        return plates(from_load_g=mounted, to_load_g=nxt['target_load_g'], bar_g=fact['bar_g'],
                      inventory_per_side={int(k): v for k, v in fact['inventory_per_side'].items()})

    def history(self, exercise, machine_ref):
        legacy = self.config.get('legacy_member_sessions', {}).get(self.principal['member_id'], [])
        require(all(type(sid) is int and sid>0 for sid in legacy), 'CONFIG', 'invalid legacy member history mapping')
        return [dict(r, load_g=grams(r.pop('weight_kg'))) for r in rows(self.conn,
            "SELECT s.row_status,s.set_kind,s.weight_kg,s.reps,s.rir FROM workout_sets s "
            "JOIN workouts w ON w.id=s.session_id WHERE s.exercise_name=? AND s.machine_ref IS ? "
            "AND s.row_status='logged' AND s.set_kind='working' AND s.weight_kg IS NOT NULL AND s.reps IS NOT NULL AND (w.member_id=? OR "
            "(w.session_version=1 AND w.id IN ("+','.join('?' for _ in legacy)+"))) "
            "ORDER BY s.recorded_at,s.id", (exercise, machine_ref, self.principal['member_id'], *legacy))]

    def target(self, state, lift, policy, *, load=None, reps=None, previous=None):
        c = self.catalog(lift['variant_id']); spec = self.spec(lift['variant_id'])
        rep_range = spec['rep_range']
        require(spec['role'] == ('strength' if c['exercise_id'] in HEAVY else 'hypertrophy'),
                'POLICY', 'program role differs from approved heavy roster')
        if c['exercise_id'] in HEAVY:
            require(rep_range == [3, 5] and policy['compound.heavy_rep_cap'] == 5,
                    'POLICY', 'approved heavy program requires range [3,5] and cap 5')
        return working_target(catalog=c, policy=policy, program_range=rep_range,
            available_loads_g=self.grid(state, lift), previous_load_g=previous,
            history=self.history(c['display_name'], lift.get('machine_ref')),
            required_sets=spec['working_sets'], selected_load_g=load, selected_reps=reps,
            calibration_load_g=spec.get('calibration_load_g'))

    def make_plan(self, conn, command, policy, variants):
        sid = command['session_id']; result = []
        base = conn.execute('SELECT COALESCE(MAX(order_index),-1)+1 FROM workout_lifts WHERE session_id=?', (sid,)).fetchone()[0]
        for index, variant in enumerate(variants):
            c = self.catalog(variant); spec = self.spec(variant)
            eq = self.equipment(sid, variant, command['command_id']+':'+str(index))
            conn.execute('INSERT INTO session_equipment_snapshots('+','.join(eq)+') VALUES('+','.join('?' for _ in eq)+')', tuple(eq.values()))
            lift = dict(exercise_id=c['exercise_id'], exercise_name=c['display_name'],
                        variant_id=variant, order_index=base+index, slot_num=base+index+1,
                        machine_ref=eq['machine_ref'], implement_resolution_id=eq['id'])
            prescriptions = []
            if spec.get('dose'):
                dose = spec['dose']
                load = dose['load_g']
                require(load is None or load in self.grid({'evidence': []}, lift), 'EQUIPMENT', 'loaded prep is unavailable')
                prescriptions.append((dose['kind'], load, dose['unit'], dose['count'], 0))
            else:
                history = self.history(c['display_name'], eq['machine_ref'])
                previous = history[-1]['load_g'] if history else None
                target = self.target({'evidence': []}, lift, policy, previous=previous)
                Writer.evidence(conn,dict(id=f'basis:{sid}:{command["command_id"]}:{index}',session_id=sid,
                    event_id=command['command_id'],kind='prescription_basis',
                    value=dict(variant_id=variant,basis=target['basis'],program_digest=digest(spec)),
                    unit=None,scope='lift',observed_at=self.clock(),source_kind='calculator',
                    source_ref=self.config['config_digest']))
                grid = self.grid({'evidence': []}, lift)
                ramp_loads = [n for n in spec['ramp_loads_g'] if n in grid]
                for r in taper(ramp_loads, target['load_g'], policy['warmup.taper']) if ramp_loads else []:
                    prescriptions.append(('ramp', r['load_g'], 'reps', r['target_reps'], 0))
                rest = policy['rest.by_lift'][c['exercise_id']] + policy['rest.setup_lead_ms']
                prescriptions += [('working', target['load_g'], 'reps', target['target_reps'], rest)] * spec['working_sets']
            sets = [dict(set_kind=kind, step_order=i, target_load_g=load,
                target_unit=unit, target_reps=count if unit=='reps' else None,
                target_count=count if unit!='reps' else None, target_hold_sec=count if unit=='seconds' else None,
                target_rir_min=spec.get('rir_min') if kind=='working' else None,
                target_rir_max=spec.get('rir_max') if kind=='working' else None,
                target_rest_ms=rest, equipment_snapshot_id=eq['id'])
                for i, (kind, load, unit, count, rest) in enumerate(prescriptions)]
            result.append(dict(lift=lift, sets=sets))
        return result

    def plan(self, conn, command, policy):
        split = row(conn,'SELECT split_id FROM session_input_state WHERE session_id=?',(command['session_id'],))['split_id']
        variants = self.config['splits'].get(split) if split else self.config['free_start']
        require(variants is not None, 'SPLIT', 'split has no pinned program')
        return self.make_plan(conn, command, policy, variants)

    def recalculate(self, state, command, policy):
        event = command['event_type']; args = command['args']; result = {}
        correcting=event in ('undo','correct','reclassify')
        recorded=next((s for s in state['sets'] if s['id']==args.get('row_id')),None)
        lid = recorded['lift_id'] if correcting and recorded else args.get('lift_id', state['workout']['active_lift_id'])
        lift = next(l for l in state['lifts'] if l['id'] == lid)
        require(not any(e['kind']=='pain' and json.loads(e['value']).get('lift_id')==lid for e in state['evidence']),
                'PAIN', 'pain-constrained lift requires a reviewed alternative')
        planned = [s for s in state['sets'] if s['lift_id']==lid and s['row_status']=='planned']
        work = next((s for s in planned if s['set_kind']=='working'), None)
        if not work:
            require(not any(s['set_kind']=='ramp' for s in planned), 'PLAN', 'ramps lack working target')
            grid=self.grid(state,lift)
            for s in planned:
                if args.get('scope')=='current_only' and s['id']!=state['workout']['active_set_id']:
                    continue
                rx={k:s[k] for k in TARGET_FIELDS}
                rx['equipment_snapshot_id']=lift['implement_resolution_id']
                load=args.get('load_g',s['target_load_g'])
                if event=='missing_weight' and load is not None and load not in grid:
                    load=min(grid,key=lambda n:(abs(n-load),n))
                require(load is None or load in grid,'EQUIPMENT','dose load is unavailable')
                rx['target_load_g']=load
                if 'target_reps' in args:
                    require(s['target_unit']=='reps','UNIT','counted/time dose cannot use change_reps')
                    rx['target_reps']=args['target_reps']
                result[s['id']]=rx
            return result
        active=next((s for s in planned if s['id']==state['workout']['active_set_id']),None)
        ramp_selection=event=='select_load' and active and active['set_kind']=='ramp'
        load = work['target_load_g'] if ramp_selection else args.get('load_g', work['target_load_g'])
        grid = self.grid(state, lift)
        if event == 'missing_weight' and load not in grid:
            load = min(grid, key=lambda n: (abs(n-load), n))
        # A correction must not erase a separately accepted target override.
        # Revalidate the remaining full tuples against the corrected logged
        # history and pinned constraints, retaining their explicit rep choice.
        target = self.target(state, lift, policy, load=load,
                             reps=work['target_reps'] if correcting else args.get('target_reps'),
                             previous=work['target_load_g'])
        ramp_loads={s['id']:(args['load_g'] if ramp_selection and s['id']==active['id'] else s['target_load_g'])
                    for s in planned if s['set_kind']=='ramp'}
        require(all(n in grid for n in ramp_loads.values()),'EQUIPMENT','ramp load is unavailable')
        ramps = taper(list(ramp_loads.values()), target['load_g'], policy['warmup.taper'])
        ramp_targets = {r['load_g']: r['target_reps'] for r in ramps}; seen = set()
        for s in planned:
            if args.get('scope')=='current_only' and s['id']!=state['workout']['active_set_id']:
                continue
            rx = {k: s[k] for k in TARGET_FIELDS}
            rx['equipment_snapshot_id'] = lift['implement_resolution_id']
            if s['set_kind']=='ramp':
                n = ramp_loads[s['id']]
                if n not in ramp_targets or n in seen:
                    result[s['id']] = {'row_status': 'omitted'}; continue
                seen.add(n); rx['target_load_g']=n; rx['target_reps'] = ramp_targets[n]; rx['target_rest_ms'] = 0
            elif s['set_kind']=='working':
                rx.update(target_load_g=target['load_g'], target_reps=target['target_reps'],
                          target_rest_ms=policy['rest.by_lift'][lift['exercise_id']]+policy['rest.setup_lead_ms'])
            result[s['id']] = rx
        return result

    def validate_actual(self, state, actuals):
        s = next((s for s in state['sets'] if s['id']==state['workout']['active_set_id']), None)
        require(s, 'STAGE', 'actual has no resolved row')
        lift = next(l for l in state['lifts'] if l['id']==s['lift_id'])
        require(actuals['unit']==s['target_unit'], 'UNIT', 'actual dose unit mismatch')
        require(not any(e['kind']=='pain' and json.loads(e['value']).get('lift_id')==lift['id'] for e in state['evidence']),
                'PAIN', 'pain-constrained lift cannot be logged')
        load = actuals['load_g']
        require((load is None and s['target_load_g'] is None) or load in self.grid(state, lift),
                'EQUIPMENT', 'actual load is not on the known equipment grid')

    def resolve(self, state, command, policy):
        a = command['args']; event = command['event_type']; sid = command['session_id']
        if event in ('free_lift', 'substitute', 'choose_split', 'choose_post'):
            if event=='free_lift' and 'variant_id' not in a:
                candidates=[v for v,c in self.catalogs.items() if c['exercise_id']==a['exercise_id'] and c['review_status']=='approved']
                require(len(candidates)==1,'CATALOG','pain alternative needs one resolved approved variant')
                a=dict(a,variant_id=candidates[0])
            variants = (self.config['splits'][a['split_id']] if event=='choose_split' else
                        self.config['post'][a['kind']] if event=='choose_post' else [a['variant_id']])
            plan=self.make_plan(self.conn, command, policy, variants)
            if event=='choose_post':
                doses=[s for l in plan for s in l['sets']]
                require(all(s['set_kind']=='post' and s['target_unit']=='seconds' for s in doses) and a['duration_ms']%1000==0,
                        'POST','post duration requires whole seconds')
                if len(doses)==1:
                    doses[0]['target_count']=doses[0]['target_hold_sec']=a['duration_ms']//1000
                else:
                    require(sum(s['target_count']*1000 for s in doses)==a['duration_ms'], 'POST', 'post sequence duration does not match choice')
            return {'plan': plan}
        if event=='claim_split':
            needed = set(self.config['splits'][a['split_id']])
            done = {l['variant_id'] for l in state['lifts'] if any(s['lift_id']==l['id'] and s['row_status']=='logged' and s['set_kind']=='working' for s in state['sets'])}
            require(needed <= done, 'CLAIM', 'logged sets do not cover requested split')
            return {'covered_split_id': a['split_id']}
        if event=='missing_weight':
            return {}
        lift = next(l for l in state['lifts'] if l['id']==a['lift_id'])
        variant = a.get('variant_id', lift['variant_id'])
        eq = self.equipment(sid, variant, command['command_id'], a.get('machine_ref'))
        facts = dict(catalog=self.catalog(variant), equipment=eq,
                     machine_capabilities=self.config['machines'].get(eq['machine_ref'], {}).get('capabilities', []))
        if event=='photo_result':
            evidence = self.config.get('photo_evidence',{}).get(a['evidence_id'])
            require(evidence, 'EVIDENCE', 'photo requires verified equipment evidence')
            facts['evidence'] = evidence
        return facts

    def legacy_start(self, conn, command):
        # Kit rotation uses session_type_id; the pack reader must use phase for
        # lifecycle. No filesystem .active-session authority is reintroduced.
        split = command['args'].get('split_id')
        if command['args']['mode']=='regular' and split is None:
            from datetime import datetime
            from kit_life import rotation_current
            today=datetime.fromtimestamp(self.clock()/1000,ZoneInfo(self.config['timezone'])).date()
            split=rotation_current(conn=conn,now=today)['next_split']
            require(split, 'SPLIT', 'kit rotation has no active split')
        if split:
            Writer.split(conn, command['session_id'], split)
            conn.execute('UPDATE session_input_state SET split_id=? WHERE session_id=?',(split,command['session_id']))

    def legacy_close(self, conn, command, state):
        sid = command['session_id']
        meta = row(conn, 'SELECT started_at FROM session_input_state WHERE session_id=?', (sid,))
        w=state['workout'];note=w['note'] or ''
        if w['kcal_status']=='unmeasured' and '[unmeasured]' not in note:
            note=(note.rstrip()+' [unmeasured]').strip()
        elif w['kcal_status']=='measured':
            note=note.replace('[unmeasured]','').strip()
        update(conn, 'workouts', sid, {'minutes': max(0, w['closed_at']-meta['started_at'])/60000,
                                     'note':note or None})
        import kit_life
        from workout_history import reconcile_completed
        reconcile_completed(kit_life, conn, w['date'], session_id=sid)

    def approve(self, conn, command, state, policy):
        receipt = json.loads(row(conn, 'SELECT receipt_json FROM session_commands WHERE session_id=? AND committed_revision=?',
                                 (command['session_id'], command['expected_revision']))['receipt_json'])
        self.before = project(conn, self, state, receipt)
        d = self.trainer._checked_decision(command['args']['decision'], self.before, self.templates)
        return self.trainer._approval(d, self.before, self.templates, f"{command['session_id']}:{command['command_id']}:prepared").to_dict()

    def bind_approval(self, conn, command, state, policy):
        receipt = receipt_for(state, command)
        after = project(conn, self, state, receipt)
        return self.trainer.bind_committed(command, self.before, receipt, after, self.templates,
                                           f"{command['session_id']}:{command['command_id']}:bound").to_dict()

    def rebind_rest(self, conn, command, before, after, approval):
        old_receipt = json.loads(row(conn, 'SELECT receipt_json FROM session_commands WHERE session_id=? AND committed_revision=?',
                                (command['session_id'], command['expected_revision']))['receipt_json'])
        old = project(conn, self, before, old_receipt)
        receipt = receipt_for(after, command)
        new = project(conn, self, after, receipt)
        if new['phase']!='ready' or old['dependencies']!=new['dependencies']:
            return None
        return self.trainer.rebind_rest_end(approval, old, receipt, new, self.templates,
                                            f"{command['session_id']}:{command['command_id']}:rest-bound").to_dict()

    def import_resolution(self, conn, command, state, policy):
        resolution = self.config.get('imports', {}).get(str(command['session_id']))
        require(resolution, 'IMPORT', 'reviewed reconciliation export is required')
        for item in resolution['lifts']:
            lift = item['plan']['lift']; catalog=self.catalog(lift['variant_id'])
            require(lift['exercise_id']==catalog['exercise_id'] and lift['exercise_name']==catalog['display_name'],
                    'IMPORT', 'reviewed identity differs from pinned catalog')
            eq=EquipmentSnapshot(item['equipment']).to_dict()
            require(eq['session_id']==command['session_id'] and eq['variant_id']==lift['variant_id'] and
                    eq['catalog_version']==self.config['catalog_version'] and
                    all(eq[k]==catalog[k] for k in ('load_basis','implement_count')) and
                    eq['id']==lift['implement_resolution_id'], 'IMPORT', 'equipment reconciliation mismatch')
            conn.execute('INSERT INTO session_equipment_snapshots('+','.join(eq)+') VALUES('+','.join('?' for _ in eq)+')',tuple(eq.values()))
            for s in item['plan']['sets']:
                require(s['equipment_snapshot_id']==eq['id'], 'IMPORT', 'planned equipment mismatch')
                require(s['target_load_g'] is None or s['target_load_g'] in self.grid(state,lift),
                        'IMPORT', 'reconciled load not on pinned grid')
                if s['set_kind']=='working':
                    self.target(state,lift,policy,load=s['target_load_g'],reps=s['target_reps'],previous=s['target_load_g'])
                    require(s['target_rest_ms']==policy['rest.by_lift'][catalog['exercise_id']]+policy['rest.setup_lead_ms'],
                            'IMPORT', 'reconciled rest differs from pinned policy')
        return resolution


def private_json(path):
    path = Path(path)
    st = path.stat()
    require(st.st_uid == os.getuid() and st.st_mode & 0o077 == 0, 'CONFIG', 'service configuration must be owner-private')
    from session_delivery import decode
    return decode(path.read_text())


def read_runtime_receipt(config):
    """Read only the installed framework seat, before a writer transaction."""
    path = Path(config['installed_root'])/'config/kit_life/session-v2.runtime-receipt.json'
    try:
        require(path.is_file() and not path.is_symlink(), 'CAPABILITY',
                'session-open runtime receipt missing or not a regular file')
        return private_json(path)
    except (OSError, ValueError, TypeError) as error:
        raise CommandError('CAPABILITY', 'invalid session-open runtime receipt: '+str(error)) from error


def executing_kit_files(root):
    """Installer and verifier share the complete executing kit inventory."""
    root = Path(root)
    files = [root/'service/kit_life/session-command', root/'database/schema.sql']
    files += [root/'database'/name for name in ('session_command.py', 'session_policy.py',
              'session_projection.py', 'session_reconcile.py', 'session_authority.py', 'session_delivery.py',
              'session_program.py', 'session_instance_members.py', 'kit_life.py',
              'workout_history.py')]
    files += [root/'database/session_kit_binding'/name for name in ('__init__.py', 'config_install.py', 'program_runtime.py')]
    files += [p for p in (root/'database/session_v2_contract').iterdir() if p.is_file()]
    return [p.resolve() for p in files]


def installed_configuration():
    # The installed seat fixes the root/config/DB. A child environment cannot
    # substitute a different private configuration and mint its own authority.
    installed_root = Path(__file__).resolve().parents[2]
    config_path = installed_root/'config/kit_life/session-v2.json'
    require(config_path.is_file(), 'CONFIG', 'installed private session configuration is required')
    config = private_json(config_path)
    from session_program import guard_configuration
    guard_configuration(config)
    require(Path(config['installed_root']).resolve() == installed_root and
            Path(config['db_path']).resolve() == (installed_root/'database/kit_life.db').resolve(),
            'CONFIG', 'configuration differs from the installed root/shared DB')
    require(configuration_digest(config)==config['config_digest'], 'CONFIG', 'installed configuration digest changed')
    key = bytes.fromhex(config['service_key_hex'])
    require(len(key)>=32, 'CONFIG', 'service key must be at least 32 bytes')
    # Receipt is signed by the same privileged installer/bridge seat. The raw
    # envelope may only refer to its ID, never supply a self-attested receipt.
    attestation = {k: config[k] for k in ('challenge', 'capability_receipt', 'legacy_retirement', 'installation_closure')}
    require(hmac.compare_digest(hmac.new(key, canonical(attestation).encode(), hashlib.sha256).hexdigest(),
                                config['capability_signature']), 'CAPABILITY', 'invalid bridge attestation')
    receipt = _program_capability_response(config['challenge'], config['capability_receipt'],
                                           config.get('required_features', ()))
    require(receipt['artifacts'] == config['artifact_digests'] and
            receipt['config_digest'] == config['config_digest'] and
            receipt['installed_root'] == config['installed_root'],
            'CAPABILITY', 'receipt does not identify this installed bundle')
    require(type(config['artifact_files']) is dict and
            set(config['artifact_files']) == {'pack', 'kit', 'framework'},
            'ARTIFACT', 'artifact_files requires pack/kit/framework; installer comes from capability receipt')
    for artifact in config['files']:
        actual = 'sha256:'+hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest()
        require(actual==artifact['digest'], 'ARTIFACT', 'installed file changed: '+artifact['path'])
    manifest = {a['path']: a['digest'] for a in config['files']}
    for owner in ('pack','kit','framework'):
        paths = config['artifact_files'][owner]
        require(paths and all(p in manifest for p in paths) and
                digest({p:manifest[p] for p in sorted(paths)})==config['artifact_digests'][owner],
                'ARTIFACT', owner+' manifest differs from capability receipt')
    for resolution in config.get('imports',{}).values():
        require(all(manifest.get(s['path'])==s['digest'] for s in resolution['sources']),
                'ARTIFACT', 'reconciliation sources are not pinned')
    pack = Path(config['pack_v2_path']).resolve()
    pinned = {str(Path(a['path']).resolve()) for a in config['files']}
    require(all(str(p.resolve()) in pinned for p in pack.iterdir() if p.suffix in ('.py', '.json')) and
            str(Path(config['templates_path']).resolve()) in pinned, 'ARTIFACT', 'pack/compiler/copy is not fully pinned')
    kit_files=executing_kit_files(installed_root)
    require({str(p.resolve()) for p in kit_files}<={str(Path(p).resolve()) for p in config['artifact_files']['kit']},
            'ARTIFACT','executing kit writer and contracts are not pinned to the receipt')
    if config.get('schema') == 'session_program_facts/2':
        from session_kit_binding.program_runtime import preflight
        preflight(config)
    return config, key


def authenticated_principal(command, auth, key):
    """Verify signed command identity before any local invocation preparation.

    A null/empty attempt requests kit preparation, not admission. Expiry and
    current activation are checked by the writer before preparing or applying.
    Recovery may verify an expired original credential to read its receipt.
    """
    from session_delivery import decode
    if type(auth) is str:
        auth = decode(auth)
    require(type(auth) is dict, 'AUTH', 'signed caller context required')
    principal = auth.get('context', {})
    from session_delivery import fields, integer, string, strict_json
    fields(auth, 'context signature')
    fields(principal, 'actor session_id member_id chat_id source_kinds expires_at command_digest attempt')
    strict_json(principal)
    integer(principal['session_id'], 1); integer(principal['expires_at'])
    for name in ('actor','member_id','chat_id','command_digest'): string(principal[name])
    require(principal['attempt'] is None or type(principal['attempt']) is dict,
            'AUTH', 'invalid caller attempt')
    require(type(principal['source_kinds']) is list and principal['source_kinds'] and
            all(type(k) is str for k in principal['source_kinds']) and
            len(set(principal['source_kinds'])) == len(principal['source_kinds']),
            'AUTH_FAILED', 'invalid caller source kinds')
    expected = hmac.new(key, canonical(principal).encode(), hashlib.sha256).hexdigest()
    require(type(auth['signature']) is str and hmac.compare_digest(expected, auth['signature']),
            'AUTH', 'invalid caller signature')
    require(principal.get('command_digest')==digest(dict(command)), 'AUTH', 'caller signature does not bind command')
    return principal


def authenticated_writer(command):
    config, key = installed_configuration()
    principal = authenticated_principal(command, os.environ.get('LIFEKIT_SESSION_CALLER', '{}'), key)
    pack = Path(config['pack_v2_path']).resolve()
    runtime = runtime_for(config, principal, load_pack(pack))
    if command['event_type'] == 'start':
        runtime.runtime_receipt = read_runtime_receipt(config)
    runtime.activation_authority = activation_authority(config)
    runtime.delivery_authority = None
    if config.get('delivery'):
        from session_delivery import DeliveryError
        try:
            runtime.delivery_authority = delivery_authority(config, key, dict(operation='command_bind', args=dict(envelope_id=command['source_ref']['ref'])))
        except DeliveryError:
            # Pre-birth start is the only command that may be unbound (no
            # envelope export yet); every other command still fails closed.
            if command['event_type'] != 'start':
                raise
    return Writer(runtime, clock=runtime.clock, token_key=key, local_timezone=ZoneInfo(config['timezone'])), config['db_path']


class OriginalCommandRecovery:
    """Trusted process seat for the framework's original-command dependency.

    Construct only from verified installation facts (or use the factory below).
    Reads accept the original signed credential after expiry; they never grant
    a new invocation. Unknown/in-flight commands remain pending. Committed
    recovery imports the durable acknowledgement without executing any command.
    """
    def __init__(self, config, key):
        self.config, self.key = config, key
        self.activation = activation_authority(config)

    def _binding(self, binding, delivery, record=None):
        from session_delivery import scope_shape, decode
        from session_program import ProgramCommand
        require(type(binding) is dict, 'AUTH', 'original command binding required')
        for name in ('command_bytes', 'command_digest', 'binding_id'):
            require(type(binding.get(name)) is str and binding[name], 'AUTH', 'missing original '+name)
        command = ProgramCommand(decode(binding['command_bytes']))
        require(command.to_json() == binding['command_bytes'] and
                digest(command) == binding['command_digest'], 'AUTH', 'original command bytes differ')
        principal = authenticated_principal(command, binding.get('caller'), self.key)
        s, p = binding.get('S'), binding.get('P')
        scope_shape(s)
        delivery.scope(s, p)
        delivery.command_envelope(command, s, p)
        require(command['session_id'] == binding.get('session_id') == s['session_id'] == principal['session_id'] and
                command['actor'] == principal['actor'] == p['actor'] and
                principal['member_id'] == p['member_id'] and str(s['chat_id']) == principal['chat_id'] and
                p['publisher_id'] == self.config['activation']['pack'] and
                binding.get('source_ref') == command['source_ref'] and
                command['source_ref']['kind'] in principal['source_kinds'],
                'AUTH', 'original scope/principal/source differs')
        source = command['source_ref']
        require(source.get('member_id', p['member_id']) == p['member_id'] and
                source.get('chat_id', str(s['chat_id'])) == str(s['chat_id']),
                'AUTH', 'original source identity differs')
        if record is not None:
            envelope, owner = record['envelope'], record['principal']
            surface = envelope.get('surface')
            require(record['pack'] == p['publisher_id'] and
                    record['generation'] == self.config['activation']['generation'] and
                    (owner['actor'], owner['member_id'], owner['chat_id']) ==
                    (p['actor'], p['member_id'], s['chat_id']) and
                    envelope['chat_id'] == s['chat_id'] and
                    type(surface) is dict and surface.get('surface_id') == s['surface_id'] and
                    surface.get('epoch') == s['epoch'] and
                    source['ref'] == envelope['envelope_id'] and
                    record['envelope_digest'] == digest(envelope),
                    'AUTH', 'original ingress differs')
        return command

    def _lookup(self, conn, binding, command, envelope):
        from session_delivery import stored_scope
        installed = self.config['activation']
        fence = row(conn, 'SELECT * FROM session_pack_fences WHERE pack=?', (installed['pack'],))
        require(fence and fence['active_engine'] == installed['engine'] and
                fence['active_generation'] == installed['generation'] and
                fence['closure'] == canonical(self.config['installation_closure']),
                'FENCE_STALE', 'original activation is closed or obsolete')
        self.activation.validate_closure(conn, json.loads(fence['closure']),
            json.loads(fence['fence']), json.loads(fence['checkpoint']))
        old = row(conn, 'SELECT * FROM session_live_commands WHERE binding_id=?', (binding['binding_id'],))
        require(old, 'NOT_FOUND', 'original command is unknown')
        require(old['scope'] == stored_scope(conn, binding['S']) and
                old['session_id'] == command['session_id'] and
                old['principal'] == canonical(binding['P']) and
                old['envelope_id'] == command['source_ref']['ref'] and
                old['command_digest'] == binding['command_digest'] and
                old['command_json'] == binding['command_bytes'] and
                old['envelope_digest'] == envelope['envelope_digest'],
                'AUTH', 'stored original binding differs')
        require(old['state'] in ('bound', 'committed', 'rejected'), 'SURFACE_PENDING', 'original state unresolved')
        result = dict(binding, state=old['state'], invocation_started=bool(old['invocation_started']),
            invocation_generation=old['invocation_generation'], command_bytes=old['command_json'],
            command=command.to_dict(), receipt_bytes=old['receipt'],
            receipt=json.loads(old['receipt']) if old['receipt'] else None,
            acknowledgement_bytes=old['acknowledgement'],
            ack=json.loads(old['acknowledgement']) if old['acknowledgement'] else None,
            delivery=json.loads(old['delivery']) if old['delivery'] else None)
        if old['state'] == 'committed':
            require(result['receipt'] is not None and result['ack'] is not None,
                    'SURFACE_PENDING', 'original committed receipt unavailable')
            receipt = result['receipt']
            prior = row(conn, 'SELECT * FROM session_commands WHERE session_id=? AND command_id=?',
                (command['session_id'], receipt['command_id']))
            require(prior and prior['receipt_json'] == old['receipt'] and
                    (receipt['command_id'] != command['command_id'] or
                     prior['input_digest'] == binding['command_digest']) and
                    result['ack']['receipt'] == receipt,
                    'CONFLICT', 'original receipt journals differ')
            result['outbox'] = self.activation.committed_outbox(conn, receipt)
        return result

    def _read(self, binding, record=None):
        # Read and verify installation-owned exports before the DB lock. This
        # also supports scope_template installations without fixed session IDs.
        require(type(binding) is dict and type(binding.get('source_ref')) is dict,
                'AUTH', 'original source binding required')
        delivery = delivery_authority(self.config, self.key, dict(operation='command_bind',
            args=dict(envelope_id=binding['source_ref']['ref'])))
        command = self._binding(binding, delivery, record)
        from session_authority import transaction
        with self.activation.connection() as conn, transaction(conn):
            return self._lookup(conn, binding, command, delivery.envelopes[0])

    def validate_binding(self, binding, record):
        self._read(binding, record)
        return True

    def command_lookup(self, record):
        return self._read(record['command_binding'], record)

    def recover_original(self, record, path, original):
        # The framework authenticates the ingress file/path. This authority
        # re-reads the DB under the writer lock and trusts no supplied outcome.
        current = self.command_lookup(record)
        require(all(current[k] == original.get(k) for k in
                    ('binding_id', 'command_digest', 'command_bytes', 'S', 'P')),
                'AUTH', 'recovery original changed')
        require(current['state'] in ('committed', 'rejected') and current['ack'] is not None,
                'SURFACE_PENDING', 'original invocation outcome pending')
        return current

    def committed_snapshot(self, original):
        current = self._read(original)
        require(current['state'] == 'committed', 'SURFACE_PENDING', 'original commit pending')
        require(all(current[k] == original.get(k) for k in ('receipt', 'receipt_bytes')),
                'AUTH', 'original receipt changed')
        return current


def original_command_recovery():
    """Installed host factory; no environment or request can select a DB/key."""
    config, key = installed_configuration()
    return OriginalCommandRecovery(config, key)


def authenticated_callback(raw):
    # Native bridge resolves a keyboard selector to the exact signed envelope;
    # the service never interprets callback bytes as executable arguments.
    from session_v2_contract.commands import CommandEnvelope
    command = CommandEnvelope.from_json(raw).to_dict()
    require(command['source_ref']['kind']=='callback', 'AUTH', 'tap requires native callback context')
    writer, path = authenticated_writer(command)
    return writer, path, command


def delivery_authority(config, service_key, request=None):
    """Read installation-owned keys/evidence before any DB transaction.

    Export files are separate authenticated framework evidence. Signing a
    delivery request does not turn a guessed transport result into a receipt.
    """
    from session_delivery import Authority, DeliveryError, fields, need, scope_shape, decode, installed_scope, hash_value
    c = config['delivery']
    fields(c, 'publisher_id scopes', 'coordinator recovery_authorities scope_template')
    publisher = c['publisher_id']
    need(isinstance(publisher, str) and __import__('re').fullmatch('[a-z][a-z0-9_]{1,40}', publisher), 'AUTH_FAILED')
    base = Path(config['installed_root'])/'.telegram_bot/live-surface'
    key_path = base/'publishers'/(publisher+'.key')
    try:
        st = key_path.stat()
    except FileNotFoundError as error:
        raise DeliveryError('AUTH_FAILED') from error
    need(st.st_uid == os.getuid() and st.st_mode & 0o077 == 0, 'AUTH_FAILED')
    key = key_path.read_bytes()
    need(len(key) >= 32, 'AUTH_FAILED')
    from session_v2_contract.transport import SURFACE_KINDS
    need(type(c['scopes']) is list and (c['scopes'] or c.get('scope_template')), 'AUTH_FAILED')
    scopes = [installed_scope(scope) for scope in c['scopes']]
    for scope in scopes:
        fields(scope, 'S session_id member_id actors slots action_slot transient_slots')
        scope_shape(scope['S'])
        need(type(scope['session_id']) is int and scope['session_id'] > 0, 'AUTH_FAILED')
        need(type(scope['slots']) is dict and scope['action_slot'] in scope['slots'] and
             scope['slots'][scope['action_slot']] == 'trainer' and
             len(set(scope['slots'].values())) == len(scope['slots']) and
             all(v in SURFACE_KINDS for v in scope['slots'].values()), 'AUTH_FAILED')
    envelopes, receipts, evidence, selections, invocations = [], [], [], [], []
    recovery_keys = {}
    exports = []
    for authority_id in c.get('recovery_authorities', []):
        need(type(authority_id) is str and __import__('re').fullmatch('[a-z][a-z0-9_]{1,40}', authority_id), 'AUTH_FAILED')
        path = base/'recovery-authorities'/(authority_id+'.key')
        try:
            st = path.stat()
            need(st.st_uid == os.getuid() and st.st_mode & 0o077 == 0, 'AUTH_FAILED')
            recovery_keys[authority_id] = path.read_bytes()
        except FileNotFoundError as error:
            raise DeliveryError('AUTH_FAILED') from error
        need(len(recovery_keys[authority_id]) >= 32, 'AUTH_FAILED')
    def export(kind, identity, by_digest=False, optional=False):
        if by_digest: hash_value(identity)
        path = base/'kit-exports'/publisher/(kind+'-'+(identity[7:] if by_digest else digest(identity)[7:])+'.json')
        try:
            st = path.stat()
            need(st.st_uid == os.getuid() and st.st_mode & 0o077 == 0, 'AUTH_FAILED')
            raw = path.read_bytes()
            data = decode(raw)
        except FileNotFoundError as error:
            if optional: return None
            raise DeliveryError('AUTH_FAILED' if kind == 'envelope' else 'RECEIPT_MISMATCH') from error
        need(type(data) is dict and type(data.get('mac')) is str and
             __import__('re').fullmatch('[0-9a-f]{64}', data['mac']) is not None, 'AUTH_FAILED')
        fields(data, 'kind value mac')
        need(data['kind'] == kind, 'AUTH_FAILED')
        signature = hmac.new(key, canonical(dict(kind=kind, value=data['value'])).encode(), hashlib.sha256).hexdigest()
        need(hmac.compare_digest(signature, data['mac']), 'AUTH_FAILED')
        if by_digest:
            need(digest(data if kind == 'receipt' else data['value']) == identity, 'RECEIPT_MISMATCH')
            if kind == 'receipt': need(raw == canonical(data).encode('utf-8'), 'RECEIPT_MISMATCH')
        elif kind in ('receipt','selection'): need(data['value'] == identity, 'RECEIPT_MISMATCH')
        exports.append(data)
        return data['value']
    if request:
        a = request['args']
        if request['operation'] in ('selector_resolve', 'command_bind'):
            envelopes.append(export('envelope', a['envelope_id']))
        if request['operation'] == 'command_bind':
            proof = export('invocation', a['envelope_id'], optional=True)
            if proof is not None: invocations.append(proof)
        if request['operation'] == 'delivery_select':
            selections.append(export('selection', a['selection']))
            receipts.append(export('receipt', a['selection']['trigger']['receipt_digest'], by_digest=True))
        for name in ('receipt', 'replacement_receipt', 'retirement_receipt'):
            if a.get(name) is not None: receipts.append(export('receipt', a[name]))
    template = c.get('scope_template')
    if template is not None and not c['scopes']:
        # Derive from verified publisher-MAC'd envelope exports only.
        fields(template, 'actors slots action_slot')
        scopes = []
        for e in envelopes:
            scope_shape(e['S'])
            need(e['P']['publisher_id'] == publisher, 'AUTH_FAILED')
            scopes.append(installed_scope(dict(template, S=e['S'], session_id=e['S']['session_id'],
                                               member_id=e['P']['member_id'])))
    if template is not None and not c['scopes'] and not envelopes and request:
        # Non-ingress operations have no envelope_id in their frozen ABI.
        # Recover only a scope already stored by an authenticated committed
        # command. Never grant a new scope from caller-supplied S/P alone.
        a = request['args']
        scope_shape(a['S'])
        candidates = []
        db_uri = Path(config['db_path']).resolve().as_uri() + '?mode=ro'
        conn = sqlite3.connect(db_uri, uri=True)
        try:
            saved = conn.execute('SELECT scope,session_id,publisher_id,member_id,slots,action_slot '
                'FROM session_live_scopes WHERE session_id=?', (a['S']['session_id'],)).fetchall()
        finally:
            conn.close()
        for raw, sid, owner, member, slots, action in saved:
            if (owner != publisher or member != a['P']['member_id'] or
                    json.loads(slots) != template['slots'] or action != template['action_slot']):
                continue
            candidate = installed_scope(dict(template, S=json.loads(raw), session_id=sid, member_id=member))
            if candidate['S'] == a['S']:
                candidates.append(candidate)
        need(len(candidates) == 1, 'AUTH_FAILED')
        scopes = candidates
    for receipt in receipts:
        if receipt.get('evidence_digest') is not None:
            evidence.append(export('evidence', receipt['evidence_digest'], by_digest=True))
    return Authority(publisher, key, scopes, service_key, envelopes=envelopes,
                     receipts=receipts, coordinator=c.get('coordinator', False), evidence=evidence,
                     selections=selections, recovery_keys=recovery_keys, invocations=invocations, exports=exports)


def authenticated_delivery(request):
    """Installed service/artifact checks supplement every publisher MAC."""
    from session_delivery import DeliveryError
    try:
        config, key = installed_configuration()
        require(config['capability_receipt']['config_digest'] == config['config_digest'], 'AUTH', 'receipt/config mismatch')
        return delivery_authority(config, key, request), config['db_path']
    except CommandError as error:
        raise DeliveryError('AUTH_FAILED') from error


def activation_authority(config):
    """Installed, pinned root only; no authority paths accepted from callers."""
    from session_authority import Authority
    from session_delivery import fields, string
    c = config.get('activation')
    fields(c, 'authority_id pack engine generation')
    from session_delivery import integer
    integer(c['generation'], 1)
    for name in ('authority_id','pack','engine'):
        string(c[name])
        require(__import__('re').fullmatch('[a-z][a-z0-9_-]{1,63}', c[name]), 'CONFIG', 'invalid authority identity')
    base = Path(config['installed_root'])/'.telegram_bot/live-surface'
    def key(path):
        st = path.stat()
        require(not path.is_symlink() and st.st_uid == os.getuid() and st.st_mode & 0o077 == 0,
                'CONFIG', 'authority key must be owner-private')
        return path.read_bytes()
    return Authority(config['db_path'], c['authority_id'],
        key(base/'recovery-authorities'/(c['authority_id']+'.key')),
        key(base/'publishers'/(c['pack']+'.key')), key(base/'coordinator.key'),
        installed_root=config['installed_root'],config_digest=config['config_digest'],
        capability_receipt_id=config['capability_receipt']['receipt_id'],
        clock=lambda:time.time_ns()//1_000_000)


def runtime_for(config, principal, trainer, **kwargs):
    if config.get('schema') == 'session_program_facts/2':
        from session_kit_binding.program_runtime import SlotRuntime
        return SlotRuntime(config, principal, trainer, **kwargs)
    return KitRuntime(config, principal, trainer, **kwargs)
