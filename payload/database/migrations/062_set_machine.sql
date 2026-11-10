-- 062_set_machine.sql  (target user_version 62)
--
-- reversible: YES, losslessly while no set names a machine.  Rollback =
--   DROP TRIGGER trg_workout_sets_machine_known / _upd; DROP INDEX
--   idx_workout_sets_machine; ALTER TABLE workout_sets DROP COLUMN
--   machine_ref; PRAGMA user_version = 61.
--
-- ==========================================================================
-- WHAT.  Which machine a set was done on (ticket 2026-09-30 per-machine cable
-- lattice).  The gym has two cable machines with different pin stacks; one
-- exercise binds exactly one machine (ax_exercise.machine_ref), so a set done
-- on the other stack snapped to the wrong grid (25 -> 23.75).
--
--   workout_sets.machine_ref TEXT NULL -> ax_machine.ulid
--     NULL = not recorded (every existing row, every freeweight set, and any
--     set logged by a pack that has not been taught to ask).  The exercise's
--     bound machine stays the default; the per-set ref overrides it.
--
-- OWNER RULING 2026-09-30: open the STRUCTURE only.  This file seeds no
-- machine and restores nothing from archive_health_reset_ax_machine.  The
-- health agent captures machines in conversation (first cable exercise of a
-- session: "which cable?", remembered after) through the kit verbs
-- machine-register / machine-list / machine-last; the pin grid is LEARNED
-- from the loads he logs on each machine (machine-grid / machine-snap read
-- them back) until he states it (machine-upd).  None of that is stored twice:
-- "which machine last time" and "which pins exist" are reads over the set
-- rows, not columns.
--
-- WHY A TRIGGER AND NOT (ONLY) REFERENCES.  foreign_keys is a per-connection
-- switch: kit_life's get_conn turns it on, the pack's writers (set-add) open
-- plain sqlite3 connections where it is off, and those are the rows this
-- column exists for.  A declared REFERENCES would be enforced for the one
-- writer that never writes it.  The two triggers refuse an unknown ulid on
-- every connection.  (Plain TEXT also keeps DROP COLUMN legal for rollback;
-- SQLite refuses DROP COLUMN on a REFERENCES column.)
-- A retired machine may still be named (history is history); ax_machine rows
-- are retired by status, never deleted.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_062(seen_user_version);
CREATE TEMP TRIGGER _guard_062_refuse BEFORE INSERT ON _guard_062 BEGIN
  SELECT RAISE(FAIL, 'migration 062 refused: this database is not at user_version 61 -- either 062 was already applied (>= 62) or the chain up to 061 has not run (< 61). Nothing changed');
END;
INSERT INTO _guard_062
  SELECT user_version FROM pragma_user_version WHERE user_version <> 61;
DROP TABLE temp._guard_062;

BEGIN IMMEDIATE;

ALTER TABLE workout_sets ADD COLUMN machine_ref TEXT;
CREATE INDEX idx_workout_sets_machine
    ON workout_sets(machine_ref, recorded_at) WHERE machine_ref IS NOT NULL;

CREATE TRIGGER trg_workout_sets_machine_known
BEFORE INSERT ON workout_sets
WHEN NEW.machine_ref IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM ax_machine WHERE ulid = NEW.machine_ref)
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: machine_ref names no ax_machine row -- register it first (kit_life.sh machine-register)');
END;
CREATE TRIGGER trg_workout_sets_machine_known_upd
BEFORE UPDATE OF machine_ref ON workout_sets
WHEN NEW.machine_ref IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM ax_machine WHERE ulid = NEW.machine_ref)
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: machine_ref names no ax_machine row -- register it first (kit_life.sh machine-register)');
END;

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 62;
COMMIT;
