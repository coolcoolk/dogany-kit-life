-- 056_muscle_fatigue.sql  (target user_version 56)
--
-- RENUMBER RISK -- READ FIRST.  055 is taken by a parallel branch
-- (dsp/goal-interview-redesign, 055_user_axis.sql).  This file is written
-- against the lineage AFTER 055: its guard requires user_version EXACTLY 55.
-- Until 055 lands on main, 056 cannot apply to a real database (the guard
-- refuses a 54 database -- fail-closed, nothing changes).  Whoever merges the
-- two branches resolves the two lockstep pins (schema.sql PRAGMA user_version
-- and kit_life.EXPECTED_USER_VERSION) to 56, and folds 055's schema.sql hunks
-- in beside this file's.  If 055 is dropped or renumbered instead, this file
-- is renumbered to 055 and its guard to 54 -- nothing in it depends on 055's
-- content.
--
-- reversible: YES for structure, NO for the map re-weighting.
--   Structure rollback (SQLite >= 3.35): DROP TRIGGER trg_ax_exercise_requires_map,
--   trg_ax_exercise_requires_map_rename; ALTER TABLE ... DROP COLUMN for
--   workout_types.muscle_node and pain_event.sensation
--   (first DROP + re-CREATE the two pain views from 047); PRAGMA user_version=55.
--   The data section re-weights exercise_subregion_map rows in place; the old
--   weights are the ones in pack_health's health_taxonomy_seed.sql (every row
--   touched here is listed below with its old value), so a rollback is
--   re-applying those literals.  New taxonomy nodes/labels/tags are plain
--   inserts and can be deleted.
--
-- ==========================================================================
-- WHY -- per-muscle fatigue is COMPUTED, and this file adds only what cannot
-- be computed (spec: muscle-fatigue-spec.md A2.1, owner rulings 2026-09-26
-- 11:05-11:37, muscle-fatigue function-tracking notes).
--
-- The read model (database/muscle_fatigue.py, verbs muscle-state /
-- muscle-week) derives fatigue at read time from workout_sets x the muscle
-- map x elapsed-time decay.  Nothing here stores a fatigue number: a stored
-- value would go stale on every map fix, every late set edit and every
-- deleted row, and each of those would need a recompute hook.
--
-- What the read model needs and could not get before:
--
--   M1  workout_types.muscle_node  -- "which muscle node does this region type
--       mean?"  A session with no sets (a watch summary) is still classified by
--       region (workout_classifications); without this column the declared lane
--       cannot be read at all.  Backfilled for the single-region strength
--       subtypes only; composite subtypes ("어깨, 하체, 가슴, 삼두") and routine
--       names (앞A) stay NULL and are reported as unmapped, never guessed.
--
--   M2  NO COLUMN.  "Did a person confirm this map?" is answered by an
--       exercise tag (ax_tag tag_group='map' value='draft'), written by
--       exercise-add when it drafts a map mid-session (0 questions) and removed
--       by exercise-confirm at session end.  The spec proposed a `source`
--       column on exercise_muscle_map; it was rejected at build time because
--       pack_health's taxonomy seed inserts that table POSITIONALLY
--       (`INSERT OR IGNORE INTO exercise_muscle_map VALUES(a,b,c)`, 53 rows) --
--       a fourth column would make every install's seed fail.  No tag = a
--       seeded or hand-entered map = explicit.
--
--   M3  pain_event.sensation 'pain'|'stiff'|'tight'|'sore'  -- "what kind of
--       body signal is this?"  "햄스 뻣뻣" said outside a workout is a body
--       signal, not an injury event.  Without the column a stiffness entry
--       would count toward injury promotion, so v_pain_promotion_candidate is
--       rebuilt with sensation = 'pain'.  DEFAULT 'pain' keeps every existing
--       row and every pain-add call exactly what it was.
--
--   M4  trg_ax_exercise_requires_map  -- the exercise-creation gate as a
--       MACHINE: an ax_exercise row is refused unless the exercise already has
--       a primary muscle-map row and a fatigue-cost row.  The verb
--       (exercise-add) writes those first in the same transaction; raw SQL
--       cannot get around it.  Existing rows are not re-checked (live: 0
--       active exercises without map or cost at 2026-09-26).
--
-- DATA -- the map errors the spec simulation found (section S4), and the
-- small muscles the owner named (08-14).  Every statement is CONDITIONAL on
-- the rows it corrects being present: a fresh mint carries no taxonomy rows
-- (they ship in pack_health's seed), so on a fresh mint this section is a
-- no-op and the pack seed must carry the same corrections (pack-lane
-- follow-up, named in the branch report).
--
--   D1  new nodes, all kind='subregion' so they stay OUT of rx-calc's muscle
--       vocabulary (a new kind='muscle' row would enter MUSCLE_GROUP and the
--       volume audit):
--         rotator_cuff    under shoulders  band rotator_cuff (own; unbanded)
--         obliques        under abs        band obliques
--         brachialis      under biceps     band biceps   (elbow-flexor band:
--                         moving pronated-pull credit onto it must not move the
--                         biceps prescription total)
--         brachioradialis under forearms   band forearms
--         hip_flexors     under legs       band hip_flexors (iliopsoas)
--         tibialis_anterior under legs     band tibialis_anterior
--       heatmap sub-nodes (the pack renderer's node keys), each banded to
--       its parent so a band total never moves when D5 splits a share:
--         serratus                              under chest      (small)
--         forearm_flexors, forearm_extensors    under forearms
--         rect_fem, vast_lat, vast_med          under quads
--         hams_lateral, hams_medial             under hamstrings
--       + Korean labels, + long labels for the three back parts
--       (승모근 / 광배근 / 척추기립근: the back is displayed split).
--
--   D2  erector spinae credited where it barely works.  Seed split every back
--       exercise evenly (grade 'even' = no evidence) into erectors/lats/upper.
--       old -> new (weights sum unchanged, so the rx-calc `back` band total
--       does not move -- every back subregion bands to `back`):
--         lat_pulldown, chinup, pullup     .33/.33/.34 -> erectors 0 (row
--                                          deleted) / lats .70 / upper .30
--         chest_supported_t_bar_row        .33/.33/.34 -> 0 / .50 / .50
--                                          (the chest pad removes the spinal
--                                          hold)
--         barbell_row, pendlay_row         .33/.33/.34 -> .15 / .45 / .40
--                                          (bent-over isometric hold is real
--                                          but secondary)
--         deadlift, rdl, single_leg_deadlift .16/.17/.17 -> .30 / .10 / .10
--                                          (the hinge is where the erectors
--                                          work; back stays secondary = .50)
--
--   D3  biceps over-credit on overhand grips.  Every pronated pull
--       (exercise_movement_grip.grip = 'pronated') plus lat_pulldown (grip not
--       recorded; the standard pulldown is overhand -- a stated assumption)
--       credited biceps a full secondary .25/.25.  An overhand pull shifts
--       elbow flexion to brachialis/brachioradialis.  New: biceps_distal .125
--       / biceps_prox .125 / brachialis .25.  The elbow-flexor total stays .50
--       and brachialis bands to `biceps`, so the rx-calc biceps band total
--       does not move; only the fatigue read model (and the per-subregion
--       display) sees the shift.  exercise_muscle_map is NOT touched:
--       muscle-preset.py --validate reconstructs it from movement+grip and a
--       role change there would break that parity.
--
--   D4  stabilizer and laterality tags (ax_tag + ax_exercise_tag) -- the
--       spec's draft list (A2.4), attached only to exercises registered in
--       ax_exercise.  The read model credits a `stabilizes` node with one
--       fixed tier (0.3), never more than what the map already gives.
--       The grip-hold stabilizer is tagged on forearm_flexors (the finger
--       flexors hold the bar), not on the forearms parent.
--
--   D5  sub-node shares for every heatmap node (pack renderer contract,
--       muscle_fatigue.HEATMAP_NODES).  Literature-based ESTIMATES (EMG /
--       MRI-activation reviews, practitioner consensus; grade 'C'): nothing
--       here is measured on the owner.  Provenance is read, not stored: a row
--       is 'measured' only when its grade is 'measured' (a later writer); every
--       row written here reads 'estimate' (muscle-state --json node_values).
--       No provenance column: the pack seed inserts this table positionally
--       (same reason as M2).
--       Conservation rule: an (exercise, parent) group is rewritten ONLY when
--       the exercise's current share under that parent (rows on the parent or
--       its children; with no rows, the exercise_muscle_map role weight) equals
--       the new group's sum -- the parent's credit never moves, only its
--       split.  Anything a person re-weighted to another total is left alone.
--         hinge (deadlift/rdl/single_leg_deadlift): hams_lateral .55 /
--           hams_medial .45 (biceps femoris long head leads hip extension);
--           glute_max .80 / glute_med .20 (.70/.30 single-leg)
--         leg_curl: hams_lateral .35 / hams_medial .65 (knee flexion leads
--           with semitendinosus)
--         squat / v_squat / split squat / wall sit: rect_fem .15 / vast_lat
--           .45 / vast_med .40 (biarticular RF is shortened at the hip);
--           squat glutes max .40 / med .10 of the .50 secondary share
--         leg extension: rect_fem .40 / vast_lat .30 / vast_med .30
--         incline press: chest_upper .50 / mid .30 / lower .10 / serratus .10;
--           flat machine press .25 / .50 / .15 / .10; cable fly .30/.45/.25
--         overhead press: front_delt .65 / side_delt .35; lateral raise:
--           side .80 / front .10 / rotator_cuff .10 (supraspinatus);
--           face pull: rear .60 / rotator_cuff .40 (external rotation);
--           rear fly: rear .80 / rotator_cuff .20
--         curls: supinated = biceps-led, brachialis .15-.20; hammer curl
--           brachialis .40, forearms brachioradialis .35 / flexors .10 /
--           extensors .05
--         pushdown: triceps_lat_med .65 / long .35 (arm at the side); presses
--           .30 / .20
--         cable crunch: abs .60 / abs_lower .25 / obliques .15; leg raise
--           abs .25 / abs_lower .60 / obliques .15
--         standing calf raise: gastroc .70 / soleus .30; seated (knee bent):
--           soleus .85 / gastroc .15
--         tibialis_raise: one catalog row (tibialis_anterior 1.0), the one
--           movement that loads the dorsiflexors; subregion row only, so the
--           rx-calc muscle vocabulary is untouched.
--       Overhand pulls keep D3's brachialis; chinup / t-bar row are unchanged.
--
-- ==========================================================================
-- GUARD.  1. user_version EXACTLY 55, before any transaction.  2. shape: none
-- of the two columns and neither trigger exists yet, and the two pain views
-- exist.  3. after: the two columns and both triggers exist.  Any failure
-- aborts under .bail, the transaction rolls back, user_version stays 55.
--
-- APPLY NOTE.  NOTHING IN THIS COMMIT HAS BEEN APPLIED TO ANY DATABASE.  The
-- live DB and the 09-11 backup were opened read-only (mode=ro&immutable=1) to
-- read the current map rows and to derive the fatigue grade thresholds
-- (aggregates only, recorded in muscle_fatigue.py).  Applying is the kit
-- owner's act, after 055, with the usual backup first:
--   cp ~/.dogany/crews/kit_life/kit_life.db ~/.dogany/crews/kit_life/kit_life.db.pre056
.bail on
.timeout 5000

-- guard 1: version
CREATE TEMP TABLE _guard_056(seen_user_version);
CREATE TEMP TRIGGER _guard_056_refuse BEFORE INSERT ON _guard_056 BEGIN
  SELECT RAISE(FAIL, 'migration 056 refused: this database is not at user_version 55 -- either 056 was already applied (>= 56) or the chain up to 055 has not run (< 55; 055 lives on a parallel branch until merged). Nothing changed');
END;
INSERT INTO _guard_056
  SELECT user_version FROM pragma_user_version WHERE user_version <> 55;
DROP TABLE temp._guard_056;

BEGIN IMMEDIATE;

-- guard 2: shape
CREATE TEMP TABLE _guard_056_pre(seen_count);
CREATE TEMP TRIGGER _guard_056_pre_refuse BEFORE INSERT ON _guard_056_pre BEGIN
  SELECT RAISE(FAIL, 'migration 056 refused: expected workout_types / pain_event WITHOUT muscle_node / sensation, no ax_exercise gate trigger, and both pain views present -- this is not the shape this file was written against. Nothing changed');
END;
INSERT INTO _guard_056_pre
  SELECT 1 WHERE
       EXISTS (SELECT 1 FROM pragma_table_info('workout_types') WHERE name='muscle_node')
    OR EXISTS (SELECT 1 FROM pragma_table_info('pain_event') WHERE name='sensation')
    OR EXISTS (SELECT 1 FROM sqlite_master WHERE type='trigger'
                AND name IN ('trg_ax_exercise_requires_map','trg_ax_exercise_requires_map_rename'))
    OR (SELECT COUNT(*) FROM sqlite_master WHERE type='view'
         AND name IN ('v_pain_event','v_pain_promotion_candidate')) <> 2;
DROP TABLE temp._guard_056_pre;

-- M1
ALTER TABLE workout_types ADD COLUMN muscle_node TEXT REFERENCES muscle_node(node);

-- M3
ALTER TABLE pain_event ADD COLUMN sensation TEXT NOT NULL DEFAULT 'pain' CHECK (sensation IN ('pain','stiff','tight','sore'));
DROP VIEW v_pain_promotion_candidate;
DROP VIEW v_pain_event;
-- M3 views: 047's text with sensation exposed and promotion limited to pain.
CREATE VIEW v_pain_event AS
SELECT
    pe.id,
    pe.ulid,
    pe.owning_agent,
    pe.observed_at,
    CASE WHEN pe.set_ref     IS NOT NULL THEN 'set'
         WHEN pe.session_ref IS NOT NULL THEN 'session'
         ELSE 'none'
    END                                           AS link_grade,
    pe.session_ref,
    pe.set_ref,
    w.date                                        AS session_date,
    COALESCE(ws.exercise_name, pe.exercise_label) AS exercise_name,
    pe.exercise_label,
    COALESCE(ws.weight_kg, pe.load_kg)            AS load_kg,
    COALESCE(ws.set_seq,   pe.set_no)             AS set_no,
    COALESCE(ws.rir,       pe.rir)                AS rir,
    ws.reps,
    ws.set_kind,
    pe.site,
    pe.site_origin,
    pe.side,
    pe.pain,
    pe.sensation,
    pe.rom_phase,
    pe.fault,
    pe.action,
    pe.session_label,
    pe.note,
    pe.guardrail,
    pe.program_change,
    pe.status,
    pe.source,
    pe.recorded_at,
    -- the ledger_constraint FAMILY these events belong to.  Derived, never
    -- stored: the day the owner renames a site the key follows, and a stored
    -- copy would not.  UNSIDED on purpose -- the sided form (injury_shoulder_r)
    -- is the owner's refinement of a part he chose to track per side, and an
    -- event cannot know which parts those are.  NULL when site is unknown: an
    -- unsited event cannot name a constraint, and saying so is the point.
    CASE WHEN pe.site IS NULL THEN NULL
         ELSE 'injury_' || pe.site
    END                                           AS constraint_family_key
FROM pain_event pe
LEFT JOIN workout_sets ws ON ws.id = pe.set_ref
LEFT JOIN workouts     w  ON w.id  = pe.session_ref;

-- A4: THE promotion surface.  Aggregate only -- it reports, it never writes.
--
-- One row per BODY PART (see the header on why not per side).  The 28d / 84d
-- windows are the two a program review actually asks about -- one mesocycle,
-- one block; an arbitrary window is the CLI's job
-- (`kit_life.sh pain-candidates --weeks N`), not a second view.
CREATE VIEW v_pain_promotion_candidate AS
SELECT
    v.owning_agent,
    v.site,
    v.constraint_family_key,
    COUNT(*)                                                   AS total_events,
    SUM(CASE WHEN v.observed_at >= datetime('now','localtime','-28 day')
             THEN 1 ELSE 0 END)                                AS events_28d,
    SUM(CASE WHEN v.observed_at >= datetime('now','localtime','-84 day')
             THEN 1 ELSE 0 END)                                AS events_84d,
    -- still unanswered.  A different question from recurrence, so its own column.
    SUM(CASE WHEN v.status = 'open' THEN 1 ELSE 0 END)         AS open_events,
    -- side BREAKDOWN, not a grouping key.  This is what tells the owner whether
    -- the part he is about to declare is a one-sided story or a both-sides one
    -- -- i.e. whether the row he writes should be injury_<site> or
    -- injury_<site>_r.  The view does not decide that for him.
    SUM(CASE WHEN v.side = 'L' THEN 1 ELSE 0 END)              AS side_l,
    SUM(CASE WHEN v.side = 'R' THEN 1 ELSE 0 END)              AS side_r,
    SUM(CASE WHEN v.side IS NULL OR v.side IN ('both','NA')
             THEN 1 ELSE 0 END)                                AS side_other,
    -- provenance split.  A candidate standing on transcribed sites is a weaker
    -- claim than one standing on stated ones, and the owner is entitled to see
    -- which he is being shown.
    SUM(CASE WHEN v.site_origin = 'stated'  THEN 1 ELSE 0 END) AS site_stated,
    SUM(CASE WHEN v.site_origin = 'carried' THEN 1 ELSE 0 END) AS site_carried,
    MAX(v.pain)                                                AS max_pain,
    ROUND(AVG(v.pain), 1)                                      AS avg_pain,
    MIN(v.observed_at)                                         AS first_seen,
    MAX(v.observed_at)                                         AS last_seen,
    COUNT(DISTINCT v.session_ref)                              AS sessions_hit,
    -- how much of this evidence reaches a SET.  A candidate with set-linked
    -- events can say "at this weight, on this set"; one without can only say
    -- "on these days", and the two are not the same claim to hand a program.
    SUM(CASE WHEN v.set_ref IS NOT NULL THEN 1 ELSE 0 END)     AS set_linked,
    -- the standing constraint(s) already covering this part, across the whole
    -- injury_<site>% family.  Empty = the events have no standing row to keep
    -- alive, which is the shape the #1 SAFETY finding is about.
    (SELECT COUNT(*) FROM ledger_constraint lc
      WHERE lc.class = 'safety' AND lc.status = 'active'
        AND (lc.key = v.constraint_family_key
             OR lc.key LIKE v.constraint_family_key || '\_%' ESCAPE '\')
    )                                                          AS constraint_active,
    (SELECT group_concat(lc.key, ',') FROM ledger_constraint lc
      WHERE lc.class = 'safety'
        AND (lc.key = v.constraint_family_key
             OR lc.key LIKE v.constraint_family_key || '\_%' ESCAPE '\')
    )                                                          AS constraint_keys
FROM v_pain_event v
WHERE v.site IS NOT NULL
  AND v.sensation = 'pain'
GROUP BY v.owning_agent, v.site, v.constraint_family_key;

-- M4: the creation gate.  INSERT and rename both pass through it.
CREATE TRIGGER trg_ax_exercise_requires_map BEFORE INSERT ON ax_exercise
WHEN NOT EXISTS (SELECT 1 FROM exercise_muscle_map
                  WHERE exercise_name = NEW.exercise_name AND role = 'primary')
  OR NOT EXISTS (SELECT 1 FROM exercise_fatigue_cost
                  WHERE exercise_name = NEW.exercise_name)
BEGIN
  SELECT RAISE(ABORT, 'ax_exercise refused: register the exercise with a primary muscle map and a fatigue cost first (kit_life.sh exercise-add)');
END;
CREATE TRIGGER trg_ax_exercise_requires_map_rename BEFORE UPDATE OF exercise_name ON ax_exercise
WHEN NOT EXISTS (SELECT 1 FROM exercise_muscle_map
                  WHERE exercise_name = NEW.exercise_name AND role = 'primary')
  OR NOT EXISTS (SELECT 1 FROM exercise_fatigue_cost
                  WHERE exercise_name = NEW.exercise_name)
BEGIN
  SELECT RAISE(ABORT, 'ax_exercise refused: register the exercise with a primary muscle map and a fatigue cost first (kit_life.sh exercise-add)');
END;

-- ── DATA (conditional; a no-op where the taxonomy was never seeded) ───────

-- D1 nodes.  Parent must exist (FK); an existing node is left untouched.
INSERT INTO muscle_node (node, parent, kind, sort, band_unit)
  SELECT v.node, v.parent, 'subregion', v.sort, v.band
    FROM (SELECT 'rotator_cuff' AS node, 'shoulders' AS parent, 19 AS sort, 'rotator_cuff' AS band
          UNION ALL SELECT 'obliques',        'abs',      20, 'obliques'
          UNION ALL SELECT 'brachialis',      'biceps',   21, 'biceps'
          UNION ALL SELECT 'brachioradialis', 'forearms', 22, 'forearms'
          UNION ALL SELECT 'hip_flexors',     'legs',     23, 'hip_flexors'
          UNION ALL SELECT 'tibialis_anterior', 'legs',   24, 'tibialis_anterior'
          UNION ALL SELECT 'serratus',        'chest',    25, 'chest'
          UNION ALL SELECT 'forearm_flexors', 'forearms', 26, 'forearms'
          UNION ALL SELECT 'forearm_extensors','forearms',27, 'forearms'
          UNION ALL SELECT 'rect_fem',        'quads',    28, 'quads'
          UNION ALL SELECT 'vast_lat',        'quads',    29, 'quads'
          UNION ALL SELECT 'vast_med',        'quads',    30, 'quads'
          UNION ALL SELECT 'hams_lateral',    'hamstrings', 31, 'hamstrings'
          UNION ALL SELECT 'hams_medial',     'hamstrings', 32, 'hamstrings') v
   WHERE EXISTS (SELECT 1 FROM muscle_node p WHERE p.node = v.parent)
     AND NOT EXISTS (SELECT 1 FROM muscle_node x WHERE x.node = v.node);

INSERT OR IGNORE INTO muscle_label (node, lang, form, text)
  SELECT v.node, 'ko', v.form, v.text
    FROM (SELECT 'rotator_cuff' AS node, 'long' AS form, '회전근개' AS text
          UNION ALL SELECT 'rotator_cuff',    'short', '회전근개'
          UNION ALL SELECT 'obliques',        'long',  '복사근'
          UNION ALL SELECT 'obliques',        'short', '복사근'
          UNION ALL SELECT 'brachialis',      'long',  '상완근'
          UNION ALL SELECT 'brachialis',      'short', '상완근'
          UNION ALL SELECT 'brachioradialis', 'long',  '상완요골근'
          UNION ALL SELECT 'brachioradialis', 'short', '상완요골'
          UNION ALL SELECT 'hip_flexors',     'long',  '장요근'
          UNION ALL SELECT 'hip_flexors',     'short', '장요근'
          UNION ALL SELECT 'tibialis_anterior', 'long',  '전경골근'
          UNION ALL SELECT 'tibialis_anterior', 'short', '정강이'
          UNION ALL SELECT 'serratus',        'long',  '전거근'
          UNION ALL SELECT 'serratus',        'short', '전거근'
          UNION ALL SELECT 'forearm_flexors', 'long',  '전완 굴곡근'
          UNION ALL SELECT 'forearm_flexors', 'short', '전완굴곡'
          UNION ALL SELECT 'forearm_extensors','long', '전완 신전근'
          UNION ALL SELECT 'forearm_extensors','short','전완신전'
          UNION ALL SELECT 'rect_fem',        'long',  '대퇴직근'
          UNION ALL SELECT 'rect_fem',        'short', '대퇴직근'
          UNION ALL SELECT 'vast_lat',        'long',  '외측광근'
          UNION ALL SELECT 'vast_lat',        'short', '외측광근'
          UNION ALL SELECT 'vast_med',        'long',  '내측광근'
          UNION ALL SELECT 'vast_med',        'short', '내측광근'
          UNION ALL SELECT 'hams_lateral',    'long',  '대퇴이두근'
          UNION ALL SELECT 'hams_lateral',    'short', '외측 햄스'
          UNION ALL SELECT 'hams_medial',     'long',  '반건·반막양근'
          UNION ALL SELECT 'hams_medial',     'short', '내측 햄스'
          UNION ALL SELECT 'back_upper',      'long',  '승모근'
          UNION ALL SELECT 'back_lats',       'long',  '광배근'
          UNION ALL SELECT 'back_erectors',   'long',  '척추기립근') v
   WHERE EXISTS (SELECT 1 FROM muscle_node n WHERE n.node = v.node);

-- D2 back split.  Only exercises that carry all three back rows are touched
-- (the known seed shape); anything else was edited by a person and is left.
CREATE TEMP TABLE _d2(exercise_name TEXT, erectors REAL, lats REAL, upper_ REAL);
INSERT INTO _d2 VALUES
  ('lat_pulldown', 0, .70, .30), ('chinup', 0, .70, .30), ('pullup', 0, .70, .30),
  ('chest_supported_t_bar_row', 0, .50, .50),
  ('barbell_row', .15, .45, .40), ('pendlay_row', .15, .45, .40),
  ('deadlift', .30, .10, .10), ('rdl', .30, .10, .10), ('single_leg_deadlift', .30, .10, .10);
DELETE FROM temp._d2 WHERE (SELECT COUNT(*) FROM exercise_subregion_map m
                             WHERE m.exercise_name = temp._d2.exercise_name
                               AND m.subregion IN ('back_erectors','back_lats','back_upper')) <> 3;
UPDATE exercise_subregion_map
   SET weight = (SELECT d.lats FROM temp._d2 d WHERE d.exercise_name = exercise_subregion_map.exercise_name), grade = 'C'
 WHERE subregion = 'back_lats' AND exercise_name IN (SELECT exercise_name FROM temp._d2);
UPDATE exercise_subregion_map
   SET weight = (SELECT d.upper_ FROM temp._d2 d WHERE d.exercise_name = exercise_subregion_map.exercise_name), grade = 'C'
 WHERE subregion = 'back_upper' AND exercise_name IN (SELECT exercise_name FROM temp._d2);
UPDATE exercise_subregion_map
   SET weight = (SELECT d.erectors FROM temp._d2 d WHERE d.exercise_name = exercise_subregion_map.exercise_name), grade = 'C'
 WHERE subregion = 'back_erectors' AND exercise_name IN (SELECT exercise_name FROM temp._d2 WHERE erectors > 0);
DELETE FROM exercise_subregion_map
 WHERE subregion = 'back_erectors' AND exercise_name IN (SELECT exercise_name FROM temp._d2 WHERE erectors = 0);
DROP TABLE temp._d2;

-- D3 overhand pulls: biceps .25/.25 -> .125/.125 + brachialis .25.
CREATE TEMP TABLE _d3(exercise_name TEXT PRIMARY KEY);
INSERT OR IGNORE INTO temp._d3
  SELECT exercise_name FROM exercise_movement_grip
   WHERE grip = 'pronated' AND movement = 'pull_compound'
  UNION SELECT 'lat_pulldown';
DELETE FROM temp._d3 WHERE NOT (
      EXISTS (SELECT 1 FROM exercise_subregion_map m WHERE m.exercise_name = temp._d3.exercise_name
               AND m.subregion = 'biceps_distal' AND abs(m.weight - .25) < 1e-9)
  AND EXISTS (SELECT 1 FROM exercise_subregion_map m WHERE m.exercise_name = temp._d3.exercise_name
               AND m.subregion = 'biceps_prox' AND abs(m.weight - .25) < 1e-9)
  AND EXISTS (SELECT 1 FROM muscle_node n WHERE n.node = 'brachialis'));
UPDATE exercise_subregion_map SET weight = .125, grade = 'C'
 WHERE subregion IN ('biceps_distal','biceps_prox')
   AND exercise_name IN (SELECT exercise_name FROM temp._d3);
INSERT OR IGNORE INTO exercise_subregion_map (exercise_name, subregion, weight, grade)
  SELECT exercise_name, 'brachialis', .25, 'C' FROM temp._d3;
DROP TABLE temp._d3;

-- D4 stabilizer / laterality tags.  Vocabulary rows carry fixed ulids so a
-- pack re-seed can INSERT OR IGNORE the same rows; junction rows are deduped
-- on (exercise, tag), not on ulid.  owning_agent is taken from the tagged
-- exercise row (the vocabulary row borrows the first exercise's), never a
-- hardcoded slug.
CREATE TEMP TABLE _d4tag(ulid TEXT, grp TEXT, value TEXT);
INSERT INTO _d4tag VALUES
  ('01KZ056TAG0STAB0000000ERE', 'stabilizes', 'back_erectors'),
  ('01KZ056TAG0STAB0000000FFL', 'stabilizes', 'forearm_flexors'),
  ('01KZ056TAG0STAB0000000ADD', 'stabilizes', 'adductors'),
  ('01KZ056TAG0STAB0000000RDT', 'stabilizes', 'rear_delt'),
  ('01KZ056TAG0STAB0000000ABS', 'stabilizes', 'abs'),
  ('01KZ056TAG0STAB0000000GMD', 'stabilizes', 'glute_med'),
  ('01KZ056TAG0STAB0000000RTC', 'stabilizes', 'rotator_cuff'),
  ('01KZ056TAG0LATR0000000UNI', 'laterality', 'unilateral');
CREATE TEMP TABLE _d4map(exercise_name TEXT, grp TEXT, value TEXT);
INSERT INTO _d4map VALUES
  ('deadlift','stabilizes','back_erectors'), ('deadlift','stabilizes','forearm_flexors'), ('deadlift','stabilizes','adductors'),
  ('rdl','stabilizes','back_erectors'), ('rdl','stabilizes','forearm_flexors'), ('rdl','stabilizes','adductors'),
  ('barbell_row','stabilizes','back_erectors'), ('barbell_row','stabilizes','forearm_flexors'), ('barbell_row','stabilizes','rear_delt'),
  ('pendlay_row','stabilizes','back_erectors'), ('pendlay_row','stabilizes','forearm_flexors'), ('pendlay_row','stabilizes','rear_delt'),
  ('back_squat','stabilizes','back_erectors'), ('back_squat','stabilizes','abs'), ('back_squat','stabilizes','adductors'),
  ('bulgarian_split_squat','stabilizes','glute_med'), ('bulgarian_split_squat','stabilizes','adductors'),
  ('single_leg_deadlift','stabilizes','glute_med'), ('single_leg_deadlift','stabilizes','adductors'),
  ('overhead_press','stabilizes','abs'), ('overhead_press','stabilizes','rotator_cuff'),
  ('incline_barbell_press','stabilizes','rotator_cuff'), ('incline_dumbbell_press','stabilizes','rotator_cuff'),
  ('chinup','stabilizes','forearm_flexors'), ('pullup','stabilizes','forearm_flexors'), ('lat_pulldown','stabilizes','forearm_flexors'),
  ('face_pull','stabilizes','rotator_cuff'), ('reverse_pec_deck_fly','stabilizes','rotator_cuff'),
  ('reverse_pecdec','stabilizes','rotator_cuff'),
  ('bulgarian_split_squat','laterality','unilateral'), ('single_leg_deadlift','laterality','unilateral'),
  ('single_leg_extension','laterality','unilateral');
-- a tag whose node is not in the taxonomy would be a dangling promise
DELETE FROM temp._d4tag WHERE grp = 'stabilizes'
   AND NOT EXISTS (SELECT 1 FROM muscle_node n WHERE n.node = temp._d4tag.value);
INSERT OR IGNORE INTO ax_tag (ulid, owning_agent, tag_group, value, note, status, created_at)
  SELECT t.ulid, (SELECT e.owning_agent FROM ax_exercise e ORDER BY e.created_at, e.ulid LIMIT 1),
         t.grp, t.value, '056 draft (spec A2.4)', 'active', '2026-09-26T00:00:00Z'
    FROM temp._d4tag t
   WHERE EXISTS (SELECT 1 FROM temp._d4map m JOIN ax_exercise e ON e.exercise_name = m.exercise_name
                  WHERE m.grp = t.grp AND m.value = t.value)
     AND NOT EXISTS (SELECT 1 FROM ax_tag x WHERE x.tag_group = t.grp AND x.value = t.value);
INSERT OR IGNORE INTO ax_exercise_tag (ulid, owning_agent, exercise_ref, tag_ref, created_at)
  SELECT '01KZ056XT' || upper(hex(randomblob(9))), e.owning_agent, e.ulid, tg.ulid,
         '2026-09-26T00:00:00Z'
    FROM temp._d4map m
    JOIN ax_exercise e ON e.exercise_name = m.exercise_name
    JOIN ax_tag tg ON tg.tag_group = m.grp AND tg.value = m.value AND tg.status = 'active'
   WHERE NOT EXISTS (SELECT 1 FROM ax_exercise_tag x WHERE x.exercise_ref = e.ulid AND x.tag_ref = tg.ulid);
DROP TABLE temp._d4tag;
DROP TABLE temp._d4map;

-- D5 sub-node shares.  (exercise, parent, node, weight); a group is applied
-- whole or not at all (see the header's conservation rule).
CREATE TEMP TABLE _d5(exercise_name TEXT, parent TEXT, node TEXT, weight REAL);
INSERT INTO _d5 VALUES
  ('incline_barbell_press','chest','chest_upper',.50), ('incline_barbell_press','chest','chest_mid',.30),
  ('incline_barbell_press','chest','chest_lower',.10), ('incline_barbell_press','chest','serratus',.10),
  ('incline_dumbbell_press','chest','chest_upper',.50), ('incline_dumbbell_press','chest','chest_mid',.30),
  ('incline_dumbbell_press','chest','chest_lower',.10), ('incline_dumbbell_press','chest','serratus',.10),
  ('chest_press_machine','chest','chest_upper',.25), ('chest_press_machine','chest','chest_mid',.50),
  ('chest_press_machine','chest','chest_lower',.15), ('chest_press_machine','chest','serratus',.10),
  ('cable_fly','chest','chest_upper',.30), ('cable_fly','chest','chest_mid',.45), ('cable_fly','chest','chest_lower',.25),
  ('overhead_press','shoulders','front_delt',.65), ('overhead_press','shoulders','side_delt',.35),
  ('overhead_press_smith_seated','shoulders','front_delt',.65), ('overhead_press_smith_seated','shoulders','side_delt',.35),
  ('cable_lateral_raise','shoulders','side_delt',.80), ('cable_lateral_raise','shoulders','front_delt',.10),
  ('cable_lateral_raise','shoulders','rotator_cuff',.10),
  ('face_pull','shoulders','rear_delt',.60), ('face_pull','shoulders','rotator_cuff',.40),
  ('reverse_pecdec','shoulders','rear_delt',.80), ('reverse_pecdec','shoulders','rotator_cuff',.20),
  ('reverse_pec_deck_fly','shoulders','rear_delt',.80), ('reverse_pec_deck_fly','shoulders','rotator_cuff',.20),
  ('bicep_curl','biceps','biceps_prox',.40), ('bicep_curl','biceps','biceps_distal',.40), ('bicep_curl','biceps','brachialis',.20),
  ('preacher_curl','biceps','biceps_prox',.30), ('preacher_curl','biceps','biceps_distal',.50), ('preacher_curl','biceps','brachialis',.20),
  ('incline_dumbbell_curl','biceps','biceps_prox',.45), ('incline_dumbbell_curl','biceps','biceps_distal',.40),
  ('incline_dumbbell_curl','biceps','brachialis',.15),
  ('hammer_curl','biceps','biceps_prox',.30), ('hammer_curl','biceps','biceps_distal',.30), ('hammer_curl','biceps','brachialis',.40),
  ('hammer_curl','forearms','brachioradialis',.35), ('hammer_curl','forearms','forearm_flexors',.10),
  ('hammer_curl','forearms','forearm_extensors',.05),
  ('tricep_pushdown_rope','triceps','triceps_lat_med',.65), ('tricep_pushdown_rope','triceps','triceps_long',.35),
  ('chest_press_machine','triceps','triceps_lat_med',.30), ('chest_press_machine','triceps','triceps_long',.20),
  ('incline_barbell_press','triceps','triceps_lat_med',.30), ('incline_barbell_press','triceps','triceps_long',.20),
  ('incline_dumbbell_press','triceps','triceps_lat_med',.30), ('incline_dumbbell_press','triceps','triceps_long',.20),
  ('overhead_press','triceps','triceps_lat_med',.30), ('overhead_press','triceps','triceps_long',.20),
  ('overhead_press_smith_seated','triceps','triceps_lat_med',.30), ('overhead_press_smith_seated','triceps','triceps_long',.20),
  ('cable_crunch','abs','abs',.60), ('cable_crunch','abs','abs_lower',.25), ('cable_crunch','abs','obliques',.15),
  ('leg_raise','abs','abs',.25), ('leg_raise','abs','abs_lower',.60), ('leg_raise','abs','obliques',.15),
  ('back_squat','glutes','glute_max',.40), ('back_squat','glutes','glute_med',.10),
  ('v_squat','glutes','glute_max',.40), ('v_squat','glutes','glute_med',.10),
  ('bulgarian_split_squat','glutes','glute_max',.65), ('bulgarian_split_squat','glutes','glute_med',.35),
  ('deadlift','glutes','glute_max',.80), ('deadlift','glutes','glute_med',.20),
  ('rdl','glutes','glute_max',.80), ('rdl','glutes','glute_med',.20),
  ('single_leg_deadlift','glutes','glute_max',.70), ('single_leg_deadlift','glutes','glute_med',.30),
  ('back_squat','quads','rect_fem',.15), ('back_squat','quads','vast_lat',.45), ('back_squat','quads','vast_med',.40),
  ('v_squat','quads','rect_fem',.15), ('v_squat','quads','vast_lat',.45), ('v_squat','quads','vast_med',.40),
  ('bulgarian_split_squat','quads','rect_fem',.15), ('bulgarian_split_squat','quads','vast_lat',.45),
  ('bulgarian_split_squat','quads','vast_med',.40),
  ('wall_sit','quads','rect_fem',.15), ('wall_sit','quads','vast_lat',.45), ('wall_sit','quads','vast_med',.40),
  ('single_leg_extension','quads','rect_fem',.40), ('single_leg_extension','quads','vast_lat',.30),
  ('single_leg_extension','quads','vast_med',.30),
  ('deadlift','hamstrings','hams_lateral',.55), ('deadlift','hamstrings','hams_medial',.45),
  ('rdl','hamstrings','hams_lateral',.55), ('rdl','hamstrings','hams_medial',.45),
  ('single_leg_deadlift','hamstrings','hams_lateral',.55), ('single_leg_deadlift','hamstrings','hams_medial',.45),
  ('leg_curl','hamstrings','hams_lateral',.35), ('leg_curl','hamstrings','hams_medial',.65),
  ('standing_calf_raise','calves','calf_gastroc',.70), ('standing_calf_raise','calves','calf_soleus',.30),
  ('seated_calf_raise','calves','calf_gastroc',.15), ('seated_calf_raise','calves','calf_soleus',.85),
  ('smith_seated_calf_raise','calves','calf_gastroc',.15), ('smith_seated_calf_raise','calves','calf_soleus',.85);
CREATE TEMP TABLE _d5g AS
  SELECT exercise_name, parent, SUM(weight) AS total FROM temp._d5 GROUP BY exercise_name, parent;
-- every node of a group must be the parent itself or a child of it
DELETE FROM temp._d5g WHERE EXISTS (
  SELECT 1 FROM temp._d5 d
   WHERE d.exercise_name = temp._d5g.exercise_name AND d.parent = temp._d5g.parent
     AND NOT EXISTS (SELECT 1 FROM muscle_node n WHERE n.node = d.node
                      AND (n.node = d.parent OR n.parent = d.parent)));
-- conservation: the current share under the parent equals the group's sum
DELETE FROM temp._d5g WHERE abs(COALESCE(
    (SELECT SUM(m.weight) FROM exercise_subregion_map m
       LEFT JOIN muscle_node n ON n.node = m.subregion
      WHERE m.exercise_name = temp._d5g.exercise_name
        AND (m.subregion = temp._d5g.parent OR n.parent = temp._d5g.parent)),
    (SELECT CASE em.role WHEN 'primary' THEN 1.0 ELSE 0.5 END FROM exercise_muscle_map em
      WHERE em.exercise_name = temp._d5g.exercise_name AND em.muscle = temp._d5g.parent),
    0) - temp._d5g.total) > 1e-6;
DELETE FROM exercise_subregion_map WHERE EXISTS (
  SELECT 1 FROM temp._d5g g
   WHERE g.exercise_name = exercise_subregion_map.exercise_name
     AND (exercise_subregion_map.subregion = g.parent
          OR exercise_subregion_map.subregion IN
             (SELECT n.node FROM muscle_node n WHERE n.parent = g.parent)));
INSERT INTO exercise_subregion_map (exercise_name, subregion, weight, grade)
  SELECT d.exercise_name, d.node, d.weight, 'C'
    FROM temp._d5 d JOIN temp._d5g g
      ON g.exercise_name = d.exercise_name AND g.parent = d.parent;
DROP TABLE temp._d5g;
DROP TABLE temp._d5;
-- D5 tibialis_raise: the catalog row for the dorsiflexors.
INSERT INTO exercise_subregion_map (exercise_name, subregion, weight, grade)
  SELECT 'tibialis_raise', 'tibialis_anterior', 1.0, 'C'
   WHERE EXISTS (SELECT 1 FROM muscle_node WHERE node = 'tibialis_anterior')
     AND NOT EXISTS (SELECT 1 FROM exercise_subregion_map WHERE exercise_name = 'tibialis_raise');

-- M1 backfill: single-region strength subtypes only.
UPDATE workout_types
   SET muscle_node = CASE subtype
         WHEN '가슴' THEN 'chest'     WHEN '등' THEN 'back'
         WHEN '어깨' THEN 'shoulders' WHEN '하체' THEN 'legs'
         WHEN '삼두' THEN 'triceps'   WHEN '이두' THEN 'biceps'
         WHEN '복근' THEN 'abs'       WHEN '쿼드' THEN 'quads'
         WHEN '사두' THEN 'quads'     WHEN '햄스트링' THEN 'hamstrings' END
 WHERE category = '근력'
   AND subtype IN ('가슴','등','어깨','하체','삼두','이두','복근','쿼드','사두','햄스트링')
   AND EXISTS (SELECT 1 FROM muscle_node n WHERE n.node = CASE workout_types.subtype
         WHEN '가슴' THEN 'chest'     WHEN '등' THEN 'back'
         WHEN '어깨' THEN 'shoulders' WHEN '하체' THEN 'legs'
         WHEN '삼두' THEN 'triceps'   WHEN '이두' THEN 'biceps'
         WHEN '복근' THEN 'abs'       WHEN '쿼드' THEN 'quads'
         WHEN '사두' THEN 'quads'     WHEN '햄스트링' THEN 'hamstrings' END);

-- guard 3: the structure landed.
CREATE TEMP TABLE _guard_056_post(seen_count);
CREATE TEMP TRIGGER _guard_056_post_refuse BEFORE INSERT ON _guard_056_post BEGIN
  SELECT RAISE(FAIL, 'migration 056 refused: a column, trigger or view is missing after the change -- rolled back, nothing changed');
END;
INSERT INTO _guard_056_post
  SELECT 1 WHERE
       NOT EXISTS (SELECT 1 FROM pragma_table_info('workout_types') WHERE name='muscle_node')
    OR NOT EXISTS (SELECT 1 FROM pragma_table_info('pain_event') WHERE name='sensation')
    OR (SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'
         AND name IN ('trg_ax_exercise_requires_map','trg_ax_exercise_requires_map_rename')) <> 2
    OR (SELECT COUNT(*) FROM sqlite_master WHERE type='view'
         AND name IN ('v_pain_event','v_pain_promotion_candidate')) <> 2;
DROP TABLE temp._guard_056_post;

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 56;
COMMIT;
