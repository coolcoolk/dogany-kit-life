-- 018_warg_ledger_exercise.sql  (target user_version 18)
-- reversible: yes
-- Source: Warg 017_warg_ledger_exercise (folds W01-W03 + extra exercise tables;
-- renumbered, file-number == target uv). v17 -> v18. All pure-additive.
-- Also folds live un-versioned health/workout columns present in the live DB
-- but absent from the frozen canonical v11 schema (guarded/idempotent).
-- Reference tables get their spec-pinned seed; instance-data tables
-- (workout_sets, ledger_*) stay EMPTY on fresh mint.

-- un-versioned live column folds (health/workout domain).
ALTER TABLE meals ADD COLUMN aa_protein REAL NOT NULL DEFAULT 0;
ALTER TABLE meals ADD COLUMN sodium_mg REAL NOT NULL DEFAULT 0;
ALTER TABLE meals ADD COLUMN caffeine_mg REAL NOT NULL DEFAULT 0;
ALTER TABLE workouts ADD COLUMN session_type_id INTEGER REFERENCES session_type(id);

CREATE TABLE IF NOT EXISTS ledger_goal (
    id           INTEGER PRIMARY KEY,
    ulid         TEXT NOT NULL UNIQUE,
    layer        TEXT NOT NULL,
    parent_id    INTEGER REFERENCES ledger_goal(id),
    title        TEXT NOT NULL,
    detail       TEXT,
    status       TEXT NOT NULL DEFAULT 'proposed',
    starts_on    TEXT,
    ends_on      TEXT,
    resume_to_id INTEGER REFERENCES ledger_goal(id),
    source       TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 0,
    CHECK (layer IN ('long','mid','short','event_overlay')),
    CHECK (layer != 'event_overlay' OR ends_on IS NOT NULL),
    CHECK (layer != 'event_overlay' OR resume_to_id IS NOT NULL),
    CHECK (status IN ('proposed','active','suspended','completed',
                      'failed','superseded','archived'))
);

CREATE TABLE IF NOT EXISTS ledger_preference (
    id          INTEGER PRIMARY KEY,
    ulid        TEXT NOT NULL UNIQUE,
    item        TEXT NOT NULL,
    polarity    TEXT NOT NULL,
    note        TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    source      TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK (polarity IN ('inviolable','negotiable'))
);

CREATE TABLE IF NOT EXISTS ledger_resource (
    id          INTEGER PRIMARY KEY,
    ulid        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    detail      TEXT,
    volatile    INTEGER NOT NULL DEFAULT 0,
    as_of       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    source      TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_constraint (
    id          INTEGER PRIMARY KEY,
    ulid        TEXT NOT NULL UNIQUE,
    class       TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    goal_id     INTEGER REFERENCES ledger_goal(id),
    inviolable  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'active',
    source      TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK (class IN ('nutrition','safety','time')),
    CHECK (inviolable IN (0,1))
);

CREATE TABLE IF NOT EXISTS ledger_audit (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    goal_ulid TEXT,
    detail    TEXT
);

CREATE TABLE IF NOT EXISTS workout_sets (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    exercise_name TEXT NOT NULL,
    slot_num      INTEGER NOT NULL,
    set_seq       INTEGER NOT NULL,
    weight_kg     REAL,
    reps          INTEGER NOT NULL,
    rir           INTEGER,
    raw_input     TEXT,
    recorded_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(session_id, exercise_name, set_seq)
);

CREATE TABLE IF NOT EXISTS exercise_muscle (
    exercise_name TEXT NOT NULL,
    muscle        TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('primary','secondary')),
    PRIMARY KEY (exercise_name, muscle)
);

CREATE TABLE IF NOT EXISTS exercise_subregion (
    exercise_name TEXT NOT NULL,
    subregion     TEXT NOT NULL,
    weight        REAL NOT NULL,
    grade         TEXT NOT NULL,
    PRIMARY KEY (exercise_name, subregion)
);

CREATE TABLE IF NOT EXISTS exercise_movement_grip (
    exercise_name TEXT PRIMARY KEY,
    movement      TEXT NOT NULL,
    equipment     TEXT,
    grip          TEXT
);

