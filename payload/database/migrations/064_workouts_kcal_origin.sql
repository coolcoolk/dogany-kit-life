-- 064_workouts_kcal_origin.sql  (target user_version 64)
--
-- reversible: YES while no row holds kcal NULL.  Rollback = DROP TRIGGER
--   trg_workouts_kcal_origin_ins / _upd; ALTER TABLE workouts DROP COLUMN
--   kcal_origin (its CHECK names only itself and kcal -- allowed); the
--   writable_schema swap with the two strings exchanged; PRAGMA
--   user_version = 63.  Once a not-entered row exists, going back means
--   writing a number nobody recorded into it -- the very defect this file ends.
--
-- ==========================================================================
-- WHAT.  Ticket 2026-09-02 kcal-not-entered-and-target-sot, defect 1, re-cut
-- on current main (the sandbox patch was built as 036, a number since taken
-- by 036_travel_block_axis).  workouts.kcal was NOT NULL DEFAULT 0, so "burned
-- nothing" and "nobody wrote it down" were the same byte; the owner ruled
-- 2026-09-02 that the unrecorded rows are NOT-ENTERED, not zero.
--
--   kcal        REAL NULL   NULL = no value was ever recorded (no default:
--                           an omitted kcal is absent, not 0)
--   kcal_origin TEXT NULL   the 0-D provenance vocabulary, reused, not new:
--                           measured | computed | default | declared | unknown
--
-- THE PAIRING, and why it is a CHECK one way and a trigger the other.
--   "An origin claimed for a number that does not exist" -> refused by the
--   column CHECK (kcal_origin needs kcal).
--   "A number with no stated origin" -> STAMPED 'unknown' by an AFTER trigger,
--   not refused.  Every legacy writer (the pack's fixtures, any pinned pack
--   build, any hand INSERT) writes kcal without an origin; refusing them would
--   break every install at its current pin for a question those writers cannot
--   answer.  'unknown' is the honest answer for them -- it is exactly what the
--   backfill below says about every existing number -- and the invariant
--   (kcal IS NULL) = (kcal_origin IS NULL) holds after every statement.  The
--   kit's own writers (workout-add / workout-upd) state the origin.
--
-- BACKFILL, and what is deliberately NOT done.
--   Every existing number keeps its value and is labelled 'unknown' -- not
--   'measured', even where a memo says 애플워치 (run-3 discipline: unknown ->
--   measured is always possible later, a wrong 'measured' is not detectable).
--   Existing zeros are NOT turned into NULL here.  The ruling was about 35
--   specific rows (ids 10039..10073) of one estate, pinned by id, never by
--   `kcal = 0`; this file ships to every install, where those ids name other
--   rows or none.  On the crew DB they now live only in
--   archive_health_reset_workouts (live workouts = 0 rows since the
--   2026-09-27 health reset), which this file does not touch.
--   Recovering numbers from memos: NOT authorised (ticket 비고 (1)), not done.
--
-- v_daily_energy is unchanged: SUM(kcal) skips NULL and burn_kcal keeps its
-- COALESCE-to-0 contract, so no ^key= consumer moves.  "Trained but not
-- entered" is read in Python (kit_life.day_burn_kcal / targets_for_date,
-- burn_known) -- the product decision on such a day is the owner's (ticket
-- 비고 (2)): the target stays base and the condition is only surfaced.
--
-- ORDER (see 061): ADD COLUMN + triggers first, the text swap last.
-- GUARD: exactly 63; workouts carries 'kcal       REAL NOT NULL DEFAULT 0,'
-- or its 064 form exactly once; afterwards the old form is gone.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_064(seen_user_version);
CREATE TEMP TRIGGER _guard_064_refuse BEFORE INSERT ON _guard_064 BEGIN
  SELECT RAISE(FAIL, 'migration 064 refused: this database is not at user_version 63 -- either 064 was already applied (>= 64) or the chain up to 063 has not run (< 63). Nothing changed');
END;
INSERT INTO _guard_064
  SELECT user_version FROM pragma_user_version WHERE user_version <> 63;
DROP TABLE temp._guard_064;

BEGIN IMMEDIATE;

CREATE TEMP TABLE _guard_064_pre(n);
CREATE TEMP TRIGGER _guard_064_pre_refuse BEFORE INSERT ON _guard_064_pre BEGIN
  SELECT RAISE(FAIL, 'migration 064 refused: workouts does not carry "kcal       REAL NOT NULL DEFAULT 0," (or its 064 form) exactly once -- not the shape this file was written against. Nothing changed');
END;
INSERT INTO _guard_064_pre
  SELECT 1 FROM sqlite_master
   WHERE type = 'table' AND name = 'workouts'
     AND (length(sql) - length(replace(sql, 'kcal       REAL NOT NULL DEFAULT 0,', '')))
           / length('kcal       REAL NOT NULL DEFAULT 0,')
       + (length(sql) - length(replace(sql, 'kcal       REAL,', '')))
           / length('kcal       REAL,') <> 1;
DROP TABLE temp._guard_064_pre;

ALTER TABLE workouts ADD COLUMN kcal_origin TEXT
    CHECK (kcal_origin IS NULL OR (kcal IS NOT NULL AND kcal_origin IN
           ('measured','computed','default','declared','unknown')));

UPDATE workouts SET kcal_origin = 'unknown' WHERE kcal IS NOT NULL;

CREATE TRIGGER trg_workouts_kcal_origin_ins
AFTER INSERT ON workouts
WHEN NEW.kcal IS NOT NULL AND NEW.kcal_origin IS NULL
BEGIN
  UPDATE workouts SET kcal_origin = 'unknown' WHERE id = NEW.id;
END;
CREATE TRIGGER trg_workouts_kcal_origin_upd
AFTER UPDATE OF kcal, kcal_origin ON workouts
WHEN NEW.kcal IS NOT NULL AND NEW.kcal_origin IS NULL
BEGIN
  UPDATE workouts SET kcal_origin = 'unknown' WHERE id = NEW.id;
END;

PRAGMA writable_schema = ON;
UPDATE sqlite_master
   SET sql = replace(sql, 'kcal       REAL NOT NULL DEFAULT 0,', 'kcal       REAL,')
 WHERE type = 'table' AND name = 'workouts';
PRAGMA writable_schema = OFF;

CREATE TEMP TABLE _guard_064_post(n);
CREATE TEMP TRIGGER _guard_064_post_refuse BEFORE INSERT ON _guard_064_post BEGIN
  SELECT RAISE(FAIL, 'migration 064 refused: kcal NOT NULL DEFAULT 0 survived the swap -- rolled back, nothing changed');
END;
INSERT INTO _guard_064_post
  SELECT 1 FROM sqlite_master
   WHERE type = 'table' AND name = 'workouts'
     AND instr(sql, 'kcal       REAL NOT NULL DEFAULT 0,') > 0;
DROP TABLE temp._guard_064_post;

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 64;
COMMIT;
