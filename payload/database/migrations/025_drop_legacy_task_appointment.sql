-- 025_drop_legacy_task_appointment.sql  (target user_version 25)
-- reversible: NO. This legalizes the removal of three legacy tables whose data
--   already migrated to `timeblock` (023 event->timeblock). Rollback would need
--   the original rows, which no longer exist as separate tables -- the data
--   lives in timeblock now. Roll-forward only.
--
-- WHY (DGN-895): the live crew DB drifted OUT OF BAND. On 2026-08-11 the three
--   legacy tables (tasks / appointments / appointment_persons) were dropped
--   directly on the live DB (evidence: kit_life.db.bak-predrop-20260811-063624)
--   WITHOUT a migration, so user_version stayed 24 while the tables vanished.
--   Result: fresh-mint schema.sql (v24) still CREATEs the three tables, but the
--   migrated/live DB has them gone -> the C3 parity contract (fresh==migrated)
--   is FALSE on live, and Ag relmod.py's legacy appointment lanes hit
--   `no such table: appointments` (crash path -- guarded in DGN-895 (1)).
--   Data-loss check (DGN-895 handoff): rows already live in
--   timeblock (kind=appointment 374 / task 2396 vs predrop backup 338 / 1855)
--   -> net gain, zero loss. Data decision (a): legalize the drop.
--
-- WHAT: drop the three legacy tables (+ their five indexes, auto-dropped with
--   the table) as a real migration so fresh==migrated at v25. The kit authors the
--   matching fresh schema.sql edit (remove the three CREATEs at lines ~55/945/
--   960 + the five indexes at lines ~1065-1069 + bump the stamp to 25) and the
--   EXPECTED_USER_VERSION lockstep bump (24 -> 25) per propagation lane
--   (DGN-708). The framework authored THIS migration SQL. v24 -> v25.
--
-- IDEMPOTENCY / drift-legalize: DROP TABLE IF EXISTS makes this run cleanly on
--   BOTH the already-drifted live DB (tables absent -> no-op) AND any clean
--   DB still at a true v24 (tables present -> real drop). Either way the end
--   state is identical: tables gone, user_version=25. This is the crux -- the
--   migration must converge both populations to one legal state.
--
-- DROP ORDER: appointment_persons FIRST (it FK-references appointments via
--   ON DELETE CASCADE; drop the child before the parent so an enabled
--   foreign_keys pragma cannot object), then appointments, then tasks
--   (independent -- no inbound FK, verified against schema.sql). Indexes
--   idx_tasks_done / idx_tasks_due / idx_tasks_notion / idx_appt_notion /
--   idx_appt_start drop automatically with their tables -- no explicit
--   DROP INDEX needed, and IF EXISTS on the tables covers the drifted case.
--
-- NO new data, no backfill: the surviving data already lives in timeblock (023).
--   This migration only removes dead schema objects.
--
-- LANDING (mandatory, DGN-895 recurrence prevention): apply on live ONLY via
--   update.sh (the migration runner), which applies THIS SQL and bumps the code
--   pin EXPECTED_USER_VERSION 24->25 in one run. NEVER run this file by hand
--   (`sqlite3 live.db < 025...`): a manual exec stamps user_version=25 while the
--   code pin stays 24 -> kit_life.py:1786 `v != EXPECTED_USER_VERSION` fail-closes
--   EVERY event verb (full SDK stop). Manual DB surgery is exactly what caused
--   the 2026-08-11 out-of-band drop this migration is legalizing -- do not reuse
--   the failure's tool as the fix's landing path.
--
-- Atomicity wrapper (matches 023/024, DGN-871 NO-GO #1): `.bail on` aborts on
--   first error; BEGIN IMMEDIATE .. COMMIT makes the drops PLUS the user_version
--   stamp one atomic unit, so a mid-statement failure rolls back and leaves
--   user_version at 24 (no half-migrated laundering). Dot-commands are
--   sqlite3-CLI-only -- the runner consumes this via the CLI.
.bail on
.timeout 5000
BEGIN IMMEDIATE;

-- 1. child first: appointment_persons (FK -> appointments ON DELETE CASCADE)
DROP TABLE IF EXISTS appointment_persons;

-- 2. parent: appointments (indexes idx_appt_notion / idx_appt_start drop with it)
DROP TABLE IF EXISTS appointments;

-- 3. independent: tasks (idx_tasks_done / idx_tasks_due / idx_tasks_notion drop with it)
DROP TABLE IF EXISTS tasks;

PRAGMA user_version = 25;

-- Close the atomicity wrapper. If any statement above failed, .bail on aborted
-- before here and this COMMIT never ran -> rollback, user_version stays 24.
COMMIT;
