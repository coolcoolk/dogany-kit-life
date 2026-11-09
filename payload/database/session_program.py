"""Strict lossless program contracts, separate from the /1 runtime adapter.

Validation is not capability admission, policy approval, or a calculator.
Execution additionally requires installed calculator/projection support, explicit
policy approval and native feature negotiation. Validation never grants those.
"""
import hashlib
import json
from zoneinfo import ZoneInfo

from session_v2_contract.policy import CatalogEntry
from session_v2_contract.schema import (BOOL, DIGEST, INT, JSON, POS, STR, TOKEN, UNIT,
    ValidationError, array, enum, mapping, nullable, obj, validate)

FACTS_SCHEMA = 'session_program_facts/2'
MANIFEST_SCHEMA = 'kit_session_configuration/2'
MINIMUM_SCHEMA = 84
# These are format features, NOT a claim that this writer can execute them.
FORMAT_FEATURES = frozenset(('slot_program/1', 'ordered_blocks/1',
    'prescription_hook/1', 'typed_doses/1', 'history_bindings/1',
    'verified_equipment/1', 'source_ranges/1', 'maintenance_hold/1',
    'v1_progression/1', 'v1_ramps/1', 'laterality/1', 'open_until_rir/1'))
BASE_FEATURES = frozenset(('slot_program/1', 'prescription_hook/1',
                          'typed_doses/1', 'verified_equipment/1'))
LOAD_BASIS = enum('total', 'per_hand', 'assistance')
LOAD_STATE = enum('known', 'unloaded', 'calibration_required', 'unprescribed')
BAND = array(INT, 2)
LATERALITY = obj({'side_order': array(enum('left', 'right')),
    'repetitions_per_side': nullable(POS), 'hold_repetitions': nullable(POS),
    'recording_mode': enum('bilateral', 'separate_sides', 'both_one_record', 'alternating')})
SOURCE = obj({'source_ids': array(STR, 1), 'source_digest': DIGEST,
    'normalization': array(STR), 'evidence_digest': DIGEST})
CONVERSION = obj({'source_basis': enum('per_side'), 'source_g': INT,
    'side_count': POS, 'tare_g': INT, 'canonical_basis': enum('total'),
    'formula': enum('source_g * side_count + tare_g'), 'provenance_ref': STR})
VERIFICATION = obj({'status': enum('verified', 'unverified', 'from_v1'),
    'observation_id': STR, 'owning_agent': STR, 'venue_ref': nullable(STR),
    'machine_ref': nullable(STR), 'implement_ref': nullable(STR),
    'basis': LOAD_BASIS, 'confirmed_at': nullable(INT),
    'valid_until': nullable(INT), 'supersedes': nullable(STR), 'digest': DIGEST})
EQUIPMENT = obj({'variant_id': STR, 'machine_ref': nullable(STR),
    'available_loads_g': array(INT), 'grid_version': STR, 'inventory_version': STR,
    'mounted_load_g': nullable(INT), 'verification': VERIFICATION,
    'provenance_ref': STR}, {'bar_g': INT, 'inventory_per_side': mapping(INT),
                            'conversion': CONVERSION,
                            'inherited': obj({'schema': enum('from_v1_equipment/1'),
                                'source_digest': DIGEST, 'rule_digest': DIGEST,
                                'available_loads_g': array(INT), 'evidence': mapping(JSON)})})
WORKING = obj({'repeat_sets': POS, 'unit': UNIT, 'range': nullable(BAND),
    'initial_target': nullable(POS), 'load_state': LOAD_STATE,
    'load_g': nullable(INT), 'load_basis': LOAD_BASIS,
    'laterality': LATERALITY, 'rir_band': nullable(BAND), 'rir_floor': nullable(INT),
    'designated_rest_ms': INT, 'provenance_ref': STR}, {
    'effective_target_envelope': obj({'version': enum('v1_reps_first/1'),
        'source_range': BAND, 'effective_range': BAND, 'rule_digest': DIGEST}),
    'effort_semantics': obj({'version': enum('v1_effort/1'), 'rule_digest': DIGEST})})
SLOT = obj({'variant_id': STR, 'equipment_ref': STR,
    'source_identity': obj({'routine_id': STR, 'split_id': STR,
                            'slot_number': POS, 'occurrence_index': INT}),
    'role': STR, 'working': WORKING, 'progression_ref': STR,
    'ramp_ref': STR, 'provenance_ref': STR})
ROW = obj({'row_ref': STR, 'source_ref': STR, 'slot_id': nullable(STR),
    'block_id': nullable(STR), 'set_kind': enum('prep', 'ramp', 'working', 'hold', 'post'),
    'step_order': INT, 'unit': UNIT, 'target_reps': nullable(POS),
    'target_count': nullable(POS), 'target_hold_sec': nullable(POS),
    'load_state': LOAD_STATE, 'load_g': nullable(INT), 'load_basis': LOAD_BASIS,
    'equipment_ref': STR, 'rir_band': nullable(BAND), 'rir_floor': nullable(INT),
    'target_rest_ms': INT, 'target_mode': enum('fixed', 'open_until_rir'),
    'laterality': LATERALITY, 'basis': obj({'decision': STR, 'reason': STR,
                                        'evidence_digest': DIGEST})}, {
    'stage': obj({'stage_id': STR, 'round_index': INT, 'source_row_ref': STR,
                  'rest_after_ms': INT, 'duration_ms': nullable(INT)})})
STAGE = obj({'stage_id': STR, 'duration_ms': nullable(INT), 'rest_ms': INT,
    'rounds': nullable(POS), 'rows': array(ROW, 1)})
BLOCK = obj({'block_id': STR, 'kind': enum('prep', 'finisher', 'circuit', 'cardio', 'cooldown'),
    'anchor': enum('session_start', 'before_slot', 'after_slot', 'after_lifts'),
    'anchor_slot_id': nullable(STR), 'required': BOOL, 'stages': array(STAGE, 1),
    'provenance_ref': STR})
CALCULATOR = obj({'family': STR, 'version': STR, 'artifact_path': STR,
    'code_digest': DIGEST, 'rule_digest': DIGEST, 'policy_ref': STR,
    'request_version': enum('prescription_request/1'),
    'response_version': enum('prescription_response/1'), 'required_inputs': array(STR)})
BINDING = obj({'legacy_session_ids': array(POS), 'owning_agent': STR,
    'attribution_evidence': array(STR, 1), 'attribution_digest': DIGEST}, {
    'instance_roster': obj({'schema': enum('instance_member_roster/1'),
        'members': array(STR), 'source_ref': STR,
        'method': enum('bridge_ownership_precedence/1'), 'source_digest': DIGEST})})
