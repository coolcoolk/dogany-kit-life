-- 027_timeblock_project_id.sql  (target user_version 27)
-- reversible: effectively YES (additive, no data rewrite). Adds a single
--   NULLABLE column timeblock.project_id -> projects(id). No backfill, no CHECK
--   change, no index churn on the hot path. Rollback = drop the column (SQLite
--   3.35+ supports ALTER TABLE ... DROP COLUMN); until then a v27 DB is a strict
--   superset of v26 and every pre-027 query ignores the new NULL column.
--
-- Source: the 2026-08-17 life-integration DESIGN, P2 achievement
--   axis (task -> project). The ag-life-integration DESIGN (owner-confirmed
--   direction: SILENT AUTO-LINK) needs a durable home for the task<->project
--   link that the `task-untimed-open --suggest` auto-suggest already computes at
--   read time. routine_def already carries project_id, so this makes timeblock
--   symmetric. Lane: kit_life (DGN-708) -- not a framework handoff.
--
-- 026 was TAKEN by 026_event_experience_overlay.sql (event/experience overlay,
--   target user_version 26), so this migration is 027 / user_version 27. It
--   DEPENDS ON 026 (v26).
--
-- LANDING (mandatory, same discipline as 025/026): apply on live ONLY via the
--   migration runner + code-pin bump (EXPECTED_USER_VERSION 26->27) in one
--   coordinated run. The live kit_life.db is SHARED by the life agent AND the health agent, so both
--   code pins must move in the same lockstep. NEVER hand-run
--   `sqlite3 live.db < 027...` -- a manual exec stamps user_version=27 while a
--   code pin stays lower -> the SDK fail-closes every event verb. That live
--   application + lockstep is a SEPARATE post-workout step done by the
--   dispatcher; it is NOT performed by authoring this file.
--
-- SAFETY: additive column is invisible to the running v26 agent code (they
--   never SELECT/INSERT it). The persistence code that writes project_id
--   feature-detects the column at runtime (PRAGMA table_info) and no-ops when it
--   is absent, so the write path is safe to sit in the live tree BEFORE this
--   migration is applied.
--
-- Atomicity wrapper (matches 023-026, DGN-871 NO-GO #1): `.bail on` aborts on the
--   first error; BEGIN IMMEDIATE .. COMMIT makes the ALTER PLUS the user_version
--   stamp one atomic unit -> a mid-statement failure rolls back and leaves
--   user_version at 26 (no half-migrated laundering).
.bail on
.timeout 5000
BEGIN IMMEDIATE;

-- Achievement axis: task/appointment/presence blocks link to their owning
-- project. NULLABLE, DEFAULT NULL, no REFERENCES-time scan. measure-first: the
-- column may stay empty; the silent auto-link fills it opportunistically.
ALTER TABLE timeblock ADD COLUMN project_id INTEGER REFERENCES projects(id);

-- Partial index on the linked rows only (NULLs excluded) -- keeps the group-by
-- read (brief/retro "tasks by project") cheap without bloating the common
-- unlinked case.
CREATE INDEX idx_timeblock_project
    ON timeblock(project_id) WHERE project_id IS NOT NULL;

PRAGMA user_version = 27;

-- Close the atomicity wrapper. Any failure above -> .bail aborted, no COMMIT,
-- rollback, user_version stays 26.
COMMIT;
