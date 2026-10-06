-- 044_criterion_period.sql  (target user_version 44)
--
-- reversible: NO down-migration ships.  Hand-rollback: DROP VIEW
--   v_criterion_verdict; restore v_criterion_span / v_criterion /
--   v_domain_state from 037/038's text; DROP TABLE period_anchor; PRAGMA
--   user_version = 43.  Views hold no data, but period_anchor MAY by then
--   hold rows the OWNER declared -- copy them out before dropping, exactly
--   as a metric_freshness rollback would have to.  A FAILED apply needs no
--   rollback: guard first, single transaction, .bail on, stamp last (023
--   discipline).
--
-- WHY -- A PERIOD CRITERION WAS STORED AND THEN SILENTLY IGNORED.
--
-- commitment.period landed in 037 with a CHECK roster (day..year) and the
-- judgement never read it: v_domain_state contained the string 'period'
-- ZERO times and v_criterion_span did not even project the column (measured
-- 2026-09-09 on the live copy, uv43).  So `period='week'` changed NOTHING
-- about how the rule was judged -- the layer compared the single freshest
-- measure row against lo/hi either way.  A per-period rule was
-- indistinguishable from a point-in-time rule: declaration without
-- machinery, the defect class this estate spent 2026-09-09 removing.  The
-- owner's own case (CRITERION-LAYERING §2, 17:24 축자): accumulating
-- quantities have no physiological day boundary -- "결국 엄격한 계산을
-- 주간으로 봐야한다" -- and the kit could not express that judgement.
--
-- WHAT LANDS.  The judgement now distinguishes two kinds of criterion:
--   point   (period NULL)  the freshest in-force actual value vs lo/hi.
--                          Byte-for-byte today's behaviour -- unchanged.
--   period  (period set)   the AGGREGATE over the CURRENT period vs lo/hi.
-- Four kit-level decisions, each defended below; per CRITERION-LAYERING §1
-- the kit ships the seat and the machinery and holds NO numbers -- no
-- boundary, no offset, no cadence is seeded by this file.
--
-- 1. WHICH AGGREGATE: SUM, and only for metric_def.accrual = 'accrued'.
--    'accrued' MEANS the rows are increments (a meal's kcal, a session's
--    count), so "the period's total is the sum" is true by that column's
--    own definition -- domain-agnostic, hence kit.  For 'observed' (a
--    LEVEL: body fat) the sum is nonsense and the honest aggregate
--    (mean? min? last?) is a domain choice the kit must not guess -- so a
--    period criterion on a non-accrued metric is NEVER judged (silence,
--    fail-CLOSED).  Same for 'derived' (its rows are projections of other
--    rows; summing them double-counts).  The discriminator REUSES
--    metric_def.accrual, which already carries exactly this fact -- a new
--    per-criterion aggregate column would be a second home for it.
--
-- 2. PERIOD BOUNDARIES: a new SEAT, period_anchor, fail-CLOSED until
--    declared -- the metric_freshness shape.  Where a week starts and which
--    local midnight closes a day are the OWNER's clock ("이 값이 이 사람이라서
--    다른가" -> the agent asks, the owner answers); hardcoding Monday would
--    be the 041 defect (a number the kit invented) and the view cannot read
--    config/agent.conf (038: no fourth clock, no hardcoded +9), so the
--    declared offset lands HERE as data.  NOTHING is seeded: an undeclared
--    period keeps every criterion on it silent, which is the honest state
--    until onboarding fills the seat (CRITERION-LAYERING §4).
--
-- 3. PARTIAL PERIODS: the mid-period aggregate IS judged -- the current
--    period is the only one still steerable, and the owner's carry-over
--    rationale ("어제 못한걸 내일 이월해도 '어느정도'는 효과가 있다")
--    only works if the running total is visible mid-week.  Judging silently
--    until the period closes would mute the layer about the current period
--    100% of the time.  The cost of judging early is contained by the
--    verdict vocabulary instead (next point).
--
--    The verdict is asymmetric ON PURPOSE, and the asymmetry is arithmetic,
--    not domain knowledge: an accrued sum can only GROW within its period.
--      sum > hi        'over'     definitive at any instant -- it cannot
--                                 come back down.
--      sum < lo        'pending'  NOT 'under': the floor can still be
--                                 reached, and calling it 미달 at 40% of the
--                                 week would read as failing every Monday --
--                                 the same manufactured-미달 class 034
--                                 removed from workout history.
--      otherwise       'within'
--    A retro verdict on a CLOSED period is a different read (an as-of
--    window, like v_criterion_span) and deliberately not built here.
--
-- 4. FRESHNESS FOR AN AGGREGATE: the metric's own B1 rule, unchanged -- the
--    aggregate is judged only while the metric has a fresh in-force actual
--    row per metric_freshness.  The period is NOT the freshness window: a
--    Monday binge followed by six unlogged days would otherwise read as a
--    current weekly total all week -- a lie of omission.  Requiring
--    arrivals to stay inside the metric's declared cadence keeps the sum
--    honest and reuses the existing seat instead of minting a second
--    freshness rule.
--
-- WHAT DOES NOT LAND.  No renderer (cut-1 exclusion stands).  No backfill
-- and no reshaping of the three live ledger_goal criteria -- the owner is
-- redoing Warg onboarding (2026-09-09 17:40) and those values will be
-- replaced; the kit gets the expressive power, onboarding fills the values.
-- kcal_intake's period=day row in particular is a MIS-EXPRESSION
-- (CRITERION-LAYERING §3, applies_on=rest_day judged daily) and must not be
-- migrated as if it were a spec.  goal_target deliberately gains NO period
-- column: the owner's 08-24 split keeps cadence on the rule arm, and the
-- goal arm projects period=NULL (a goal target is a 조준점).
--
-- GUARD.  Both-sided, 037/040/043 discipline: refused unless user_version
-- is EXACTLY 43, BEFORE any transaction or write -- exit != 0, database
-- untouched (a re-apply refused; a skipped 021~043 chain refused).
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_044(seen_user_version);
CREATE TEMP TRIGGER _guard_044_refuse BEFORE INSERT ON _guard_044 BEGIN
  SELECT RAISE(FAIL, 'migration 044 refused: this database is not at user_version 43 -- either 044 was already applied (>= 44) or the 021~043 chain has not run (< 43). Nothing changed');
