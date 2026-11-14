-- 081_session_delivery.sql: DGN-2085 F1 rev5 section 2.7.
-- Additive adapter identity mappings; existing typed encodings stay unchanged.
-- reversible: YES before use by dropping session_live_* tables in dependency
-- order and restoring user_version=80 with its matching runtime/schema bundle.
-- After use restore the verified pre-migration backup to preserve replay evidence.
.bail on
.timeout 5000
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TEMP TABLE session_delivery_migration_guard (version INTEGER CHECK(version=80));
INSERT INTO session_delivery_migration_guard SELECT user_version FROM pragma_user_version;
DROP TABLE session_delivery_migration_guard;
CREATE TABLE session_live_scopes (
 scope TEXT PRIMARY KEY CHECK(json_valid(scope)), session_id INTEGER NOT NULL REFERENCES workouts(id) DEFERRABLE INITIALLY DEFERRED,
 publisher_id TEXT NOT NULL, member_id TEXT NOT NULL, slots TEXT NOT NULL CHECK(json_valid(slots)),
 action_slot TEXT NOT NULL, UNIQUE(session_id,scope)
);
CREATE TABLE session_live_selectors (
 binding_id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES session_live_scopes(scope),
 handle TEXT NOT NULL, selector TEXT NOT NULL, revision INTEGER NOT NULL,
 slot TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('prepared','active','retired')),
 request TEXT NOT NULL CHECK(json_valid(request)), result TEXT NOT NULL CHECK(json_valid(result)),
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 UNIQUE(scope,handle,selector), UNIQUE(handle,selector)
);
CREATE TABLE session_live_commands (
 binding_id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES session_live_scopes(scope),
 envelope_id TEXT NOT NULL, envelope_digest TEXT NOT NULL, command_digest TEXT NOT NULL,
 command_json TEXT NOT NULL CHECK(json_valid(command_json)), principal TEXT NOT NULL CHECK(json_valid(principal)),
 resolve_request TEXT CHECK(resolve_request IS NULL OR json_valid(resolve_request)),
 resolve_result TEXT CHECK(resolve_result IS NULL OR json_valid(resolve_result)),
 invocation_started INTEGER NOT NULL DEFAULT 0 CHECK(invocation_started IN (0,1)),
 state TEXT NOT NULL CHECK(state IN ('bound','committed','rejected')),
 acknowledgement TEXT, receipt TEXT, delivery TEXT,
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 UNIQUE(scope,envelope_id), UNIQUE(scope,command_digest)
);
CREATE TABLE session_live_deliveries (
 outbox_id TEXT PRIMARY KEY, delivery_key TEXT NOT NULL UNIQUE,
 binding_id TEXT NOT NULL UNIQUE REFERENCES session_live_commands(binding_id),
 scope TEXT NOT NULL REFERENCES session_live_scopes(scope),
 typed_outbox TEXT NOT NULL UNIQUE CHECK(json_valid(typed_outbox)),
 receipt_digest TEXT NOT NULL, revision INTEGER NOT NULL,
 plan_digest TEXT, plan TEXT, frame_digest TEXT,
 holder_id TEXT, lease_fence INTEGER NOT NULL DEFAULT 0, lease_until_ms INTEGER NOT NULL DEFAULT 0,
 lease_history TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(lease_history)),
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)), completion TEXT
);
CREATE TABLE session_live_stages (
 stage_key TEXT PRIMARY KEY CHECK(json_valid(stage_key)),
 outbox_id TEXT NOT NULL REFERENCES session_live_deliveries(outbox_id), ordinal INTEGER NOT NULL,
 binding TEXT NOT NULL CHECK(json_valid(binding)), spec TEXT NOT NULL CHECK(json_valid(spec)),
 state TEXT NOT NULL CHECK(state IN ('pending','inflight','uncertain','acknowledged')),
 seq INTEGER NOT NULL DEFAULT 0, attempt_fence INTEGER, receipt TEXT,
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 rebound INTEGER NOT NULL DEFAULT 0 CHECK(rebound IN (0,1)), UNIQUE(outbox_id,ordinal)
);
CREATE TABLE session_live_events (
 event_id TEXT PRIMARY KEY, outbox_id TEXT NOT NULL REFERENCES session_live_deliveries(outbox_id),
 stage_key TEXT NOT NULL REFERENCES session_live_stages(stage_key),
 request TEXT NOT NULL CHECK(json_valid(request)), result TEXT NOT NULL CHECK(json_valid(result)),
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 entry_id TEXT NOT NULL UNIQUE, attempt_fence INTEGER, acknowledgement_fence INTEGER NOT NULL
);
CREATE TABLE session_live_slots (
 scope TEXT NOT NULL REFERENCES session_live_scopes(scope), slot TEXT NOT NULL,
 message_id INTEGER NOT NULL, revision INTEGER NOT NULL, epoch INTEGER NOT NULL,
 protection TEXT NOT NULL CHECK(json_valid(protection)), markup_active INTEGER NOT NULL CHECK(markup_active IN (0,1)),
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 PRIMARY KEY(scope,slot)
);
CREATE TABLE session_live_history (
 history_id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES session_live_scopes(scope),
 slot TEXT NOT NULL, message_id INTEGER NOT NULL, protection TEXT NOT NULL CHECK(json_valid(protection)),
 retirement_receipt TEXT, replacement_receipt TEXT, cleanup TEXT NOT NULL,
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1))
);
PRAGMA user_version=81;
COMMIT;
