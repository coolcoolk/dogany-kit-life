-- 078_trainer_session_v2.sql (target user_version 78)
-- Canonical v2 session log; legacy rows/IDs/timestamps stay byte-for-byte intact.
-- reversible: YES before any v2 writes, by restoring the verified pre-078
-- backup. After v2 activity use forward recovery; never replay through v1 writers.
-- Run offline on a consistent copy first. This migration does NOT authorize
-- importing an open legacy session or activating callbacks (see session_command).
.bail on
.timeout 5000
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TEMP TABLE _guard_078(reason TEXT);
CREATE TEMP TRIGGER _guard_078_fail BEFORE INSERT ON _guard_078 BEGIN
 SELECT RAISE(ROLLBACK,'migration 078 refused: version, legacy triggers or set-kind constraint mismatch');
END;
INSERT INTO _guard_078 SELECT 'version' FROM pragma_user_version WHERE user_version<>77;
INSERT INTO _guard_078 SELECT 'trigger' WHERE NOT EXISTS (SELECT 1 FROM sqlite_master WHERE name='trg_workout_sets_reps_counted' AND replace(replace(replace(replace(rtrim(sql,';'),char(10),''),char(13),''),char(9),''),' ','')='CREATETRIGGERtrg_workout_sets_reps_countedBEFOREINSERTONworkout_setsWHENNEW.repsISNULLANDNEW.set_kindNOTIN(''prep'',''ramp'')BEGINSELECTRAISE(ABORT,''workout_setsrefused:repsisrequiredforaworking/holdset--onlyapreporramprowmaybeadone-tap(repsNULL,target_reps=theprescription)'');END');
INSERT INTO _guard_078 SELECT 'trigger' WHERE NOT EXISTS (SELECT 1 FROM sqlite_master WHERE name='trg_workout_sets_reps_counted_upd' AND replace(replace(replace(replace(rtrim(sql,';'),char(10),''),char(13),''),char(9),''),' ','')='CREATETRIGGERtrg_workout_sets_reps_counted_updBEFOREUPDATEOFreps,set_kindONworkout_setsWHENNEW.repsISNULLANDNEW.set_kindNOTIN(''prep'',''ramp'')BEGINSELECTRAISE(ABORT,''workout_setsrefused:repsisrequiredforaworking/holdset--onlyapreporramprowmaybeadone-tap(repsNULL,target_reps=theprescription)'');END');
INSERT INTO _guard_078 SELECT 'trigger' WHERE NOT EXISTS (SELECT 1 FROM sqlite_master WHERE name='trg_workout_sets_machine_known' AND replace(replace(replace(replace(rtrim(sql,';'),char(10),''),char(13),''),char(9),''),' ','')='CREATETRIGGERtrg_workout_sets_machine_knownBEFOREINSERTONworkout_setsWHENNEW.machine_refISNOTNULLANDNOTEXISTS(SELECT1FROMax_machineWHEREulid=NEW.machine_ref)BEGINSELECTRAISE(ABORT,''workout_setsrefused:machine_refnamesnoax_machinerow--registeritfirst(kit_life.shmachine-register)'');END');
INSERT INTO _guard_078 SELECT 'trigger' WHERE NOT EXISTS (SELECT 1 FROM sqlite_master WHERE name='trg_workout_sets_machine_known_upd' AND replace(replace(replace(replace(rtrim(sql,';'),char(10),''),char(13),''),char(9),''),' ','')='CREATETRIGGERtrg_workout_sets_machine_known_updBEFOREUPDATEOFmachine_refONworkout_setsWHENNEW.machine_refISNOTNULLANDNOTEXISTS(SELECT1FROMax_machineWHEREulid=NEW.machine_ref)BEGINSELECTRAISE(ABORT,''workout_setsrefused:machine_refnamesnoax_machinerow--registeritfirst(kit_life.shmachine-register)'');END');
INSERT INTO _guard_078 SELECT 'extra trigger' WHERE (SELECT count(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='workout_sets')<>4;
INSERT INTO _guard_078 SELECT 'set_kind' WHERE NOT EXISTS (SELECT 1 FROM sqlite_master WHERE name='workout_sets' AND instr(sql,"'prep','ramp','working','hold'")>0);
DROP TABLE temp._guard_078;
CREATE TABLE training_policy_bundles (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, version INTEGER NOT NULL,
 parent_defaults_version TEXT NOT NULL, effective_at INTEGER NOT NULL,
 digest TEXT NOT NULL, UNIQUE(owner_id,version)
);
CREATE TABLE training_policy_rules (
 bundle_id TEXT NOT NULL REFERENCES training_policy_bundles(id), key TEXT NOT NULL,
 value_type TEXT NOT NULL, value_json TEXT NOT NULL CHECK(json_valid(value_json)),
 scope TEXT NOT NULL CHECK(scope IN ('general','owner','session')),
 source_ref TEXT NOT NULL, source_date TEXT NOT NULL,
 approval_status TEXT NOT NULL CHECK(approval_status IN ('proposed','approved','rejected')),
 PRIMARY KEY(bundle_id,key,scope)
);
CREATE TABLE workout_lifts (
 id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES workouts(id),
 exercise_id TEXT NOT NULL, exercise_name TEXT NOT NULL, slot_num INTEGER,
 order_index INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','closed','skipped')),
 closed_reason TEXT, closed_revision INTEGER, execution_index INTEGER,
 variant_id TEXT NOT NULL, machine_ref TEXT, implement_resolution_id TEXT,
 UNIQUE(session_id,order_index), UNIQUE(id,session_id)
);
CREATE TABLE session_equipment_snapshots (
 id TEXT PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES workouts(id), catalog_version INTEGER NOT NULL,
 variant_id TEXT NOT NULL, machine_ref TEXT, grid_version TEXT NOT NULL, inventory_version TEXT NOT NULL,
 load_basis TEXT NOT NULL CHECK(load_basis IN ('total','per_hand','assistance')),
 implement_count INTEGER NOT NULL CHECK(implement_count>=0), mounted_load_g INTEGER CHECK(mounted_load_g>=0), evidence_id TEXT
);
CREATE TABLE session_commands (
 session_id INTEGER NOT NULL REFERENCES workouts(id), command_id TEXT NOT NULL,
 event_version INTEGER NOT NULL CHECK(event_version=2), actor TEXT NOT NULL CHECK(actor IN ('member','trainer','system')),
 expected_revision INTEGER NOT NULL, input_digest TEXT NOT NULL, event_type TEXT NOT NULL,
 input_json TEXT NOT NULL CHECK(json_valid(input_json)), committed_revision INTEGER NOT NULL,
 receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)), accepted_at INTEGER NOT NULL,
 source_message_id TEXT, undo_of TEXT, PRIMARY KEY(session_id,command_id)
);
CREATE TABLE session_trainer_approvals (
 id TEXT PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES workouts(id), base_revision INTEGER NOT NULL,
 view_digest TEXT NOT NULL, decision_json TEXT NOT NULL CHECK(json_valid(decision_json)), cue_ast TEXT NOT NULL CHECK(json_valid(cue_ast)),
 dependency_digest TEXT NOT NULL, policy_bundle_id TEXT NOT NULL REFERENCES training_policy_bundles(id),
 catalog_version INTEGER NOT NULL, expires_epoch INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('prepared','bound','invalidated','expired'))
);
CREATE TABLE session_outbox (
 session_id INTEGER NOT NULL REFERENCES workouts(id), revision INTEGER NOT NULL,
 frame_kind TEXT NOT NULL CHECK(frame_kind IN ('step','answer','closeout','timer','setup')),
 session_epoch INTEGER NOT NULL, rest_epoch INTEGER, view_digest TEXT NOT NULL,
 trainer_approval_id TEXT REFERENCES session_trainer_approvals(id), not_before_at INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('waiting_trainer','ready','publishing','delivered','superseded','uncertain')),
 stage TEXT NOT NULL CHECK(stage IN ('retire_markup','neutralize_trainer','edit_table','edit_trainer','attach_keyboard','acknowledged')),
 attempts INTEGER NOT NULL DEFAULT 0, lease_fence INTEGER NOT NULL DEFAULT 0, last_error TEXT,
 PRIMARY KEY(session_id,revision,frame_kind)
);
CREATE TABLE session_surfaces (
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 surface_kind TEXT NOT NULL CHECK(surface_kind IN ('table','trainer','pin','end_workout','end_fatigue','end_calories')),
 chat_id TEXT NOT NULL, message_id TEXT NOT NULL, published_revision INTEGER NOT NULL,
 body_digest TEXT NOT NULL, markup_digest TEXT NOT NULL,
 markup_state TEXT NOT NULL CHECK(markup_state IN ('clear','active','unknown')),
 delivery_key TEXT NOT NULL, receipt_id TEXT NOT NULL, PRIMARY KEY(session_id,surface_kind),
 CHECK(markup_state!='active' OR surface_kind='trainer')
);
CREATE TABLE session_evidence (
 id TEXT PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES workouts(id), event_id TEXT NOT NULL,
 kind TEXT NOT NULL, value TEXT NOT NULL CHECK(json_valid(value)), unit TEXT,
 scope TEXT NOT NULL CHECK(scope IN ('entry','set','lift','session')), observed_at INTEGER NOT NULL,
 source_kind TEXT NOT NULL, source_ref TEXT NOT NULL
);
CREATE TABLE session_delivery_journal (
 journal_seq INTEGER PRIMARY KEY, operation_id TEXT NOT NULL, session_id INTEGER NOT NULL REFERENCES workouts(id),
 command_id TEXT NOT NULL, revision INTEGER NOT NULL, frame_kind TEXT NOT NULL,
 sender TEXT NOT NULL CHECK(sender='publisher'), surface_kind TEXT, message_id TEXT, callback_digest TEXT,
 session_epoch INTEGER NOT NULL, rest_epoch INTEGER, operation TEXT NOT NULL, request_digest TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('requested','acknowledged','unchanged','rejected','failed','uncertain','quarantined','reconciled')),
 acknowledged_result TEXT CHECK(acknowledged_result IS NULL OR json_valid(acknowledged_result)),
 latency_ms INTEGER NOT NULL CHECK(latency_ms>=0), error TEXT, artifact_digest TEXT NOT NULL,
 delivery_key TEXT NOT NULL, lease_fence INTEGER NOT NULL,
 CHECK((state IN ('acknowledged','unchanged','reconciled')) = (acknowledged_result IS NOT NULL))
);
ALTER TABLE workouts ADD COLUMN type TEXT;
ALTER TABLE workouts ADD COLUMN member_id TEXT;
ALTER TABLE workouts ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1 CHECK(session_version IN (1,2));
ALTER TABLE workouts ADD COLUMN revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0);
ALTER TABLE workouts ADD COLUMN phase TEXT CHECK(phase IN ('plan','prep','entry','ready','rest','lift_done','post','closed'));
ALTER TABLE workouts ADD COLUMN active_lift_id INTEGER REFERENCES workout_lifts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE workouts ADD COLUMN active_set_id INTEGER REFERENCES workout_sets(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE workouts ADD COLUMN action_token_hash TEXT;
ALTER TABLE workouts ADD COLUMN action_kind TEXT;
ALTER TABLE workouts ADD COLUMN input_stage TEXT;
ALTER TABLE workouts ADD COLUMN policy_bundle_id TEXT REFERENCES training_policy_bundles(id);
ALTER TABLE workouts ADD COLUMN catalog_version INTEGER;
ALTER TABLE workouts ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workouts ADD COLUMN rest_epoch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workouts ADD COLUMN rest_started_at INTEGER;
ALTER TABLE workouts ADD COLUMN rest_deadline_at INTEGER;
ALTER TABLE workouts ADD COLUMN rest_duration_ms INTEGER;
ALTER TABLE workouts ADD COLUMN closed_at INTEGER;
ALTER TABLE workouts ADD COLUMN close_reason TEXT;
ALTER TABLE workouts ADD COLUMN kcal_status TEXT NOT NULL DEFAULT 'pending' CHECK(kcal_status IN ('pending','measured','unmeasured'));
ALTER TABLE workout_sets ADD COLUMN lift_id INTEGER REFERENCES workout_lifts(id);
ALTER TABLE workout_sets ADD COLUMN row_status TEXT NOT NULL DEFAULT 'logged' CHECK(row_status IN ('planned','logged','void','omitted'));
ALTER TABLE workout_sets ADD COLUMN step_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workout_sets ADD COLUMN target_load_g INTEGER CHECK(target_load_g>=0);
ALTER TABLE workout_sets ADD COLUMN target_rir_min INTEGER CHECK(target_rir_min BETWEEN 0 AND 4);
ALTER TABLE workout_sets ADD COLUMN target_rir_max INTEGER CHECK(target_rir_max BETWEEN 0 AND 4);
ALTER TABLE workout_sets ADD COLUMN target_rest_ms INTEGER CHECK(target_rest_ms>=0);
ALTER TABLE workout_sets ADD COLUMN target_unit TEXT NOT NULL DEFAULT 'reps' CHECK(target_unit IN ('reps','seconds','breaths','steps'));
ALTER TABLE workout_sets ADD COLUMN target_count INTEGER;
ALTER TABLE workout_sets ADD COLUMN target_hold_sec INTEGER;
ALTER TABLE workout_sets ADD COLUMN actual_hold_sec INTEGER;
ALTER TABLE workout_sets ADD COLUMN actual_unit TEXT CHECK(actual_unit IN ('reps','seconds','breaths','steps'));
ALTER TABLE workout_sets ADD COLUMN rir_is_lower_bound INTEGER NOT NULL DEFAULT 0 CHECK(rir_is_lower_bound IN (0,1));
ALTER TABLE workout_sets ADD COLUMN actual_source TEXT DEFAULT 'legacy_unknown' CHECK(actual_source IN ('tap_confirmed','typed','legacy_unknown'));
ALTER TABLE workout_sets ADD COLUMN prescribed_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workout_sets ADD COLUMN recorded_revision INTEGER;
ALTER TABLE workout_sets ADD COLUMN policy_bundle_id TEXT REFERENCES training_policy_bundles(id);
ALTER TABLE workout_sets ADD COLUMN basis_event_id TEXT;
ALTER TABLE workout_sets ADD COLUMN equipment_snapshot_id TEXT REFERENCES session_equipment_snapshots(id);
CREATE UNIQUE INDEX one_open_workout_per_member ON workouts(member_id) WHERE session_version=2 AND phase!='closed';
CREATE UNIQUE INDEX one_active_keyboard ON session_surfaces(session_id) WHERE markup_state='active';
CREATE UNIQUE INDEX one_active_keyboard_per_chat ON session_surfaces(chat_id) WHERE markup_state='active';
CREATE INDEX idx_v2_set_order ON workout_sets(session_id,lift_id,row_status,step_order);
CREATE VIEW v2_logged_workout_sets AS SELECT * FROM workout_sets WHERE row_status='logged';
-- Extra service metadata is not an alternative prescription store.
CREATE TABLE session_input_state (
 session_id INTEGER PRIMARY KEY REFERENCES workouts(id), member_id TEXT NOT NULL,
 chat_id TEXT NOT NULL, pending_completed_at INTEGER, started_at INTEGER NOT NULL,
 token_nonce TEXT,
 mode TEXT NOT NULL CHECK(mode IN ('regular','free')), split_id TEXT, claimed_split_id TEXT
);
CREATE TABLE session_import_audit (
 source_digest TEXT NOT NULL, session_id INTEGER NOT NULL REFERENCES workouts(id),
 resolution_json TEXT NOT NULL CHECK(json_valid(resolution_json)), accepted_at INTEGER NOT NULL,
 PRIMARY KEY(source_digest,session_id)
);
CREATE TRIGGER v2_validate_workouts_insert BEFORE INSERT ON workouts
WHEN NEW.session_version=2 AND (NEW.member_id IS NULL OR NEW.phase IS NULL OR NEW.policy_bundle_id IS NULL OR NEW.catalog_version IS NULL OR
 (NEW.phase='closed' AND (NEW.rest_deadline_at IS NOT NULL OR NEW.action_token_hash IS NOT NULL OR NEW.active_set_id IS NOT NULL OR NEW.closed_at IS NULL)) OR
 (NEW.rest_deadline_at IS NOT NULL AND (NEW.phase!='rest' OR NEW.rest_started_at IS NULL OR NEW.rest_duration_ms IS NULL OR NEW.rest_duration_ms<0 OR NEW.rest_deadline_at!=NEW.rest_started_at+NEW.rest_duration_ms)) OR
 (NEW.active_lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.active_lift_id AND session_id=NEW.id AND status='open')) OR
 (NEW.active_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_sets WHERE id=NEW.active_set_id AND session_id=NEW.id AND row_status IN ('planned','void'))))
