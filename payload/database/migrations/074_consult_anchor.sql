-- 074_consult_anchor.sql  (target user_version 74)
--
-- reversible: YES.  Rollback = DROP TABLE consult_answer; DROP TABLE
--   availability_window; ALTER TABLE ledger_preference DROP COLUMN detail;
--   ... DROP COLUMN topic; ALTER TABLE ledger_constraint DROP COLUMN care;
--   ALTER TABLE ledger_resource DROP COLUMN place_id; PRAGMA user_version =
--   73.  Additive: two new tables (+ their indexes), four NULLable columns.
--   No existing row is read or rewritten; every existing row reads the new
--   columns NULL = exactly the pre-074 meaning.  No config row is written
--   (051 convention).
--
-- ==========================================================================
-- WHAT.  Every consult answer lands in the kit, and the goal consult holds
-- the list of where each one landed (owner 2026-10-09 09:23 / 09:25; audit
-- consult-to-kit-audit.md, fixes 2 5 6 7 8 + the
-- anchor).  Pack consults write THROUGH kit verbs; nothing here is a pack
-- table.
--
--   ledger_resource.place_id   which kit place IS this training place?  An
--                     env resource (the gym the user named) used to be a
--                     label only: travel / prep / brief could not reach it
--                     (live: resource #1 학동 휘트니스M, no place).
--                     resource-add --kind env resolves or creates the
--                     place (address / coordinates when given);
--                     resource-place-link links an existing row.
--   ledger_constraint.care     what does the user do for this site before
--                     or after training?  JSON array of drills, safety rows
--                     only (live: the adduction / halo answers sat inside
--                     the verbatim value text).  The value stays the user's
--                     words; care is the structured reading of them.
--   ledger_preference.topic / .detail   which question a preference
--                     answers (cardio) and its shape ({"minutes": 20}).
--                     The table existed (028) with no writer; preference-add
--                     is its first.  item / polarity / note keep their 028
--                     meaning.  NOT the taste map (its spec drafts are not
--                     built; module_shopping's taste_mark stays its own).
--   availability_window       when else could the user train?  Extra-day
--                     windows (live: WED 05-07, SAT/SUN anytime) that only
--                     lived in the pack's plan state.  NULL start/end =
--                     anytime that day.
--   consult_answer            the anchor.  One row per (answer, landing):
--                     the question, the user's words, and the kit row that
--                     holds it (landed_table + landed_ref).  A pointer, not
--                     a copy: the reader resolves the landed row's live
--                     status, so a retired landing stops being "held"
--                     without a second write.  Read by retro / brief via
--                     consult-answers.
--
-- WHY POINTERS, NOT A SNAPSHOT.  A copied value is a second clock (036/039):
-- the owner's gym equipment changes through resource-equipment, the anchor
-- must show the new list, not the one said at intake.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_074(seen_user_version);
CREATE TEMP TRIGGER _guard_074_refuse BEFORE INSERT ON _guard_074 BEGIN
  SELECT RAISE(FAIL, 'migration 074 refused: this database is not at user_version 73 -- either 074 was already applied (>= 74) or the chain up to 073 has not run (< 73). Nothing changed');
END;
INSERT INTO _guard_074
  SELECT user_version FROM pragma_user_version WHERE user_version <> 73;
DROP TABLE temp._guard_074;

BEGIN IMMEDIATE;

ALTER TABLE ledger_resource ADD COLUMN place_id INTEGER REFERENCES place(id);
ALTER TABLE ledger_constraint ADD COLUMN care TEXT
    CHECK (care IS NULL OR (class = 'safety' AND json_valid(care)
                            AND json_type(care) = 'array'));
ALTER TABLE ledger_preference ADD COLUMN topic TEXT
    CHECK (topic IS NULL OR TRIM(topic) <> '');
ALTER TABLE ledger_preference ADD COLUMN detail TEXT
    CHECK (detail IS NULL OR (json_valid(detail)
                              AND json_type(detail) = 'object'));

CREATE TABLE availability_window (
  id          INTEGER PRIMARY KEY,
  ulid        TEXT NOT NULL UNIQUE,
  domain      TEXT NOT NULL CHECK (TRIM(domain) <> ''),
  weekdays    TEXT NOT NULL CHECK (weekdays GLOB '[A-Z][A-Z][A-Z]*'),
  start_hm    TEXT CHECK (start_hm IS NULL OR start_hm GLOB '[0-2][0-9]:[0-5][0-9]'),
  end_hm      TEXT CHECK (end_hm IS NULL OR end_hm GLOB '[0-2][0-9]:[0-5][0-9]'),
  note        TEXT,
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
  source      TEXT NOT NULL CHECK (TRIM(source) <> ''),
  recorded_at TEXT NOT NULL,
  retired_at  TEXT,
  CHECK ((start_hm IS NULL) = (end_hm IS NULL)),
  CHECK (start_hm IS NULL OR start_hm < end_hm),
  CHECK ((status = 'retired') = (retired_at IS NOT NULL))
);
CREATE UNIQUE INDEX ux_availability_window_active ON availability_window(
  domain, weekdays, COALESCE(start_hm, ''), COALESCE(end_hm, ''))
  WHERE status = 'active';

CREATE TABLE consult_answer (
  id            INTEGER PRIMARY KEY,
  ulid          TEXT NOT NULL UNIQUE,
  domain        TEXT NOT NULL CHECK (TRIM(domain) <> ''),
  why_id        INTEGER REFERENCES life_why(id),
  question      TEXT NOT NULL CHECK (question GLOB '[a-z]*'),
  words         TEXT,
  landed_table  TEXT NOT NULL CHECK (landed_table IN (
                  'ledger_resource','ledger_constraint','ledger_preference',
                  'availability_window','place','routine_def','config',
                  'life_why','ledger_goal','goal_target','metric_axis',
                  'pack_file')),
  landed_ref    TEXT NOT NULL CHECK (TRIM(landed_ref) <> ''),
  asked_by      TEXT NOT NULL CHECK (TRIM(asked_by) <> ''),
  status        TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','superseded')),
  recorded_at   TEXT NOT NULL,
  superseded_at TEXT,
  CHECK ((status = 'superseded') = (superseded_at IS NOT NULL))
);
CREATE UNIQUE INDEX ux_consult_answer_active ON consult_answer(
  domain, question, landed_table, landed_ref) WHERE status = 'active';
CREATE INDEX ix_consult_answer_why ON consult_answer(why_id)
  WHERE why_id IS NOT NULL;

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 74;
COMMIT;
