-- Source-backed nullable rep actuals. Generic loaded rows retain their guards.
-- reversible: YES before null actuals exist: restore the two schema-78 triggers.
.bail on
.timeout 5000
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TEMP TABLE session_nullable_guard(version INTEGER CHECK(version=84));
INSERT INTO session_nullable_guard SELECT user_version FROM pragma_user_version;
DROP TABLE session_nullable_guard;
DROP TRIGGER v2_validate_workout_sets_insert;
DROP TRIGGER v2_validate_workout_sets_update;
CREATE TRIGGER v2_validate_workout_sets_insert BEFORE INSERT ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=2 AND (
 NEW.actual_source IS 'legacy_unknown' OR NEW.policy_bundle_id IS NULL OR NEW.target_rest_ms IS NULL OR
 (NEW.lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.lift_id AND session_id=NEW.session_id)) OR
 (NEW.target_unit='reps' AND (NEW.target_reps IS NULL OR NEW.target_reps<=0 OR NEW.target_count IS NOT NULL)) OR
 (NEW.target_unit!='reps' AND (NEW.target_reps IS NOT NULL OR NEW.target_count IS NULL OR NEW.target_count<=0)) OR
 (NEW.actual_unit IS NOT NULL AND NEW.actual_unit!=NEW.target_unit) OR
 (NEW.row_status='logged' AND (NEW.actual_source IS NULL OR NEW.actual_unit IS NULL OR NEW.recorded_revision IS NULL OR NEW.recorded_at IS NULL)) OR
 (NEW.row_status='logged' AND NEW.set_kind IN ('working','ramp') AND (NEW.reps IS NULL OR NEW.reps<=0 OR (NEW.weight_kg IS NULL AND NOT EXISTS(
 SELECT 1 FROM session_evidence e, json_each(CASE e.kind WHEN 'program_plan/1'
 THEN json_extract(e.value,'$.rows') ELSE json_extract(e.value,'$.applied_rows') END) b
 WHERE e.session_id=NEW.session_id AND e.kind IN ('program_plan/1','prescription_basis/2')
 AND json_extract(b.value,'$.set_id')=NEW.id
 AND json_extract(b.value,'$.row.load_state')='unloaded'
 AND json_type(b.value,'$.row.load_g')='null' AND NEW.target_load_g IS NULL)))) OR
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
 (NEW.row_status='logged' AND NEW.set_kind IN ('working','ramp') AND (NEW.reps IS NULL OR NEW.reps<=0 OR (NEW.weight_kg IS NULL AND NOT EXISTS(
 SELECT 1 FROM session_evidence e, json_each(CASE e.kind WHEN 'program_plan/1'
 THEN json_extract(e.value,'$.rows') ELSE json_extract(e.value,'$.applied_rows') END) b
 WHERE e.session_id=NEW.session_id AND e.kind IN ('program_plan/1','prescription_basis/2')
 AND json_extract(b.value,'$.set_id')=NEW.id
 AND json_extract(b.value,'$.row.load_state')='unloaded'
 AND json_type(b.value,'$.row.load_g')='null' AND NEW.target_load_g IS NULL)))) OR
 (NEW.row_status='logged' AND NEW.target_unit='seconds' AND (NEW.actual_hold_sec IS NULL OR NEW.actual_hold_sec<0)) OR
 (NEW.rir_is_lower_bound=1 AND (NEW.rir IS NULL OR NEW.rir!=4)) OR
 (NEW.target_rir_min IS NOT NULL AND NEW.target_rir_max IS NOT NULL AND NEW.target_rir_min>NEW.target_rir_max))
BEGIN SELECT RAISE(ABORT,'invalid v2 workout_sets'); END;
PRAGMA user_version=85;
COMMIT;
