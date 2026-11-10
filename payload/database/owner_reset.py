# Reset CLI flags and archives use historical workspace-role keys, independent
# of the agents installed at the explicit roots. Keep help, diagnostics and
# plan strings byte-identical: existing archive digests include the plan copy.
(
    'Owner-scoped Ag/Warg archive reset. No discovery by table-name patterns.\n'  # estate-scrub:allow(legacy CLI help names explicit workspace roles; preserve help bytes)
    '\n'
    'CLI: kit_life.sh owner-reset backup|plan|apply|restore --db PATH --ag ROOT\n'
    '     --archive DIR [--scope full|ledger|health] [--warg ROOT] [--expect DIGEST]\n'  # estate-scrub:allow(legacy CLI help names explicit workspace roles; preserve help bytes)
    '     [--writers-idle] [--offline-file]. Full/health require --warg;\n'  # estate-scrub:allow(legacy CLI help names explicit workspace roles; preserve help bytes)
    '     ledger changes Ag files only, health changes Warg files only.\n'  # estate-scrub:allow(legacy CLI help names explicit workspace roles; preserve help bytes)
    '\n'
    'backup captures the ENTIRE DB plus every file this verb can change. Take full\n'
    'workspace tar backups as documented in docs/owner-reset.md as well. plan is\n'
    'strictly read-only; apply requires its exact digest and an unchanged backup.\n'
    'Restore is a guarded whole-DB recovery: any subsequent DB write refuses it.\n'
    'Ledger scope with --offline-file captures exact bytes for disconnected DELETE\n'
    'journaling; WAL databases always use SQLite backup recovery.\n'
    'The caller must arrange an idle maintenance window; this verb never controls\n'
    'services, sends messages, or invokes pack hooks. Safety and the Why tree stay.\n'
)
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import quote

VERSION = 1
OFFLINE = False
# Actual operational tables, NOT every table containing 'ledger' or 'session'.
WHOLE = (
    'ledger_receipt_allocation', 'ledger_settlement_match', 'ledger_share',
    'ledger_split', 'ledger_capture_notice', 'spend_link', 'spend_link_rule',
    'pay_purpose_tag', 'pay_funding', 'fixed_cost_key', 'fixed_cost', 'spend_entry',
    'pay_method', 'session_muscle', 'session_type', 'ax_projection_slot', 'workout_sets', 'workout_classifications', 'workout_lifts',
    'session_equipment_snapshots', 'session_commands', 'session_trainer_approvals',
    'session_outbox', 'session_surfaces', 'session_evidence', 'session_delivery_journal',
    'session_input_state', 'session_import_audit', 'session_live_scopes',
    'session_live_selectors', 'session_live_commands', 'session_live_deliveries',
    'session_live_events', 'session_live_slots', 'session_live_history',
    'session_live_stages', 'session_live_selections', 'session_live_evidence',
    'session_pack_fences', 'session_pack_checkpoints', 'session_invocation_admissions',
    'session_invocation_revocations', 'session_transition_acks', 'workouts',
    'satiety_answer', 'health_regimen_session_end',
)
# Ledger transaction stores. Shared stores use explicit predicates below.
LEDGER_WHOLE = (
    'ledger_receipt_allocation', 'ledger_settlement_match', 'ledger_share',
    'ledger_split', 'ledger_capture_notice', 'spend_link', 'spend_link_rule',
    'pay_purpose_tag', 'pay_funding', 'fixed_cost_key', 'fixed_cost',
    'spend_entry', 'pay_method', 'shop_payment', 'shop_capture_payment',
)
# Explicit health record/program stores. Catalogs and gym resources survive.
HEALTH_WHOLE = (
    'ax_gym_exercise_machine',
    'session_muscle',
    'session_type',
    'ax_projection_slot',
    'workout_sets',
    'workout_classifications',
    'workout_lifts',
    'session_equipment_snapshots',
    'session_commands',
    'session_trainer_approvals',
    'session_outbox',
    'session_surfaces',
    'session_evidence',
    'session_delivery_journal',
    'session_input_state',
    'session_import_audit',
    'session_live_scopes',
    'session_live_selectors',
    'session_live_commands',
    'session_live_deliveries',
    'session_live_events',
    'session_live_slots',
    'session_live_history',
    'session_live_stages',
    'session_live_selections',
    'session_live_evidence',
    'session_pack_fences',
    'session_pack_checkpoints',
    'session_invocation_admissions',
    'session_invocation_revocations',
    'session_transition_acks',
    'workouts',
    'satiety_answer',
    'health_regimen_session_end',
)
HEALTH_NOTES = (
    'Why/life_why and canonical goal/goal_target survive; health module rungs and interview progress clear to confirm_direction at goal.',
    'All Ag files, ledger rows/consult answers/config landings/contracts/guards survive unchanged.',  # estate-scrub:allow(legacy reset plan workspace-role label; preserve hashed plan and archive compatibility)
    'Safety/injury/care, stable profile, gym resources/equipment identities, food references and cooked inventory survive; personal load grids, gym exercise choices and goal features clear.',
    'Sleep/steps/heart-rate streams and step summary config survive; only workout/body ingest provenance clears.',
    'Raw evidence, historical archives, unknown stores and engine memories survive; no services or messages are changed.',
)
# Fresh schema defaults are reference policy, never previous owner answers.
LEDGER_GUARD_DEFAULTS = {
    'auto_entry_enabled': (0, 'flag', 'block', 0),
    'entry_amount_ceiling': (3000000, 'KRW', 'hold', 0),
    'dup_window': (1, 'day', 'hold', 0),
    'budget_threshold': (None, 'KRW', 'inform', 0),
    'split_aggregation': (0, 'flag', 'inform', 0),
    'import_auto_accept': (0, 'flag', 'block', 0),
}
LEDGER_DOMAINS = "('ledger','spend','spending')"
LEDGER_NOTES = (
    'Shared health/safety, Why/goal tree, shopping objects, receipts and catalogs survive.',
    'Ledger contract shells are archived in place; ordinary contracts survive.',
    'Unchanged seed guards, shared noun_ask_daily_cap and the monotonic spend_id_floor survive; owner ledger guard overrides clear (known globals return to seed defaults).',
    'Memories are engine-owned and reported read-only; they are never reset.',
)

