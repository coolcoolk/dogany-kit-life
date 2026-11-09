-- 070_routine_ride_travel.sql  (target user_version 70)
--
-- reversible: YES.  Rollback = ALTER TABLE routine_def DROP COLUMN placement;
--   ... DROP COLUMN ride_prefer; ... DROP COLUMN ride_not_before;
--   ... DROP COLUMN fallback_at; PRAGMA user_version = 69.  Additive: four
--   NULLable columns on routine_def.  No existing row is read or rewritten;
--   every existing def reads placement NULL = a fixed-time routine, exactly
--   as before.
--
-- ==========================================================================
-- WHAT.  A routine that can be done anywhere (말해보카 15 min) rides the
-- day's dead time: the travel.
--
--   placement       NULL = fixed time (time_of_day, every routine so far)
--                   'ride_travel' = the day's first move leg that is at
--                   least duration_min long (and starts at/after
--                   ride_not_before) holds the block, from the leg's start;
--                   no such leg -> the block sits at fallback_at.
--   fallback_at     the no-travel time.  'HH:MM' or a weekday set:
--                   'MON,TUE,WED,THU,FRI=21:00;SAT,SUN=10:00'; a bare
--                   'HH:MM' item is the default for days no set names
--                   (routine_projection.parse_fallback is the grammar).
--   ride_not_before 'HH:MM' -- a leg starting earlier does not qualify.
--   ride_prefer     'morning' | 'evening' -- that half of the day's legs is
--                   tried first (split at local noon); the other half still
--                   qualifies before the fallback.
--
-- WHY (routine-placement request; decision
-- 2026-08-12: 말해보카 daily, inside a travel block when there is travel,
-- else 21:00).  A routine was either timeless or one fixed clock time; a
-- fixed time lands on the commute only by luck.  Overlap with the leg is
-- allowed (2026-07-28 place-boundness: travel is place-bound, a ride
-- routine is not -- both are slot-soft, so nothing here touches the slot
-- predicate).
--
-- WHY COLUMNS, NOT A CADENCE SUFFIX.  The cadence string says WHICH days;
-- this says WHERE in the day.  The roller's conformance compares rows to
-- the instant function, so the placement has to be a declared input of
-- that function, not a parse side effect.
--
-- The cross-column CHECK lives on `placement` (added LAST so the columns it
-- names exist): a ride routine is a timed task with no place and a fallback.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_070(seen_user_version);
CREATE TEMP TRIGGER _guard_070_refuse BEFORE INSERT ON _guard_070 BEGIN
  SELECT RAISE(FAIL, 'migration 070 refused: this database is not at user_version 69 -- either 070 was already applied (>= 70) or the chain up to 069 has not run (< 69). Nothing changed');
END;
INSERT INTO _guard_070
  SELECT user_version FROM pragma_user_version WHERE user_version <> 69;
DROP TABLE temp._guard_070;

BEGIN IMMEDIATE;

ALTER TABLE routine_def ADD COLUMN fallback_at TEXT
    CHECK (fallback_at IS NULL OR TRIM(fallback_at) <> '');
ALTER TABLE routine_def ADD COLUMN ride_not_before TEXT
    CHECK (ride_not_before IS NULL
           OR ride_not_before GLOB '[0-2][0-9]:[0-5][0-9]');
ALTER TABLE routine_def ADD COLUMN ride_prefer TEXT
    CHECK (ride_prefer IS NULL OR ride_prefer IN ('morning','evening'));
ALTER TABLE routine_def ADD COLUMN placement TEXT
    CHECK (placement IS NULL
           OR (placement = 'ride_travel' AND kind = 'task'
               AND schedule_kind = 'timed' AND place_id IS NULL
               AND fallback_at IS NOT NULL));

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 70;
COMMIT;