FACTS = obj({'schema': enum(FACTS_SCHEMA), 'catalog': array(CatalogEntry.SCHEMA, 1),
    'catalog_version': POS, 'machines': mapping(obj({'capabilities': array(STR, 1),
                                                  'available_loads_g': array(INT, 1)})),
    'timezone': STR, 'capability_max_age_ms': POS, 'program_slots': mapping(SLOT),
    'splits': mapping(obj({'slot_ids': array(STR, 1), 'blocks': array(BLOCK)})),
    'free_start': array(STR), 'free_blocks': array(BLOCK), 'equipment': mapping(EQUIPMENT),
    'calculators': mapping(CALCULATOR), 'provenance': mapping(SOURCE),
    'history_bindings': mapping(BINDING), 'required_features': array(STR, 1)})
FACT_KEYS = frozenset(FACTS['properties'])


def need(condition, path, message):
    if not condition:
        raise ValidationError(path + ': ' + message)


def unique(values, path):
    need(len(values) == len(set(values)), path, 'duplicate identity')


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=False, allow_nan=False).encode('utf-8')
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def inherited(eq):
    return eq['verification']['status'] == 'from_v1'


def target_range(working):
    envelope = working.get('effective_target_envelope')
    return envelope['effective_range'] if envelope else working['range']


def load_allowed(eq, load):
    # Empty inherited grids retain v1's ask-at-first-set path. A chosen load
    # still passes the pinned source calculator before it can become a target.
    return load is None or load in eq['available_loads_g'] or (
        inherited(eq) and not eq['available_loads_g'])


def check_band(band, path):
    if band is not None:
        need(len(band) == 2 and 0 < band[0] <= band[1], path, 'expected ordered positive range')


def check_effort(band, floor, path):
    if band is not None:
        need(len(band) == 2 and 0 <= band[0] <= band[1] <= 4,
             path, 'expected ordered RIR band within 0..4')
    need(floor is None or floor <= 4, path, 'invalid RIR floor')
    if floor is not None and band is not None:
        need(band[0] >= floor, path, 'RIR band violates floor')


def check_load(value, path):
    known = value['load_state'] == 'known'
    need((value['load_g'] is not None) == known, path,
         'known load needs integer grams; unloaded/calibration needs null')


def check_laterality(value, path):
    order = value['side_order']
    unique(order, path + '.side_order')
    mode = value['recording_mode']
    if mode == 'bilateral':
        need(not order and value['repetitions_per_side'] is None, path,
             'bilateral recording must not imply side expansion')
    else:
        # One completion record can preserve the source's unspecified side
        # order. Separate side rows need an actual ordering declaration.
        known_order = set(order) == {'left', 'right'}
        need((known_order or (not order and mode == 'both_one_record')) and
             value['repetitions_per_side'] is not None, path,
             'side recording/count/order must preserve the source declaration')


def check_row(row, path):
    validate(ROW, row, path)
    need((row['slot_id'] is None) != (row['block_id'] is None), path,
         'row must bind exactly one slot or block')
    check_load(row, path)
    check_effort(row['rir_band'], row['rir_floor'], path)
    check_laterality(row['laterality'], path + '.laterality')
    if row['unit'] == 'reps':
        need(row['target_reps'] is not None and row['target_count'] is None and
             row['target_hold_sec'] is None, path, 'rep target fields disagree')
    else:
        need(row['target_reps'] is None and row['target_count'] is not None, path,
             'counted target fields disagree')
        need(row['target_hold_sec'] == (row['target_count'] if row['unit'] == 'seconds' else None),
             path, 'hold target must equal seconds count')
    if row['set_kind'] == 'hold':
        need(row['unit'] == 'seconds', path, 'hold rows require seconds')
    if row['target_mode'] == 'open_until_rir':
        need(row['unit'] == 'reps' and row['rir_floor'] is not None, path,
             'open target requires reps and explicit RIR floor')
    return row