BEGIN SELECT RAISE(ABORT,'invalid v2 workouts'); END;
CREATE TRIGGER v2_validate_workouts_update BEFORE UPDATE ON workouts
WHEN NEW.session_version=2 AND (NEW.member_id IS NULL OR NEW.phase IS NULL OR NEW.policy_bundle_id IS NULL OR NEW.catalog_version IS NULL OR
 (NEW.phase='closed' AND (NEW.rest_deadline_at IS NOT NULL OR NEW.action_token_hash IS NOT NULL OR NEW.active_set_id IS NOT NULL OR NEW.closed_at IS NULL)) OR
 (NEW.rest_deadline_at IS NOT NULL AND (NEW.phase!='rest' OR NEW.rest_started_at IS NULL OR NEW.rest_duration_ms IS NULL OR NEW.rest_duration_ms<0 OR NEW.rest_deadline_at!=NEW.rest_started_at+NEW.rest_duration_ms)) OR
 (NEW.active_lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.active_lift_id AND session_id=NEW.id AND status='open')) OR
 (NEW.active_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_sets WHERE id=NEW.active_set_id AND session_id=NEW.id AND row_status IN ('planned','void'))))
BEGIN SELECT RAISE(ABORT,'invalid v2 workouts'); END;
CREATE TRIGGER v2_validate_workout_sets_insert BEFORE INSERT ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=2 AND (
 NEW.actual_source IS 'legacy_unknown' OR NEW.policy_bundle_id IS NULL OR NEW.target_rest_ms IS NULL OR
 (NEW.lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.lift_id AND session_id=NEW.session_id)) OR
 (NEW.target_unit='reps' AND (NEW.target_reps IS NULL OR NEW.target_reps<=0 OR NEW.target_count IS NOT NULL)) OR
 (NEW.target_unit!='reps' AND (NEW.target_reps IS NOT NULL OR NEW.target_count IS NULL OR NEW.target_count<=0)) OR
 (NEW.actual_unit IS NOT NULL AND NEW.actual_unit!=NEW.target_unit) OR
 (NEW.row_status='logged' AND (NEW.actual_source IS NULL OR NEW.actual_unit IS NULL OR NEW.recorded_revision IS NULL OR NEW.recorded_at IS NULL)) OR
 (NEW.row_status='logged' AND NEW.set_kind IN ('working','ramp') AND (NEW.reps IS NULL OR NEW.reps<=0 OR NEW.weight_kg IS NULL)) OR
 (NEW.row_status='logged' AND NEW.target_unit='seconds' AND (NEW.actual_hold_sec IS NULL OR NEW.actual_hold_sec<0)) OR
 (NEW.rir_is_lower_bound=1 AND (NEW.rir IS NULL OR NEW.rir!=4)) OR
 (NEW.target_rir_min IS NOT NULL AND NEW.target_rir_max IS NOT NULL AND NEW.target_rir_min>NEW.target_rir_max))
