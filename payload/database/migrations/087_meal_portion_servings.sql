-- reversible: restore a pre-087 backup to recover legacy serving amounts.
-- Owner 2026-10-18: one bowl/plate, not one piece, is the starting portion.
-- Generic sources/proxies are documented in meal-log/reference/portions.json.
-- Only known legacy defaults are replaced; calibrated quantities survive.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_087(v);
CREATE TEMP TRIGGER _guard_087_refuse BEFORE INSERT ON _guard_087 BEGIN
 SELECT RAISE(FAIL, 'migration 087 requires user_version 86');
END;
INSERT INTO _guard_087 SELECT user_version FROM pragma_user_version WHERE user_version <> 86;
DROP TABLE temp._guard_087;
BEGIN IMMEDIATE;
ALTER TABLE meal_portion ADD COLUMN unit TEXT NOT NULL DEFAULT 'plate' CHECK (unit IN ('bowl','plate'));
ALTER TABLE meal_portion ADD COLUMN basis TEXT NOT NULL DEFAULT 'registered';
UPDATE meal_portion SET unit='plate' WHERE key=char(47564,46160);
UPDATE meal_portion SET grams=250, n=1, basis='standard_restaurant', updated_by='migration-087', updated_at=datetime('now','localtime') WHERE key=char(47564,46160) AND grams=35 AND n=1;
UPDATE meal_portion SET unit='bowl' WHERE key=char(46972,47732);
UPDATE meal_portion SET grams=550, n=1, basis='standard_restaurant_estimate', updated_by='migration-087', updated_at=datetime('now','localtime') WHERE key=char(46972,47732) AND grams=35 AND n=1;
UPDATE meal_portion SET unit='plate' WHERE key=char(46497,48374,51060);
UPDATE meal_portion SET grams=200, n=1, basis='standard_restaurant', updated_by='migration-087', updated_at=datetime('now','localtime') WHERE key=char(46497,48374,51060) AND grams=40 AND n=1;
UPDATE meal_portion SET unit='plate' WHERE key=char(49352,50864,53888,44608);
UPDATE meal_portion SET grams=100, n=1, basis='standard_restaurant', updated_by='migration-087', updated_at=datetime('now','localtime') WHERE key=char(49352,50864,53888,44608) AND grams=35 AND n=1;
UPDATE meal_portion SET unit='plate' WHERE key=char(54588,51088,32,46972,51648);
UPDATE meal_portion SET grams=150, n=1, basis='standard_restaurant', updated_by='migration-087', updated_at=datetime('now','localtime') WHERE key=char(54588,51088,32,46972,51648) AND grams=115 AND n=1;
UPDATE meal_portion SET unit='bowl' WHERE key=char(52852,50724,52852,47924);
UPDATE meal_portion SET unit='bowl' WHERE key=char(54252,52992,48380);
PRAGMA user_version = 87;
COMMIT;
