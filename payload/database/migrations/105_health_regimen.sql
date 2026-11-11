-- reversible: restore the pre-105 backup, or drop the three additive health_regimen* tables.
-- 104 belongs to the parallel satiety branch. Accept exactly 104.
.bail on
.timeout 5000
BEGIN IMMEDIATE;
CREATE TEMP TABLE _guard_105(v);
CREATE TEMP TRIGGER _guard_105_refuse BEFORE INSERT ON _guard_105 BEGIN
 SELECT RAISE(ABORT, 'migration 105 requires user_version 104');
END;
INSERT INTO _guard_105 SELECT user_version FROM pragma_user_version WHERE user_version<>104;
DROP TABLE _guard_105;
CREATE TABLE health_regimen (
 id INTEGER PRIMARY KEY,
 member_id TEXT NOT NULL CHECK(length(trim(member_id))>0),
 name TEXT NOT NULL CHECK(length(trim(name))>0),
 kind TEXT NOT NULL CHECK(kind IN ('supplement','medication')),
 amount TEXT NOT NULL CHECK(length(trim(amount))>0),
 unit TEXT NOT NULL CHECK(length(trim(unit))>0),
 schedule_kind TEXT NOT NULL CHECK(schedule_kind IN ('clock','meal','before_workout')),
 clock_time TEXT CHECK(clock_time IS NULL OR (length(clock_time)=5 AND clock_time GLOB '[0-2][0-9]:[0-5][0-9]' AND clock_time<'24:00')),
 meal_slot TEXT,
 weekdays TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
 timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
 created_at TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 CHECK((schedule_kind='clock' AND clock_time IS NOT NULL AND meal_slot IS NULL)
    OR (schedule_kind='meal' AND clock_time IS NULL AND meal_slot IS NOT NULL)
    OR (schedule_kind='before_workout' AND clock_time IS NULL AND meal_slot IS NULL)),
 UNIQUE(member_id,name,schedule_kind,clock_time,meal_slot,weekdays)
);
CREATE TABLE health_regimen_reminder (
 id TEXT PRIMARY KEY,
 member_id TEXT NOT NULL,
 planned_at TEXT NOT NULL,
 due_at TEXT NOT NULL,
 timezone TEXT NOT NULL,
 check_mode INTEGER NOT NULL DEFAULT 0 CHECK(check_mode IN (0,1)),
 revision INTEGER NOT NULL DEFAULT 0,
 sent_revision INTEGER NOT NULL DEFAULT -1,
 sending_agent TEXT,
 message_id INTEGER,
 chat_id INTEGER CHECK(chat_id IS NULL OR chat_id>0),
 UNIQUE(member_id,planned_at)
);
CREATE TABLE health_regimen_taken (
 regimen_id INTEGER NOT NULL REFERENCES health_regimen(id),
 planned_at TEXT NOT NULL,
 reminder_id TEXT NOT NULL REFERENCES health_regimen_reminder(id),
 position INTEGER NOT NULL CHECK(position>0),
 name TEXT NOT NULL,
 amount TEXT NOT NULL,
 unit TEXT NOT NULL,
 taken_at TEXT,
 source TEXT CHECK(source IN ('button','words')),
 CHECK((taken_at IS NULL AND source IS NULL) OR (taken_at IS NOT NULL AND source IS NOT NULL)),
 PRIMARY KEY(regimen_id,planned_at),
 UNIQUE(reminder_id,position)
);
CREATE INDEX health_regimen_due ON health_regimen_reminder(due_at);
PRAGMA user_version=105;
COMMIT;
