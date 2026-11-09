-- reversible: additive, no backfill; restore the pre-095 snapshot to reverse.
-- Since 036 event_persons points at block, not the experience event table.
-- A participant must be attachable without inventing a calendar block.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_095(v);
CREATE TEMP TRIGGER _guard_095_refuse BEFORE INSERT ON _guard_095 BEGIN
 SELECT RAISE(FAIL, 'migration 095 requires user_version 94');
END;
INSERT INTO _guard_095 SELECT user_version FROM pragma_user_version WHERE user_version<>94;
DROP TABLE temp._guard_095;
BEGIN IMMEDIATE;
CREATE TABLE experience_person (
  event_id INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (event_id, person_id)
);
CREATE INDEX idx_experience_person_person ON experience_person(person_id);
PRAGMA user_version = 95;
COMMIT;
