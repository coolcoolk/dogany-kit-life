-- 019_owning_agent_writelayer.sql  (target user_version 19)
-- reversible: no
-- Source: DGN-791 #1 KILL (owning_agent write-layer). v18 -> v19.
-- owning_agent NOT NULL on the 6 domain tables (meals, workouts, metric_log,
-- routine_def, tasks, appointments), backfilled by domain owner.
--
-- DROP+rebuild migration: SQLite cannot ADD a NOT NULL column without a default
-- onto a populated table, so each table is rewritten (create new / copy / drop /
-- rename). reversible:no -- this is a destructive table-rewrite.
--
-- The new-table DDL below is byte-matched to the fresh-mint v19 schema.sql so
-- `fresh == migrated`. Backfill owner (datamodel #9 / #2):
--   warg: meals, workouts, metric_log
--   ag:   tasks, appointments
--   routine_def: per-row -- 'ag' if created_by IN ('ag','notion-import') else 'warg'
--
-- foreign_keys is turned OFF for the rewrite: dropping the old workouts table
-- with FKs ON would CASCADE-delete workout_sets children. A foreign_key_check
-- at the end validates referential integrity.

PRAGMA foreign_keys = OFF;
PRAGMA legacy_alter_table = ON;

-- ---- meals ----
ALTER TABLE "meals" RENAME TO "meals__v19_old";
CREATE TABLE "meals" (
  id         INTEGER PRIMARY KEY,
  date       TEXT NOT NULL,
  meal       TEXT,
  name       TEXT NOT NULL,
  grams      REAL,
  carb       REAL NOT NULL DEFAULT 0,
  protein    REAL NOT NULL DEFAULT 0,
  fat        REAL NOT NULL DEFAULT 0,
  fiber      REAL NOT NULL DEFAULT 0,
  sugar      REAL NOT NULL DEFAULT 0,
  alt_sugar  REAL NOT NULL DEFAULT 0,
  alcohol    REAL NOT NULL DEFAULT 0,
  kcal       REAL GENERATED ALWAYS AS
               (protein*4 + fat*9 + (carb-fiber)*4 + alcohol*7) STORED,
  area_id    INTEGER REFERENCES areas(id),
  notion_id  TEXT,
  owning_agent TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
, aa_protein  REAL NOT NULL DEFAULT 0, sodium_mg   REAL NOT NULL DEFAULT 0, caffeine_mg REAL NOT NULL DEFAULT 0);
INSERT INTO "meals"
  (id, date, meal, name, grams, carb, protein, fat, fiber, sugar, alt_sugar,
   alcohol, area_id, notion_id, created_at, aa_protein, sodium_mg, caffeine_mg,
   owning_agent)
  SELECT id, date, meal, name, grams, carb, protein, fat, fiber, sugar, alt_sugar,
   alcohol, area_id, notion_id, created_at, aa_protein, sodium_mg, caffeine_mg,
   'warg' FROM "meals__v19_old";
DROP TABLE "meals__v19_old";
CREATE INDEX idx_meals_date ON meals(date);
CREATE UNIQUE INDEX idx_meals_notion ON meals(notion_id) WHERE notion_id IS NOT NULL;

-- ---- workouts ----
ALTER TABLE "workouts" RENAME TO "workouts__v19_old";
CREATE TABLE workouts (
  id         INTEGER PRIMARY KEY,
  date       TEXT NOT NULL,
  minutes    REAL NOT NULL DEFAULT 0,
  kcal       REAL NOT NULL DEFAULT 0,
  note       TEXT,
  area_id    INTEGER REFERENCES areas(id),
  notion_id  TEXT,
  owning_agent TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
, avg_hr REAL, session_type_id INTEGER REFERENCES session_type(id));
INSERT INTO "workouts"
  (id, date, minutes, kcal, note, area_id, notion_id, created_at, avg_hr,
   session_type_id, owning_agent)
  SELECT id, date, minutes, kcal, note, area_id, notion_id, created_at, avg_hr,
   session_type_id, 'warg' FROM "workouts__v19_old";
DROP TABLE "workouts__v19_old";
CREATE INDEX idx_workouts_date ON workouts(date);
CREATE UNIQUE INDEX idx_workouts_notion ON workouts(notion_id) WHERE notion_id IS NOT NULL;

-- ---- metric_log ----
ALTER TABLE "metric_log" RENAME TO "metric_log__v19_old";
CREATE TABLE metric_log (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, metric TEXT NOT NULL,
  value REAL NOT NULL, note TEXT,
  owning_agent TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE(date, metric)
);
INSERT INTO "metric_log"
  (id, date, metric, value, note, created_at, owning_agent)
  SELECT id, date, metric, value, note, created_at, 'warg'
  FROM "metric_log__v19_old";
DROP TABLE "metric_log__v19_old";
CREATE INDEX idx_metric_log_metric_date ON metric_log(metric, date);

-- ---- routine_def ----
ALTER TABLE "routine_def" RENAME TO "routine_def__v19_old";
CREATE TABLE "routine_def" (
    id            INTEGER PRIMARY KEY,
    ulid          TEXT NOT NULL UNIQUE,
    recurrence_id TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'task',
    cadence       TEXT,
    schedule_kind TEXT NOT NULL,
    time_of_day   TEXT,
    duration_min  INTEGER,
    exclusive     INTEGER NOT NULL DEFAULT 0,
    display_tz    TEXT NOT NULL DEFAULT 'Asia/Seoul',
    area_id       INTEGER REFERENCES areas(id),
    project_id    INTEGER REFERENCES projects(id),
    purpose       TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    valid_until   TEXT NOT NULL,
    rule_effective_from TEXT NOT NULL,
    anomaly_ack   TEXT,
    notify_policy   TEXT,
    notify_lead_min INTEGER,
    version       INTEGER NOT NULL DEFAULT 0,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    place_id      INTEGER REFERENCES place(id),
    owning_agent  TEXT NOT NULL,
    CHECK (kind IN ('task','appointment','presence')),
    CHECK (kind != 'presence' OR place_id IS NOT NULL),
    CHECK (schedule_kind IN ('timed','all_day')),
    CHECK (status IN ('active','paused','retired')),
    CHECK (schedule_kind != 'timed' OR time_of_day IS NOT NULL),
    CHECK (exclusive IN (0,1)),
    CHECK (cadence IS NOT NULL OR status = 'retired'),
    CHECK (notify_policy IS NULL OR notify_policy IN
           ('default','silent','start_only','custom')),
    CHECK (notify_lead_min IS NULL OR COALESCE(notify_policy,'') = 'custom'),
    CHECK (COALESCE(notify_policy,'') != 'custom'
           OR (notify_lead_min IS NOT NULL AND notify_lead_min >= 0))
);
INSERT INTO "routine_def"
  (id, ulid, recurrence_id, title, kind, cadence, schedule_kind, time_of_day,
   duration_min, exclusive, display_tz, area_id, project_id, purpose, status,
   start_date, end_date, valid_until, rule_effective_from, anomaly_ack,
   notify_policy, notify_lead_min, version, created_by, created_at, updated_at,
   place_id, owning_agent)
  SELECT id, ulid, recurrence_id, title, kind, cadence, schedule_kind, time_of_day,
   duration_min, exclusive, display_tz, area_id, project_id, purpose, status,
   start_date, end_date, valid_until, rule_effective_from, anomaly_ack,
   notify_policy, notify_lead_min, version, created_by, created_at, updated_at,
   place_id,
   CASE WHEN created_by IN ('ag','notion-import') THEN 'ag' ELSE 'warg' END
  FROM "routine_def__v19_old";
DROP TABLE "routine_def__v19_old";

-- ---- tasks ----
ALTER TABLE "tasks" RENAME TO "tasks__v19_old";
CREATE TABLE tasks (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  area_id    INTEGER REFERENCES areas(id),
  due_start  TEXT,
  due_end    TEXT,
  done       INTEGER NOT NULL DEFAULT 0,
  is_routine INTEGER NOT NULL DEFAULT 0,
  priority   TEXT,
  seq        REAL,
  note       TEXT,
  notion_id  TEXT,
  owning_agent TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
, archived_at TEXT);
INSERT INTO "tasks"
  (id, name, area_id, due_start, due_end, done, is_routine, priority, seq, note,
   notion_id, created_at, archived_at, owning_agent)
  SELECT id, name, area_id, due_start, due_end, done, is_routine, priority, seq, note,
   notion_id, created_at, archived_at, 'ag' FROM "tasks__v19_old";
DROP TABLE "tasks__v19_old";
CREATE INDEX idx_tasks_done ON tasks(done);
CREATE INDEX idx_tasks_due ON tasks(due_start);
CREATE UNIQUE INDEX idx_tasks_notion ON tasks(notion_id) WHERE notion_id IS NOT NULL;

-- ---- appointments ----
ALTER TABLE "appointments" RENAME TO "appointments__v19_old";
CREATE TABLE appointments (
  id           INTEGER PRIMARY KEY,
  title        TEXT NOT NULL,
  start_at     TEXT,
  end_at       TEXT,
  location     TEXT,
  location_url TEXT,
  purpose      TEXT,
  summary      TEXT,
  notion_id    TEXT,
  owning_agent TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
INSERT INTO "appointments"
  (id, title, start_at, end_at, location, location_url, purpose, summary,
   notion_id, created_at, owning_agent)
  SELECT id, title, start_at, end_at, location, location_url, purpose, summary,
   notion_id, created_at, 'ag' FROM "appointments__v19_old";
DROP TABLE "appointments__v19_old";
CREATE UNIQUE INDEX idx_appt_notion ON appointments(notion_id) WHERE notion_id IS NOT NULL;
CREATE INDEX idx_appt_start ON appointments(start_at);

PRAGMA legacy_alter_table = OFF;

PRAGMA user_version = 19;
PRAGMA foreign_keys = ON;
