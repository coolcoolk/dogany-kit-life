-- Lossless program evidence seat; this does NOT enable the /2 planner.
-- reversible: YES before typed evidence is written: drop the three
-- session_program_evidence_* triggers and restore user_version=83.
-- After typed evidence exists, restore the verified pre-084 backup instead.
-- Existing session/history rows and the load-basis CHECK are unchanged.
.bail on
.timeout 5000
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TEMP TABLE session_program_evidence_guard (version INTEGER CHECK(version=83));
INSERT INTO session_program_evidence_guard SELECT user_version FROM pragma_user_version;
DROP TABLE session_program_evidence_guard;
CREATE TRIGGER session_program_evidence_shape BEFORE INSERT ON session_evidence
WHEN NEW.kind IN ('program_plan/1','prescription_basis/2')
BEGIN
 SELECT CASE WHEN json_valid(NEW.value)<>1 THEN RAISE(ABORT,'invalid typed program evidence JSON') END;
 SELECT CASE WHEN json_extract(NEW.value,'$.schema') IS NOT NEW.kind
  THEN RAISE(ABORT,'typed program evidence discriminator mismatch') END;
 SELECT CASE WHEN NEW.kind='program_plan/1' AND (
  NEW.scope<>'session' OR
  json_type(NEW.value,'$.configuration_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.program_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.policy_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.slots') IS NOT 'array' OR
  json_type(NEW.value,'$.blocks') IS NOT 'array' OR
  json_type(NEW.value,'$.calculators') IS NOT 'object')
  THEN RAISE(ABORT,'incomplete program plan evidence') END;
 SELECT CASE WHEN NEW.kind='prescription_basis/2' AND (
  NEW.scope<>'lift' OR
  json_type(NEW.value,'$.slot_id') IS NOT 'text' OR
  json_type(NEW.value,'$.calculator_ref') IS NOT 'text' OR
  json_type(NEW.value,'$.request_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.response_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.dependencies_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.basis') IS NOT 'text' OR
  json_type(NEW.value,'$.evidence_digest') IS NOT 'text')
  THEN RAISE(ABORT,'incomplete prescription basis evidence') END;
END;
CREATE TRIGGER session_program_evidence_immutable BEFORE UPDATE ON session_evidence
WHEN OLD.kind IN ('program_plan/1','prescription_basis/2') OR
 NEW.kind IN ('program_plan/1','prescription_basis/2')
BEGIN SELECT RAISE(ABORT,'immutable typed program evidence'); END;
CREATE TRIGGER session_program_evidence_retained BEFORE DELETE ON session_evidence
WHEN OLD.kind IN ('program_plan/1','prescription_basis/2')
BEGIN SELECT RAISE(ABORT,'retained typed program evidence'); END;
PRAGMA user_version=84;
COMMIT;
