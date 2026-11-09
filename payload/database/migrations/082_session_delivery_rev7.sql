-- 082_session_delivery_rev7.sql: F1 rev7 terminal outcomes and sealed branches.
-- Additive logical extension: rebuild only the stage CHECK, copying every row.
-- Historical signed scope/plan/receipt bytes remain unchanged; runtime resolves
-- old adapter labels through session_live_scopes.session_id, never by casting.
-- Cached origin refs (124) contain no 082; live ls-remote was credential-denied.
-- reversible: NO after rev7 writes; restore the verified pre-upgrade backup.
.bail on
.timeout 5000
PRAGMA foreign_keys=OFF;
BEGIN IMMEDIATE;
CREATE TEMP TABLE delivery_rev7_guard (version INTEGER CHECK(version=81));
INSERT INTO delivery_rev7_guard SELECT user_version FROM pragma_user_version;
DROP TABLE delivery_rev7_guard;
CREATE TABLE session_live_stages_new (
 stage_key TEXT PRIMARY KEY CHECK(json_valid(stage_key)),
 outbox_id TEXT NOT NULL REFERENCES session_live_deliveries(outbox_id), ordinal INTEGER NOT NULL,
 binding TEXT NOT NULL CHECK(json_valid(binding)), spec TEXT NOT NULL CHECK(json_valid(spec)),
 state TEXT NOT NULL CHECK(state IN ('pending','inflight','uncertain','acknowledged','satisfied_absent','satisfied_retained','satisfied_not_selected','waiting_retry')),
 next_attempt_at_ms INTEGER, seq INTEGER NOT NULL DEFAULT 0, attempt_fence INTEGER, receipt TEXT,
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 rebound INTEGER NOT NULL DEFAULT 0 CHECK(rebound IN (0,1)), UNIQUE(outbox_id,ordinal)
);
INSERT INTO session_live_stages_new(stage_key,outbox_id,ordinal,binding,spec,state,seq,attempt_fence,receipt,session_id,rebound)
 SELECT stage_key,outbox_id,ordinal,binding,spec,state,seq,attempt_fence,receipt,session_id,rebound FROM session_live_stages;
DROP TABLE session_live_stages;
ALTER TABLE session_live_stages_new RENAME TO session_live_stages;
ALTER TABLE session_live_commands ADD COLUMN invocation_generation INTEGER NOT NULL DEFAULT 0;
CREATE TABLE session_live_selections (
 outbox_id TEXT NOT NULL REFERENCES session_live_deliveries(outbox_id), group_id TEXT NOT NULL,
 selection_digest TEXT NOT NULL UNIQUE, selection TEXT NOT NULL CHECK(json_valid(selection)),
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 PRIMARY KEY(outbox_id,group_id)
);
CREATE TABLE session_live_evidence (
 digest TEXT PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL CHECK(json_valid(value)),
 outbox_id TEXT REFERENCES session_live_deliveries(outbox_id), binding_id TEXT REFERENCES session_live_commands(binding_id),
 session_id INTEGER NOT NULL REFERENCES workouts(id)
);
CREATE TRIGGER session_live_selections_immutable BEFORE UPDATE ON session_live_selections
BEGIN SELECT RAISE(ABORT,'immutable branch selection'); END;
CREATE TRIGGER session_live_selections_retained BEFORE DELETE ON session_live_selections
BEGIN SELECT RAISE(ABORT,'retained branch selection'); END;
CREATE TRIGGER session_live_evidence_immutable BEFORE UPDATE ON session_live_evidence
BEGIN SELECT RAISE(ABORT,'immutable delivery evidence'); END;
CREATE TRIGGER session_live_evidence_retained BEFORE DELETE ON session_live_evidence
BEGIN SELECT RAISE(ABORT,'retained delivery evidence'); END;
CREATE TEMP TABLE delivery_rev7_fk_guard (violations INTEGER CHECK(violations=0));
INSERT INTO delivery_rev7_fk_guard SELECT count(*) FROM pragma_foreign_key_check;
DROP TABLE delivery_rev7_fk_guard;
PRAGMA user_version=82;
COMMIT;
PRAGMA foreign_keys=ON;