BEGIN SELECT RAISE(ABORT,'invalid v2 workout_sets'); END;
CREATE TRIGGER v2_validate_workout_sets_update BEFORE UPDATE ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=2 AND (
 NEW.actual_source IS 'legacy_unknown' OR NEW.policy_bundle_id IS NULL OR NEW.target_rest_ms IS NULL OR
 (NEW.lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.lift_id AND session_id=NEW.session_id)) OR
 (NEW.target_unit='reps' AND (NEW.target_reps IS NULL OR NEW.target_reps<=0 OR NEW.target_count IS NOT NULL)) OR
 (NEW.target_unit!='reps' AND (NEW.target_reps IS NOT NULL OR NEW.target_count IS NULL OR NEW.target_count<=0)) OR
 (NEW.actual_unit IS NOT NULL AND NEW.actual_unit!=NEW.target_unit) OR
 (NEW.row_status='logged' AND (NEW.actual_source IS NULL OR NEW.actual_unit IS NULL OR NEW.recorded_revision IS NULL OR NEW.recorded_at IS NULL)) OR
 (NEW.row_status='logged' AND NEW.set_kind IN ('working','ramp') AND (NEW.reps IS NULL OR NEW.reps<=0 OR NEW.weight_kg IS NULL)) OR
 (NEW.row_status='logged' AND NEW.target_unit='seconds' AND (NEW.actual_hold_sec IS NULL OR NEW.actual_hold_sec<0)) OR
 (NEW.rir_is_lower_bound=1 AND (NEW.rir IS NULL OR NEW.rir!=4)) OR
 (NEW.target_rir_min IS NOT NULL AND NEW.target_rir_max IS NOT NULL AND NEW.target_rir_min>NEW.target_rir_max))
