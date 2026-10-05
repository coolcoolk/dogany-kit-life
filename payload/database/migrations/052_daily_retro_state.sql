-- 052_daily_retro_state.sql  (target user_version 52)
--
-- reversible: YES, losslessly.  Rollback (SQLite >= 3.35) =
--   ALTER TABLE block DROP COLUMN retro_line;
--   ALTER TABLE routine_def DROP COLUMN retro_skip;
--   PRAGMA user_version = 51;
-- Two ADD COLUMN statements on two tables, no rebuild, no seed, no backfill.
-- Every existing block row gets NULL retro_line ("no line was ever taken on
-- this event"); every existing routine_def row gets retro_skip=0 ("still
-- askable"), which is byte-identical to today's behaviour since nothing
-- reads either column before this migration lands.
--
-- ==========================================================================
-- WHY -- THE DAILY RETRO ASKS TWO QUESTIONS THAT HAVE NO ROW TO LAND IN.
--
-- The daily-retro / morning-brief flow (2026-09-21 retro overhaul,
-- daily-first) needs to (1) remember what the
-- owner said about a specific event after the fact, and (2) remember that a
-- given routine is not worth asking about in retro at all -- without
-- touching whether that routine still runs or still shows on the schedule.
-- Neither fact has a home today:
--
--   block            has event identity and timing but no reflective-text
--                     column (schema.sql block DDL).  block_attr can
--                     physically carry an arbitrary attribute, but no
--                     retro_line token or ownership contract exists for one,
--                     and timeblock.summary is already claimed by
--                     appointment-only META_COLS_BY_KIND -- a different fact
--                     with a different owner.
--   routine_def      has status (active|paused|retired), which changes
--                     MATERIALIZATION, not retro scope.  Skipping retro
--                     questions for a routine must not pause it or hide its
--                     events; conflating the two would make "stop asking
--                     about this" also mean "stop running this".
--
-- Both gaps are DGN retro-verb dispatch design work (see the design's own
-- storage-evidence sections); this migration is the storage half only.  The
-- four verbs that read/write these columns (event-retro-set,
-- routine-retro-skip, routine-for-event, and the event-window/appt-find
-- person+presence extension) land in the same cut as ordinary kit_life.py
-- code, gated on this migration having already applied -- same order 051
-- used for its pack-side dispatch.
--
-- ==========================================================================
-- WHAT THIS MIGRATION DELIBERATELY DOES NOT DECIDE.
--
-- block.retro_line is REPLACEMENT state, not an append-only transcript: one
-- received line per event, because that is the smallest schema the approved
-- daily flow asks for.  Multiple timestamped retro entries per event, should
-- that ever be wanted, is a later column (or table) this migration does not
-- pre-build.
--
-- No FK, no UNIQUE, no index on either column: retro_line is read by exactly
-- the event it is set on (point lookup by block.id), and retro_skip is read
-- by exactly the routine_def row it gates (point lookup by routine_def.id).
-- Neither is ever the predicate of a range scan.
--
-- ==========================================================================
-- GUARD.  user_version must be EXACTLY 51, checked BEFORE any transaction --
-- a re-apply (>= 52) or a skipped chain (< 51) is refused, database
-- untouched, same 037/040/044/045/046/047/048 discipline.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_052(seen_user_version);
CREATE TEMP TRIGGER _guard_052_refuse BEFORE INSERT ON _guard_052 BEGIN
  SELECT RAISE(FAIL, 'migration 052 refused: this database is not at user_version 51 -- either 052 was already applied (>= 52) or the 021~051 chain has not run (< 51). Nothing changed');
END;
INSERT INTO _guard_052
  SELECT user_version FROM pragma_user_version WHERE user_version <> 51;
DROP TABLE temp._guard_052;
BEGIN IMMEDIATE;

-- A1: the owner's retro line for one event.  NULL for every existing row =
-- "never asked, never answered".  Replacement semantics live in the verb
-- (event-retro-set), not here -- this column has no history of its own.
ALTER TABLE block ADD COLUMN retro_line TEXT;

-- A2: per-routine retro-question exclusion.  DEFAULT 0 = every existing
-- routine stays askable, unchanged.  This gates retro QUESTIONING ONLY --
-- it must never be read by the roller/materializer, which is why it has no
-- relationship to `status`.
ALTER TABLE routine_def ADD COLUMN retro_skip INTEGER NOT NULL DEFAULT 0
    CHECK (retro_skip IN (0,1));

-- The timeblock compat view must expose retro_line so read paths keyed off
-- `timeblock` (rather than `block` directly) can see it -- appended as the
-- view's FINAL column so no existing positional consumer's column count
-- moves (036's own compat contract; SELECT * FROM timeblock reads go through
-- _travel_row/_travel_rows, which are dict-keyed, so an appended column is
-- inert to them).  SQLite has no ALTER VIEW; the view is dropped and
-- recreated verbatim from schema.sql with the one appended column.
DROP VIEW timeblock;
CREATE VIEW timeblock AS
SELECT b.id                AS id,
       b.ulid              AS ulid,
       b.title             AS title,
       b.start_at          AS start_at,
       b.end_at            AS end_at,
       b.kind_token        AS kind,
       s.state_token       AS status,
       b.settled_at        AS settled_at,
       b.settled_outcome   AS settled_outcome,
       b.settled_by        AS settled_by,
       b.place_id          AS place_id,
       b.rec_date          AS rec_date,
       b.created_at        AS created_at,
       (SELECT a.ulid FROM block a WHERE a.id = b.derived_from) AS derived_from,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'schedule_kind' LIMIT 1) AS schedule_kind,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'completion_rule' LIMIT 1) AS completion_rule,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'display_tz' LIMIT 1) AS display_tz,
       CAST((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'slot_exclusive' LIMIT 1) AS INTEGER)
                                       AS slot_exclusive,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'open_ended' LIMIT 1),
              CASE WHEN b.start_at IS NOT NULL AND b.end_at IS NULL
                   THEN 1 ELSE 0 END) AS INTEGER)
                                       AS open_ended,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'location' LIMIT 1) AS location,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'location_url' LIMIT 1) AS location_url,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'purpose' LIMIT 1) AS purpose,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'summary' LIMIT 1) AS summary,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gtask_id' LIMIT 1) AS gtask_id,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'notify_policy' LIMIT 1) AS notify_policy,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'block_class' LIMIT 1) AS block_class,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'derived_role' LIMIT 1) AS derived_role,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'derived_pinned' LIMIT 1), 0) AS INTEGER)
                                       AS derived_pinned,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'derived_delta' LIMIT 1) AS derived_delta,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'time_mode' LIMIT 1) AS time_mode,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'note' LIMIT 1) AS note,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'version' LIMIT 1), 0) AS INTEGER)
                                       AS version,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'updated_at' LIMIT 1) AS updated_at,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'owning_agent' LIMIT 1) AS owning_agent,
       CAST((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'notify_lead_min' LIMIT 1) AS INTEGER)
                                       AS notify_lead_min,
       (SELECT pb.project_id FROM project_block pb WHERE pb.block_id = b.id) AS project_id,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'recurrence_id' LIMIT 1) AS recurrence_id,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'rec_exception' LIMIT 1), 0) AS INTEGER)
                                       AS rec_exception,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'created_by' LIMIT 1) AS created_by,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'is_routine' LIMIT 1), 0) AS INTEGER)
                                       AS is_routine,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gcal_event_id' LIMIT 1) AS gcal_event_id,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gcal_etag' LIMIT 1) AS gcal_etag,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gtask_etag' LIMIT 1) AS gtask_etag,
       NULL                AS area_id,  -- UNMAPPED(O-7); old readers may still name it
       b.retro_line        AS retro_line
FROM block b
LEFT JOIN v_block_state s ON s.block_id = b.id
WHERE b.ulid NOT LIKE 'x-%';

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 51.
PRAGMA user_version = 52;
COMMIT;