CREATE TABLE IF NOT EXISTS exercise_fatigue_cost (
    exercise_name TEXT PRIMARY KEY,
    cost          TEXT NOT NULL,
    source        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exercise_pattern (
    exercise_name TEXT PRIMARY KEY,
    pattern       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movement_muscle_base (
    movement TEXT NOT NULL,
    muscle   TEXT NOT NULL,
    role     TEXT NOT NULL CHECK(role IN ('primary','secondary')),
    PRIMARY KEY (movement, muscle)
);

CREATE TABLE IF NOT EXISTS grip_muscle_delta (
    grip     TEXT NOT NULL,
    muscle   TEXT NOT NULL,
    role     TEXT NOT NULL CHECK(role IN ('primary','secondary')),
    evidence TEXT,
    PRIMARY KEY (grip, muscle)
);

CREATE TABLE IF NOT EXISTS grip_vocab (
    grip TEXT PRIMARY KEY,
    note TEXT NOT NULL
);

-- indexes (Warg-exclusive)
CREATE UNIQUE INDEX IF NOT EXISTS idx_goal_active_layer
  ON ledger_goal(layer)
  WHERE status = 'active' AND layer IN ('long','mid','short');
CREATE UNIQUE INDEX IF NOT EXISTS idx_overlay_single_active
  ON ledger_goal(layer)
  WHERE status = 'active' AND layer = 'event_overlay';
CREATE INDEX IF NOT EXISTS idx_workout_sets_session ON workout_sets(session_id);
CREATE INDEX IF NOT EXISTS idx_workout_sets_exercise ON workout_sets(exercise_name, recorded_at);

-- reference-table seeds (spec-pinned muscle/movement/grip vocab).
-- INSERT OR IGNORE (idempotent). workout_sets / ledger_* are NOT seeded.

-- seed: exercise_muscle
INSERT OR IGNORE INTO exercise_muscle VALUES('back_squat','glutes','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('back_squat','quads','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('barbell_row','back','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('barbell_row','biceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('bicep_curl','biceps','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('bulgarian_split_squat','glutes','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('bulgarian_split_squat','quads','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('cable_crunch','abs','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('cable_fly','chest','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('cable_lateral_raise','shoulders','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('chest_press_machine','chest','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('chest_press_machine','triceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('chest_supported_t_bar_row','back','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('chest_supported_t_bar_row','biceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('chinup','back','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('chinup','biceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('deadlift','back','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('deadlift','glutes','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('deadlift','hamstrings','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('face_pull','shoulders','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('hammer_curl','biceps','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('hammer_curl','forearms','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('incline_barbell_press','chest','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('incline_barbell_press','shoulders','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('incline_barbell_press','triceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('incline_dumbbell_curl','biceps','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('incline_dumbbell_press','chest','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('incline_dumbbell_press','shoulders','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('incline_dumbbell_press','triceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('lat_pulldown','back','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('lat_pulldown','biceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('leg_curl','hamstrings','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('leg_raise','abs','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('overhead_press','shoulders','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('overhead_press','triceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('overhead_press_smith_seated','shoulders','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('overhead_press_smith_seated','triceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('pendlay_row','back','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('pendlay_row','biceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('preacher_curl','biceps','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('pullup','back','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('pullup','biceps','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('rdl','back','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('rdl','glutes','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('rdl','hamstrings','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('reverse_pecdec','shoulders','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('seated_calf_raise','calves','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('single_leg_extension','quads','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('standing_calf_raise','calves','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('tricep_overhead_extension','triceps','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('tricep_pushdown_rope','triceps','primary');
INSERT OR IGNORE INTO exercise_muscle VALUES('v_squat','glutes','secondary');
INSERT OR IGNORE INTO exercise_muscle VALUES('v_squat','quads','primary');

-- seed: exercise_subregion
INSERT OR IGNORE INTO exercise_subregion VALUES('back_squat','adductors',0.5,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('back_squat','glute_max',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('back_squat','glute_med',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('back_squat','quads',1.0,'group');
INSERT OR IGNORE INTO exercise_subregion VALUES('barbell_row','back_erectors',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('barbell_row','back_lats',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('barbell_row','back_upper',0.3400000000000000244,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('barbell_row','biceps_distal',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('barbell_row','biceps_prox',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('bicep_curl','biceps_distal',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('bicep_curl','biceps_prox',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('bulgarian_split_squat','adductors',0.5,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('bulgarian_split_squat','glute_max',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('bulgarian_split_squat','glute_med',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('bulgarian_split_squat','quads',1.0,'group');
INSERT OR IGNORE INTO exercise_subregion VALUES('cable_crunch','abs',1.0,'group');
INSERT OR IGNORE INTO exercise_subregion VALUES('cable_fly','chest_lower',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('cable_fly','chest_mid',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('cable_fly','chest_upper',0.3400000000000000244,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('cable_lateral_raise','side_delt',1.0,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('chest_press_machine','chest_lower',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chest_press_machine','chest_mid',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chest_press_machine','chest_upper',0.3400000000000000244,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chest_press_machine','triceps_lat_med',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chest_press_machine','triceps_long',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chest_supported_t_bar_row','back_erectors',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chest_supported_t_bar_row','back_lats',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chest_supported_t_bar_row','back_upper',0.3400000000000000244,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chest_supported_t_bar_row','biceps_distal',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chest_supported_t_bar_row','biceps_prox',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chinup','back_erectors',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chinup','back_lats',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chinup','back_upper',0.3400000000000000244,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chinup','biceps_distal',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('chinup','biceps_prox',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('deadlift','adductors',0.5,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('deadlift','back_erectors',0.1600000000000000033,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('deadlift','back_lats',0.1700000000000000122,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('deadlift','back_upper',0.1700000000000000122,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('deadlift','glute_max',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('deadlift','glute_med',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('deadlift','hamstrings',1.0,'group');
INSERT OR IGNORE INTO exercise_subregion VALUES('face_pull','rear_delt',1.0,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('hammer_curl','biceps_distal',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('hammer_curl','biceps_prox',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('hammer_curl','forearms',0.5,'group');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_barbell_press','chest_lower',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_barbell_press','chest_mid',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_barbell_press','chest_upper',0.3400000000000000244,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_barbell_press','front_delt',0.5,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_barbell_press','triceps_lat_med',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_barbell_press','triceps_long',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_dumbbell_curl','biceps_distal',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_dumbbell_curl','biceps_prox',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_dumbbell_press','chest_lower',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_dumbbell_press','chest_mid',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_dumbbell_press','chest_upper',0.3400000000000000244,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_dumbbell_press','front_delt',0.5,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_dumbbell_press','triceps_lat_med',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('incline_dumbbell_press','triceps_long',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('leg_curl','hamstrings',1.0,'group');
INSERT OR IGNORE INTO exercise_subregion VALUES('overhead_press','front_delt',1.0,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('overhead_press','triceps_lat_med',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('overhead_press','triceps_long',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('overhead_press_smith_seated','front_delt',1.0,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('overhead_press_smith_seated','triceps_lat_med',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('overhead_press_smith_seated','triceps_long',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('pendlay_row','back_erectors',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('pendlay_row','back_lats',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('pendlay_row','back_upper',0.3400000000000000244,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('pendlay_row','biceps_distal',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('pendlay_row','biceps_prox',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('preacher_curl','biceps_distal',0.5999999999999999778,'C');
INSERT OR IGNORE INTO exercise_subregion VALUES('preacher_curl','biceps_prox',0.4000000000000000222,'C');
INSERT OR IGNORE INTO exercise_subregion VALUES('pullup','back_erectors',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('pullup','back_lats',0.3300000000000000155,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('pullup','back_upper',0.3400000000000000244,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('pullup','biceps_distal',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('pullup','biceps_prox',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('rdl','adductors',0.5,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('rdl','back_erectors',0.1600000000000000033,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('rdl','back_lats',0.1700000000000000122,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('rdl','back_upper',0.1700000000000000122,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('rdl','glute_max',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('rdl','glute_med',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('rdl','hamstrings',1.0,'group');
INSERT OR IGNORE INTO exercise_subregion VALUES('reverse_pecdec','rear_delt',1.0,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('seated_calf_raise','calf_soleus',1.0,'group');
INSERT OR IGNORE INTO exercise_subregion VALUES('single_leg_extension','quads',1.0,'group');
INSERT OR IGNORE INTO exercise_subregion VALUES('standing_calf_raise','calf_gastroc',1.0,'group');
INSERT OR IGNORE INTO exercise_subregion VALUES('tricep_overhead_extension','triceps_lat_med',0.4000000000000000222,'B');
INSERT OR IGNORE INTO exercise_subregion VALUES('tricep_overhead_extension','triceps_long',0.5999999999999999778,'B');
INSERT OR IGNORE INTO exercise_subregion VALUES('tricep_pushdown_rope','triceps_lat_med',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('tricep_pushdown_rope','triceps_long',0.5,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('v_squat','adductors',0.5,'functional');
INSERT OR IGNORE INTO exercise_subregion VALUES('v_squat','glute_max',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('v_squat','glute_med',0.25,'even');
INSERT OR IGNORE INTO exercise_subregion VALUES('v_squat','quads',1.0,'group');

-- seed: exercise_movement_grip
INSERT OR IGNORE INTO exercise_movement_grip VALUES('back_squat','squat','barbell',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('barbell_row','pull_compound','barbell','pronated');
INSERT OR IGNORE INTO exercise_movement_grip VALUES('bicep_curl','curl','barbell','supinated');
INSERT OR IGNORE INTO exercise_movement_grip VALUES('bulgarian_split_squat','split_squat','dumbbell',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('cable_crunch','crunch','cable',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('cable_fly','fly_isolation','cable',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('cable_lateral_raise','lateral_raise','cable',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('chest_press_machine','flat_press_machine','machine',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('chest_supported_t_bar_row','pull_compound','t_bar',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('chinup','pull_compound','bodyweight_assist','supinated');
INSERT OR IGNORE INTO exercise_movement_grip VALUES('deadlift','hip_hinge_deadlift','barbell',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('face_pull','rear_delt_isolation','cable',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('hammer_curl','curl','dumbbell','neutral');
INSERT OR IGNORE INTO exercise_movement_grip VALUES('incline_barbell_press','incline_press','barbell',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('incline_dumbbell_curl','curl','dumbbell','supinated');
INSERT OR IGNORE INTO exercise_movement_grip VALUES('incline_dumbbell_press','incline_press','dumbbell',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('lat_pulldown','pull_compound','cable',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('leg_curl','leg_curl','machine',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('overhead_press','overhead_press','barbell',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('overhead_press_smith_seated','overhead_press','smith_machine',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('pendlay_row','pull_compound','barbell','pronated');
INSERT OR IGNORE INTO exercise_movement_grip VALUES('preacher_curl','curl','ez_bar','supinated');
INSERT OR IGNORE INTO exercise_movement_grip VALUES('pullup','pull_compound','bodyweight_assist','pronated');
INSERT OR IGNORE INTO exercise_movement_grip VALUES('rdl','hip_hinge_deadlift','barbell',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('reverse_pecdec','rear_delt_isolation','machine',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('seated_calf_raise','calf_raise','machine',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('single_leg_extension','leg_extension','machine',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('standing_calf_raise','calf_raise','machine',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('tricep_overhead_extension','tricep_isolation','cable',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('tricep_pushdown_rope','tricep_isolation','cable',NULL);
INSERT OR IGNORE INTO exercise_movement_grip VALUES('v_squat','squat','v_squat_machine',NULL);

-- seed: exercise_fatigue_cost
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('back_squat','high','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('barbell_row','high','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('bicep_curl','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('bulgarian_split_squat','med','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('cable_crunch','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('cable_fly','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('cable_lateral_raise','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('chest_press_machine','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('chest_supported_t_bar_row','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('chinup','med','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('deadlift','high','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('face_pull','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('hammer_curl','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('incline_barbell_press','med','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('incline_dumbbell_curl','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('incline_dumbbell_press','med','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('lat_pulldown','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('leg_curl','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('leg_raise','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('overhead_press','high','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('overhead_press_smith_seated','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('pendlay_row','high','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('preacher_curl','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('pullup','med','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('rdl','high','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('reverse_pecdec','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('seated_calf_raise','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('single_leg_extension','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('smith_seated_calf_raise','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('standing_calf_raise','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('tricep_overhead_extension','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('tricep_pushdown_rope','low','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('v_squat','med','explicit');
INSERT OR IGNORE INTO exercise_fatigue_cost VALUES('wall_sit','low','explicit');

-- seed: exercise_pattern
INSERT OR IGNORE INTO exercise_pattern VALUES('back_squat','squat');
INSERT OR IGNORE INTO exercise_pattern VALUES('barbell_row','horizontal_pull');
INSERT OR IGNORE INTO exercise_pattern VALUES('bicep_curl','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('bulgarian_split_squat','squat');
INSERT OR IGNORE INTO exercise_pattern VALUES('cable_crunch','core');
INSERT OR IGNORE INTO exercise_pattern VALUES('cable_fly','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('cable_lateral_raise','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('chest_press_machine','horizontal_push');
INSERT OR IGNORE INTO exercise_pattern VALUES('chest_supported_t_bar_row','horizontal_pull');
INSERT OR IGNORE INTO exercise_pattern VALUES('chinup','vertical_pull');
INSERT OR IGNORE INTO exercise_pattern VALUES('deadlift','hinge');
INSERT OR IGNORE INTO exercise_pattern VALUES('face_pull','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('hammer_curl','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('incline_barbell_press','horizontal_push');
INSERT OR IGNORE INTO exercise_pattern VALUES('incline_dumbbell_curl','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('incline_dumbbell_press','horizontal_push');
INSERT OR IGNORE INTO exercise_pattern VALUES('lat_pulldown','vertical_pull');
INSERT OR IGNORE INTO exercise_pattern VALUES('leg_curl','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('leg_raise','core');
INSERT OR IGNORE INTO exercise_pattern VALUES('overhead_press','vertical_push');
INSERT OR IGNORE INTO exercise_pattern VALUES('overhead_press_smith_seated','vertical_push');
INSERT OR IGNORE INTO exercise_pattern VALUES('pendlay_row','horizontal_pull');
INSERT OR IGNORE INTO exercise_pattern VALUES('preacher_curl','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('pullup','vertical_pull');
INSERT OR IGNORE INTO exercise_pattern VALUES('rdl','hinge');
INSERT OR IGNORE INTO exercise_pattern VALUES('reverse_pecdec','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('seated_calf_raise','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('single_leg_extension','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('smith_seated_calf_raise','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('standing_calf_raise','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('tricep_overhead_extension','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('tricep_pushdown_rope','isolation');
INSERT OR IGNORE INTO exercise_pattern VALUES('v_squat','squat');
INSERT OR IGNORE INTO exercise_pattern VALUES('wall_sit','squat');

-- seed: movement_muscle_base
INSERT OR IGNORE INTO movement_muscle_base VALUES('calf_raise','calves','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('crunch','abs','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('curl','biceps','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('flat_press_machine','chest','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('flat_press_machine','triceps','secondary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('fly_isolation','chest','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('hip_hinge_deadlift','back','secondary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('hip_hinge_deadlift','glutes','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('hip_hinge_deadlift','hamstrings','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('incline_press','chest','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('incline_press','shoulders','secondary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('incline_press','triceps','secondary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('lateral_raise','shoulders','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('leg_curl','hamstrings','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('leg_extension','quads','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('overhead_press','shoulders','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('overhead_press','triceps','secondary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('pull_compound','back','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('pull_compound','biceps','secondary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('rear_delt_isolation','shoulders','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('split_squat','glutes','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('split_squat','quads','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('squat','glutes','secondary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('squat','quads','primary');
INSERT OR IGNORE INTO movement_muscle_base VALUES('tricep_isolation','triceps','primary');

-- seed: grip_muscle_delta
INSERT OR IGNORE INTO grip_muscle_delta VALUES('neutral','forearms','secondary','hammer_curl (confirmed exercise_muscle row, 2026-07-24 mapping)');

-- seed: grip_vocab
INSERT OR IGNORE INTO grip_vocab VALUES('close','inside-shoulder; lower-lat/mid-back + more biceps on pull; differentiates at subregion, not coarse muscle');
INSERT OR IGNORE INTO grip_vocab VALUES('neutral','palms-facing (hammer); brachioradialis + forearms co-emphasis');
INSERT OR IGNORE INTO grip_vocab VALUES('pronated','overhand (palms away/down); brachioradialis + wrist-extensor emphasis on pulls');
INSERT OR IGNORE INTO grip_vocab VALUES('supinated','underhand (palms toward you); peak biceps; default curl/chin grip');
INSERT OR IGNORE INTO grip_vocab VALUES('wide','wider-than-shoulder; upper/outer-lat emphasis on pulldown; differentiates at subregion, not coarse muscle');

PRAGMA user_version = 18;