def validate_facts(facts):
    """Validate the complete /2 tree and all identities, without changing it."""
    validate(FACTS, facts)
    ZoneInfo(facts['timezone'])
    for key in ('program_slots', 'splits', 'equipment', 'calculators', 'provenance',
                'history_bindings', 'machines'):
        need(all(ref for ref in facts[key]), '$.' + key, 'empty reference identity')
    need(facts['splits'] or facts['free_start'] or facts['free_blocks'], '$', 'no explicit program composition')
    for ref, machine in facts['machines'].items():
        unique(machine['capabilities'], '$.machines.' + ref + '.capabilities')
        need(machine['available_loads_g'] == sorted(set(machine['available_loads_g'])),
             '$.machines.' + ref, 'machine pins must be sorted and unique')
    declared = facts['required_features']
    unique(declared, '$.required_features')
    need(set(declared) <= FORMAT_FEATURES, '$.required_features', 'unknown format feature')
    needed = set(BASE_FEATURES)
    catalogs = {}
    for entry in facts['catalog']:
        c = CatalogEntry(entry)
        variant = c['variant_id']
        need(variant not in catalogs, '$.catalog', 'duplicate variant')
        need(c['review_status'] == 'approved' and c['catalog_version'] == facts['catalog_version'],
             '$.catalog.' + variant, 'catalog must be approved and version-pinned')
        catalogs[variant] = c
    provenance = facts['provenance']

    def source(ref, path):
        need(ref in provenance, path, 'unknown provenance reference ' + ref)

    for ref, p in provenance.items():
        unique(p['source_ids'], '$.provenance.' + ref)
    for ref, calc in facts['calculators'].items():
        path = '$.calculators.' + ref
        parts = calc['artifact_path'].split('/')
        need(all(p not in ('', '.', '..') for p in parts) and
             not calc['artifact_path'].startswith('/'), path, 'calculator artifact must be root-relative')
        unique(calc['required_inputs'], path + '.required_inputs')
    for ref, eq in facts['equipment'].items():
        path = '$.equipment.' + ref
        source(eq['provenance_ref'], path)
        need(eq['variant_id'] in catalogs, path, 'unknown catalog variant')
        c = catalogs[eq['variant_id']]
        grid = eq['available_loads_g']
        need(grid == sorted(set(grid)), path, 'grid must be sorted and unique')
        v = eq['verification']
        need(v['machine_ref'] == eq['machine_ref'] and v['basis'] == c['load_basis'],
             path, 'verification scope/basis differs from equipment')
        if v['status'] == 'verified':
            need(v['confirmed_at'] is not None and
                 (v['machine_ref'] is not None or v['implement_ref'] is not None),
                 path, 'verified equipment requires observation time and physical scope')
            need(v['valid_until'] is None or v['valid_until'] >= v['confirmed_at'],
                 path, 'verification expires before observation')
        if inherited(eq):
            need('inherited' in eq, path, 'from_v1 requires pinned inherited evidence')
            old = eq['inherited']
            need('from_v1' in provenance[eq['provenance_ref']]['normalization'] and
                 old['source_digest'] == provenance[eq['provenance_ref']]['source_digest'],
                 path, 'inherited equipment lacks from_v1 source provenance')
            data = old['evidence']
            need(v['digest'] == fingerprint({k: value for k, value in data.items() if k != 'digest'}) and
                 data.get('provenance') == 'from_v1' and
                 old['available_loads_g'] == grid, path, 'inherited evidence/grid digest differs')
            need(v['confirmed_at'] is None and v['valid_until'] is None and
                 eq['mounted_load_g'] is None and 'inventory_per_side' not in eq,
                 path, 'inherited assumptions cannot claim measured inventory or mounted loads')
        else:
            need('inherited' not in eq, path, 'inherited evidence must retain its own evidence class')
        machine = eq['machine_ref']
        if machine is not None:
            need(machine in facts['machines'], path, 'unknown machine')
            m = facts['machines'][machine]
            need(set(c['compatible_machine_capabilities']) <= set(m['capabilities']),
                 path, 'machine lacks catalog capabilities')
            need(set(grid) <= set(m['available_loads_g']), path, 'grid exceeds registered machine pins')
        else:
            need(inherited(eq) or not c['compatible_machine_capabilities'], path, 'catalog requires a machine')
        if 'conversion' in eq:
            conversion = eq['conversion']
            source(conversion['provenance_ref'], path + '.conversion')
            need(c['load_basis'] == 'total' and conversion['side_count'] == 2,
                 path, 'symmetric per-side conversion requires two sides and total basis')
        if c['implement_kind'] == 'barbell' and not inherited(eq):
            need('bar_g' in eq and 'inventory_per_side' in eq, path,
                 'barbell requires explicit mass and counted symmetric inventory')
            for mass, count in eq['inventory_per_side'].items():
                need(mass.isascii() and mass.isdecimal() and str(int(mass)) == mass and int(mass) > 0,
                     path, 'plate denomination must be canonical positive integer grams')
        if eq['mounted_load_g'] is not None:
            need(eq['mounted_load_g'] in grid, path, 'mounted load is outside feasible grid')
    identities = []
    for sid, slot in facts['program_slots'].items():
        path = '$.program_slots.' + sid
        source(slot['provenance_ref'], path)
        source(slot['working']['provenance_ref'], path + '.working')
        need(slot['variant_id'] in catalogs, path, 'unknown catalog variant')
        need(slot['equipment_ref'] in facts['equipment'], path, 'unknown equipment reference')
        eq = facts['equipment'][slot['equipment_ref']]
        need(eq['variant_id'] == slot['variant_id'], path, 'equipment belongs to another variant')
        identities.append(fingerprint(slot['source_identity']))
        for key in ('progression_ref', 'ramp_ref'):
            need(slot[key] in facts['calculators'], path, 'unknown calculator ' + slot[key])
        working = slot['working']
        check_load(working, path + '.working')
        need(working['load_state'] != 'unprescribed', path,
             'unprescribed numeric loads are source block doses, not working calibration')
        check_band(working['range'], path + '.working.range')
        check_effort(working['rir_band'], working['rir_floor'], path + '.working')
        check_laterality(working['laterality'], path + '.working.laterality')
        need(working['load_basis'] == catalogs[slot['variant_id']]['load_basis'], path,
             'working load basis differs from catalog')
        for extension in ('effective_target_envelope', 'effort_semantics'):
            if extension in working:
                calc = facts['calculators'][slot['progression_ref']]
                need(inherited(eq) and working[extension]['rule_digest'] == calc['rule_digest'] and
                     eq['inherited']['rule_digest'] == calc['rule_digest'] and
                     calc['family'] in ('v1_progression', 'maintenance_hold'), path,
                     'source semantics require the pinned inherited v1 calculator')
        if 'effective_target_envelope' in working:
            e = working['effective_target_envelope']
            band = working['range']
            need(band is not None and e['source_range'] == band and
                 e['effective_range'] == [band[0], band[1] + 2], path,
                 'effective envelope must preserve the pinned v1 +2 rule and source range')
        band = target_range(working)
        if working['initial_target'] is not None and band is not None:
            need(band[0] <= working['initial_target'] <= band[1],
                 path, 'initial target outside source range')
        if working['range'] is not None:
            needed.add('source_ranges/1')
        if working['laterality']['recording_mode'] != 'bilateral':
            needed.add('laterality/1')
        for key in ('progression_ref', 'ramp_ref'):
            family = facts['calculators'][slot[key]]['family']
            if family == 'maintenance_hold': needed.add('maintenance_hold/1')
            if family == 'v1_progression': needed.add('v1_progression/1')
            if family == 'v1_ramps': needed.add('v1_ramps/1')
    unique(identities, '$.program_slots.source_identity')
    used = set()
    block_ids = []

    def roster(slots, blocks, path, split=None):
        unique(slots, path + '.slot_ids')
        need(set(slots) <= facts['program_slots'].keys(), path, 'unknown slot reference')
        used.update(slots)
        for sid in slots:
            if split is not None:
                need(facts['program_slots'][sid]['source_identity']['split_id'] == split,
                     path, 'slot source split differs from roster')
        if blocks: needed.add('ordered_blocks/1')
        for block in blocks:
            bp = path + '.blocks.' + block['block_id']
            block_ids.append(block['block_id'])
            source(block['provenance_ref'], bp)
            anchored = block['anchor'] in ('before_slot', 'after_slot')
            need((block['anchor_slot_id'] is not None) == anchored and
                 (not anchored or block['anchor_slot_id'] in slots), bp, 'invalid anchor slot')
            unique([s['stage_id'] for s in block['stages']], bp + '.stages')
            row_refs = []
            for stage in block['stages']:
                orders = []
                for r in stage['rows']:
                    rp = bp + '.rows.' + r['row_ref']
                    check_row(r, rp)
                    need('stage' not in r, rp, 'stage bindings are allocated by the writer')
                    source(r['source_ref'], rp)
                    row_refs.append(r['row_ref']); orders.append(r['step_order'])
                    need(r['block_id'] == block['block_id'] and r['slot_id'] is None,
                         rp, 'row belongs to another block')
                    need(r['equipment_ref'] in facts['equipment'], rp, 'unknown equipment reference')
                    need(r['load_state'] != 'unprescribed' or inherited(facts['equipment'][r['equipment_ref']]),
                         rp, 'unprescribed block load requires inherited source evidence')
                    need(r['load_basis'] == catalogs[facts['equipment'][r['equipment_ref']]['variant_id']]['load_basis'],
                         rp, 'row basis differs from catalog')
                    if r['target_mode'] == 'open_until_rir': needed.add('open_until_rir/1')
                    if r['laterality']['recording_mode'] != 'bilateral': needed.add('laterality/1')
                need(orders == list(range(len(orders))), bp, 'stage rows must have contiguous source order')
            unique(row_refs, bp + '.rows')
    for split, plan in facts['splits'].items():
        roster(plan['slot_ids'], plan['blocks'], '$.splits.' + split, split)
    roster(facts['free_start'], facts['free_blocks'], '$.free')
    need(used == facts['program_slots'].keys(), '$.program_slots', 'uncovered source slot')
    unique(block_ids, '$.blocks')
    bound = []
    for member, binding in facts['history_bindings'].items():
        if 'instance_roster' in binding:
            need(binding['instance_roster']['members'] == [member], '$.history_bindings.' + member,
                 'mechanical attribution requires exactly one matching instance member')
        unique(binding['legacy_session_ids'], '$.history_bindings.' + member)
        unique(binding['attribution_evidence'], '$.history_bindings.' + member)
        bound += binding['legacy_session_ids']
    unique(bound, '$.history_bindings')
    if bound: needed.add('history_bindings/1')
    need(needed <= set(declared), '$.required_features',
         'omitted features: ' + ', '.join(sorted(needed - set(declared))))
    return facts


