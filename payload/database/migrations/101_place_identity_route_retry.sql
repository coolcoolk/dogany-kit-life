-- reversible: restore the pre-101 snapshot (person_home / place alias / route retry rows are additive).
-- Same-address confirmation and durable directional route retries.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_101(v);
CREATE TEMP TRIGGER _guard_101_refuse BEFORE INSERT ON _guard_101 BEGIN
 SELECT RAISE(ABORT, 'migration 101 requires user_version 100');
END;
INSERT INTO _guard_101 SELECT user_version FROM pragma_user_version WHERE user_version <> 100;
DROP TABLE _guard_101;
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS person_home (
 person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
 place_id INTEGER NOT NULL REFERENCES place(id) ON DELETE CASCADE,
 PRIMARY KEY(person_id, place_id)
);
CREATE TABLE IF NOT EXISTS place_identity_ask (
 id INTEGER PRIMARY KEY, query TEXT NOT NULL, place_id INTEGER NOT NULL REFERENCES place(id) ON DELETE CASCADE,
 address TEXT, payload TEXT, state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','yes','no')),
 UNIQUE(query, place_id)
);
CREATE TABLE IF NOT EXISTS travel_leg_retry (
 id INTEGER PRIMARY KEY, anchor_ulid TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('before','after')),
 origin_id INTEGER NOT NULL REFERENCES place(id), dest_id INTEGER NOT NULL REFERENCES place(id),
 attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','done','failed','cancelled')),
 error TEXT, notice TEXT, notice_read INTEGER NOT NULL DEFAULT 0,
 receipt_token TEXT, -- Single reserved seam for the framework maintainer's future carrier contract; no promise text.
 UNIQUE(anchor_ulid,role,origin_id,dest_id)
);
PRAGMA user_version=101;
COMMIT;