BEGIN SELECT RAISE(ABORT,'invalid v2 workout_sets'); END;
DROP TRIGGER trg_workout_sets_reps_counted;
CREATE TRIGGER trg_workout_sets_reps_counted
BEFORE INSERT ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=1 AND NEW.reps IS NULL AND NEW.set_kind NOT IN ('prep','ramp')
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: reps is required for a working/hold set -- only a prep or ramp row may be a done-tap (reps NULL, target_reps = the prescription)');
END;
DROP TRIGGER trg_workout_sets_reps_counted_upd;
CREATE TRIGGER trg_workout_sets_reps_counted_upd
BEFORE UPDATE OF reps, set_kind ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=1 AND NEW.reps IS NULL AND NEW.set_kind NOT IN ('prep','ramp')
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: reps is required for a working/hold set -- only a prep or ramp row may be a done-tap (reps NULL, target_reps = the prescription)');
END;
CREATE TRIGGER v2_logged_targets_frozen BEFORE UPDATE ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=OLD.session_id)=2 AND OLD.row_status IN ('logged','void') AND (
 NEW.target_reps IS NOT OLD.target_reps OR NEW.target_load_g IS NOT OLD.target_load_g OR
 NEW.target_unit IS NOT OLD.target_unit OR NEW.target_count IS NOT OLD.target_count OR
 NEW.target_rir_min IS NOT OLD.target_rir_min OR NEW.target_rir_max IS NOT OLD.target_rir_max OR
 NEW.target_rest_ms IS NOT OLD.target_rest_ms OR NEW.target_hold_sec IS NOT OLD.target_hold_sec OR
 NEW.prescribed_revision IS NOT OLD.prescribed_revision OR NEW.policy_bundle_id IS NOT OLD.policy_bundle_id OR
 NEW.equipment_snapshot_id IS NOT OLD.equipment_snapshot_id OR NEW.basis_event_id IS NOT OLD.basis_event_id)