END;
INSERT INTO _guard_044
  SELECT user_version FROM pragma_user_version WHERE user_version <> 43;
DROP TABLE temp._guard_044;
BEGIN IMMEDIATE;

-- 1. The seat.  One row per period token the owner has declared a boundary
--    for.  Shipped EMPTY on purpose (see WHY §2).
CREATE TABLE IF NOT EXISTS period_anchor (
  period          TEXT PRIMARY KEY
                  CHECK (period IN ('day','week','month','quarter','year')),
  -- The declared local-clock offset, minutes east of UTC (real offsets
  -- include :30/:45, so minutes, not hours).  This is the SAME value the
  -- instance declares as AGENT_TZ_OFFSET_HOURS -- landed here as data
  -- because view SQL cannot reach config/agent.conf (038 discipline).
  utc_offset_min  INTEGER NOT NULL CHECK (utc_offset_min BETWEEN -720 AND 840),
  -- Week start in strftime('%w') numbering (0=Sunday .. 6=Saturday) --
  -- SQLite's own day numbering, reused instead of minting a second one.
  -- Required for 'week' (refused at write, never guessed at read) and
  -- refused elsewhere (a month has no week_start; a stored one would be a
  -- dead knob that reads as if it did something).
  week_start      INTEGER CHECK (week_start BETWEEN 0 AND 6),
  declared_by     TEXT NOT NULL DEFAULT 'module'
                  CHECK (declared_by IN ('module','user','migration')),
  note            TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (period <> 'week' OR week_start IS NOT NULL),
  CHECK (period = 'week' OR week_start IS NULL)
);

