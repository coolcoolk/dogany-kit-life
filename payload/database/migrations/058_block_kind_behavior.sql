-- 058_block_kind_behavior.sql  (target user_version 58)
--
-- reversible: YES, losslessly. Additive only: one new table + its seed rows;
--   no existing row or column is changed. Rollback = DROP TABLE
--   block_kind_behavior; PRAGMA user_version = 57;
--
-- 058: behavior belonging to a block kind. Existing blocks are untouched.
CREATE TEMP TABLE _guard_058(seen_user_version);
CREATE TEMP TRIGGER _guard_058_refuse BEFORE INSERT ON _guard_058 BEGIN
  SELECT RAISE(FAIL, 'migration 058 requires user_version 57');
END;
INSERT INTO _guard_058
  SELECT user_version FROM pragma_user_version WHERE user_version <> 57;
DROP TABLE temp._guard_058;

CREATE TABLE IF NOT EXISTS block_kind_behavior (
  token TEXT PRIMARY KEY REFERENCES block_kind(token),
  mirror_surface TEXT NOT NULL CHECK (mirror_surface IN ('calendar','tasks','none')),
  calendar_key TEXT,
  timed_calendar_key TEXT,
  overnight_calendar_key TEXT,
  reminder_lead_min INTEGER NOT NULL CHECK (reminder_lead_min >= 0),
  reminder_header_key TEXT NOT NULL,
  reminder_key_style TEXT NOT NULL CHECK (reminder_key_style IN ('appt','task')),
  persons_join INTEGER NOT NULL CHECK (persons_join IN (0,1)),
  travel_attach INTEGER NOT NULL CHECK (travel_attach IN (0,1)),
  presence_mode INTEGER NOT NULL CHECK (presence_mode IN (0,1)),
  requires_place INTEGER NOT NULL CHECK (requires_place IN (0,1)),
  adoptable INTEGER NOT NULL CHECK (adoptable IN (0,1)),
  adopt_path TEXT NOT NULL CHECK (adopt_path IN ('appt','task','presence','none')),
  location_mode TEXT NOT NULL CHECK (location_mode IN ('meta','place','required_place','none'))
);
INSERT OR IGNORE INTO block_kind_behavior VALUES
  ('task','tasks','task','task',NULL,30,'remind.task','task',0,1,0,0,1,'task','place'),
  ('appointment','calendar','appt',NULL,NULL,120,'remind.appointment','appt',1,1,0,0,1,'appt','meta'),
  ('presence','calendar','travel','task','travel',30,'remind.presence','task',0,0,1,1,1,'presence','required_place'),
  ('record','none',NULL,NULL,NULL,30,'remind.record','task',0,0,0,0,0,'none','none');
PRAGMA user_version = 58;
