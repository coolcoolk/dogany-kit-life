-- Groups use the same owner-set emoji presentation as persons. Membership
-- remains in persons.groups; this adds no second identity/membership store.
-- reversible: ALTER TABLE person_group DROP COLUMN emoji; PRAGMA user_version=114;
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_115(v);
CREATE TEMP TRIGGER _guard_115_refuse BEFORE INSERT ON _guard_115 BEGIN
 SELECT RAISE(FAIL, 'migration 115 requires user_version 114');
END;
INSERT INTO _guard_115 SELECT 1 WHERE (SELECT user_version FROM pragma_user_version)<>114;
BEGIN IMMEDIATE;
ALTER TABLE person_group ADD COLUMN emoji TEXT;
PRAGMA user_version=115;
COMMIT;
DROP TABLE _guard_115;
