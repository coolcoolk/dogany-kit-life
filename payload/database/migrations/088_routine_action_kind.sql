-- reversible: additive routine_def.action_kind; restore the pre-088 DB backup to reverse.
-- Routine occurrences retain the owner's interaction kind. The occurrence
-- copy is stored in block_attr, leaving the generic block kind as task.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_088(v);
CREATE TEMP TRIGGER _guard_088_refuse BEFORE INSERT ON _guard_088 BEGIN
 SELECT RAISE(FAIL, 'migration 088 requires user_version 87');
END;
INSERT INTO _guard_088 SELECT user_version FROM pragma_user_version WHERE user_version <> 87;
DROP TABLE temp._guard_088;
BEGIN IMMEDIATE;
ALTER TABLE routine_def ADD COLUMN action_kind TEXT NOT NULL DEFAULT 'check'
    CHECK (action_kind IN ('check','workout','retro','session','other'));
PRAGMA user_version = 88;
COMMIT;