def guard_configuration(config):
    if 'schema' not in config:
        need(not ({'program_slots', 'free_blocks', 'calculators', 'provenance',
                   'history_bindings', 'required_features'} & config.keys()),
             '$.schema', '/2 program fields require the exact version discriminator')
        return
    need(config['schema'] == FACTS_SCHEMA, '$.schema', 'unknown program facts version')
    need(not ({'program', 'post', 'legacy_member_sessions'} & config.keys()),
         '$', 'mixed /1 and /2 configuration')
    validate_executable({k: config[k] for k in FACT_KEYS if k in config})


def refuse_unimplemented(facts):
    """Never let a schema validator stand in for installed /2 execution."""
    validate_facts(facts)
    raise ValidationError('session_program_facts/2: installed execution unavailable: '
        'slot-pinned recalculation, audited v1 calculator, ordered block lifecycle, '
        'and native feature negotiation must land together')


def validate_history_bindings(conn, facts):
    """Check explicit associations against rows; never rewrite legacy history."""
    validate_facts(facts)
    for member, binding in facts['history_bindings'].items():
        if 'instance_roster' in binding:
            from session_instance_members import single_member_binding
            expected = single_member_binding(binding['instance_roster'], binding['owning_agent'],
                                             binding['legacy_session_ids'])
            need(expected == {member: binding}, '$.history_bindings', 'mechanical attribution evidence differs')
        for sid in binding['legacy_session_ids']:
            row = conn.execute('SELECT owning_agent,member_id,session_version FROM workouts WHERE id=?',
                               (sid,)).fetchone()
            need(row is not None, '$.history_bindings.' + member, 'missing legacy session ' + str(sid))
            need(row[0] == binding['owning_agent'] and row[1] in (None, member) and row[2] == 1,
                 '$.history_bindings.' + member, 'legacy session ownership/version mismatch ' + str(sid))

# Calculator payloads contain values and identities, never a database path.
HISTORY_ROW = obj({'session_id': POS, 'row_id': POS, 'routine_id': nullable(STR),
    'split_id': nullable(STR), 'slot_id': nullable(STR), 'owning_agent': STR,
    'member_id': nullable(STR), 'machine_ref': nullable(STR), 'recorded_at': STR,
    'row_status': enum('logged', 'void', 'omitted', 'planned'),
    'set_kind': enum('prep', 'ramp', 'working', 'hold', 'post'), 'unit': UNIT,
    'load_basis': LOAD_BASIS, 'load_g': nullable(INT), 'reps': nullable(INT),
    'count': nullable(INT), 'hold_sec': nullable(INT), 'rir': nullable(INT),
    'condition': nullable(STR), 'stop_reason': nullable(STR),
    'attribution_digest': DIGEST}, {'rir_is_lower_bound': BOOL, 'session_date': STR})
REQUEST = obj({'schema': enum('prescription_request/1'), 'member_id': STR,
    'session_id': POS, 'split_id': nullable(STR), 'slot_id': STR, 'template': WORKING,
    'calculator_ref': STR, 'code_digest': DIGEST, 'rule_digest': DIGEST,
    'policy_bundle_id': STR, 'policy_digest': DIGEST, 'equipment': EQUIPMENT,
    'history': array(HISTORY_ROW), 'current_rows': array(ROW), 'inputs': mapping(JSON),
    'reason': enum('start', 'load_change', 'reps_change', 'machine_change', 'undo',
                   'correction', 'reclassification', 'remeasurement'),
    'dependencies_digest': DIGEST})
RESPONSE = obj({'schema': enum('prescription_response/1'), 'request_digest': DIGEST,
    'dependencies_digest': DIGEST, 'calculator_ref': STR,
    'code_digest': DIGEST, 'rule_digest': DIGEST, 'rows': array(ROW, 1),
    'ramp_decision': obj({'decision': enum('ramp', 'none'), 'reason': STR})}, {'transition': obj({'schema': enum('source_topology/1'),
        'rule_digest': DIGEST, 'working_sets': POS, 'reason': STR})})


