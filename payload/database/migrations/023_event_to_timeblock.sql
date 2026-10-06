-- 023_event_to_timeblock.sql  (target user_version 23)
-- reversible: yes (symmetric rename back timeblock -> event; no data change).
-- Source: DGN-855 event -> timeblock core-table rename (kit_life).
--   Transition B (atomic, keep-FK-names, single lockstep). Owner-approved
--   2026-08-13 22:37.
-- v22 -> v23.
--
-- SCOPE: rename ONLY the core TABLE event -> timeblock and its table-refs.
--   KEPT unchanged (NOT renamed):
--     * COLUMN identifiers: event_id (FK cols in sub_event/event_persons),
--       event_ulid (reschedule_requests), derived_from, placement_depends_on.
--     * FK TABLE names + columns: sub_event, event_persons (and their
--       idx_sub_event_* / idx_event_persons_* indexes).
--     * VIEW NAME v_event_resolution (its BODY is repointed FROM event ->
--       FROM timeblock).
--     * anchor_type='event' in travel_rule -- a SEMANTIC ENUM value, not a
--       table ref.
--
-- Transition B = NO writable alias. No back-compat `event` view is created;
--   every SQL table-ref is cut over to `timeblock` in the same lockstep
--   (kit_life.py / routine_roller.py / remind_select.py + tests), and the
--   mirror/adapter cutover lands in the coordinated framework release.
--
-- WHY the ALTER auto-rewrites dependents:
--   SQLite >= 3.25 (legacy_alter_table OFF, the default) makes
--   `ALTER TABLE ... RENAME TO ...` automatically repoint FK references,
--   view bodies, and trigger bodies that name the old table. The FK columns
--   in sub_event / event_persons therefore keep their names but their
--   REFERENCES target follows to `timeblock` with no manual rebuild.
--
--   The ALTER does NOT rename dependent INDEX names -- an index on the old
--   `event` table keeps its `idx_event_*` name (now pointing at timeblock).
--   To match the fresh-mint v23 schema.sql (fresh == migrated), the
--   event-table-body indexes are dropped and recreated as idx_timeblock_*.
--   The view is likewise dropped and recreated (name kept) so its stored body
--   text is byte-identical to schema.sql.

-- Atomicity wrapper (DGN-871 grill, NO-GO #1): the migration runner
--   consumes this file via `sqlite3 $DB < mig` with .bail OFF by default. Without
--   the wrapper a mid-statement failure keeps going and still stamps
--   PRAGMA user_version=23 at the end -> a half-migrated DB launders as "in sync"
--   on the next install run (reproduced in grill /tmp/grill871). `.bail on`
--   aborts on the first error; BEGIN IMMEDIATE .. COMMIT makes the whole rename
--   PLUS the user_version stamp a single atomic unit (rolled back together on any
--   failure). NOTE: dot-commands are sqlite3-CLI-only (not Python executescript)
--   -- the runner is the CLI, so this is consistent.
.bail on
.timeout 5000
-- Ensure modern rename semantics (default on 3.25+, set explicitly for safety).
PRAGMA legacy_alter_table=OFF;
BEGIN IMMEDIATE;

-- ============================================================================
-- 1. Rename the core table. FK refs (sub_event.event_id, event_persons.event_id)
--    and the v_event_resolution view body auto-repoint to `timeblock`.
-- ============================================================================
ALTER TABLE event RENAME TO timeblock;

-- ============================================================================
-- 2. Repoint the resolution view. Keep the NAME v_event_resolution; the body
--    now reads FROM timeblock. (DROP + recreate so the stored text matches the
--    fresh-mint schema.sql exactly, independent of the ALTER auto-rewrite.)
-- ============================================================================
DROP VIEW IF EXISTS v_event_resolution;
CREATE VIEW v_event_resolution AS
SELECT e.id, e.ulid, e.kind, e.time_mode,
  CASE
    WHEN e.settled_outcome = 'abandoned' THEN 'skipped'
    WHEN e.settled_outcome = 'done' THEN
         CASE WHEN e.time_mode = 'anchored' THEN 'happened' ELSE 'done' END
    WHEN e.time_mode = 'anchored'
         AND (CASE WHEN e.end_at IS NULL THEN '~~~~~~~~~~~~~~~~~~~~'
                   WHEN e.end_at = e.start_at THEN
                        strftime('%Y-%m-%dT%H:%M:%SZ', e.start_at, '+1 second')
                   ELSE e.end_at END)
             <= strftime('%Y-%m-%dT%H:%M:%SZ','now')
      THEN 'missed'
    ELSE 'open'
  END AS resolution
FROM timeblock e
WHERE e.kind != 'presence';

-- ============================================================================
-- 3. Rename the event-table-body indexes -> idx_timeblock_*.
--    (idx_sub_event_* and idx_event_persons_* belong to the FK tables which
--    keep their names -- they are LEFT UNTOUCHED.)
-- ============================================================================
DROP INDEX IF EXISTS idx_event_notion;
CREATE UNIQUE INDEX idx_timeblock_notion
    ON timeblock(notion_id) WHERE notion_id IS NOT NULL;

DROP INDEX IF EXISTS idx_event_overlap;
CREATE INDEX idx_timeblock_overlap
    ON timeblock(slot_exclusive, settled_at, start_at, end_at);

DROP INDEX IF EXISTS idx_event_kind;
CREATE INDEX idx_timeblock_kind ON timeblock(kind);

DROP INDEX IF EXISTS idx_event_time_mode;
CREATE INDEX idx_timeblock_time_mode ON timeblock(time_mode, start_at);

DROP INDEX IF EXISTS idx_event_place;
CREATE INDEX idx_timeblock_place
    ON timeblock(place_id) WHERE place_id IS NOT NULL;

DROP INDEX IF EXISTS idx_event_presence_live;
CREATE INDEX idx_timeblock_presence_live
    ON timeblock(start_at, end_at) WHERE kind = 'presence' AND settled_at IS NULL;

DROP INDEX IF EXISTS idx_event_rec_live;
CREATE UNIQUE INDEX idx_timeblock_rec_live
    ON timeblock(recurrence_id, rec_date)
    WHERE recurrence_id IS NOT NULL
      AND rec_date IS NOT NULL
      AND settled_at IS NULL
      AND rec_exception = 0;

DROP INDEX IF EXISTS idx_event_travel_live;
CREATE UNIQUE INDEX idx_timeblock_travel_live
    ON timeblock(derived_from, derived_role)
    WHERE block_class = 'travel' AND settled_at IS NULL;

PRAGMA user_version = 23;

-- Close the atomicity wrapper (NO-GO #1). If any statement above failed, .bail on
-- aborted before here and this COMMIT never ran -> the transaction rolls back and
-- user_version stays at 22 (no half-migrated laundering).
COMMIT;
