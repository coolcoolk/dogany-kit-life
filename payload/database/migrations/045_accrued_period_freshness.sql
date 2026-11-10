-- 045_accrued_period_freshness.sql  (target user_version 45)
--
-- reversible: NO down-migration ships.  Hand-rollback: DROP VIEW
--   v_criterion_verdict; DROP VIEW v_domain_state; restore both from 044's
--   text; PRAGMA user_version = 44.  Views hold no data, nothing else is
--   touched.  A FAILED apply needs no rollback: guard first, single
--   transaction, .bail on, stamp last (023 discipline).
--
-- WHY -- THE PERIOD VERDICT VANISHED EXACTLY WHEN IT HAD THE MOST TO SAY.
--
-- 044's period_value joined fresh_point: the period sum was judged only
-- while the metric's LATEST arrival sat inside metric_freshness's window.
-- That is the LEVEL question ("does the last reading still estimate now?")
-- asked of non-level data.  An accrued period sum is a count over a CLOSED
-- set of in-force rows inside a bounded window: it does not decay, and the
-- age of the most recent arrival has no bearing on whether the sum is
-- correct -- it is correct, exactly.  Measured cost on the live copy
-- (2026-09-10, uv44): workout session gaps of 1d x39, 2d x17, 3d x1,
-- 4d x2, 5d x1, 6d x2 and 94d x1; under any week-scale cadence roughly 87
-- of those 94 days produce NO verdict row at all.  "이번 주 0회" is not an
-- unknown -- it is a true and useful statement, and 044 deleted it.  On
-- live today the weekly_sessions criterion has NO verdict row for a second
-- reason with the same root: no metric_freshness declaration exists for it
-- (fail-CLOSED), so the period verdict could never appear at all -- and
-- seeding a cadence to work around that would be a kit-invented number
-- (the 041 defect class 042 exists to undo).
--
-- 044's own defence of the join was:
--   "the period is NOT the freshness window, or a Monday binge would read
--    as a current weekly total all week"
-- Tested and found unsound: four sessions on Monday IS the current weekly
-- total all week -- the sum claims nothing about rate or future arrivals,
-- so the feared reading is TRUE under accrued semantics.  For its stated
-- purpose ("keeps the sum honest") the join was already redundant: 044's
-- own pending asymmetry guarantees a partial sum never manufactures
-- 'under' (short of the floor mid-period reads 'pending'), and an
-- undercount can only lower the sum, so a false 'over' is arithmetically
-- impossible.  The one scenario the join could claim to catch -- the owner
-- keeps doing but stops LOGGING -- it does not catch either: elapsed time
-- cannot distinguish "no event" from "unlogged event" for an event-type
-- accrued metric (the absence of a row IS the datum), so the join only
-- traded a possibly-incomplete truth for certain silence, which conveys
-- strictly less.
--
-- WHAT LANDS.
--   1. period_value loses the fresh_point join: an accrued period
--      criterion is judged whenever its window is DECLARED (period_anchor)
--      and its rows are increments (accrual='accrued').  Nothing else about
--      the period arm changes: SUM, the pending asymmetry, fail-CLOSED on
--      undeclared anchors and non-accrued metrics all stand.
--   2. v_domain_state's sayable leg mirrors it (the two views must not
--      disagree about whether a sentence exists): a period criterion is
--      sayable on accrued + declared anchor; a POINT criterion still
--      requires B1 freshness, unchanged -- a level genuinely decays.
--   3. The recording gap is SURFACED, not judged: both arms project
--      last_logged_at (the newest in-force actual stamp <= now; NULL when
--      the metric has no rows).  The consumer can now tell "0 this week,
--      last log 2 days ago" from "0 this week, last log 94 days ago".  No
--      threshold decides which of those is bad -- that judgement is the
--      owner's, and a kit number here would be the 041 defect again
--      (CRITERION-LAYERING).
--
-- WHAT DOES NOT LAND.  No metric_freshness row for weekly_sessions (or
-- anything else) is seeded.  The point path is byte-for-byte 044's.  No
-- renderer.  metric_freshness itself keeps its job everywhere it has one:
-- levels, and the freshness-touch/report cadence hooks.
--
-- GUARD.  Both-sided, 037/040/044 discipline: refused unless user_version
-- is EXACTLY 44, BEFORE any transaction or write -- exit != 0, database
-- untouched (a re-apply refused; a skipped 021~044 chain refused).
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_045(seen_user_version);
CREATE TEMP TRIGGER _guard_045_refuse BEFORE INSERT ON _guard_045 BEGIN
  SELECT RAISE(FAIL, 'migration 045 refused: this database is not at user_version 44 -- either 045 was already applied (>= 45) or the 021~044 chain has not run (< 44). Nothing changed');
END;
INSERT INTO _guard_045
  SELECT user_version FROM pragma_user_version WHERE user_version <> 44;
DROP TABLE temp._guard_045;
BEGIN IMMEDIATE;