def validate_request(request, facts):
    validate_facts(facts)
    validate(REQUEST, request)
    sid = request['slot_id']
    need(sid in facts['program_slots'], '$.slot_id', 'unknown slot')
    slot = facts['program_slots'][sid]
    need(request['template'] == slot['working'], '$.template', 'request differs from pinned template')
    need(request['equipment'] == facts['equipment'][slot['equipment_ref']],
         '$.equipment', 'request differs from pinned equipment')
    ref = request['calculator_ref']
    need(ref in (slot['progression_ref'], slot['ramp_ref']), '$.calculator_ref', 'undeclared slot calculator')
    calc = facts['calculators'][ref]
    for key in ('code_digest', 'rule_digest'):
        need(request[key] == calc[key], '$.' + key, 'stale calculator dependency')
    need(request['policy_bundle_id'] == calc['policy_ref'], '$.policy_bundle_id', 'unapproved policy reference')
    missing = [key for key in calc['required_inputs']
               if key not in request['inputs'] or request['inputs'][key] is None]
    need(not missing, '$.inputs', 'missing declared inputs: ' + ', '.join(missing))
    expected_split = slot['source_identity']['split_id']
    need(request['split_id'] in (None, expected_split), '$.split_id', 'slot belongs to another split')
    bindings = facts['history_bindings'].get(request['member_id'])
    unique([h['row_id'] for h in request['history']], '$.history.row_id')
    for h in request['history']:
        need(not h.get('rir_is_lower_bound') or h['rir'] == 4, '$.history', 'RIR lower bound requires 4+')
        need(h['session_id'] != request['session_id'], '$.history', 'current session belongs in current_rows')
        if bindings and h['session_id'] in bindings['legacy_session_ids']:
            need(h['owning_agent'] == bindings['owning_agent'] and
                 h['attribution_digest'] == bindings['attribution_digest'],
                 '$.history', 'legacy history attribution differs from binding')
        need(h['member_id'] == request['member_id'] or
             (h['member_id'] is None and bindings is not None and
              h['session_id'] in bindings['legacy_session_ids'] and
              h['owning_agent'] == bindings['owning_agent'] and
              h['attribution_digest'] == bindings['attribution_digest']),
             '$.history', 'history has no authorized member attribution')
    for r in request['current_rows']:
        check_row(r, '$.current_rows')
        need(r['slot_id'] == sid and r['block_id'] is None, '$.current_rows', 'current row belongs to another slot')
    return request


def validate_response(response, request, facts, *, approved_rir_floor):
    """Validate complete hook output before the caller allocates any DB rows.

    The caller must independently verify policy approval and physical evidence.
    Matching a request digest is not an authorization to run a calculator.
    """
    validate_request(request, facts)
    validate(RESPONSE, response)
    need(type(approved_rir_floor) is int and 0 <= approved_rir_floor <= 4,
         '$.rir_floor', 'approved policy floor is required')
    need(response['request_digest'] == fingerprint(request), '$.request_digest', 'response is for another request')
    for key in ('dependencies_digest', 'calculator_ref', 'code_digest', 'rule_digest'):
        need(response[key] == request[key], '$.' + key, 'stale or mismatched response dependency')
    slot = facts['program_slots'][request['slot_id']]
    template = slot['working']
    equipment = request['equipment']
    need(equipment['verification']['status'] == 'verified' or inherited(equipment), '$.equipment', 'unverified physical facts')
    all_rows = response['rows']
    unique([r['row_ref'] for r in all_rows], '$.rows.row_ref')
    need([r['step_order'] for r in all_rows] == list(range(len(all_rows))),
         '$.rows', 'rows must preserve contiguous order')
    transition = response.get('transition')
    if transition:
        need(inherited(equipment) and 'effort_semantics' in template and
             transition['rule_digest'] == request['rule_digest'] == template['effort_semantics']['rule_digest'] and
             request['reason'] != 'start' and
             any(a['row_status'] == 'logged' for a in request['inputs'].get('command_args', {}).get('current_actuals', [])),
             '$.transition', 'topology requires pinned source rules and actual evidence')
    working = []
    ramps = []
    seen_working = False
    for r in all_rows:
        check_row(r, '$.rows.' + r['row_ref'])
        need('stage' not in r, '$.rows', 'calculator cannot allocate block stage bindings')
        need(r['load_state'] != 'unprescribed', '$.rows', 'unprescribed loads belong to source blocks')
        need(r['slot_id'] == request['slot_id'] and r['block_id'] is None,
             '$.rows', 'response belongs to another occurrence')
        need(r['source_ref'] in facts['provenance'], '$.rows.source_ref', 'uncovered source')
        need(r['equipment_ref'] == slot['equipment_ref'] and r['load_basis'] == template['load_basis'],
             '$.rows', 'equipment/basis differs from pinned slot')
        need(r['load_state'] != 'calibration_required' or
             (inherited(equipment) and template['load_state'] == 'calibration_required'),
             '$.rows', 'unresolved calibration cannot be written')
        need(r['load_state'] != 'known' or load_allowed(equipment, r['load_g']),
             '$.rows', 'infeasible row; never filter unavailable rungs')
        if r['set_kind'] == 'ramp':
            need(not seen_working, '$.rows', 'ramp appears after working row')
            ramps.append(r)
        else:
            need(r['set_kind'] in ('working', 'hold'), '$.rows', 'block rows cannot masquerade as slot rows')
            seen_working = True
            working.append(r)
            need(r['unit'] == template['unit'], '$.rows', 'working unit changed')
            args = request['inputs'].get('command_args', {})
            source_transition = ('effort_semantics' in template and
                ((request['reason'] == 'start' and any(h['row_status'] == 'logged' and
                    h['load_g'] is not None for h in request['history'])) or
                 (request['reason'] in ('remeasurement', 'correction', 'undo') and
                  any(a['row_status'] == 'logged' and a['load_g'] is not None
                      for a in args.get('current_actuals', [])))))
            need(r['load_state'] == template['load_state'] or
                 (inherited(equipment) and template['load_state'] == 'calibration_required' and
                  r['load_state'] == 'known' and
                  (source_transition or (request['reason'] == 'load_change' and
                   args.get('load_g') == r['load_g']))),
                 '$.rows', 'working load state changed')
            need(r['target_rest_ms'] == template['designated_rest_ms'], '$.rows', 'designated rest changed')
            need(r['laterality'] == template['laterality'], '$.rows', 'recording meaning changed')
            count = r['target_reps'] if r['unit'] == 'reps' else r['target_count']
            band = target_range(template)
            if band is not None:
                need(band[0] <= count <= band[1], '$.rows', 'target outside source range')
            if r['unit'] == 'reps' and 'effort_semantics' in template:
                need(r['rir_band'] == template['rir_band'] and
                     (r['rir_floor'] == template['rir_floor'] or
                      (r['target_mode'] == 'open_until_rir' and r['rir_floor'] is not None and
                       r['rir_band'] is not None and r['rir_floor'] == r['rir_band'][0])),
                     '$.rows', 'working row changed pinned source effort semantics')
            elif r['unit'] == 'reps':
                need(r['rir_floor'] is not None and r['rir_floor'] >= approved_rir_floor,
                     '$.rows', 'working row violates approved effort floor')
                if template['rir_floor'] is not None:
                    need(r['rir_floor'] >= template['rir_floor'], '$.rows', 'working row weakens source floor')
            family = facts['calculators'][slot['progression_ref']]['family']
            if family == 'maintenance_hold':
                need(r['load_g'] == template['load_g'] and r['load_state'] == template['load_state'],
                     '$.rows', 'maintenance hold changed load')
    need(len(working) == (transition['working_sets'] if transition else template['repeat_sets']), '$.rows', 'working/hold set coverage differs from source')
    need(bool(ramps) == (response['ramp_decision']['decision'] == 'ramp'),
         '$.ramp_decision', 'empty plan needs explicit no-ramp decision/reason')
    return response