BODY_METRICS = ('weight_kg', 'skeletal_muscle_kg', 'body_fat_pct', 'fat_mass_kg',
                'lean_mass_kg', 'bmi', 'bmr_kcal', 'inbody_score', 'tbw_l',
                'protein_kg', 'mineral_kg', 'slm_kg', 'smi', 'visceral_fat_level',
                'whr', 'lean_arm_l_kg', 'lean_arm_r_kg', 'lean_leg_l_kg',
                'lean_leg_r_kg', 'lean_trunk_kg', 'fat_arm_l_kg', 'fat_arm_r_kg',
                'fat_leg_l_kg', 'fat_leg_r_kg', 'fat_trunk_kg')
CONFIG_KEYS = (
    'consult_state', 'consult_modules', 'consult_offered', 'goal_mode', 'goal_restart',
    'weight_kg', 'skeletal_muscle_kg', 'fat_mass_kg', 'lean_mass_kg', 'body_fat_pct',
    'deficit_kcal', 'surplus_kcal', 'protein_g', 'fat_ratio', 'training_experience',
    'training_years_band', 'training_years_band_as_of', 'calorie_bmr',
    'calorie_bmr_source', 'calorie_set_on', 'calorie_target_kcal', 'diet_bmr_method',
    'diet_protein_mode', 'diet_protein_override', 'diet_target_source',
    'maintenance_basis', 'maintenance_high', 'maintenance_low', 'maintenance_mid',
    'activity_job', 'avg_steps', 'steps_weekly_mean', 'neat_steps_source', 'other_neat_kcal',
    'health_link', 'health_link_on',
)
CONFIG_PREFIXES = ('meal_time.', 'meal_history_confirm.',
                   'workout_photo_turn.')
BODY_MEASURES = (*BODY_METRICS, 'chest_cm', 'waist_cm', 'hip_cm',
                 'shoulder_width_cm', 'inseam_cm', 'arm_length_cm', 'foot_length_mm')
HEALTH_CONFIG_KEYS = tuple(k for k in CONFIG_KEYS if k not in (
    'avg_steps', 'steps_weekly_mean', 'neat_steps_source')) + (
    'calorie_balance_override_kcal', 'calorie_chosen_deficit_kcal',
    'calorie_chosen_surplus_kcal', 'calorie_goal_pace_kcal',
    'calorie_override_goal_signature', 'maintenance_activity_policy',
    'maintenance_mismatch', 'maintenance_pal_mid', 'maintenance_pal_other_required',
    'health_goal_features',
)
# These are stated/learned load answers, not machine identity or structure.
# No machine/grid is seeded by the schema; machine-upd and session capture
# write these values. Keep every other column, including both timestamps.
HEALTH_MACHINE_ANSWERS = ('available_plates', 'lattice_base', 'lattice_step')
# File ownership is explicit. In particular files/program also holds unrelated
# automation on the life agent, and gym-layout.md on the health agent: never remove that directory.
PROGRAM_NAMES = (
    'program.yaml', 'prep.yaml', '.program-plan.json', '.domain-onboarding.json',
    '.health-link-offer.json', '.program-pending-changes.json', '.swap-history.json',
    '.implement-last.json', '.rir-explainer.json', '.session-closed', '.water-last',
    '.affordance-ledger', '.arm-gen', '.closeout-media', '.cue-shown', '.day-trim',
    '.exercise-closed', '.exercise-entered', '.exercise-paused', '.fatigue-brief',
    '.flow', '.free-sessions', '.idrill-arm', '.implement-pick', '.implement-preferences',
    '.inplace', '.kept-blocks', '.live-calibration', '.live-estimate-said',
    '.machine-pick', '.plate-cue', '.post-blocks', '.preparation-skips', '.rest-alerts',
    '.rest-state', '.row-kb', '.session-clock', '.session-estimate',
    '.set-arm-override', '.set-table-published', 'retro-meals',
)
HEALTH_PROGRAM_NAMES = ('.session-bindings', '.execution-identity', '.addon-pending', '.row-keyboards')
PROGRAM_PREFIXES = ('.program-plan.json.', '.program-update-', '.prep-preview-',
                    '.meal-card-', '.meal-turn-', '.transition-', '.hk-arrival-')
CONF_KEYS = ('LEDGER_SPEND_APP',)
AMBIGUITIES = (
    'Why/life_why and canonical goal/goal_target tree remain pending Dogany DB; '
    'module ledger_goal rows are archived out of the active store.',
    'Injuries, pain_event and ALL safety constraints/care survive, including '
    'calorie safety floors; deleting them requires a separate owner decision.',
    'Stable profile height/age/sex and gym/place/equipment catalog survive; '
    'profile_keep forbids removing height_cm.',
    'Meal food/alias/portion lookup catalogs, lookup receipts and cooking dishes '
    'survive as reusable reference data; raw photos/artifacts and memories survive.',
    'Workout import consent/refs are archived, requiring new approval with no '
    'backfill; sleep/steps/heart-rate consents and records remain.',
)


def encode(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                      default=lambda x: {'blob': base64.b64encode(x).decode('ascii')})


def digest(obj):
    return hashlib.sha256(encode(obj).encode()).hexdigest()


def q(name):
    return '"' + name.replace('"', '""') + '"'


def ro(path):
    # Never fall back to RW. Immutable is permitted only by the explicit idle
    # contract and only with NO WAL/SHM to miss. A busy source always refuses.
    path = Path(path).resolve()
    uri = 'file:' + quote(str(path)) + '?mode=ro'
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute('SELECT count(*) FROM sqlite_master').fetchone()
        return conn
    except sqlite3.OperationalError:
        if conn is not None:
            conn.close()
        if not OFFLINE or not path.is_file() or any(Path(str(path) + s).exists() for s in ('-wal', '-shm')):
            raise
        return sqlite3.connect(uri + '&immutable=1', uri=True)