-- The two read windows this file restates (038 idiom: DROP-then-CREATE so a
-- stale definition is REPLACED, never silently kept).  Text below is
-- identical to schema.sql's -- the parity suite compares normalized DDL.
-- v_criterion_span / v_criterion are NOT touched: 045 changes no criterion
-- roster, only how the period arm is judged and what both arms project.
DROP VIEW IF EXISTS v_criterion_verdict;
DROP VIEW IF EXISTS v_domain_state;
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
   -- 044: the judgement READS period.  A POINT criterion (period NULL) is
   -- sayable exactly as before: its metric must be FRESH by its own B1
   -- window (a LEVEL decays -- an old reading stops estimating now).  A
   -- PERIOD criterion speaks about an AGGREGATE, so it is sayable only
   -- when that aggregate is constructible: the metric's rows must be
   -- increments (metric_def.accrual = 'accrued' -- summing a LEVEL like
   -- body fat is nonsense the kit refuses to compute, and which honest
   -- aggregate a level takes is a DOMAIN choice, not the kit's) AND the
   -- period's boundary must be DECLARED (period_anchor -- where a week
   -- starts is the owner's clock; no declaration -> silence, fail-CLOSED,
   -- the metric_freshness shape).
   -- 045: point-freshness is NOT a leg of the period shape any more.  An
   -- accrued period sum is a count over a CLOSED set of in-force rows
   -- inside a bounded window -- it does not decay the way a level does,
   -- so the age of the LATEST arrival has no bearing on whether the sum
   -- is correct (it is, exactly).  Under 044 the freshness leg deleted
   -- the sentence precisely when it said the most: a long recording gap
   -- read as silence instead of the true "0 this period" (measured on
   -- live: a 94-day session gap, ~87 of those days verdict-less under any
   -- week-scale cadence).  v_criterion_verdict restates these same legs
   -- on the value side and carries last_logged_at so a recording gap is
   -- SHOWN to the consumer, never judged by a kit-invented threshold.
   WHERE (c.period IS NULL
          AND EXISTS (SELECT 1 FROM fresh_metric f
                       WHERE f.metric = dm.metric))
      OR (c.period IS NOT NULL
          AND EXISTS (SELECT 1 FROM metric_def md
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
  -- choice the kit must not guess -- no row, silence).  045: fresh_point
  -- is deliberately NOT joined here any more.  The sum is exact over a
  -- CLOSED set of in-force rows inside the declared window, however old
  -- the latest arrival is -- point-freshness answers a LEVEL question
  -- ("does the last reading still estimate now?") that an accrued sum
  -- never asks.  044's stated fear (a Monday binge reading as a current
  -- weekly total all week) describes a TRUE statement -- four sessions on
  -- Monday IS the week's total until another arrives -- and the pending
  -- asymmetry below already keeps a partial sum from ever manufacturing
  -- 'under'.  The real residual (the owner keeps doing but stops LOGGING)
  -- is SURFACED, not judged: last_logged_at rides along so the consumer
  -- can tell "0 and the last log was 2 days ago" from "0 and the last log
  -- was 94 days ago"; which of those is bad is the owner's call and no
  -- kit threshold pre-empts it (the 042 discipline).  COALESCE 0: an
  -- empty window honestly means nothing was RECORDED this period --
  -- last_logged_at carries the context that says how far away the record
  -- last spoke.
  SELECT c.source AS source, c.criterion_id AS criterion_id,
         c.metric AS metric, c.lo AS lo, c.hi AS hi,
         c.period AS period, pw.win_start AS period_start,
         COALESCE((SELECT SUM(lv.value) FROM live_value lv
                    WHERE lv.metric = c.metric
                      AND lv.graded_at >= pw.win_start
                      AND lv.graded_at <= strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                  0.0) AS value,
         (SELECT MAX(lv.graded_at) FROM live_value lv
           WHERE lv.metric = c.metric
             AND lv.graded_at <= strftime('%Y-%m-%dT%H:%M:%SZ','now'))
         AS last_logged_at
    FROM v_criterion c
    JOIN metric_def md ON md.token = c.metric AND md.accrual = 'accrued'
    JOIN period_window pw ON pw.period = c.period
   WHERE c.period IS NOT NULL
)
SELECT c.source AS source, c.criterion_id AS criterion_id, c.metric AS metric,
       c.lo AS lo, c.hi AS hi, NULL AS period, NULL AS period_start,
       fp.value AS value,
       -- POINT verdict: symmetric.  A level below the floor is genuinely
       -- under RIGHT NOW -- nothing about a point read is provisional.
       CASE WHEN c.lo IS NOT NULL AND fp.value < c.lo THEN 'under'
            WHEN c.hi IS NOT NULL AND fp.value > c.hi THEN 'over'
            ELSE 'within' END AS verdict,
       -- 045: the stamp behind the judged value -- the same fact the
       -- period arm carries, projected so both arms stay one shape.
       fp.graded_at AS last_logged_at
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
            ELSE 'within' END AS verdict,
       last_logged_at
  FROM period_value;

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 44.
PRAGMA user_version = 45;
COMMIT;
