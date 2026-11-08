-- 065_strand_measure_tokens.sql  (target user_version 65)
--
-- reversible: YES, losslessly while no strand links one of these tokens.
--   Rollback = DELETE FROM goal_metric_source WHERE metric_token IN (the six
--   below) AND axis_key IS NULL; PRAGMA user_version = 64.  Data only: six
--   seed rows, no table / column / trigger touched.
--
-- ==========================================================================
-- WHAT.  Two measures a goal-interview strand could not own (owner
-- rehearsal 2026-09-30 19:40-19:53; research dsp-20260930-195331 findings
-- 3-6, pack_health body_ideal_archetypes.json v1):
--
--   lean_<seg>_kg  (arm_l, arm_r, trunk, leg_l, leg_r)  source metric_log
--     The InBody segmental lean readings.  The kit already writes and reads
--     them (log-metric / body-segment, _SEGMENT_KEYS) but no
--     goal_metric_source row named them, so a strand could not link them --
--     and an X-taper strand (wide shoulders/back AND developed thighs, a
--     narrow waist; the legs are judged) was left with body fat as its
--     easiest link: the error the owner caught.  source_expr = the
--     metric_log.metric key itself, exactly like skeletal_muscle_kg (033).
--     The per-segment "% of standard" (lean_<seg>_pct) stays a reading: it
--     is the sheet's own standard, not a quantity a person moves.
--
--   controlled_tempo_share_sets  source program
--     Share of work sets done as slow, controlled, full-range MOVEMENT,
--     loaded or not.  Pilates is controlled movement, not a timed hold;
--     zero_load_control_share_sets (055) counts isometric holds
--     (rx-calc _is_isometric_hold) and stays registered for holds, but is no
--     longer what the kit offers a Pilates / connection strand.  source
--     'program' = computed by the health pack from program.yaml, like the
--     two 055 shares; the kit reads no value for it.  Until a pack computes
--     it the strand reads it as a check with no number yet.
--
-- axis_key NULL: a user strand claims a token via axis-add (055).
-- OR IGNORE: a store that already carries a row keeps it untouched.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_065(seen_user_version);
CREATE TEMP TRIGGER _guard_065_refuse BEFORE INSERT ON _guard_065 BEGIN
  SELECT RAISE(FAIL, 'migration 065 refused: this database is not at user_version 64 -- either 065 was already applied (>= 65) or the chain up to 064 has not run (< 64). Nothing changed');
END;
INSERT INTO _guard_065
  SELECT user_version FROM pragma_user_version WHERE user_version <> 64;
DROP TABLE temp._guard_065;

BEGIN IMMEDIATE;

INSERT OR IGNORE INTO goal_metric_source VALUES('lean_arm_l_kg','metric_log','lean_arm_l_kg','health','up','warg','2026-09-30T00:00:00Z',NULL);
INSERT OR IGNORE INTO goal_metric_source VALUES('lean_arm_r_kg','metric_log','lean_arm_r_kg','health','up','warg','2026-09-30T00:00:00Z',NULL);
INSERT OR IGNORE INTO goal_metric_source VALUES('lean_trunk_kg','metric_log','lean_trunk_kg','health','up','warg','2026-09-30T00:00:00Z',NULL);
INSERT OR IGNORE INTO goal_metric_source VALUES('lean_leg_l_kg','metric_log','lean_leg_l_kg','health','up','warg','2026-09-30T00:00:00Z',NULL);
INSERT OR IGNORE INTO goal_metric_source VALUES('lean_leg_r_kg','metric_log','lean_leg_r_kg','health','up','warg','2026-09-30T00:00:00Z',NULL);
INSERT OR IGNORE INTO goal_metric_source VALUES('controlled_tempo_share_sets','program','controlled_tempo_share_sets','health','up','warg','2026-09-30T00:00:00Z',NULL);

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 65;
COMMIT;
