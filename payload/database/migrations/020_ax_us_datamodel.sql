-- 020_ax_us_datamodel.sql  (target user_version 20)
-- reversible: yes
-- Source: DGN-789 AX/US data-model decision artifact
--   (files/handoff/ax-us-datamodel-decision-artifact-20260809.md).
-- v19 -> v20.
--
-- Pure-additive migration: 7 NEW health-domain tables (us_step, ax_exercise,
-- ax_machine, ax_projection_slot, ax_projection_rule, ax_tag, ax_exercise_tag),
-- all with a ULID string PK + owning_agent CHECK-guarded column, closed-enum
-- CHECKs, open-tag registry, 9 seeded ax_projection_rule rows (7 base + Track B cable/freeweight).
-- All CREATE ... IF NOT EXISTS; no ALTER on existing tables. reversible:yes --
-- new-table adds only (a rollback would DROP these 7 tables). ULID PKs are
-- disjoint across agents by construction -> merge-order-free (datamodel sec 4).
--
-- SEAM: this is the DATA-MODEL + rule-CONTENT only. The projection ENGINE (rule
-- evaluation, sequence render/delivery, JIT-gate, place-FK-rewrite lockstep) is
-- the framework's DGN-782 build -- these tables + seeded rule rows are what it consumes.
--
-- The CREATE TABLE / INDEX DDL below is byte-matched to the fresh-mint v20
-- schema.sql so `fresh == migrated`.

