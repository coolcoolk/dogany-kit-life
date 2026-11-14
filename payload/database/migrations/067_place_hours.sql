-- 067_place_hours.sql  (target user_version 67)
--
-- reversible: YES.  Rollback = DROP TABLE place_hours; PRAGMA user_version =
--   66.  Additive: one new table, no existing table / column / trigger touched.
--
-- ==========================================================================
-- WHAT.  When a place is open, once per place: weekday ranges, breaks,
-- closed days, the public-holiday rule, the page it was read from and the
-- day it was read.
--
-- WHY (owner 2026-10-06 14:24, after the life agent filed a dentist visit and then told
-- the owner to check the opening hours / call the clinic): "the agent should
-- have looked those up instead of asking me back".  The kit had nowhere to
-- keep hours, so every turn started from nothing and the agent pushed the
-- lookup onto the owner.  Now the kit asks the AGENT (PLACE_HOURS_NEEDED,
-- the TRAVEL_PLACE_NEEDED pattern), the agent looks the hours up silently
-- and stores them here (kit_life.sh place-hours-set), and every later card /
-- visit question reads them (kit_life.sh place-hours <place> [date]).
--
-- WHY A TABLE.  The existing per-place stores do not fit:
--   measure      scoped to block / entity / place_link, REAL values only.
--   place_coord  (066) one row per place too, but lat/lng are NOT NULL --
--                a place can have hours and no coordinates.
--   place.note   free text the owner owns.
-- A week of ranges is one structured value read whole, so it is stored as
-- one normalized JSON document per place (shape owned by kit_life.py
-- place_hours_parse; the kit never writes anything else into it).
--
--   hours       normalized JSON: {"days": {"mon": {"open": "09:30",
--               "close": "18:30", "breaks": [["13:00","14:00"]]} | "closed",
--               ...}, "holiday": "closed" | "open" | absent}; or {"none":
--               true} = the place keeps no opening hours (a home, a park,
--               an office) or none could be found -- not looked up again
--               inside the stale window.
--   source_url  the page the hours were read from (NULL = the owner said).
--   fetched_on  local date (YYYY-MM-DD) the hours were read; older than the
--               stale window (90 days) -> the kit asks for a refresh.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_067(seen_user_version);
CREATE TEMP TRIGGER _guard_067_refuse BEFORE INSERT ON _guard_067 BEGIN
  SELECT RAISE(FAIL, 'migration 067 refused: this database is not at user_version 66 -- either 067 was already applied (>= 67) or the chain up to 066 has not run (< 66). Nothing changed');
END;
INSERT INTO _guard_067
  SELECT user_version FROM pragma_user_version WHERE user_version <> 66;
DROP TABLE temp._guard_067;

BEGIN IMMEDIATE;

CREATE TABLE place_hours (
  place_id   INTEGER PRIMARY KEY REFERENCES place(id) ON DELETE CASCADE,
  hours      TEXT NOT NULL CHECK (json_valid(hours)),
  source_url TEXT CHECK (source_url IS NULL OR TRIM(source_url) <> ''),
  fetched_on TEXT NOT NULL
             CHECK (fetched_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 67;
COMMIT;