PLAN = obj({'schema': enum('program_plan/1'), 'configuration_digest': DIGEST,
    'program_digest': DIGEST, 'policy_digest': DIGEST, 'split_id': nullable(STR),
    'slots': array(obj({'slot_id': STR, 'equipment_ref': STR, 'lift_id': POS,
                        'set_ids': array(POS, 1)})),
    'blocks': array(obj({'block_id': STR, 'set_ids': array(POS, 1)})),
    'calculators': mapping(obj({'version': STR, 'code_digest': DIGEST, 'rule_digest': DIGEST}))},
    {'rows': array(obj({'set_id': POS, 'row': ROW}))})
BASIS = obj({'schema': enum('prescription_basis/2'), 'slot_id': STR,
    'calculator_ref': STR, 'request_digest': DIGEST, 'response_digest': DIGEST,
    'dependencies_digest': DIGEST, 'basis': STR, 'evidence_digest': DIGEST},
    {'request': REQUEST, 'response': RESPONSE, 'revision': POS, 'lift_id': POS,
     'applied_rows': array(obj({'set_id': POS, 'row': ROW})),
     'topology': obj({'lift_id': POS, 'added_set_ids': array(POS), 'omitted_set_ids': array(POS),
                      'kind_changes': array(obj({'set_id': POS, 'before': STR, 'after': STR})),
                      'rows': array(obj({'set_id': POS, 'row': ROW}), 1)})})


def validate_evidence(conn, evidence):
    """Typed plan IDs are allocated and checked inside the writer transaction."""
    kind = evidence['kind']
    if kind not in ('program_plan/1', 'prescription_basis/2'):
        return
    need(conn.in_transaction, '$.evidence', 'typed plan evidence requires the writer transaction')
    need(conn.execute('PRAGMA user_version').fetchone()[0] >= MINIMUM_SCHEMA,
         '$.evidence', 'typed program evidence requires schema 84')
    value = evidence['value']
    need(evidence['scope'] == ('session' if kind == 'program_plan/1' else 'lift'),
         '$.evidence.scope', 'typed evidence scope mismatch')
    validate(PLAN if kind == 'program_plan/1' else BASIS, value)
    if kind == 'prescription_basis/2':
        need(('request' in value) == ('response' in value), '$.basis', 'request/response must be retained together')
        need(('revision' in value) == ('applied_rows' in value), '$.basis', 'applied rows require a revision')
        if 'request' in value:
            q, r = value['request'], value['response']
            need(fingerprint(q) == value['request_digest'] == r['request_digest'] and
                 fingerprint(r) == value['response_digest'] and q['slot_id'] == value['slot_id'] and
                 q['session_id'] == evidence['session_id'] and
                 all(q[k] == value[k] == r[k] for k in ('calculator_ref', 'dependencies_digest')) and
                 all(q[k] == r[k] for k in ('code_digest', 'rule_digest')),
                 '$.basis', 'retained exchange differs from immutable digests/identity')
        if 'applied_rows' in value:
            need('response' in value, '$.basis', 'applied rows require their full exchange')
            unique([b['set_id'] for b in value['applied_rows']], '$.basis.applied_rows')
            stored = conn.execute("SELECT value FROM session_evidence WHERE session_id=? AND kind='program_plan/1'",
                                  (evidence['session_id'],)).fetchall()
            need(len(stored) == 1, '$.basis', 'applied rows require one immutable program plan')
            pins = json.loads(stored[0][0])['slots']
            for extension in conn.execute("SELECT value FROM session_evidence WHERE session_id=? AND kind='program_extension/1'", (evidence['session_id'],)):
                pins += json.loads(extension[0])['slots']
            row_ids = [b['set_id'] for b in value['applied_rows']]
            lift_ids = {r[0] for rid in row_ids for r in conn.execute('SELECT lift_id FROM workout_sets WHERE id=?', (rid,))}
            if value.get('lift_id'):
                lift_ids.add(value['lift_id'])
            if value.get('topology'):
                lift_ids.add(value['topology']['lift_id'])
            candidates = [s for s in pins if s['slot_id']==value['slot_id'] and
                          (not lift_ids or lift_ids == {s['lift_id']})]
            need(len(candidates)==1, '$.basis', 'applied rows require one pinned occurrence')
            pin = candidates[0]
            allowed = set(pin['set_ids'])
            prior = conn.execute("SELECT value FROM session_evidence WHERE session_id=? AND kind='prescription_basis/2'", (evidence['session_id'],)).fetchall()
            for event in prior:
                t = json.loads(event[0]).get('topology', {})
                if t.get('lift_id') == pin['lift_id']:
                    allowed.update(t['added_set_ids'])
            if 'topology' in value:
                t = value['topology']
                need('transition' in value['response'] and t['lift_id'] == pin['lift_id'], '$.topology', 'source transition required')
                unique(t['added_set_ids'], '$.topology.added_set_ids')
                unique([b['set_id'] for b in t['rows']], '$.topology.rows')
                need(not allowed.intersection(t['added_set_ids']), '$.topology', 'added identity already bound')
                allowed.update(t['added_set_ids'])
                need(set(t['omitted_set_ids']) <= allowed, '$.topology', 'omission outside slot')
                for b in t['rows']:
                    own = conn.execute('SELECT session_id,lift_id FROM workout_sets WHERE id=?', (b['set_id'],)).fetchone()
                    need(own and tuple(own) == (evidence['session_id'], pin['lift_id']) and
                         b['set_id'] in allowed and b['row'] in value['response']['rows'], '$.topology', 'foreign topology binding')
                need({b['set_id'] for b in t['rows']} == allowed - set(t['omitted_set_ids']) and
                     len(t['rows']) == len(value['response']['rows']), '$.topology', 'incomplete topology binding')
            for b in value['applied_rows']:
                check_row(b['row'], '$.basis.applied_rows')
                own = conn.execute('SELECT session_id FROM workout_sets WHERE id=?', (b['set_id'],)).fetchone()
                need(own and own[0] == evidence['session_id'] and b['set_id'] in allowed and
                     b['row']['slot_id'] == value['slot_id'] and b['row'] in value['response']['rows'],
                     '$.basis.applied_rows', 'applied row is not in this session/response')
        return
    unique([s['slot_id'] for s in value['slots']], '$.slots')
    unique([s['lift_id'] for s in value['slots']], '$.slots.lift_id')
    unique([b['block_id'] for b in value['blocks']], '$.blocks')
    allocated = []
    for slot in value['slots']:
        lift = conn.execute('SELECT session_id FROM workout_lifts WHERE id=?', (slot['lift_id'],)).fetchone()
        need(lift and lift[0] == evidence['session_id'], '$.slots.lift_id', 'lift belongs to another session')
        for sid in slot['set_ids']:
            row = conn.execute('SELECT session_id,lift_id FROM workout_sets WHERE id=?', (sid,)).fetchone()
            need(row and tuple(row) == (evidence['session_id'], slot['lift_id']),
                 '$.slots.set_ids', 'set belongs to another lift/session')
        allocated += slot['set_ids']
    for block in value['blocks']:
        for sid in block['set_ids']:
            row = conn.execute('SELECT session_id FROM workout_sets WHERE id=?', (sid,)).fetchone()
            need(row and row[0] == evidence['session_id'], '$.blocks.set_ids', 'set belongs to another session')
        allocated += block['set_ids']
    unique(allocated, '$.set_ids')
    if 'rows' in value:
        unique([b['set_id'] for b in value['rows']], '$.rows.set_id')
        need({b['set_id'] for b in value['rows']} == set(allocated), '$.rows', 'immutable row coverage differs from plan')
        for b in value['rows']:
            check_row(b['row'], '$.rows')


