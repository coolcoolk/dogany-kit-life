-- reversible: restore a pre-110 backup; the new schedule values cannot be represented at 109.
-- Regimen only: preserve ids, completed snapshots and child FKs; no owner-data conversion.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_110(v);
CREATE TEMP TRIGGER _guard_110_refuse BEFORE INSERT ON _guard_110 BEGIN
 SELECT RAISE(ABORT, 'migration 110 requires user_version 109');
END;
INSERT INTO _guard_110 SELECT user_version FROM pragma_user_version WHERE user_version<>109;
DROP TABLE _guard_110;
PRAGMA foreign_keys=OFF;
BEGIN IMMEDIATE;
-- Keep the final CREATE text equal to schema.sql. RENAME adds identifier
-- quotes to sqlite_master; staging rows also avoids rewriting child FK names.
CREATE TEMP TABLE _health_regimen_110 AS SELECT * FROM health_regimen;
DROP TABLE health_regimen;
CREATE TABLE health_regimen (
 id INTEGER PRIMARY KEY,
 member_id TEXT NOT NULL CHECK(length(trim(member_id))>0),
 name TEXT NOT NULL CHECK(length(trim(name))>0),
 kind TEXT NOT NULL CHECK(kind IN ('supplement','medication')),
 amount TEXT NOT NULL CHECK(length(trim(amount))>0),
 unit TEXT NOT NULL CHECK(length(trim(unit))>0),
 schedule_kind TEXT NOT NULL CHECK(schedule_kind IN ('clock','meal','before_workout','after_workout','record_only')),
 clock_time TEXT CHECK(clock_time IS NULL OR (length(clock_time)=5 AND clock_time GLOB '[0-2][0-9]:[0-5][0-9]' AND clock_time<'24:00')),
 meal_slot TEXT,
 weekdays TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
 timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
 created_at TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 CHECK((schedule_kind='clock' AND clock_time IS NOT NULL AND meal_slot IS NULL)
    OR (schedule_kind='meal' AND clock_time IS NULL AND meal_slot IS NOT NULL)
    OR (schedule_kind IN ('before_workout','after_workout','record_only') AND clock_time IS NULL AND meal_slot IS NULL)),
 UNIQUE(member_id,name,schedule_kind,clock_time,meal_slot,weekdays)
);
INSERT INTO health_regimen SELECT * FROM _health_regimen_110;
DROP TABLE _health_regimen_110;
CREATE TABLE health_regimen_session_end (
 session_id INTEGER PRIMARY KEY REFERENCES workouts(id) ON DELETE CASCADE,
 member_id TEXT NOT NULL,
 closed_at TEXT NOT NULL
);
CREATE TEMP TABLE _fk_110(v CHECK(v=0));
INSERT INTO _fk_110 SELECT count(*) FROM pragma_foreign_key_check('health_regimen_taken');
DROP TABLE _fk_110;
PRAGMA user_version=110;
COMMIT;
PRAGMA foreign_keys=ON;
