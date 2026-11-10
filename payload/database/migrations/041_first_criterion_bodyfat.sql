-- 041_first_criterion_bodyfat.sql  (target user_version 41)
--
-- reversible: NO down-migration ships.  Additive only -- one entity row, one
--   metric_def row, one domain_metric link, one metric_freshness row, six
--   measure rows, one goal row, one goal_target row.  Zero schema change.
--   Hand-rollback: DELETE the seven inserted rows by their ulid/token
--   (all listed below), PRAGMA user_version = 40.  A FAILED apply needs no
--   rollback: single transaction, .bail on, stamp last (023 discipline).
--
-- WHY (cut 1 completion condition, 2026-09-09 lifekit cut-1 notes):
--
--   037 built the judgement layer and it answers NOTHING.  Measured 2026-09-09:
--     measure        6,124 rows   but ALL of them are the diet lane
--     v_criterion        0 rows   no comparison basis exists at all
--     goal / goal_target / commitment   0 rows
--     metric_def        13 tokens, ALL nutrition+travel -- no body composition
--   The owner's three tracked criteria (body_fat_pct / kcal_intake /
--   weekly_sessions) had no metric_def token, no measure row and no criterion
--   row.  The layer was structurally complete and factually empty.
--
--   037's own header said this would happen: "metric_log's 26 tokens
--   (weight_kg / body_fat_pct …) have 0 measure rows.  Body composition is
--   therefore NOT sayable."  This migration makes ONE metric sayable, end to
--   end, so the completion condition ("the judgement layer returns a NON-empty
--   answer on a copy of live data") is met by real data and not by an empty
--   database passing vacuously.
--
-- SCOPE -- one metric, deliberately.  The other two criteria are NOT here:
--   kcal_intake      is an AGGREGATE (daily sum over meal blocks), not a
--                    stored measurement.  Materialising an aggregate into
--                    `measure` is a design question, not a data move.
--   weekly_sessions  likewise (weekly count over workouts).
--   Both stay in cut 2.  body_fat_pct is the only one of the three that is a
--   directly observed value with rows already on disk (metric_log, 6 rows).
--
-- WHY AN ENTITY ROW IS NOT OPTIONAL (the mechanical reason cut 1 touches cut 2):
--   measure carries CHECK (block_id IS NOT NULL OR entity_id IS NOT NULL OR
--   place_link_id IS NOT NULL).  Body composition attaches to none of the
--   existing scopes -- the diet lane uses block_id, travel uses place_link_id.
--   So a body metric CANNOT be stored without seeding the entity it measures.
--   That CHECK, not a preference, is what binds cut 1 to the entity seed.
--   Exactly ONE entity is seeded here (the owner's body).  The full entity
--   roster (통장/사람/…) stays in cut 2.

BEGIN;

-- 1. The entity the measurement is ABOUT.  fungible=0: a body is not
--    interchangeable.  origin='seed': shipped, not user-declared.
INSERT OR IGNORE INTO entity(ulid, token, fungible, origin, status)
VALUES ('01M21K0BODY0000000000041AA', '몸', 0, 'seed', 'active');

-- 2. The metric definition.  scope_kind='entity' (it is a property of the body,
--    not of a block or a place_link).  accrual='observed' -- an InBody reading
--    is observed at a moment, never accrued across a period like nutrition.
INSERT OR IGNORE INTO metric_def(token, scope_kind, accrual, unit_token,
                                 owner_module, origin, status)
VALUES ('body_fat_pct', 'entity', 'observed', '%', 'health', 'module', 'active');

-- 3. Which domain owns the metric.  training (id 1) is the live domain whose
--    owning agent is Warg; the goal row below carries owning_agent='warg'.
--    NOTE: ledger_goal.domain says 'health', which is NOT a row in `domain`
--    (the roster is training/diet/ledger/life).  That mismatch is recorded in
--    the cut 1 ticket and NOT silently reconciled here.
INSERT OR IGNORE INTO domain_metric(domain_id, metric)
SELECT d.id, 'body_fat_pct' FROM domain d WHERE d.token = 'training';

-- 4. Freshness window (regrill B1: no declaration -> the JOIN drops the metric
--    -> never fresh, fail-CLOSED).  336h = 14 days.
--    DERIVED FROM THE OWNER'S OWN CADENCE, not invented:
--      InBody readings 2026-07-09 / 07-22 / 07-28 / 08-19 / 08-27 / 09-09
--      gaps in days:            13     6     22      8     13
--      median 13, mean 12.4, max 22
--    14d sits just above the median, so a normal rhythm reads fresh and the
--    22-day gap correctly reads stale.  declared_by='user' because the owner
--    ruled (2026-09-07) that body-composition cadence is an ONBOARDING FIELD,
--    not a constant -- this row is the seed value that onboarding overwrites,
--    never a hardcoded answer.
INSERT OR IGNORE INTO metric_freshness(metric, stale_after_hours, declared_by, note)
VALUES ('body_fat_pct', 336.0, 'user',
        'seed from owner cadence (6 InBody readings, median gap 13d); onboarding overrides');

-- 5. The values.  Copied from metric_log, which stays untouched (this is a
--    projection, not a move -- the old lane keeps working until cut 2 retires it).
--    value_origin='measured' (InBody device), basis='actual' (a reading, not a plan).
--    observed_at is date-only, the same grade the nutrition lane already emits;
--    v_domain_state floors a non-canonical grade to 00:00:00Z of its own date
--    and clamps the future, so this grade is handled fail-CLOSED by design.
INSERT INTO measure(metric, value, unit, basis, entity_id, observed_at,
                    source, value_origin, note)
SELECT 'body_fat_pct', ml.value, '%', 'actual', e.id, ml.date,
       'metric_log:041', 'measured', ml.note
  FROM metric_log ml
  CROSS JOIN entity e
 WHERE ml.metric = 'body_fat_pct'
   AND e.token = '몸'
   AND NOT EXISTS (SELECT 1 FROM measure m
                    WHERE m.metric = 'body_fat_pct'
                      AND m.observed_at = ml.date
                      AND m.source = 'metric_log:041');

-- 5b. Stage roster.  MEASURED, not assumed: `stage` was EMPTY (0 rows) and
--     v_criterion_span INNER-JOINs it (JOIN stage s ON s.token = g.stage_token,
--     WHERE s.lifecycle='open').  An empty roster therefore drops EVERY goal
--     criterion silently -- the criterion side of the judgement layer could not
--     have returned a row no matter what was inserted into goal_target.
--     Four tokens covering the four lifecycles so the roster is total; a goal
--     can only ever be in one of them.
INSERT OR IGNORE INTO stage(token, lifecycle, sort_rank, origin) VALUES
  ('active',    'open',   1, 'seed'),
  ('paused',    'held',   2, 'seed'),
  ('done',      'closed', 3, 'seed'),
  ('abandoned', 'void',   4, 'seed');

-- 6. The goal.  Projected from ledger_goal 10001 (the owner's long-horizon
--    recomposition goal).  horizon_kind='thesis_bound': it has no end date and
--    is not periodic -- it ends when the thesis it serves is settled.
--    stage_token='active': ledger_goal.status is 'active' for this row.
INSERT OR IGNORE INTO goal(ulid, title, horizon_kind, stage_token,
                           focus_state, priority_rank)
SELECT lg.ulid, lg.title, 'thesis_bound', 'active', 'focus', 1
  FROM ledger_goal lg WHERE lg.id = 10001;

-- 7. The criterion.  THE RANGE WINS OVER THE SCALAR, and they do not conflict:
--      ledger_goal.target_value = 18.0, target_dir = 'down'  (a ceiling)
--      detail.target_bf_pct_range = [15, 18]                 (a band)
--    18.0 IS the band's upper bound, so the scalar is the band restated with
--    its floor dropped.  Keeping only the ceiling would let 0% read as success;
--    the band is the owner's actual intent (기능성 재조성 -- muscle is kept).
--    Hence target_low=15, target_high=18.
--    valid_from is stamped NOW as a canonical UTC-Z instant (the GLOB CHECK
--    rejects any other grade).  version=1, supersedes=NULL: this is the first
--    statement of this criterion, not a revision.
INSERT INTO goal_target(goal_id, metric, baseline_value, baseline_as_of,
                        target_low, target_high, version, valid_from,
                        rung_state)
SELECT g.id, 'body_fat_pct', lg.baseline_value, lg.baseline_as_of,
       15.0, 18.0, 1, strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'tracking'
  FROM ledger_goal lg
  JOIN goal g ON g.ulid = lg.ulid
 WHERE lg.id = 10001
   AND NOT EXISTS (SELECT 1 FROM goal_target gt
                    WHERE gt.goal_id = g.id AND gt.metric = 'body_fat_pct');

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 40.
PRAGMA user_version = 41;
COMMIT;
