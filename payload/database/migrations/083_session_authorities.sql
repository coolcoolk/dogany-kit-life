-- K1: additive shared-DB admission, revocation and transition evidence.
-- reversible: YES before any K1 write, by dropping the five session_* authority tables
--   and setting user_version back to 82; NO after fences/admissions/acks are written
--   (authority evidence would be lost) -- restore the verified pre-083 backup instead.
-- Cached local/shared refs (318 distinct tips) allocate no migration above 082.
-- No rollback operation is supplied by this migration.
.bail on
.timeout 5000
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TEMP TABLE session_authority_guard (version INTEGER CHECK(version=82));
INSERT INTO session_authority_guard SELECT user_version FROM pragma_user_version;
DROP TABLE session_authority_guard;
CREATE TABLE session_pack_fences (
 pack TEXT PRIMARY KEY, token INTEGER NOT NULL CHECK(token>0),
 engine TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
 checkpoint TEXT NOT NULL CHECK(json_valid(checkpoint)),
 fence TEXT NOT NULL CHECK(json_valid(fence)),
 active_engine TEXT, active_generation INTEGER, closure TEXT CHECK(json_valid(closure))
);
CREATE TABLE session_pack_checkpoints (
 pack TEXT NOT NULL, token INTEGER NOT NULL CHECK(token>0),
 checkpoint TEXT NOT NULL CHECK(json_valid(checkpoint)),
 fence TEXT NOT NULL CHECK(json_valid(fence)), closure TEXT CHECK(json_valid(closure)),
 PRIMARY KEY(pack,token), UNIQUE(pack,checkpoint)
);
CREATE TRIGGER session_checkpoint_immutable BEFORE UPDATE ON session_pack_checkpoints
WHEN NEW.pack<>OLD.pack OR NEW.token<>OLD.token OR NEW.checkpoint<>OLD.checkpoint OR
 NEW.fence<>OLD.fence OR OLD.closure IS NOT NULL
BEGIN SELECT RAISE(ABORT,'immutable completed checkpoint'); END;
CREATE TRIGGER session_checkpoint_retained BEFORE DELETE ON session_pack_checkpoints
BEGIN SELECT RAISE(ABORT,'retained checkpoint'); END;
CREATE TABLE session_invocation_admissions (
 binding_id TEXT NOT NULL REFERENCES session_live_commands(binding_id),
 invocation_generation INTEGER NOT NULL CHECK(invocation_generation>0),
 attempt TEXT NOT NULL CHECK(json_valid(attempt)), command_digest TEXT NOT NULL,
 principal TEXT NOT NULL CHECK(json_valid(principal)),
 PRIMARY KEY(binding_id,invocation_generation)
);
CREATE TRIGGER session_admission_immutable BEFORE UPDATE ON session_invocation_admissions
BEGIN SELECT RAISE(ABORT,'immutable invocation admission'); END;
CREATE TRIGGER session_admission_retained BEFORE DELETE ON session_invocation_admissions
BEGIN SELECT RAISE(ABORT,'retained invocation admission'); END;
CREATE TABLE session_invocation_revocations (
 binding_id TEXT NOT NULL REFERENCES session_live_commands(binding_id),
 invocation_generation INTEGER NOT NULL CHECK(invocation_generation>0),
 command_digest TEXT NOT NULL, principal TEXT NOT NULL CHECK(json_valid(principal)),
 record TEXT NOT NULL CHECK(json_valid(record)),
 PRIMARY KEY(binding_id,invocation_generation)
);
CREATE TABLE session_transition_acks (
 pack TEXT NOT NULL, token INTEGER NOT NULL CHECK(token>0),
 target TEXT NOT NULL CHECK(json_valid(target)), ack_digest TEXT NOT NULL UNIQUE,
 acknowledgement TEXT NOT NULL CHECK(json_valid(acknowledgement)),
 PRIMARY KEY(pack,token,target)
);
CREATE TRIGGER session_revocation_immutable BEFORE UPDATE ON session_invocation_revocations
BEGIN SELECT RAISE(ABORT,'immutable invocation revocation'); END;
CREATE TRIGGER session_revocation_retained BEFORE DELETE ON session_invocation_revocations
BEGIN SELECT RAISE(ABORT,'retained invocation revocation'); END;
CREATE TRIGGER session_transition_ack_immutable BEFORE UPDATE ON session_transition_acks
BEGIN SELECT RAISE(ABORT,'immutable transition acknowledgement'); END;
CREATE TRIGGER session_transition_ack_retained BEFORE DELETE ON session_transition_acks
BEGIN SELECT RAISE(ABORT,'retained transition acknowledgement'); END;
CREATE TRIGGER session_pack_fence_monotonic BEFORE UPDATE ON session_pack_fences
WHEN NEW.token<OLD.token OR NEW.token>OLD.token+1 OR
 (NEW.token=OLD.token AND (NEW.fence<>OLD.fence OR NEW.checkpoint<>OLD.checkpoint))
BEGIN SELECT RAISE(ABORT,'nonmonotonic pack fence'); END;
CREATE TRIGGER session_pack_fence_retained BEFORE DELETE ON session_pack_fences
BEGIN SELECT RAISE(ABORT,'retained pack fence'); END;
PRAGMA user_version=83;
COMMIT;
