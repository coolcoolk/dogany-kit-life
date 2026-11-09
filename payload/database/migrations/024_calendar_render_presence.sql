-- 024_calendar_render_presence.sql  (target user_version 24)
-- reversible: partial. ADD COLUMN is not cleanly reversible without a table
--   rebuild; the person_group CREATE and the title backfill are a pure DROP /
--   UPDATE on rollback. Rollback path = drop person_group + table-rebuild
--   dropping the two display_name columns (their data lost).
-- Source: the 2026-08-14 calendar render/presence spec (owner-designed;
--   FIRST real storage-convention application, R1-R8). The kit authors schema +
--   migration SQL; the framework migration engine applies under EXPECTED_USER_VERSION
--   lockstep. v23 -> v24.
--
-- SCOPE:
--   1. persons.display_name TEXT NULL -- calendar-facing render label. NULL ->
--      fall back to persons.name. [shared-core, R4a; persons already core]
--   2. place.display_name   TEXT NULL -- calendar-facing render label (e.g.
--      place 회사 -> "사무실"). NULL -> fall back to place.name. [shared-core]
--   3. NEW person_group (id, name UNIQUE, display_name) -- shared-core
--      display-lookup keyed by group name. v1 = display-lookup only; the FULL
--      normalize (persons.groups comma-list -> junction FK) is R2 debt (flagged
--      below, NOT built here). persons.groups stays the membership source.
--   4. Title normalization backfill (DATA, not render): today-onward
--      work-presence rows (title IN 회사/출근/재택/회사 사무실) title -> 회사, so
--      행위=title is clean. Office vs home is distinguished by PLACE (사무실 vs
--      집), NEVER by title. DGN-840 today-onward scope; past rows untouched.
--
-- R8 owning_agent: persons / place / person_group are the front-door people/
--   place model -> owning_agent = Ag (enforced by the SDK write-layer for
--   person_group, same pattern as place.is_base / granularity; the two
--   ADD-COLUMN tables keep their existing owning_agent posture).
--
-- R7: display_name nullable, no new PII (a shorter label of an existing name).
--
-- The ADD COLUMN order + the person_group DDL below are byte-matched to the
--   fresh-mint v24 schema.sql so `fresh == migrated`. The runner gates on
--   user_version, so the non-idempotent ALTERs run exactly once.
--
-- Atomicity wrapper (DGN-871 NO-GO #1, matches 023): the runner consumes this
--   file via `sqlite3 $DB < mig` with .bail OFF by default; without the wrapper
--   a mid-statement failure keeps going and still stamps user_version=24 -> a
--   half-migrated DB launders as "in sync". `.bail on` aborts on first error;
--   BEGIN IMMEDIATE .. COMMIT makes the whole change PLUS the user_version stamp
--   one atomic unit. Dot-commands are sqlite3-CLI-only -- the runner is the CLI.
.bail on
.timeout 5000
BEGIN IMMEDIATE;

-- ============================================================================
-- 1. persons.display_name -- calendar render label. NULLABLE, DEFAULT NULL.
--    ALTER ADD COLUMN carries no CHECK; nothing to enforce here (free text).
-- ============================================================================
ALTER TABLE persons ADD COLUMN display_name TEXT;

-- ============================================================================
-- 2. place.display_name -- calendar render label. NULLABLE, DEFAULT NULL.
-- ============================================================================
ALTER TABLE place ADD COLUMN display_name TEXT;

-- ============================================================================
-- 3. person_group -- shared-core display-lookup keyed by group name.
--    v1: display-lookup ONLY. Membership stays in persons.groups (comma-list).
--    R2 DEBT (flag, do NOT build here): normalize persons.groups into a
--    person_group_member(person_id, group_id) junction FK so membership is
--    single-sourced. Deferred by design -- v1 needs only the display label.
--    owning_agent enforced by the SDK write-layer (no ADD-COLUMN CHECK path
--    here since this is a fresh CREATE, but the write-layer owns the (0,1)/
--    ownership tokens the same way as place; kept NOT NULL for correctness).
-- ============================================================================
CREATE TABLE person_group (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,   -- matches a token in persons.groups
    display_name TEXT,                   -- calendar-facing label; NULL -> name
    owning_agent TEXT NOT NULL DEFAULT 'ag',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ============================================================================
-- 4. Title normalization backfill (DATA). Today-onward work-presence rows only
--    (DGN-840 scope). Office vs home is decided by PLACE downstream, so ALL of
--    회사/출근/재택/회사 사무실 collapse to the single clean 행위 title 회사.
--    Guard: kind='presence' AND NOT a travel block (block_class travel rows
--    such as "CBBG 천안" keep their own title -- they render via the 이동 /
--    여행 path, never the work-presence path). Local-date compare: presence
--    start_at is a UTC instant; 'now','localtime' -> local date string; the
--    substr(start_at,1,10) >= today keeps this a today-onward filter that is
--    robust to the +09:00 offset within a day tolerance (a boundary row at
--    local midnight is included, never a past row).
-- ============================================================================
UPDATE timeblock
   SET title = '회사'
 WHERE kind = 'presence'
   AND COALESCE(block_class,'') != 'travel'
   AND title IN ('회사','출근','재택','회사 사무실')
   AND substr(start_at,1,10) >= strftime('%Y-%m-%d','now','localtime');

PRAGMA user_version = 24;

-- Close the atomicity wrapper. If any statement above failed, .bail on aborted
-- before here and this COMMIT never ran -> the transaction rolls back and
-- user_version stays at 23 (no half-migrated laundering).
COMMIT;