def validate_executable(facts):
    """Facts-only preflight. Code, policy and native receipts are checked later."""
    validate_facts(facts)
    for sid, slot in facts['program_slots'].items():
        w = slot['working']
        eq = facts['equipment'][slot['equipment_ref']]
        need(w['load_state'] != 'known' or load_allowed(eq, w['load_g']),
             sid, 'initial load is outside the verified finite grid')
        need(w['load_state'] != 'calibration_required' or inherited(eq), sid, 'unresolved calibration')
        need(w['laterality']['recording_mode'] != 'separate_sides', sid,
             'separate side row expansion execution unavailable')
    for ref, eq in facts['equipment'].items():
        need(eq['verification']['status'] == 'verified' or inherited(eq), ref, 'unverified physical facts')
        c = next(c for c in facts['catalog'] if c['variant_id'] == eq['variant_id'])
        if c['implement_kind'] == 'barbell' and not inherited(eq):
            need(eq['mounted_load_g'] is not None, ref, 'mounted bar load unknown')
            from session_policy import plates
            for load in eq['available_loads_g']:
                plates(from_load_g=eq['bar_g'], to_load_g=load, bar_g=eq['bar_g'],
                       inventory_per_side={int(k): v for k, v in eq['inventory_per_side'].items()})
    for roster in [*facts['splits'].values(), {'blocks': facts['free_blocks']}]:
        for block in roster['blocks']:
            for stage in block['stages']:
                for r in stage['rows']:
                    need(r['set_kind'] in ('prep', 'hold', 'post'), r['row_ref'],
                         'block row kind execution unavailable')
                    need((r['load_state'] != 'calibration_required' or
                          inherited(facts['equipment'][r['equipment_ref']])) and
                         r['laterality']['recording_mode'] != 'separate_sides',
                         r['row_ref'], 'block dose execution unavailable')
                    need(r['load_state'] != 'known' or
                         load_allowed(facts['equipment'][r['equipment_ref']], r['load_g']),
                         r['row_ref'], 'block load is outside the verified finite grid')
    return facts


PROGRAM_CAPABILITY = obj({'schema': enum('session_program_capability/1'),
    'features': array(STR, 1), 'commands': enum(1), 'projection': enum(1)})


def capability_response(challenge, receipt, required_features=()):
    """Additive /2 handshake; /1 keeps its frozen native wire contract."""
    from session_v2_contract.transport import validate_capability_response
    c, r = dict(challenge), dict(receipt)
    cp, rp = c.pop('program', None), r.pop('program', None)
    base = validate_capability_response(c, r).to_dict()
    if required_features or cp is not None or rp is not None:
        validate(PROGRAM_CAPABILITY, cp, '$.challenge.program')
        validate(PROGRAM_CAPABILITY, rp, '$.receipt.program')
        for value in (cp, rp):
            unique(value['features'], '$.program.features')
            need(set(value['features']) <= FORMAT_FEATURES, '$.program', 'unknown native feature')
        need(set(required_features) <= set(cp['features']) <= set(rp['features']),
             '$.program', 'native receipt lacks required program features')
        need(c['required']['kit_schema'] >= MINIMUM_SCHEMA and
             r['capabilities']['kit_schema'] >= MINIMUM_SCHEMA,
             '$.program', 'program execution requires schema 84')
        base['program'] = rp
    return base


BLOCK_COMMAND = obj({'schema': enum(2), 'session_id': POS, 'command_id': STR,
    'actor': enum('member'), 'event_type': enum('skip_block', 'complete_block'),
    'expected_revision': INT, 'args': obj({'block_id': STR, 'reason': STR}),
    'source_ref': __import__('session_v2_contract.commands', fromlist=['SOURCE']).SOURCE})


