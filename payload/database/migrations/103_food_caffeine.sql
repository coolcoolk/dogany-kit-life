-- reversible: restore the pre-103 snapshot (three additive NULLable meal_food columns).
-- Restore food caffeine with independent provenance. Unknown stays NULL.
-- 102 belongs to the parallel satiety lane; this additive migration works
-- after either 101 or 102 and never authors or applies satiety's migration.
.bail on
.timeout 5000
BEGIN IMMEDIATE;
CREATE TEMP TABLE _guard_103(v);
CREATE TEMP TRIGGER _guard_103_refuse BEFORE INSERT ON _guard_103 BEGIN
 SELECT RAISE(ABORT, 'migration 103 requires user_version 101 or 102');
END;
INSERT INTO _guard_103 SELECT user_version FROM pragma_user_version WHERE user_version NOT IN (101,102);
DROP TABLE _guard_103;
ALTER TABLE meal_food ADD COLUMN caffeine_mg REAL CHECK (caffeine_mg IS NULL OR caffeine_mg >= 0);
ALTER TABLE meal_food ADD COLUMN caffeine_origin TEXT CHECK (caffeine_origin IS NULL OR caffeine_origin IN ('published','label','estimated'));
ALTER TABLE meal_food ADD COLUMN caffeine_source TEXT;
PRAGMA user_version=103;
COMMIT;
