-- reversible: additive person emoji; restore the pre-089 backup to reverse.
-- User-taught presentation only. NULL uses the shared smile default.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_089(v);
CREATE TEMP TRIGGER _guard_089_refuse BEFORE INSERT ON _guard_089 BEGIN
 SELECT RAISE(FAIL, 'migration 089 requires user_version 88');
END;
INSERT INTO _guard_089 SELECT user_version FROM pragma_user_version WHERE user_version <> 88;
DROP TABLE temp._guard_089;
BEGIN IMMEDIATE;
ALTER TABLE persons ADD COLUMN emoji TEXT;
PRAGMA user_version = 89;
COMMIT;