BEGIN SELECT RAISE(ABORT,'logged prescription is immutable'); END;
-- Other authority checks (pending RIR stage, event-bound actual correction,
-- revision increments, no direct v2 writers) are the kit service's transaction
-- contract; SQLite alone cannot enforce a service ACL.
CREATE TRIGGER v2_session_commands_update BEFORE UPDATE ON session_commands
BEGIN SELECT RAISE(ABORT,'append-only session_commands'); END;
CREATE TRIGGER v2_session_commands_delete BEFORE DELETE ON session_commands
BEGIN SELECT RAISE(ABORT,'append-only session_commands'); END;
CREATE TRIGGER v2_session_delivery_journal_update BEFORE UPDATE ON session_delivery_journal
BEGIN SELECT RAISE(ABORT,'append-only session_delivery_journal'); END;
CREATE TRIGGER v2_session_delivery_journal_delete BEFORE DELETE ON session_delivery_journal
BEGIN SELECT RAISE(ABORT,'append-only session_delivery_journal'); END;
CREATE TRIGGER v2_session_equipment_snapshots_update BEFORE UPDATE ON session_equipment_snapshots
BEGIN SELECT RAISE(ABORT,'append-only session_equipment_snapshots'); END;
CREATE TRIGGER v2_session_equipment_snapshots_delete BEFORE DELETE ON session_equipment_snapshots
BEGIN SELECT RAISE(ABORT,'append-only session_equipment_snapshots'); END;
CREATE TRIGGER v2_session_evidence_update BEFORE UPDATE ON session_evidence
BEGIN SELECT RAISE(ABORT,'append-only session_evidence'); END;
CREATE TRIGGER v2_session_evidence_delete BEFORE DELETE ON session_evidence
BEGIN SELECT RAISE(ABORT,'append-only session_evidence'); END;
CREATE TRIGGER v2_training_policy_bundles_update BEFORE UPDATE ON training_policy_bundles
BEGIN SELECT RAISE(ABORT,'append-only training_policy_bundles'); END;
CREATE TRIGGER v2_training_policy_bundles_delete BEFORE DELETE ON training_policy_bundles
BEGIN SELECT RAISE(ABORT,'append-only training_policy_bundles'); END;
CREATE TRIGGER v2_training_policy_rules_update BEFORE UPDATE ON training_policy_rules
BEGIN SELECT RAISE(ABORT,'append-only training_policy_rules'); END;
CREATE TRIGGER v2_training_policy_rules_delete BEFORE DELETE ON training_policy_rules
BEGIN SELECT RAISE(ABORT,'append-only training_policy_rules'); END;
CREATE TRIGGER v2_session_trainer_approvals_intent_frozen BEFORE UPDATE ON session_trainer_approvals
WHEN NEW.id IS NOT OLD.id OR
 NEW.session_id IS NOT OLD.session_id OR
 NEW.base_revision IS NOT OLD.base_revision OR
 NEW.view_digest IS NOT OLD.view_digest OR
 NEW.decision_json IS NOT OLD.decision_json OR
 NEW.cue_ast IS NOT OLD.cue_ast OR
 NEW.dependency_digest IS NOT OLD.dependency_digest OR
 NEW.policy_bundle_id IS NOT OLD.policy_bundle_id OR
 NEW.catalog_version IS NOT OLD.catalog_version OR
 NEW.expires_epoch IS NOT OLD.expires_epoch
BEGIN SELECT RAISE(ABORT,'immutable session_trainer_approvals intent'); END;
CREATE TRIGGER v2_session_outbox_intent_frozen BEFORE UPDATE ON session_outbox
WHEN NEW.session_id IS NOT OLD.session_id OR
 NEW.revision IS NOT OLD.revision OR
 NEW.frame_kind IS NOT OLD.frame_kind OR
 NEW.session_epoch IS NOT OLD.session_epoch OR
 NEW.rest_epoch IS NOT OLD.rest_epoch OR
 NEW.view_digest IS NOT OLD.view_digest OR
 NEW.not_before_at IS NOT OLD.not_before_at
BEGIN SELECT RAISE(ABORT,'immutable session_outbox intent'); END;
-- Widen only the known set-kind token list, after all ordinary DDL. No row
-- rewrite/drop: dependent pain_event FKs and every imported NULL remain intact.
PRAGMA writable_schema=ON;
UPDATE sqlite_master SET sql=replace(sql,"'prep','ramp','working','hold'", "'prep','ramp','working','hold','post'") WHERE type='table' AND name='workout_sets';
PRAGMA writable_schema=OFF;
PRAGMA user_version=78;
COMMIT;
