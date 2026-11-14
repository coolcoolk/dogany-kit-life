-- Record whether a task can be performed away from its usual place.
-- NULL deliberately means unknown/place-bound for compatibility.
-- reversible: run tools/rollback-task-portability-118.sql; it clears every portability fact and sets PRAGMA user_version=117 (the nullable column stays; 117 code never reads it).
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_118(v);
CREATE TEMP TRIGGER _guard_118_refuse BEFORE INSERT ON _guard_118 BEGIN
 SELECT RAISE(FAIL, 'migration 118 requires user_version 117');
END;
INSERT INTO _guard_118 SELECT 1 WHERE (SELECT user_version FROM pragma_user_version)<>117;
BEGIN IMMEDIATE;
-- Event facts use block_attr on the v3.2 axis (and are projected by the
-- timeblock compatibility view), so no event-table DDL is needed.
ALTER TABLE routine_def ADD COLUMN portable INTEGER CHECK(portable IN (0,1));
PRAGMA user_version=118;
COMMIT;
