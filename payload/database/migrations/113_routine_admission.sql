-- Optional pack-owned admission; existing definitions and occurrences keep NULL.
-- reversible: drop column routine_def.admit_cmd (ALTER TABLE ... DROP COLUMN), set user_version=112; or restore the pre-113 backup.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_113(v);
CREATE TEMP TRIGGER _guard_113_refuse BEFORE INSERT ON _guard_113 BEGIN
 SELECT RAISE(FAIL, 'migration 113 requires user_version 112');
END;
INSERT INTO _guard_113 SELECT 1 WHERE (SELECT user_version FROM pragma_user_version)<>112;
BEGIN IMMEDIATE;
ALTER TABLE routine_def ADD COLUMN admit_cmd TEXT
 CHECK(admit_cmd IS NULL OR (json_valid(admit_cmd) AND json_type(admit_cmd)='array'
                            AND json_array_length(admit_cmd)>0));
PRAGMA user_version=113;
COMMIT;
DROP TABLE temp._guard_113;
