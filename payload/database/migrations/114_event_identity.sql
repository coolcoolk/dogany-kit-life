-- Canonical event compatibility only. No events or memberships are rewritten.
-- reversible: restore pre-114 backup, or drop event_alias + trg_event_identity_* /
-- trg_event_time_record_* triggers, drop event.primary_facet/time_record_id,
-- and set user_version=113. Do not remove aliases after subsequent facet writes.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_114(v);
CREATE TEMP TRIGGER _guard_114_refuse BEFORE INSERT ON _guard_114 BEGIN
 SELECT RAISE(FAIL, 'migration 114 requires user_version 113');
END;
INSERT INTO _guard_114 SELECT 1 WHERE (SELECT user_version FROM pragma_user_version)<>113;
BEGIN IMMEDIATE;
ALTER TABLE event ADD COLUMN primary_facet TEXT;
ALTER TABLE event ADD COLUMN time_record_id INTEGER REFERENCES record(id) ON DELETE SET NULL;
CREATE TABLE event_alias (
 old_id INTEGER PRIMARY KEY CHECK(old_id>0),
 old_ulid TEXT NOT NULL UNIQUE CHECK(length(old_ulid)>0),
 canonical_id INTEGER NOT NULL REFERENCES event(id) ON DELETE RESTRICT,
 CHECK(old_id<>canonical_id)
);
CREATE INDEX idx_event_alias_canonical ON event_alias(canonical_id);
-- An alias is always one hop. A live event is never simultaneously an alias.
-- These guards also work on import/restore connections with foreign_keys OFF.
CREATE TRIGGER trg_event_identity_alias_insert BEFORE INSERT ON event_alias BEGIN
 SELECT RAISE(ABORT, 'event alias identity is already registered') WHERE EXISTS(
  SELECT 1 FROM event_alias WHERE (old_id=NEW.old_id OR old_ulid=NEW.old_ulid)
  AND (old_id<>NEW.old_id OR old_ulid<>NEW.old_ulid OR canonical_id<>NEW.canonical_id));
 SELECT RAISE(ABORT, 'event alias identity is still live') WHERE EXISTS(
  SELECT 1 FROM event WHERE id=NEW.old_id OR ulid=NEW.old_ulid);
 SELECT RAISE(ABORT, 'event alias target must be canonical') WHERE NOT EXISTS(
  SELECT 1 FROM event WHERE id=NEW.canonical_id) OR EXISTS(
  SELECT 1 FROM event_alias WHERE old_id=NEW.canonical_id);
END;
CREATE TRIGGER trg_event_identity_alias_update BEFORE UPDATE ON event_alias BEGIN
 SELECT RAISE(ABORT, 'event alias identity is immutable')
  WHERE NEW.old_id<>OLD.old_id OR NEW.old_ulid<>OLD.old_ulid;
 SELECT RAISE(ABORT, 'event alias target must be canonical') WHERE NOT EXISTS(
  SELECT 1 FROM event WHERE id=NEW.canonical_id) OR EXISTS(
  SELECT 1 FROM event_alias WHERE old_id=NEW.canonical_id);
END;
CREATE TRIGGER trg_event_identity_insert BEFORE INSERT ON event BEGIN
 SELECT RAISE(ABORT, 'event id is reserved by alias high-water mark')
 WHERE EXISTS(SELECT 1 FROM event_alias) AND (CASE WHEN NEW.id=-1 THEN (SELECT COALESCE(MAX(id),0)+1 FROM event)
         ELSE NEW.id END) <= (SELECT COALESCE(MAX(old_id),0) FROM event_alias);
 SELECT RAISE(ABORT, 'event ULID is reserved by alias')
 WHERE EXISTS(SELECT 1 FROM event_alias WHERE old_ulid=NEW.ulid);
 SELECT RAISE(ABORT, 'cannot replace an alias target')
 WHERE EXISTS(SELECT 1 FROM event_alias a JOIN event e ON e.id=a.canonical_id
              WHERE e.id=NEW.id OR e.ulid=NEW.ulid);
END;
CREATE TRIGGER trg_event_identity_update BEFORE UPDATE OF id,ulid ON event BEGIN
 SELECT RAISE(ABORT, 'event id is reserved by alias high-water mark')
 WHERE EXISTS(SELECT 1 FROM event_alias) AND NEW.id<>OLD.id AND NEW.id <= (SELECT COALESCE(MAX(old_id),0) FROM event_alias);
 SELECT RAISE(ABORT, 'event ULID is reserved by alias')
 WHERE EXISTS(SELECT 1 FROM event_alias WHERE old_ulid=NEW.ulid);
 SELECT RAISE(ABORT, 'cannot replace an alias target')
 WHERE EXISTS(SELECT 1 FROM event_alias a JOIN event e ON e.id=a.canonical_id
              WHERE e.id<>OLD.id AND (e.id=NEW.id OR e.ulid=NEW.ulid));
 SELECT RAISE(ABORT, 'cannot change an alias target identity')
 WHERE (NEW.id<>OLD.id OR NEW.ulid<>OLD.ulid)
 AND EXISTS(SELECT 1 FROM event_alias WHERE canonical_id=OLD.id);
END;
CREATE TRIGGER trg_event_identity_delete BEFORE DELETE ON event
WHEN EXISTS(SELECT 1 FROM event_alias WHERE canonical_id=OLD.id) BEGIN
 SELECT RAISE(ABORT, 'redirect aliases before deleting canonical event');
END;
CREATE TRIGGER trg_event_time_record_insert BEFORE INSERT ON event
WHEN NEW.time_record_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM record WHERE id=NEW.time_record_id) BEGIN
 SELECT RAISE(ABORT, 'event time provenance record missing');
END;
CREATE TRIGGER trg_event_time_record_update BEFORE UPDATE OF time_record_id ON event
WHEN NEW.time_record_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM record WHERE id=NEW.time_record_id) BEGIN
 SELECT RAISE(ABORT, 'event time provenance record missing');
END;
CREATE TRIGGER trg_event_time_record_delete BEFORE DELETE ON record BEGIN
 UPDATE event SET time_record_id=NULL WHERE time_record_id=OLD.id;
END;
PRAGMA user_version=114;
COMMIT;
DROP TABLE temp._guard_114;