-- ============================================================================
-- AX layer 1: exercise = the movement, identical wherever performed.
-- ============================================================================
CREATE TABLE IF NOT EXISTS ax_exercise (
    ulid          TEXT PRIMARY KEY,
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    exercise_name TEXT NOT NULL,
    movement      TEXT NOT NULL,
    measure       TEXT NOT NULL,
    load_type     TEXT NOT NULL,
    tempo         TEXT,
    hold_sec      INTEGER,
    machine_ref   TEXT REFERENCES ax_machine(ulid),
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    CHECK (owning_agent IN ('ag','warg','user')),
    CHECK (movement  IN ('compound','isolation')),
    CHECK (measure   IN ('reps','hold_sec','tempo')),
    CHECK (load_type IN ('freeweight','machine','cable','bodyweight')),
    CHECK (status    IN ('active','retired')),
    CHECK (measure != 'hold_sec' OR hold_sec IS NOT NULL),
    CHECK (measure != 'tempo'    OR tempo    IS NOT NULL),
    CHECK (load_type NOT IN ('machine','cable') OR machine_ref IS NOT NULL),
    CHECK (load_type NOT IN ('freeweight','bodyweight') OR machine_ref IS NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ax_exercise_name
    ON ax_exercise(owning_agent, exercise_name);
CREATE INDEX IF NOT EXISTS idx_ax_exercise_machine
    ON ax_exercise(machine_ref) WHERE machine_ref IS NOT NULL;

-- ============================================================================
-- AX layer 2: machine = a resource the gym owns (gym-scoped equipment registry).
-- ============================================================================
CREATE TABLE IF NOT EXISTS ax_machine (
    ulid            TEXT PRIMARY KEY,
    owning_agent    TEXT NOT NULL DEFAULT 'warg',
    gym_id          INTEGER REFERENCES place(id),
    label           TEXT NOT NULL,
    brand           TEXT,
    loading_method  TEXT,
    loading_side    TEXT,
    smith_type      TEXT,
    cable_lines     TEXT,
    available_plates TEXT,
    lattice_base    REAL,
    lattice_step    REAL,
    status          TEXT NOT NULL DEFAULT 'active',
    provisional     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK (owning_agent IN ('ag','warg','user')),
    CHECK (loading_side IS NULL OR loading_side IN ('single','per_side','stack')),
    CHECK (smith_type   IS NULL OR smith_type   IN ('vertical','angled')),
    CHECK (cable_lines  IS NULL OR cable_lines  IN ('single','dual')),
    CHECK (status       IN ('active','retired')),
    CHECK (provisional  IN (0,1)),
    CHECK ((lattice_base IS NULL) = (lattice_step IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_ax_machine_gym
    ON ax_machine(gym_id) WHERE gym_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ax_machine_label
    ON ax_machine(gym_id, label);

-- ============================================================================
-- AX rule registry: attribute-match -> setting-derivation, priority-ordered.
-- ============================================================================
CREATE TABLE IF NOT EXISTS ax_projection_rule (
    ulid          TEXT PRIMARY KEY,
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    code          TEXT NOT NULL UNIQUE,
    priority      INTEGER NOT NULL DEFAULT 100,
    match_movement   TEXT,
    match_load_type  TEXT,
    match_loading    TEXT,
    match_cable_lines TEXT,
    match_measure    TEXT,
    match_has_injury INTEGER,
    action_kind   TEXT NOT NULL,
    action_param  TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    CHECK (owning_agent IN ('ag','warg','user')),
    CHECK (match_movement    IS NULL OR match_movement    IN ('compound','isolation')),
    CHECK (match_load_type   IS NULL OR match_load_type   IN ('freeweight','machine','cable','bodyweight')),
    CHECK (match_cable_lines IS NULL OR match_cable_lines IN ('single','dual')),
    CHECK (match_measure     IS NULL OR match_measure     IN ('reps','hold_sec','tempo')),
    CHECK (match_has_injury  IS NULL OR match_has_injury  IN (0,1)),
    CHECK (action_kind IN (
        'empty_bar_ramp',
        'injury_drill_include',
        'order_defer',
        'tut_progression',
        'plate_projection',
        'cable_line_specify',
        'lattice_round'
    )),
    CHECK (status IN ('active','superseded'))
);

-- ============================================================================
-- AX layer 3 output: projection = resource-constrained rounder OUTPUT.
-- ============================================================================
CREATE TABLE IF NOT EXISTS ax_projection_slot (
    ulid            TEXT PRIMARY KEY,
    owning_agent    TEXT NOT NULL DEFAULT 'warg',
    step_ref        TEXT NOT NULL REFERENCES us_step(ulid) ON DELETE CASCADE,
    exercise_ref    TEXT NOT NULL REFERENCES ax_exercise(ulid),
    machine_ref     TEXT REFERENCES ax_machine(ulid),
    target_raw      REAL,
    realized_value  REAL,
    realized_detail TEXT,
    provisional     INTEGER NOT NULL DEFAULT 0,
    rule_ref        TEXT REFERENCES ax_projection_rule(ulid),
    created_at      TEXT NOT NULL,
    CHECK (owning_agent IN ('ag','warg','user')),
    CHECK (provisional  IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_ax_projection_step
    ON ax_projection_slot(step_ref);

-- ============================================================================
-- Open-tag vocabulary registry (grows by INSERT; no schema change).
-- ============================================================================
CREATE TABLE IF NOT EXISTS ax_tag (
    ulid          TEXT PRIMARY KEY,
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    tag_group     TEXT NOT NULL,
    value         TEXT NOT NULL,
    note          TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    CHECK (owning_agent IN ('ag','warg','user')),
    CHECK (status IN ('active','retired'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ax_tag_uni
    ON ax_tag(owning_agent, tag_group, value);

-- ============================================================================
-- exercise <-> open-tag junction (an exercise carries N tags across groups).
-- ============================================================================
CREATE TABLE IF NOT EXISTS ax_exercise_tag (
    ulid          TEXT PRIMARY KEY,
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    exercise_ref  TEXT NOT NULL REFERENCES ax_exercise(ulid) ON DELETE CASCADE,
    tag_ref       TEXT NOT NULL REFERENCES ax_tag(ulid),
    created_at    TEXT NOT NULL,
    CHECK (owning_agent IN ('ag','warg','user'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ax_exercise_tag_uni
    ON ax_exercise_tag(exercise_ref, tag_ref);
CREATE INDEX IF NOT EXISTS idx_ax_exercise_tag_tag
    ON ax_exercise_tag(tag_ref);

-- ============================================================================
-- US: one unified sequence-step table. One exercise = several step rows.
-- ============================================================================
CREATE TABLE IF NOT EXISTS us_step (
    ulid           TEXT PRIMARY KEY,
    owning_agent   TEXT NOT NULL DEFAULT 'warg',
    session_ref    TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    exercise_ref   TEXT NOT NULL REFERENCES ax_exercise(ulid),
    step_type      TEXT NOT NULL,
    group_id       TEXT,
    display_source TEXT NOT NULL,
    display_key    TEXT,
    equipment_need TEXT NOT NULL,
    measure        TEXT NOT NULL,
    target         TEXT,
    rest_sec       INTEGER,
    suppress_alert INTEGER NOT NULL DEFAULT 0,
    next_hint      TEXT,
    created_at     TEXT NOT NULL,
    CHECK (owning_agent IN ('ag','warg','user')),
    CHECK (step_type      IN ('prep','warmup','ramp','working','hold')),
    CHECK (display_source IN ('matrix_ref','exercise_derived')),
    CHECK (equipment_need IN ('bodyweight','equipment')),
    CHECK (measure        IN ('reps','hold_sec','tempo')),
    CHECK (suppress_alert IN (0,1)),
    CHECK (display_source != 'matrix_ref' OR display_key IS NOT NULL),
    CHECK (step_type != 'prep' OR suppress_alert = 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_us_step_seq
    ON us_step(session_ref, seq);
CREATE INDEX IF NOT EXISTS idx_us_step_exercise
    ON us_step(exercise_ref);
CREATE INDEX IF NOT EXISTS idx_us_step_group
    ON us_step(group_id) WHERE group_id IS NOT NULL;

-- ============================================================================
-- Seed: the 9 AX projection rule-content rows (7 base + 2 Track B cable/freeweight).
-- Deterministic ULID / created_at so fresh(schema.sql) == migrated at the
-- data level too. The evaluation engine is the framework build (DGN-782).
-- ============================================================================
INSERT OR IGNORE INTO ax_projection_rule
  (ulid, owning_agent, code, priority, match_movement, match_load_type,
   match_loading, match_cable_lines, match_measure, match_has_injury,
   action_kind, action_param, status, created_at)
VALUES
  ('01KZAYAH000000000000000001','warg','F6_empty_bar_ramp',10,'compound','freeweight',NULL,NULL,NULL,NULL,
     'empty_bar_ramp','{"warmup":"empty_bar","then":"ramp"}','active','2026-08-09T00:00:00Z'),
  ('01KZAYAH000000000000000002','warg','F10_injury_drill',20,NULL,NULL,NULL,NULL,NULL,1,
     'injury_drill_include','{"source":"ax_tag:injury_profile"}','active','2026-08-09T00:00:00Z'),
  ('01KZAYAH000000000000000003','warg','F15_grip_order_defer',30,NULL,NULL,NULL,NULL,NULL,NULL,
     'order_defer','{"when_tag_group":"muscle","tag_in":["forearm","grip"]}','active','2026-08-09T00:00:00Z'),
  ('01KZAYAH000000000000000004','warg','F16_isolation_tut',40,'isolation',NULL,NULL,NULL,NULL,NULL,
     'tut_progression','{"prefer_measure":["tempo","hold_sec"]}','active','2026-08-09T00:00:00Z'),
  ('01KZAYAH000000000000000005','warg','CABLE_dual_specify',50,NULL,'cable',NULL,'dual',NULL,NULL,
     'cable_line_specify','{"require":"which_line"}','active','2026-08-09T00:00:00Z'),
  ('01KZAYAH000000000000000006','warg','PLATE_projection',60,NULL,NULL,'plate_loaded',NULL,NULL,NULL,
     'plate_projection','{"source":"ax_machine.available_plates"}','active','2026-08-09T00:00:00Z'),
  ('01KZAYAH000000000000000007','warg','F17_lattice_round',70,NULL,'machine',NULL,NULL,NULL,NULL,
     'lattice_round','{"source":"ax_machine.lattice_base,lattice_step"}','active','2026-08-09T00:00:00Z');

-- ----------------------------------------------------------------------------
-- Track B (2026-08-10, kit_life): cable + freeweight weight-snap rules.
-- F17_lattice_round matches load=machine only, so cable pin stacks and free-
-- weight barbells/dumbbells were never snapped -> the projection diverged from
-- the rx-calc snap (e.g. cable 25.0 vs 26.25 pin, freeweight deadlift 132.3 vs
-- 130.0 grid). These two rows close that gap without an engine enum change:
--   CABLE_lattice_round      -- cable load_type reuses lattice_round; the engine
--                               reads the MEASURED cable machine's lattice_base/
--                               step (cable_lateral 1.25/2.5, face_pull 2.5/3.75).
--                               provisional cables (cable_fly) still no-op / gate.
--   FREEWEIGHT_lattice_round -- freeweight (no machine) snaps via an owner-supplied
--                               per-exercise grid in action_param.freeweight_grid;
--                               coarse_step = public-plate grid (per-side 2.5 =
--                               5.0 total, warmup/ramp), working_step = personal
--                               light-plate micro grid (per-side 1.25 = 2.5 total)
--                               allowed ONLY on the main lift's WORKING sets (engine
--                               gates on us_step.step_type='working'). Grids are the
--                               exact per-exercise lattices already in live program.
--                               yaml (no invention). Same deterministic ULID/ts so
--                               fresh(schema.sql) == migrated.
INSERT OR IGNORE INTO ax_projection_rule
  (ulid, owning_agent, code, priority, match_movement, match_load_type,
   match_loading, match_cable_lines, match_measure, match_has_injury,
   action_kind, action_param, status, created_at)
VALUES
  ('01KZRUL00000000000CABLE01','warg','CABLE_lattice_round',68,NULL,'cable',NULL,NULL,NULL,NULL,
     'lattice_round','{"source":"ax_machine.lattice_base,lattice_step"}','active','2026-08-10T00:00:00Z'),
  ('01KZRUL0000000000FREEWT01','warg','FREEWEIGHT_lattice_round',72,NULL,'freeweight',NULL,NULL,NULL,NULL,
     'lattice_round','{"source":"owner_freeweight_grid_2026-08-10","freeweight_grid":{"deadlift":{"type":"standard","base":0.0,"coarse_step":5.0},"back_squat":{"type":"standard","base":0.0,"coarse_step":5.0},"rdl":{"type":"standard","base":0.0,"coarse_step":5.0},"barbell_row":{"type":"standard","base":0.0,"coarse_step":5.0,"working_step":2.5},"overhead_press":{"type":"standard","base":0.0,"coarse_step":5.0,"working_step":2.5},"incline_barbell_press":{"type":"standard","base":0.0,"coarse_step":5.0,"working_step":2.5},"pendlay_row":{"type":"standard","base":0.0,"coarse_step":5.0,"working_step":2.5},"incline_dumbbell_press":{"type":"even","base":0.0,"coarse_step":2.0},"incline_dumbbell_curl":{"type":"even","base":0.0,"coarse_step":2.0},"bulgarian_split_squat":{"type":"even","base":0.0,"coarse_step":2.0}}}','active','2026-08-10T00:00:00Z');

PRAGMA user_version = 20;