-- 2. The read windows, restated (038 idiom: DROP-then-CREATE so a stale
--    definition is REPLACED, never silently kept).  Text below is identical
--    to schema.sql's -- the parity suite compares normalized DDL.
DROP VIEW IF EXISTS v_criterion_verdict;
DROP VIEW IF EXISTS v_domain_state;
DROP VIEW IF EXISTS v_criterion;
DROP VIEW IF EXISTS v_criterion_span;
CREATE VIEW v_criterion_span AS
  SELECT 'goal' AS source, gt.id AS criterion_id, gt.metric AS metric,
         gt.target_low AS lo, gt.target_high AS hi, gt.goal_id AS goal_id,
         gt.version AS version, gt.valid_from AS valid_from,
         gt.valid_until AS valid_until, gt.supersedes AS supersedes,
         -- 044: the goal arm has no period column BY DESIGN -- the owner's
         -- 08-24 split keeps cadence on the rule arm; a goal target is a
         -- 조준점 (aiming point), so it projects NULL and is judged as a
         -- point criterion.
         NULL AS period
    FROM goal_target gt
    JOIN goal g   ON g.id = gt.goal_id
    JOIN stage s  ON s.token = g.stage_token
   WHERE gt.rung_state = 'tracking'
     AND g.focus_state <> 'dormant'
     AND s.lifecycle = 'open'
     AND (gt.target_low IS NOT NULL OR gt.target_high IS NOT NULL)
  UNION ALL
  SELECT 'rule', c.id, c.metric, c.lo, c.hi, c.goal_id,
         c.version, c.valid_from, c.valid_until, c.supersedes,
         -- 044: period was stored here since 037 and never projected -- the
         -- reason a per-period rule was judged as a point rule.
         c.period
    FROM commitment c
   WHERE c.status = 'active'
     AND c.metric IS NOT NULL
     AND (c.lo IS NOT NULL OR c.hi IS NOT NULL);
CREATE VIEW v_criterion AS
  SELECT source, criterion_id, metric, lo, hi, goal_id,
         version, valid_from, valid_until, supersedes, period
    FROM v_criterion_span
   WHERE (valid_from  IS NULL
          OR valid_from  <= strftime('%Y-%m-%dT%H:%M:%SZ','now'))
     AND (valid_until IS NULL
          OR valid_until >  strftime('%Y-%m-%dT%H:%M:%SZ','now'));
