-- 038_judgment_grade_clamp.sql  (target user_version 38)
--
-- reversible: NO down-migration ships.  This file redefines ONE view and
--   touches nothing else -- no table, no trigger, no index, not one row.
--   Hand-rollback is therefore: re-create v_domain_state from 037's text and
--   PRAGMA user_version = 37.  A FAILED apply needs no rollback at all:
--   single transaction with .bail on, and the stamp is the last statement
--   before COMMIT (023 discipline).
--
-- WHAT THIS FIXES (measured 2026-09-08, live KST estate; shipped in 2.1.0).
-- 037's GRADE FLOOR rewrote a non-canonical observed_at to
-- substr(observed_at,1,10) || 'T00:00:00Z' and its header claimed "residual
-- error at most 24h and always toward STALE".  That claim is FALSE for 9
-- hours a day on any positive-offset estate: at 06:11 KST it is 21:11Z on
-- the PREVIOUS day, so a LOCAL-grade stamp '2026-09-08 06:11:40' floors to
-- '2026-09-08T00:00:00Z' -- an instant 2h49m in the FUTURE.  Age goes
-- negative and passes EVERY freshness window: the floor failed OPEN between
-- 00:00 and 09:00 local, daily.  The same hole admitted any garbage
-- observed_at that merely sorts above 'now' ('n' > '2').  Live census at the
-- measurement: 6,067 date-only rows (all past days, safe) + 25 canonical +
-- 0 local-grade -- the door was open, nothing had walked through it yet.
--
-- THE RULE (owner directive 2026-09-08 06:48).  This estate already runs
-- three clocks: UTC, AGENT_TZ_OFFSET_HOURS in config/agent.conf, and OS
-- localtime.  037's floor invented a FOURTH by guessing the offset is 0.
-- This file does not invent a fifth and hardcodes no offset: a view cannot
-- read the instance config, so it does not guess AT ALL --
--   FUTURE CLAMP: the graded instant must also be <= 'now'.  A stamp whose
--   grade is ambiguous enough to floor into the future is NOT fresh
--   evidence.  Fail-CLOSED: ambiguity reads STALE, never fresh.
-- The write side moves in the same release: kit_life's meal write resolves
-- the configured offset (agent_conf, Python CAN read the config) and stores
-- the local day start as a canonical UTC-Z instant; an UNDECLARED offset
-- does not fall back to 0 -- the date-only grade is kept byte-identical and
-- this clamp reads its ambiguity fail-closed.
--
-- COST, STATED PLAINLY: on an instance whose offset stays undeclared, a
-- date-only stamp carrying a local day ahead of the UTC day (a meal logged
-- 00:00-09:00 local) reads stale until the UTC day catches up -- at most
-- the same 9 hours, now in the STALE direction, which is the direction 037
-- promised.  The 6,067 live date-only rows all carry past days and keep
-- working unchanged.  No backfill: the stored strings were never wrong,
-- only the reader's sign was (relmod's own rule: fix readers, not data).
--
-- PARITY.  schema.sql (fresh mint) carries the identical view text and
-- stamps 38, pinned by test_mig037_judgment_layer.py parity.
--
-- GUARD.  Both-sided, 037 discipline: refused unless user_version is
-- EXACTLY 37, BEFORE any transaction or write -- exit != 0, database
-- untouched.  DROP-then-CREATE so a stale definition is replaced, never
-- silently kept; views hold no data, so dropping one costs nothing.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_038(seen_user_version);
CREATE TEMP TRIGGER _guard_038_refuse BEFORE INSERT ON _guard_038 BEGIN
  SELECT RAISE(FAIL, 'migration 038 refused: this database is not at user_version 37 -- either 038 was already applied (>= 38) or the 021~037 chain has not run (< 37). Nothing changed');
END;
INSERT INTO _guard_038
  SELECT user_version FROM pragma_user_version WHERE user_version <> 37;
DROP TABLE temp._guard_038;
BEGIN IMMEDIATE;

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
    JOIN fresh_metric f ON f.metric = dm.metric
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

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 37.
PRAGMA user_version = 38;
COMMIT;