def command(value):
    """Program feature commands are separate from the frozen /1 command ABI."""
    from session_v2_contract.commands import CommandEnvelope
    # /2 identities are explicit; the frozen /1 envelopes remain unchanged.
    event = value.get('event_type')
    lifecycle = {
        'acknowledge_condition': obj({}, {'condition': STR, 'fatigue': STR}),
        'activity_start': obj({'row_id': POS}),
        'acknowledge_activity': obj({'row_id': POS}),
        'activity_complete': obj({'row_id': POS, 'completed_at': INT}),
        'activity_timer_end': obj({'row_id': POS, 'timer_id': STR, 'deadline': INT}),
    }
    if event in lifecycle:
        from session_v2_contract.commands import SOURCE
        timer = event == 'activity_timer_end'
        validate(obj({'schema': enum(2), 'session_id': POS, 'command_id': STR,
            'actor': enum('member'), 'event_type': enum(event),
            'expected_revision': INT, 'args': lifecycle[event], 'source_ref': SOURCE},
            {'action_token': TOKEN}), value)
        source = value['source_ref']
        if timer:
            need(source['kind'] == 'scheduler' and 'action_token' not in value,
                 '$.source_ref', 'activity timers require scheduler')
        elif source['kind'] == 'callback':
            need('action_token' in value and all(source.get(k) for k in ('member_id','chat_id','message_id')),
                 '$.source_ref', 'callback requires token and authenticated member/chat/message context')
        else:
            need(source['kind'] == 'text' and 'action_token' not in value,
                 '$.source_ref', 'owner actions require authenticated text or member callback')
        return value
    extra = {
        'change_load': {'row_id': POS, 'slot_id': STR},
        'change_reps': {'row_id': POS, 'slot_id': STR},
        'select_load': {'slot_id': STR},
        'substitute': {'slot_id': STR}, 'free_lift': {'slot_id': STR},
        'choose_implement': {'slot_id': STR}, 'choose_machine': {'slot_id': STR},
        'photo_result': {'slot_id': STR}, 'pain': {'alternative_slot_id': STR},
        'choose_post': {'block_id': STR},
    }
    if event == 'undo_command' or (event in extra and set(value.get('args', {})) & set(extra[event])):
        from session_v2_contract.commands import COMMAND_ARGS, COMMAND_ACTORS, SOURCE
        args = (obj({'command_id': STR, 'reason': STR}) if event == 'undo_command'
                else dict(COMMAND_ARGS[event]))
        if event != 'undo_command':
            args['properties'] = dict(args['properties'], **extra[event])
        validate(obj({'schema': enum(2), 'session_id': POS, 'command_id': STR,
            'actor': enum('member' if event == 'undo_command' else COMMAND_ACTORS[event]),
            'event_type': enum(event), 'expected_revision': INT, 'args': args,
            'source_ref': SOURCE}), value)
        need(value['source_ref']['kind'] == 'text', '$.source_ref', 'slot operations require authenticated text')
        CommandEnvelope.check(value)
        return value
    if value.get('event_type') == 'select_reps_nullable':
        from session_v2_contract.commands import COMMAND_ARGS, SOURCE
        args = dict(COMMAND_ARGS['select_reps'])
        args['properties'] = dict(args['properties'], load_g=nullable(INT))
        validate(obj({'schema': enum(2), 'session_id': POS, 'command_id': STR,
            'actor': enum('member'), 'event_type': enum('select_reps_nullable'),
            'expected_revision': INT, 'args': args, 'source_ref': SOURCE}), value)
        need(value['source_ref']['kind'] == 'text', '$.source_ref', 'nullable rep input requires authenticated text')
        return value
    if value.get('event_type') in ('skip_block', 'complete_block'):
        validate(BLOCK_COMMAND, value)
        need(value['source_ref']['kind'] == 'text', '$.source_ref', 'block completion/skip requires chat')
        return value
    return CommandEnvelope(value).to_dict()


class ProgramCommand(dict):
    """Additive /2 service carrier; frozen /1 validation remains unchanged."""
    def __init__(self, value):
        super().__init__(command(dict(value)))

    def to_dict(self):
        return dict(self)

    def to_json(self):
        return json.dumps(self,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False)

    @classmethod
    def from_json(cls, raw):
        from session_delivery import decode
        return cls(decode(raw))


SOURCE_POLICY_APPROVAL = obj({'schema': enum('source_policy_approval/1'),
    'decision': enum('preserve_v1_rules'), 'member_id': STR, 'owning_agent': STR,
    'policy_ref': STR, 'version': POS, 'effective_at': INT, 'source_date': STR,
    'ruling_ref': STR, 'source_digest': DIGEST,
    'calculators': mapping(obj({'version': STR, 'code_digest': DIGEST, 'rule_digest': DIGEST}))})


def source_policy_bundle(facts, approval):
    """A /2 approval carrier, independent of frozen numeric/default policies.

    The caller supplies the already-authorized preserve ruling. Facts alone
    never grant approval. Exact source/code pins and mechanical ownership are
    required; no effort floor or other training default is invented.
    """
    from datetime import date
    validate_facts(facts)
    validate(SOURCE_POLICY_APPROVAL, approval)
    date.fromisoformat(approval['source_date'])
    member = approval['member_id']
    binding = facts['history_bindings'].get(member)
    need(binding is not None and binding['owning_agent'] == approval['owning_agent'] and
         binding.get('instance_roster', {}).get('members') == [member],
         '$.approval.member_id', 'source approval requires the mechanically bound instance member')
    need(all(eq['verification']['owning_agent'] == approval['owning_agent'] and inherited(eq)
             for eq in facts['equipment'].values()), '$.approval', 'source approval ownership/evidence differs')
    need(all('from_v1' in source['normalization'] and
             source['source_digest'] == approval['source_digest'] for source in facts['provenance'].values()),
         '$.approval.source_digest', 'source approval capture differs')
    pins = {ref: {k: calc[k] for k in ('version', 'code_digest', 'rule_digest')}
            for ref, calc in facts['calculators'].items()}
    need(approval['calculators'] == pins and
         all(calc['policy_ref'] == approval['policy_ref'] for calc in facts['calculators'].values()),
         '$.approval.calculators', 'source approval calculator pins/reference differ')
    rules = [dict(bundle_id=approval['policy_ref'], key='source.calculator:' + ref,
        value_type='source_calculator_approval/1', value=dict(schema='source_calculator_approval/1',
            calculator_ref=ref, approval=approval), scope='owner',
        source_ref='calculator:' + ref + ':' + calc['rule_digest'],
        source_date=approval['source_date'], approval_status='approved')
        for ref, calc in sorted(facts['calculators'].items())]
    return dict(id=approval['policy_ref'], owner_id=member, version=approval['version'],
        parent_defaults_version='source_policy/1:' + approval['source_digest'],
        effective_at=approval['effective_at'], digest=fingerprint(approval), rules=rules)


def store_source_policy(conn, facts, approval):
    """Materialize an explicit /2 source approval in the caller's transaction.

    The ordinary /1 store_policy and frozen PolicyRule schema stay unchanged.
    These versioned source rows carry the full ruling and code/source pins.
    The existing installed calculator approval reader consumes their exact
    owner-scope source_ref; effective_policy never substitutes a numeric rule.
    """
    from session_command import canonical, load_policy, row
    need(conn.in_transaction, '$.approval', 'source approval requires a caller-owned transaction')
    validate_history_bindings(conn, facts)
    bundle = source_policy_bundle(facts, approval)
    old = row(conn, 'SELECT * FROM training_policy_bundles WHERE id=?', (bundle['id'],))
    if old:
        need(load_policy(conn, bundle['id']) == bundle, '$.approval', 'source policy version already differs')
        return bundle
    header = {key: value for key, value in bundle.items() if key != 'rules'}
    conn.execute('INSERT INTO training_policy_bundles(' + ','.join(header) + ') VALUES(' +
                 ','.join('?' for _ in header) + ')', tuple(header.values()))
    for rule in bundle['rules']:
        conn.execute('INSERT INTO training_policy_rules VALUES(?,?,?,?,?,?,?,?)',
            (rule['bundle_id'], rule['key'], rule['value_type'], canonical(rule['value']),
             rule['scope'], rule['source_ref'], rule['source_date'], rule['approval_status']))
    return bundle