CREATE VIEW v_domain_state AS
WITH fresh_metric AS (
  -- A value you can say '요즘' about.  THREE independent conditions, none of
  -- them the one the first cut used:
  --   basis='actual'      a plan is not a reflection            (regrill B2)
  --   valid_until         still in force, i.e. not superseded / retracted.
  --                       This column is a SUCCESSION stamp and nothing else
  --                       (regrill B1) -- it is no longer read as freshness.
  --   observed_at + interval   the actual age test, against the metric's own
  --                       declared arrival window.  No declaration -> the JOIN
  --                       drops the metric -> never fresh (fail-CLOSED).
  -- GRADE FLOOR + FUTURE CLAMP (038): observed_at carries two grades in this
  -- estate (canonical instants from travel, date-only stamps from nutrition).
  -- Anything that is not a canonical instant is floored to 00:00:00Z of its
  -- own date -- and the graded instant must ALSO not exceed 'now'.  Without
  -- the clamp, a LOCAL-grade stamp floored to a FUTURE instant between 00:00
  -- and 09:00 local on a UTC+9 estate: negative age, passes every window,
  -- 9h/day fail-OPEN (the opposite of the sign 037's header promised).  The
  -- view cannot read the instance offset (config/agent.conf is unreachable
  -- from SQL) and hardcoding one would mint a fourth clock, so ambiguity
  -- fail-CLOSES: an instant claiming the future is not fresh evidence, and
  -- the residual error stays bounded and always toward STALE.
  SELECT DISTINCT m.metric AS metric
    FROM measure m
    JOIN metric_freshness mf ON mf.metric = m.metric
   WHERE m.basis = 'actual'
     AND (m.valid_until IS NULL
          OR m.valid_until > strftime('%Y-%m-%dT%H:%M:%SZ','now'))
     AND (CASE WHEN m.observed_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
               THEN m.observed_at
               ELSE substr(m.observed_at, 1, 10) || 'T00:00:00Z' END)
         <= strftime('%Y-%m-%dT%H:%M:%SZ','now')
     AND (CASE WHEN m.observed_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
               THEN m.observed_at
               ELSE substr(m.observed_at, 1, 10) || 'T00:00:00Z' END)
         > strftime('%Y-%m-%dT%H:%M:%SZ','now',
                    '-' || CAST(mf.stale_after_hours AS TEXT) || ' hours')
),
sayable AS (
  SELECT dm.domain_id AS domain_id,
         COUNT(DISTINCT c.metric) AS n_sayable_metrics
    FROM domain_metric dm
    JOIN v_criterion  c ON c.metric = dm.metric
    JOIN fresh_metric f ON f.metric = dm.metric
   -- 044: the judgement READS period.  A POINT criterion (period NULL) is
   -- sayable exactly as before.  A PERIOD criterion speaks about an
   -- AGGREGATE, so it is sayable only when that aggregate is constructible:
   -- the metric's rows must be increments (metric_def.accrual = 'accrued'
   -- -- summing a LEVEL like body fat is nonsense the kit refuses to
   -- compute, and which honest aggregate a level takes is a DOMAIN choice,
   -- not the kit's) AND the period's boundary must be DECLARED
   -- (period_anchor -- where a week starts is the owner's clock; no
   -- declaration -> silence, fail-CLOSED, the metric_freshness shape).
   -- v_criterion_verdict restates these same legs on the value side.
   WHERE c.period IS NULL
      OR (EXISTS (SELECT 1 FROM metric_def md
                   WHERE md.token = c.metric AND md.accrual = 'accrued')
          AND EXISTS (SELECT 1 FROM period_anchor pa
                       WHERE pa.period = c.period))
   GROUP BY dm.domain_id
)
SELECT d.id AS domain_id, d.token,
       (SELECT COUNT(*) FROM inflow f
         WHERE f.domain_id = d.id AND f.status = 'active')            AS n_inflows,
       COALESCE(sy.n_sayable_metrics, 0)                              AS n_sayable,
       (SELECT COUNT(DISTINCT c2.goal_id) FROM v_criterion c2
          JOIN domain_metric dm2 ON dm2.metric = c2.metric
          JOIN goal_thesis gth   ON gth.goal_id = c2.goal_id
         WHERE dm2.domain_id = d.id)                                  AS n_spiral_linked,
       CASE
         WHEN (SELECT COUNT(*) FROM inflow f
                WHERE f.domain_id = d.id AND f.status = 'active') = 0
              THEN 'unknown'
         WHEN COALESCE(sy.n_sayable_metrics, 0) = 0
              THEN 'collecting'
         ELSE 'managing'
       END                                                            AS state
FROM domain d
LEFT JOIN sayable sy ON sy.domain_id = d.id
WHERE d.status = 'active';
-- 3. The VALUE side of the judgement (044).  One row per live criterion
--    whose sentence is constructible RIGHT NOW; a criterion missing any leg
--    has no row (silence, never a guessed number).  This is the view that
--    makes a period rule and a point rule with identical lo/hi reach
--    DIFFERENT verdicts on the same data -- before 044 they could not.
CREATE VIEW v_criterion_verdict AS
WITH live_value AS (
  -- In-force actual rows, graded: the ONE value idiom of this layer
  -- (037 grade floor + 038 future clamp), restated per-row because this
  -- view needs the VALUES, not just the metric's existence.
  SELECT m.metric AS metric, m.value AS value,
         (CASE WHEN m.observed_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
               THEN m.observed_at
               ELSE substr(m.observed_at, 1, 10) || 'T00:00:00Z' END) AS graded_at
    FROM measure m
   WHERE m.basis = 'actual'
     AND (m.valid_until IS NULL
          OR m.valid_until > strftime('%Y-%m-%dT%H:%M:%SZ','now'))
),
fresh_point AS (
  -- The freshest fresh value per metric (B1: inside the declared arrival
  -- window; no metric_freshness row -> no row here -> fail-CLOSED).  The
  -- bare `value` column rides SQLite's documented MAX() row guarantee; on
  -- an exact graded_at tie the picked row is arbitrary, which is the same
  -- honest ambiguity the tie itself carries.
  SELECT lv.metric AS metric, lv.value AS value, MAX(lv.graded_at) AS graded_at
    FROM live_value lv
    JOIN metric_freshness mf ON mf.metric = lv.metric
   WHERE lv.graded_at <= strftime('%Y-%m-%dT%H:%M:%SZ','now')
     AND lv.graded_at > strftime('%Y-%m-%dT%H:%M:%SZ','now',
                                 '-' || CAST(mf.stale_after_hours AS TEXT) || ' hours')
   GROUP BY lv.metric
),
period_window AS (
  -- [start of the CURRENT period, now], computed from the DECLARED anchor
  -- only -- an undeclared period has no row here and every criterion on it
  -- stays silent.  The local day is found by shifting 'now' by the declared
  -- offset, the boundary is taken in local terms, and the boundary is
  -- shifted back to a canonical UTC-Z instant so it compares against graded
  -- observed_at with plain string ordering (036/038: one clock, one format).
  SELECT pa.period AS period,
         strftime('%Y-%m-%dT%H:%M:%SZ',
           datetime(CASE pa.period
             WHEN 'day'   THEN date(datetime('now', pa.utc_offset_min || ' minutes'))
             WHEN 'week'  THEN date(datetime('now', pa.utc_offset_min || ' minutes'),
                                    '-' || ((CAST(strftime('%w', datetime('now', pa.utc_offset_min || ' minutes')) AS INTEGER)
                                             - pa.week_start + 7) % 7) || ' days')
             WHEN 'month' THEN date(datetime('now', pa.utc_offset_min || ' minutes'),
                                    'start of month')
             WHEN 'quarter' THEN date(datetime('now', pa.utc_offset_min || ' minutes'),
                                    'start of month',
                                    '-' || ((CAST(strftime('%m', datetime('now', pa.utc_offset_min || ' minutes')) AS INTEGER) - 1) % 3) || ' months')
             WHEN 'year'  THEN date(datetime('now', pa.utc_offset_min || ' minutes'),
                                    'start of year')
           END, (-pa.utc_offset_min) || ' minutes')) AS win_start
    FROM period_anchor pa
),
period_value AS (
  -- The aggregate: SUM, and only over accrual='accrued' metrics (an
  -- accrued row IS an increment, so the period total is the sum by that
  -- column's own definition; for a level the honest aggregate is a domain
  -- choice the kit must not guess -- no row, silence).  The fresh_point
  -- join is the freshness leg: the sum is judged only while arrivals stay
  -- inside the metric's own declared cadence (B1) -- the period is NOT the
  -- freshness window, or a Monday binge would read as a current weekly
  -- total all week.  COALESCE 0: with arrivals current, an empty window
  -- honestly means nothing accrued YET this period.
  SELECT c.source AS source, c.criterion_id AS criterion_id,
         c.metric AS metric, c.lo AS lo, c.hi AS hi,
         c.period AS period, pw.win_start AS period_start,
         COALESCE((SELECT SUM(lv.value) FROM live_value lv
                    WHERE lv.metric = c.metric
                      AND lv.graded_at >= pw.win_start
                      AND lv.graded_at <= strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                  0.0) AS value
    FROM v_criterion c
    JOIN metric_def md ON md.token = c.metric AND md.accrual = 'accrued'
    JOIN period_window pw ON pw.period = c.period
    JOIN fresh_point fp ON fp.metric = c.metric
   WHERE c.period IS NOT NULL
)
SELECT c.source AS source, c.criterion_id AS criterion_id, c.metric AS metric,
       c.lo AS lo, c.hi AS hi, NULL AS period, NULL AS period_start,
       fp.value AS value,
       -- POINT verdict: symmetric.  A level below the floor is genuinely
       -- under RIGHT NOW -- nothing about a point read is provisional.
       CASE WHEN c.lo IS NOT NULL AND fp.value < c.lo THEN 'under'
            WHEN c.hi IS NOT NULL AND fp.value > c.hi THEN 'over'
            ELSE 'within' END AS verdict
  FROM v_criterion c
  JOIN fresh_point fp ON fp.metric = c.metric
 WHERE c.period IS NULL
UNION ALL
SELECT source, criterion_id, metric, lo, hi, period, period_start, value,
       -- PERIOD verdict: asymmetric, by arithmetic -- an accrued sum only
       -- GROWS within its period.  Over the ceiling is definitive at any
       -- instant; short of the floor mid-period is 'pending', never
       -- 'under', or a weekly floor would read as failing every Monday
       -- (the manufactured-미달 class 034 removed).
       CASE WHEN hi IS NOT NULL AND value > hi THEN 'over'
            WHEN lo IS NOT NULL AND value < lo THEN 'pending'
            ELSE 'within' END AS verdict
  FROM period_value;

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 43.
PRAGMA user_version = 44;
COMMIT;