def snapshot(conn):
    out = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        sql = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
        try:
            rows = conn.execute('SELECT rowid,* FROM ' + q(name)).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute('SELECT * FROM ' + q(name)).fetchall()
        out[name] = {'count': len(rows), 'hash': digest(sorted(rows, key=encode)), 'schema': sql}
    return out


def db_state(conn):
    return {'tables': snapshot(conn), 'schema': [list(r) for r in conn.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')],
        'version': conn.execute('PRAGMA user_version').fetchone()[0]}


def selections(conn, scope='full'):
    """Rowids materialized BEFORE deletes, so predicates cannot lose parents."""
    if scope == 'ledger':
        return ledger_selections(conn)
    if scope == 'health':
        return health_selections(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    selected = {}

    def add(table, where='1', args=()):
        if table in tables:
            selected.setdefault(table, set()).update(r[0] for r in conn.execute(
                'SELECT rowid FROM ' + q(table) + ' WHERE ' + where, args))

    for table in WHOLE:
        add(table)
    for table in ('contract_evidence', 'contract_proof', 'contract_reminder'):
        add(table, "contract_id IN (SELECT id FROM contract_record WHERE kind IN "
            "('ledger_obligation','ledger_yearly_pending') AND record_key NOT LIKE 'archived.owner-reset.%')")
    add('intake_source', "owning_module='module_ledger'")
    add('intake_match_rule', "owning_module='module_ledger'")
    add('intake_item', "source IN (SELECT key FROM intake_source WHERE owning_module='module_ledger')")
    add('availability_window', "domain IN ('health','ledger','spend','spending')")
    add('block', "source_module IN ('ledger','diet','training')")
    add('metric_log', 'metric IN (' + ','.join('?' for _ in BODY_METRICS) + ')', BODY_METRICS)
    add('entity', "ulid LIKE 'x-ent-%'")
    add('label', "namespace IN ('entity','installment') AND token IN (SELECT token FROM entity WHERE ulid LIKE 'x-ent-%')")
    add('ingest_consent', "kind='workout' OR (source='shortcut' AND kind='card_sms')")
    add('ingest_ref', "kind='workout' OR (source='shortcut' AND kind='card_sms') OR (target_table='metric_log' AND CAST(target_id AS INTEGER) IN "
        "(SELECT id FROM metric_log WHERE metric IN (" + ','.join('?' for _ in BODY_METRICS) + ')))', BODY_METRICS)
    body_measures = (*BODY_METRICS, 'chest_cm', 'waist_cm', 'hip_cm',
                     'shoulder_width_cm', 'inseam_cm', 'arm_length_cm', 'foot_length_mm')
    add('measure', "metric IN (SELECT token FROM metric_def WHERE owner_module='ledger') "
        "OR metric IN (" + ','.join('?' for _ in body_measures) + ')', body_measures)
    add('ledger_constraint', "class='nutrition' OR (class='time' AND (goal_id IN "
        "(SELECT id FROM ledger_goal WHERE domain IN ('health','spend','ledger','spending')) OR id IN "
        "(SELECT CAST(landed_ref AS INTEGER) FROM consult_answer WHERE domain IN ('health','ledger') "
        "AND landed_table='ledger_constraint')))")
    add('ledger_goal', "domain IN ('health','spend','spending','ledger')")
    add('ledger_audit', 'goal_ulid IN (SELECT ulid FROM ledger_goal WHERE domain IN (\'health\',\'spend\',\'spending\',\'ledger\'))')
    from ledger_v6 import LEDGER_PREFERENCE_TOPICS
    topics = (*LEDGER_PREFERENCE_TOPICS, 'exercise', 'cardio')
    add('ledger_preference', 'topic IN (' + ','.join('?' for _ in topics) + ") OR source='ledger_plan_fact'", topics)
    add('consult_answer', "domain IN ('health','ledger','spend','spending')")
    # Resources are NOT places; a gym resource is a consult answer pointing at
    # a preserved place. Pantry/supplement inventory is outside this reset.
    add('ledger_resource', "id IN (SELECT CAST(landed_ref AS INTEGER) FROM consult_answer "
        "WHERE domain IN ('health','ledger') AND landed_table='ledger_resource')")
    if 'config' in tables:
        keep = {r[0] for r in conn.execute('SELECT key FROM profile_keep')} if 'profile_keep' in tables else set()
        owned_whys = {r[0] for r in conn.execute(
            "SELECT id FROM life_why WHERE scope='module' AND domain IN ('health','spend','spending','ledger')")}
        ids = [rid for rid, key in conn.execute('SELECT rowid,key FROM config')
               if key not in keep and (key in CONFIG_KEYS or key.startswith(CONFIG_PREFIXES)
                   or (key.startswith('goal_move_') and key[10:].isdigit() and int(key[10:]) in owned_whys))]
        selected['config'] = set(ids)
    return dependent_selections(conn, selected, tables)


def health_selections(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    selected = {}

    def add(table, where='1', args=()):
        if table in tables:
            selected.setdefault(table, set()).update(r[0] for r in conn.execute(
                'SELECT rowid FROM ' + q(table) + ' WHERE ' + where, args))

    for table in HEALTH_WHOLE:
        add(table)
    add('block', "source_module IN ('diet','training')")
    add('metric_log', 'metric IN (' + ','.join('?' for _ in BODY_MEASURES) + ')', BODY_MEASURES)
    add('measure', 'metric IN (' + ','.join('?' for _ in BODY_MEASURES) + ')', BODY_MEASURES)
    add('ingest_consent', "kind='workout'")
    add('ingest_ref', "kind='workout' OR (target_table='metric_log' AND CAST(target_id AS INTEGER) IN "
        '(SELECT id FROM metric_log WHERE metric IN (' + ','.join('?' for _ in BODY_MEASURES) + ')))', BODY_MEASURES)
    add('availability_window', "domain='health'")
    add('ledger_goal', "domain='health'")
    add('ledger_audit', "goal_ulid IN (SELECT ulid FROM ledger_goal WHERE domain='health')")
    add('ledger_constraint', "class='nutrition' OR (class='time' AND (goal_id IN "
        "(SELECT id FROM ledger_goal WHERE domain='health') OR id IN "
        "(SELECT CAST(landed_ref AS INTEGER) FROM consult_answer WHERE domain='health' "
        "AND landed_table='ledger_constraint')))")
    add('ledger_preference', "topic IN ('exercise','cardio') OR id IN "
        "(SELECT CAST(landed_ref AS INTEGER) FROM consult_answer WHERE domain='health' "
        "AND landed_table='ledger_preference')")
    # Gym consultation receipts point at durable equipment/place knowledge.
    # Clear the answer receipt, preserving the resource the owner kept.
    add('consult_answer', "domain='health'")
    keep = {r[0] for r in conn.execute('SELECT key FROM profile_keep')}
    health_whys = {r[0] for r in conn.execute(
        "SELECT id FROM life_why WHERE scope='module' AND domain='health'")}
    selected['config'] = {rid for rid, key in conn.execute('SELECT rowid,key FROM config')
        if key not in keep and (key in HEALTH_CONFIG_KEYS or key.startswith(CONFIG_PREFIXES)
            or (key.startswith('goal_move_') and key[10:].isdigit() and int(key[10:]) in health_whys))}
    selected = dependent_selections(conn, selected, tables)
    # Consult landings are soft references. Refuse overlapping ownership rather
    # than deleting a ledger answer's payload in a shared store.
    for table, ref in conn.execute('SELECT landed_table,landed_ref FROM consult_answer '
                                  'WHERE domain IN ' + LEDGER_DOMAINS):
        if table not in selected:
            continue
        column = 'key' if table == 'config' else 'id'
        ids = {r[0] for r in conn.execute('SELECT rowid FROM ' + q(table) +
                                         ' WHERE ' + q(column) + '=?', (ref,))}
        if ids.intersection(selected[table]):
            raise ValueError('health reset overlaps a ledger consult landing: ' + table)
    return selected


def ledger_selections(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    selected = {}

    def add(table, where='1', args=()):
        if table in tables:
            selected[table] = {r[0] for r in conn.execute(
                'SELECT rowid FROM ' + q(table) + ' WHERE ' + where, args)}

    for table in LEDGER_WHOLE:
        add(table)
    for table in ('contract_evidence', 'contract_proof', 'contract_reminder'):
        add(table, "contract_id IN (SELECT id FROM contract_record WHERE kind IN "
            "('ledger_obligation','ledger_yearly_pending') AND record_key NOT LIKE 'archived.owner-reset.%')")
    add('intake_source', "owning_module='module_ledger'")
    add('intake_match_rule', "owning_module='module_ledger'")
    add('intake_item', "source IN (SELECT key FROM intake_source WHERE owning_module='module_ledger')")
    add('availability_window', 'domain IN ' + LEDGER_DOMAINS)
    add('block', "source_module='ledger'")
    # x-ent-* is the kit's financial instrument projection namespace.
    add('entity', "ulid LIKE 'x-ent-%'")
    add('label', "namespace IN ('entity','installment') AND token IN "
        "(SELECT token FROM entity WHERE ulid LIKE 'x-ent-%')")
    add('ingest_consent', "source='shortcut' AND kind='card_sms'")
    add('ingest_ref', "(source='shortcut' AND kind='card_sms') OR target_table IN ('spend_entry','pay_method','fixed_cost')")
    add('measure', "metric IN (SELECT token FROM metric_def WHERE owner_module='ledger')")
    add('ledger_goal', 'domain IN ' + LEDGER_DOMAINS)
    add('ledger_audit', 'goal_ulid IN (SELECT ulid FROM ledger_goal WHERE domain IN ' + LEDGER_DOMAINS + ')')
    from ledger_v6 import LEDGER_PREFERENCE_TOPICS
    add('ledger_preference', 'topic IN (' + ','.join('?' for _ in LEDGER_PREFERENCE_TOPICS) +
        ") OR source='ledger_plan_fact' OR id IN (SELECT CAST(landed_ref AS INTEGER) "
        'FROM consult_answer WHERE domain IN ' + LEDGER_DOMAINS +
        " AND landed_table='ledger_preference')", LEDGER_PREFERENCE_TOPICS)
    add('ledger_constraint', "class != 'safety' AND (goal_id IN "
        '(SELECT id FROM ledger_goal WHERE domain IN ' + LEDGER_DOMAINS + ') OR id IN '
        '(SELECT CAST(landed_ref AS INTEGER) FROM consult_answer WHERE domain IN ' +
        LEDGER_DOMAINS + " AND landed_table='ledger_constraint'))")
    add('ledger_resource', 'id IN (SELECT CAST(landed_ref AS INTEGER) FROM consult_answer '
        'WHERE domain IN ' + LEDGER_DOMAINS + " AND landed_table='ledger_resource')")
    add('consult_answer', 'domain IN ' + LEDGER_DOMAINS)
    add('config', "key='manage.module_ledger'")
    add('near_match_refusal', "domain='method'")
    selected['ledger_guard'] = {
        rid for rid, key, scope, value, unit, enforcement, inviolable, source in conn.execute(
            'SELECT rowid,guard_key,scope,value,unit,enforcement,inviolable,source FROM ledger_guard')
        if key not in ('spend_id_floor', 'noun_ask_daily_cap') and not (
            scope == 'global' and source in ('seed', 'seed-fallback') and
            LEDGER_GUARD_DEFAULTS.get(key) == (value, unit, enforcement, inviolable))}
    return dependent_selections(conn, selected, tables)


def dependent_selections(conn, selected, tables):
    # Known dependent rows only. Unknown dependencies fail FK check, never widen
    # the reset into somebody else's module. No trigger is disabled.
    children = {'block_attr', 'block_delta', 'measure', 'project_block', 'block_entity',
                'made_dish_meal', 'record_event', 'event_record_exclusion',
                'experience_person', 'experience_capture_key', 'ingest_effect', 'event_timeblock', 'intake_link'}
    changed = True
    while changed:
        changed = False
        for table in children & tables:
            groups = {}
            for fk in conn.execute('PRAGMA foreign_key_list(' + q(table) + ')'):
                groups.setdefault(fk[0], []).append(fk)
            for group in groups.values():
                group.sort(key=lambda fk: fk[1])
                parent = group[0][2]
                ids = selected.get(parent, set())
                if not ids:
                    continue
                # A composite FK is one tuple, not several independent keys.
                # Matching only source='healthkit' would erase sleep evidence.
                parent_cols = ','.join(q(fk[4] or 'id') for fk in group)
                child_cols = ','.join(q(fk[3]) for fk in group)
                keys = {tuple(r[1:]) for r in conn.execute(
                    'SELECT rowid,' + parent_cols + ' FROM ' + q(parent)) if r[0] in ids}
                old = len(selected.get(table, ()))
                selected.setdefault(table, set()).update(r[0] for r in conn.execute(
                    'SELECT rowid,' + child_cols + ' FROM ' + q(table)) if tuple(r[1:]) in keys)
                changed |= len(selected[table]) != old
        if 'record' in tables:
            old = len(selected.get('record', ()))
            for rid, source, source_id in conn.execute('SELECT rowid,source_table,source_id FROM record'):
                if source_id in selected.get(source, ()):
                    selected.setdefault('record', set()).add(rid)
            changed |= len(selected.get('record', ())) != old
    return {t: sorted(ids) for t, ids in sorted(selected.items())}


def health_column_updates(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ax_machine'").fetchone():
        return []
    ids = sorted(r[0] for r in conn.execute('SELECT rowid FROM ax_machine WHERE ' +
                 ' OR '.join(q(k) + ' IS NOT NULL' for k in HEALTH_MACHINE_ANSWERS)))
    return ([{'table': 'ax_machine', 'rowids': ids,
              'values': dict.fromkeys(HEALTH_MACHINE_ANSWERS)}] if ids else [])


def survivor_state(conn, selected, column_updates=()):
    """Hash every surviving row, including survivors in partly reset tables."""
    out = {}
    for table in snapshot(conn):
        if table in ('ledger_guard', 'owned_item', 'contract_record'):
            continue  # validated separately: the two intentional trigger effects
        try:
            cur = conn.execute('SELECT rowid,* FROM ' + q(table))
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute('SELECT * FROM ' + q(table)).fetchall()
        removed = set(selected.get(table, ()))
        for update in column_updates:
            if update['table'] != table:
                continue
            names = [d[0] for d in cur.description]
            ids = set(update['rowids'])
            rows = [tuple(update['values'].get(name, value)
                          for name, value in zip(names, row)) if row[0] in ids else row
                    for row in rows]
        out[table] = digest(sorted((r for r in rows if r[0] not in removed), key=encode))
    return out


CONTRACT_ARCHIVE_PREFIX = 'archived.owner-reset.'


def ledger_contracts(conn):
    return [r[0] for r in conn.execute(
        "SELECT id FROM contract_record WHERE kind IN ('ledger_obligation','ledger_yearly_pending') "
        "AND record_key NOT LIKE ?", (CONTRACT_ARCHIVE_PREFIX + '%',))]


def guard_seed(row):
    defaults = LEDGER_GUARD_DEFAULTS.get(row['guard_key'])
    if row['scope'] == 'global' and defaults is not None:
        return dict(row, value=defaults[0], unit=defaults[1], enforcement=defaults[2],
                    inviolable=defaults[3], source='seed')
    return None


def trigger_survivors(conn, selected, scope='full'):
    out = {}
    for table in ('ledger_guard', 'owned_item', 'contract_record'):
        cur = conn.execute('SELECT rowid,* FROM ' + q(table))
        names = [d[0] for d in cur.description][1:]
        out[table] = []
        for r in cur:
            row = dict(zip(names, r[1:]))
            if r[0] in set(selected.get(table, ())):
                row = guard_seed(row) if table == 'ledger_guard' else None
            if row is not None:
                out[table].append(row)
    # spend deletion preserves the shopping object and its price, while dropping
    # the now absent transaction link (existing trg_spend_entry_owned_price).
    amounts = dict(conn.execute('SELECT id,amount FROM spend_entry'))
    capture_prices = {r[0]: r[1] for r in conn.execute(
        'SELECT owned_id,amount FROM shop_capture_payment WHERE spend_id IN '
        '(SELECT id FROM spend_entry)') if r[0] is not None}
    for row in out['owned_item']:
        if row.get('spend_id') in set(selected.get('spend_entry', ())):
            amount = capture_prices.get(row['id'], amounts[row['spend_id']])
            row.update(spend_id=None, price=amount if amount >= 0 else None)
    retiring = set(ledger_contracts(conn)) if selected and scope != 'health' else set()
    for row in out['contract_record']:
        if row['id'] in retiring:
            row.update(record_key=CONTRACT_ARCHIVE_PREFIX + str(row['id']) + '.' + row['record_key'],
                       facts='{}', sources='{}', fixed_id=None)
        elif row.get('fixed_id') in set(selected.get('fixed_cost', ())):
            row['fixed_id'] = None
    for row in out['ledger_guard']:
        if row['guard_key'] == 'spend_id_floor' and row['scope'] == 'global':
            row['value'] = max(row['value'] or 0, max(selected.get('spend_entry', ()), default=0))
    return out


def reset_db(conn, selected, scope='full', column_updates=()):
    remaining = list(conn.execute('SELECT id,remaining FROM made_dish'))
    guards = []
    for rid in selected.get('ledger_guard', ()):
        cur = conn.execute('SELECT * FROM ledger_guard WHERE rowid=?', (rid,))
        row = dict(zip([d[0] for d in cur.description], cur.fetchone()))
        seed = guard_seed(row)
        if seed is not None:
            guards.append((rid, seed))
    retiring = ledger_contracts(conn) if scope != 'health' else []
    conn.execute('PRAGMA defer_foreign_keys=ON')
    # Circular instrument links and session references are deferred to commit.
    # Delete child evidence first where triggers enforce immediate relationships.
    order = sorted(selected, key=lambda t: (t not in ('ingest_effect', 'ingest_ref'),
                                             t in ('shop_payment', 'shop_capture_payment'), t))
    for table in order:
        ids = selected[table]
        conn.executemany('DELETE FROM ' + q(table) + ' WHERE rowid=?', ((i,) for i in ids))
    for update in column_updates:
        values = update['values']
        conn.executemany('UPDATE ' + q(update['table']) + ' SET ' +
                         ','.join(q(k) + '=?' for k in values) + ' WHERE rowid=?',
                         ((*values.values(), rid) for rid in update['rowids']))
    for rid, row in guards:
        conn.execute('INSERT INTO ledger_guard(rowid,' + ','.join(q(k) for k in row) +
                     ') VALUES (' + ','.join('?' for _ in range(len(row) + 1)) + ')',
                     (rid, *row.values()))
    # Contract deletion is correctly forbidden by its evidence contract.
    # Archive module-only answers in place, removing their live identity and
    # facts so a re-used stream ID cannot resurrect old onboarding answers.
    conn.executemany(
        "UPDATE contract_record SET record_key=? || id || '.' || record_key,"
        "facts='{}',sources='{}',fixed_id=NULL WHERE id=?",
        ((CONTRACT_ARCHIVE_PREFIX, ident) for ident in retiring))
    # Historical meal deletion must not replenish today's cooked inventory.
    conn.executemany('UPDATE made_dish SET remaining=? WHERE id=?',
                     ((value, ident) for ident, value in remaining))
    violations = list(conn.execute('PRAGMA foreign_key_check'))
    if violations:
        raise ValueError('out-of-scope dependency blocks reset: ' + repr(violations[:10]))


def clean_conf(body):
    return b''.join(line for line in body.splitlines(keepends=True) if not re.match(
        rb'^\s*(?:export\s+)?(?:' + b'|'.join(k.encode() for k in CONF_KEYS) + rb')\s*=', line))


def file_changes(roots, scope='full'):
    changes = []
    for agent, root in roots.items():
        if scope == 'ledger' and agent != 'ag':
            continue
        if scope == 'health' and agent != 'warg':  # estate-scrub:allow(legacy CLI option or serialized archive workspace key; independent of configured agent names)
            continue
        paths = []
        for name in ('onboarding.json', 'lookup.json', 'automation.json', 'tiers.json',
                     'opaque.json', 'sms-unread.jsonl', 'sms-cursor.json', 'sms-poll.log'):
            if scope != 'health':
                paths.append(root / 'files/ledger' / name)
        program = root / 'files/program'
        if scope in ('full', 'health') and program.is_dir():
            for path in sorted(program.iterdir()):
                if (path.name in PROGRAM_NAMES or path.name.startswith(PROGRAM_PREFIXES)
                        or (agent == 'warg' and path.name == '.training-gear')  # estate-scrub:allow(legacy archive workspace key; health-owned gear answers)
                        or (scope == 'health' and path.name in HEALTH_PROGRAM_NAMES)):
                    if path.is_symlink():
                        raise ValueError('reset path is symlink: ' + str(path))
                    paths.extend([path] if path.is_file() else sorted(path.rglob('*')))
        if scope in ('full', 'health'):
            paths.append(root / 'database/body_stats.json')
        for path in paths:
            if not path.exists() or path.is_dir():
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('reset path escapes workspace: ' + str(path))
            changes.append({'agent': agent, 'path': str(path.relative_to(root)),
                            'before': hashlib.sha256(path.read_bytes()).hexdigest(),
                            'mode': path.stat().st_mode & 0o777, 'after': None})
        for name in (() if scope == 'health' else ('kit_life.conf', 'pack_ledger.conf')):
            path = root / 'config' / name
            if path.is_file():
                if path.is_symlink():
                    raise ValueError('config path is symlink')
                body = path.read_bytes()
                clean = clean_conf(body)
                if clean != body:
                    changes.append({'agent': agent, 'path': str(path.relative_to(root)),
                                    'before': hashlib.sha256(body).hexdigest(),
                                    'mode': path.stat().st_mode & 0o777,
                                    'after': base64.b64encode(clean).decode()})
    return changes


def memory_lines(roots):
    # Read-only disclosure, never a deletion selector. No symlink traversal.
    root = roots['ag'].resolve()
    notes = root / 'memories'
    result = []
    if notes.is_dir() and not notes.is_symlink():
        for path in sorted(notes.rglob('*.md')):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue
            for number, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
                if re.search(
                    r'ledger|spend|budget|\uac00\uacc4\ubd80|\uace0\uc815\ube44|\uc9c0\ucd9c|'
                    r'\uae08\uc735|(?:\uc2e0\uc6a9|\uccb4\ud06c|\uacb0\uc81c)\uce74\ub4dc|\uacc4\uc88c|\uac70\ub798|\uc608\uc0b0|'
                    r'\uc218\uc785|\uc601\uc218\uc99d|\uc815\uc0b0|\ud560\ubd80', line, re.I):
                    result.append({'path': str(path.relative_to(root)), 'line': number, 'text': line})
    return result


def plan(db, roots, scope='full'):
    if scope == 'health':
        if not all(k in roots for k in ('ag', 'warg')):  # estate-scrub:allow(legacy CLI option or serialized archive workspace key; independent of configured agent names)
            raise ValueError('health scope needs explicit Ag and Warg roots')  # estate-scrub:allow(legacy CLI workspace-role diagnostic; preserve operator error bytes)
        ag, warg = roots['ag'].resolve(), roots['warg'].resolve()  # estate-scrub:allow(legacy CLI option or serialized archive workspace key; independent of configured agent names)
        if ag.is_relative_to(warg) or warg.is_relative_to(ag):  # estate-scrub:allow(legacy CLI option or serialized archive workspace key; independent of configured agent names)
            raise ValueError('health scope requires disjoint Ag and Warg workspaces')  # estate-scrub:allow(legacy CLI workspace-role diagnostic; preserve operator error bytes)
    with ro(db) as conn:
        conn.execute('BEGIN')
        before = db_state(conn)
        selected = selections(conn, scope)
        column_updates = health_column_updates(conn) if scope == 'health' else []
        retire_contracts = ledger_contracts(conn) if scope != 'health' else []
    result = {'format': VERSION, 'db': str(Path(db).resolve()),
              'scope': scope, 'roots': {k: str(v.resolve()) for k, v in roots.items()},
              'before': before, 'selected': selected, 'contract_archive_ids': retire_contracts,
              'files': file_changes(roots, scope),
              'ambiguities': {'ledger': LEDGER_NOTES, 'health': HEALTH_NOTES}.get(scope, AMBIGUITIES)}
    if scope == 'health':
        result['column_updates'] = column_updates
    if scope == 'ledger':
        result['retained_memory_lines'] = memory_lines(roots)
    result['digest'] = digest(result)
    result['counts'] = {t: {'archive': len(ids), 'before': before['tables'][t]['count']}
                        for t, ids in selected.items()}
    result['counts']['contract_record'] = {'archive_in_place': len(retire_contracts),
        'before': before['tables']['contract_record']['count']}
    return result


def save(path, data):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        f.write(encode(data) + '\n'); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def backup(db, roots, archive, scope='full', writers_idle=False, offline=False):
    if offline and (scope != 'ledger' or not writers_idle or not offline_file(db)):
        raise ValueError('offline file backup requires ledger scope, idle disconnected DB users, and DELETE journaling without sidecars')
    if archive.exists():
        raise ValueError('archive must be a new directory')
    for root in roots.values():
        if archive.resolve().is_relative_to(root.resolve()):
            raise ValueError('archive must be outside workspaces')
    p = plan(db, roots, scope)
    archive.mkdir(parents=True, mode=0o700)
    with ro(db) as src, sqlite3.connect(archive / 'before.db') as dst:
        src.backup(dst)
        dst.execute('PRAGMA journal_mode=DELETE')
        if db_state(dst) != p['before']:
            raise ValueError('database changed during backup; take a new archive')
    for f in p['files']:
        src = roots[f['agent']] / f['path']
        dst = archive / f['agent'] / f['path']
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    if plan(db, roots, scope)['digest'] != p['digest']:
        raise ValueError('state changed during backup; take a new archive')
    manifest = {'phase': 'backed_up', 'plan': p}
    # Byte recovery is possible only for an explicitly idle, rollback-journal
    # database. WAL recovery always stays on SQLite's online backup API.
    if offline:
        image = archive / 'before-file.db'
        shutil.copy2(db, image)
        with ro(image) as conn:
            if db_state(conn) != p['before']:
                raise ValueError('offline file changed during backup')
        manifest['offline_image'] = hashlib.sha256(image.read_bytes()).hexdigest()
    save(archive / 'manifest.json', manifest)
    return {'status': 'backed_up', 'digest': p['digest'], 'archive': str(archive)}


def offline_file(db):
    if any(Path(str(db) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        return False
    with ro(db) as conn:
        return conn.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'


def validate_archive(archive, m):
    p = m['plan']
    if m.get('offline_image'):
        image = archive / 'before-file.db'
        if hashlib.sha256(image.read_bytes()).hexdigest() != m['offline_image']:
            raise ValueError('archive offline database damaged')
        with ro(image) as conn:
            if db_state(conn) != p['before']:
                raise ValueError('archive offline database differs')
    with ro(archive / 'before.db') as conn:
        if db_state(conn) != p['before'] or conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('archive database damaged')
    for f in p['files']:
        path = archive / f['agent'] / f['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != f['before']:
            raise ValueError('archive file damaged: ' + str(path))


def write_files(archive, p, restore=False):
    for f in p['files']:
        path = Path(p['roots'][f['agent']]) / f['path']
        if restore:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive / f['agent'] / f['path'], path)
        elif f['after'] is None:
            path.unlink()
        else:
            path.write_bytes(base64.b64decode(f['after']))
            path.chmod(f['mode'])


def apply(db, roots, archive, expect, writers_idle, scope='full'):
    if not writers_idle:
        raise ValueError('an idle maintenance window is required (--writers-idle)')
    m = json.loads((archive / 'manifest.json').read_text())
    if m['phase'] != 'backed_up':
        raise ValueError('archive is not in backed_up phase')
    validate_archive(archive, m)
    p = plan(db, roots, scope)
    if not expect or expect != p['digest'] or expect != m['plan']['digest']:
        raise ValueError('stale plan or backup; run backup then plan again')
    # Prepare/validate a complete after-image before ANY workspace mutation.
    after = archive / 'after.db'
    shutil.copy2(archive / 'before.db', after)
    with sqlite3.connect(after) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        column_updates = p.get('column_updates', [])
        surviving = survivor_state(conn, p['selected'], column_updates)
        expected_triggers = trigger_survivors(conn, p['selected'], scope)
        reset_db(conn, p['selected'], scope, column_updates)
        if survivor_state(conn, p['selected']) != surviving:
            raise ValueError('reset changed an out-of-scope survivor')
        if trigger_survivors(conn, {}) != expected_triggers:
            raise ValueError('unexpected shopping or ID-floor trigger effect')
        after_state = db_state(conn)
    # Changes outside declared deletes and these three product relationships
    # are refused. Captures trigger side effects instead of assuming none.
    expected_changes = (set(p['selected']) | {u['table'] for u in column_updates} |
                        {'ledger_guard', 'owned_item', 'contract_record'})
    unexpected = [t for t, state in p['before']['tables'].items()
                  if after_state['tables'][t] != state and t not in expected_changes]
    if unexpected:
        raise ValueError('unexpected survivor changes: ' + repr(unexpected))
    m.update(phase='applying', after=after_state)
    save(archive / 'manifest.json', m)
    # Recovery protocol: archive has before/after DB and every original file.
    # If interrupted, restore recognizes either DB image and partially removed
    # files. Never blindly overwrite data written after this operation.
    try:
        write_files(archive, p)
        with sqlite3.connect(db) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            if db_state(conn) != p['before']:
                raise ValueError('database changed since plan')
            reset_db(conn, p['selected'], scope, column_updates)
            if db_state(conn) != after_state:
                raise ValueError('reset differs from prepared after-image')
    except BaseException:
        write_files(archive, p, restore=True)
        # Keep journal applying: DB commit may have succeeded before a failure.
        raise
    m['phase'] = 'applied'
    save(archive / 'manifest.json', m)
    return {'status': 'applied', 'counts': p['counts'], 'archive': str(archive)}


def restore(db, roots, archive, writers_idle, scope='full', offline=False):
    db = Path(db).resolve()
    if not writers_idle:
        raise ValueError('an idle maintenance window is required (--writers-idle)')
    m = json.loads((archive / 'manifest.json').read_text())
    if m['phase'] not in ('applying', 'applied', 'restoring'):
        raise ValueError('archive has no reset to restore')
    p = m['plan']
    if p.get('scope', 'full') != scope:
        raise ValueError('archive belongs to a different scope')
    if str(Path(db).resolve()) != p['db'] or {k: str(v.resolve()) for k, v in roots.items()} != p['roots']:
        raise ValueError('archive belongs to different paths')
    validate_archive(archive, m)
    with ro(db) as conn:
        current = db_state(conn)
    if current not in (m['after'], p['before']):
        raise ValueError('database changed after reset; restore refuses unrelated data loss')
    for f in p['files']:
        path = roots[f['agent']] / f['path']
        if path.is_symlink() or not path.resolve().is_relative_to(roots[f['agent']].resolve()):
            raise ValueError('restore path is symlink or escapes workspace')
        h = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        after_hash = hashlib.sha256(base64.b64decode(f['after'])).hexdigest() if f['after'] is not None else None
        if h not in (f['before'], after_hash):
            raise ValueError('file changed after reset: ' + str(path))
    m['phase'] = 'restoring'; save(archive / 'manifest.json', m)
    if offline and (not m.get('offline_image') or not offline_file(db)):
        raise ValueError('offline file restore requires its archived image and DELETE journaling without sidecars')
    if offline:
        # The caller asserted ALL DB users disconnected. Lock the DELETE-journal
        # file while validating it once more and installing its original bytes.
        # Never replace a WAL database or its sidecars.
        fd, name = tempfile.mkstemp(prefix=Path(db).name + '.owner-reset-', dir=Path(db).parent)
        os.close(fd)
        tmp = Path(name)
        try:
            shutil.copy2(archive / 'before-file.db', tmp)
            with tmp.open('rb') as f:
                os.fsync(f.fileno())
            with sqlite3.connect(db) as dst:
                dst.execute('BEGIN EXCLUSIVE')
                if db_state(dst) not in (m['after'], p['before']) or dst.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                    raise ValueError('database changed during offline restore')
                os.replace(tmp, db)
        finally:
            tmp.unlink(missing_ok=True)
    else:
        with ro(archive / 'before.db') as src, sqlite3.connect(db) as dst:
            src.backup(dst)
    write_files(archive, p, restore=True)
    with ro(db) as conn:
        if db_state(conn) != p['before']:
            raise ValueError('restored rows/schema differ from archive')
    m['phase'] = 'restored'; save(archive / 'manifest.json', m)
    return {'status': 'restored', 'byte_equal_rows': True,
            'byte_equal_db': bool(m.get('offline_image')) and
            hashlib.sha256(Path(db).read_bytes()).hexdigest() == m['offline_image']}


def cli(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('backup', 'plan', 'apply', 'restore'))
    ap.add_argument('--scope', choices=('full', 'ledger', 'health'), default='full')
    ap.add_argument('--health', '--warg', dest='warg', type=Path)  # estate-scrub:allow(legacy CLI option or serialized archive workspace key; independent of configured agent names)
    for key in ('db', 'ag', 'archive'):
        ap.add_argument('--' + key, required=True, type=Path)
    ap.add_argument('--offline-file', action='store_true',
                    help='assert all DB users disconnected; enable exact DELETE-journal file backup/recovery')
    ap.add_argument('--expect')
    ap.add_argument('--writers-idle', action='store_true')
    args = ap.parse_args(argv)
    global OFFLINE
    OFFLINE = args.writers_idle
    roots = {'ag': args.ag}
    if args.warg is not None:  # estate-scrub:allow(legacy CLI option or serialized archive workspace key; independent of configured agent names)
        roots['warg'] = args.warg  # estate-scrub:allow(legacy CLI option or serialized archive workspace key; independent of configured agent names)
    try:
        if (args.scope in ('full', 'health') and args.warg is None) or not args.db.is_file() or any(not p.is_dir() for p in roots.values()):  # estate-scrub:allow(legacy CLI option or serialized archive workspace key; independent of configured agent names)
            raise ValueError('explicit existing database and workspace roots required (full/health scope needs --warg)')  # estate-scrub:allow(legacy CLI workspace-role diagnostic; preserve operator error bytes)
        if args.action == 'plan':
            p = plan(args.db, roots, args.scope)
            result = {k: v for k, v in p.items() if k not in ('before', 'selected')}
            result['status'] = 'dry_run'
        elif args.action == 'backup':
            result = backup(args.db, roots, args.archive, args.scope, args.writers_idle, args.offline_file)
        elif args.action == 'apply':
            result = apply(args.db, roots, args.archive, args.expect, args.writers_idle, args.scope)
        else:
            result = restore(args.db, roots, args.archive, args.writers_idle, args.scope, args.offline_file)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'refused', 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(cli(__import__('sys').argv[1:]))
