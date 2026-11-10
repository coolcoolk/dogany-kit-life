-- 111_gym_exercise_machine.sql (target user_version 111)
-- WHY: the same exercise uses different machines/stacks at different gyms.
-- ax_exercise.machine_ref is a global default and workout_sets.machine_ref
-- is history; neither can hold the owner's per-gym choice (2026-10-31).
-- Store that choice once in the kit, rather than in pack-local JSON. Exercise
-- is the pack's exercise code (ax_exercise.exercise_name), not a display label;
-- it need not have an ax_exercise row yet. No machines or links are seeded.
-- Additive only: one table, index and guards; existing rows/schema unchanged.
-- Triggers enforce active machines even for pack connections with FKs off.
-- Archiving keeps old links inspectable for an explicitly confirmed cleanup.
-- reversible: drop this table (and its triggers/index), set user_version=110;
-- export any choices first. Existing history is never changed by migration.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_111(v);
CREATE TEMP TRIGGER _guard_111_refuse BEFORE INSERT ON _guard_111 BEGIN
 SELECT RAISE(ABORT, 'migration 111 requires user_version 110');
END;
INSERT INTO _guard_111 SELECT user_version FROM pragma_user_version WHERE user_version<>110;
DROP TABLE _guard_111;
BEGIN IMMEDIATE;

CREATE TABLE ax_gym_exercise_machine (
 gym_id INTEGER NOT NULL REFERENCES place(id),
 exercise_code TEXT NOT NULL CHECK(length(trim(exercise_code))>0),
 machine_ref TEXT NOT NULL REFERENCES ax_machine(ulid),
 source TEXT NOT NULL CHECK(length(trim(source))>0),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(gym_id,exercise_code)
);
CREATE INDEX idx_ax_gym_exercise_machine_ref
 ON ax_gym_exercise_machine(machine_ref);
CREATE TRIGGER trg_ax_gym_exercise_machine_active
BEFORE INSERT ON ax_gym_exercise_machine
WHEN NOT EXISTS(SELECT 1 FROM ax_machine WHERE ulid=NEW.machine_ref AND status='active')
BEGIN
 SELECT RAISE(ABORT, 'gym link refused: machine must exist and be active');
END;
CREATE TRIGGER trg_ax_gym_exercise_machine_active_upd
BEFORE UPDATE ON ax_gym_exercise_machine
WHEN NOT EXISTS(SELECT 1 FROM ax_machine WHERE ulid=NEW.machine_ref AND status='active')
BEGIN
 SELECT RAISE(ABORT, 'gym link refused: machine must exist and be active');
END;
CREATE TRIGGER trg_ax_gym_exercise_machine_gym
BEFORE INSERT ON ax_gym_exercise_machine
WHEN NOT EXISTS(SELECT 1 FROM place WHERE id=NEW.gym_id)
BEGIN
 SELECT RAISE(ABORT, 'gym link refused: gym must name a place');
END;
CREATE TRIGGER trg_ax_gym_exercise_machine_gym_upd
BEFORE UPDATE OF gym_id ON ax_gym_exercise_machine
WHEN NOT EXISTS(SELECT 1 FROM place WHERE id=NEW.gym_id)
BEGIN
 SELECT RAISE(ABORT, 'gym link refused: gym must name a place');
END;

PRAGMA user_version=111;
COMMIT;
