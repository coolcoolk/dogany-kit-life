-- 037_judgment_layer.sql  (target user_version 37)
--
-- reversible: NO down-migration ships.  This file is ADDITIVE: it creates ten
--   tables, three views and four triggers, consumes nothing, drops nothing and
--   rewrites no pre-existing row.  It DOES insert rows -- the domain roster and
--   the doors that arrival evidence proves (see SEEDING below) -- so "purely
--   additive" now means "adds objects and its own rows", never "touches a row
--   another lane owns".  What makes it recoverable:
--   (1) nothing is consumed, so there is nothing to park -- the _l36_* prefix
--       this migration would have used for frozen sources stays UNUSED, and
--       every pre-037 row is byte-identical afterwards;
--   (2) the objects it adds are unreferenced by any live code path, so a
--       hand-rollback is DROP VIEW v_domain_state / v_criterion /
--       v_criterion_span + DROP TRIGGER on the four + DROP TABLE on the ten
--       new tables + PRAGMA user_version = 36;
--   (3) a FAILED apply needs no rollback at all -- single transaction with
--       .bail on, and the stamp is the last statement before COMMIT.
--   Re-application is refused outright, and so is application to a database
--   that is not at 36 (the guard is now BOTH-sided -- see GUARD below).
--
-- WHAT THIS IS.  The kit could not say what it means to MANAGE a domain.  The
-- owner's definition is an ability -- 관리 = 되비출 수 있는 상태, "a state you
-- can reflect back" -- and the previous design judged that ability by asking
-- whether a goal ROW existed.  Existence is not ability: a finished goal, an
-- unconfirmed goal and a goal the user parked all have rows, and a RULE (which
-- the owner tracks daily) has no goal row at all.
--
-- The judgement is SAYABILITY: can a reflection sentence actually be built for
-- this domain right now?  "You set 150, lately you are at 110" needs exactly
-- three ingredients --
--     criterion   a comparison basis      -> v_criterion (goals AND rules)
--     value       an ACTUAL measure        -> measure rows, basis='actual'
--     freshness   "lately" must be true    -> observed_at is inside this
--                                             metric's declared arrival window
-- Miss one and no sentence exists.  So:  no door -> 'unknown';  values arrive
-- but nothing to compare against -> 'collecting';  criterion + fresh value ->
-- 'managing'.  Goal SHAPE (floor / ceiling / band / periodic) needs no special
-- case: if a sentence comes out it is managing, if not it is not.
--   spec: the 2026-09-04 sayability redesign SPEC
--   regrill: the 2026-09-07 judgment-layer regrill report
--
-- ── FRESHNESS IS NOT valid_until (regrill B1) ─────────────────────────────
-- The first cut of this file read freshness off `measure.valid_until` and
-- called a NULL there "fresh".  That is the wrong column with the wrong sign.
-- `valid_until` is a SUCCESSION stamp -- the only writer in this estate is the
-- travel lane, which stamps it when a newer answer replaces an older one, and
-- schema.sql's own comment on the column reads "없으면 stale 판정 대상"
-- (absent => a CANDIDATE for staleness), the exact opposite of "absent =>
-- fresh forever".  Measured on the live shared database: 6,060 of 6,060
-- measure rows carry valid_until NULL, so the old test was a constant TRUE and
-- a value observed in 2019 read as "요즘".
--
-- The two questions are now separate columns and separate tests:
--     IN FORCE   valid_until IS NULL OR valid_until > now   (has a successor
--                replaced or retracted this row?)          -- succession
--     FRESH      observed_at is newer than
--                now - metric_freshness.stale_after_hours   -- age
-- Both must hold.  A retracted value must not be spoken from even if it is
-- recent, and a recent-looking row that is actually two years old must not be
-- spoken from even if nothing superseded it.
--
-- HOW STALE IS STALE IS THE OWNER'S CALL, SO IT IS DATA.  `metric_freshness`
-- holds one row per metric: stale_after_hours, plus declared_by so a number
-- nobody chose is visibly a number nobody chose.  THIS MIGRATION SEEDS NO
-- INTERVAL AT ALL -- inventing "7 days" for 체지방 would be the same class of
-- error as inventing a face_when token.  The structural default is therefore
-- FAIL-CLOSED: a metric with no declared interval is never fresh, so it can
-- never make a domain read 'managing' by accident.  Silence, not a lie.
--
-- TIME-STRING GRADE (regrill B6, review F1).  Every 'now' this file emits is
-- strftime('%Y-%m-%dT%H:%M:%SZ','now').  The stored side is now enforced too,
-- which the first cut left open:
--   * goal_target / commitment valid_from|valid_until carry a GLOB CHECK that
--     admits ONLY the canonical instant.  These are new columns on new tables,
--     so the CHECK ships inline and costs nothing.
--   * measure.valid_until gets the same rule through a TRIGGER -- `measure`
--     predates this migration and a CHECK would need a table rebuild.  Safe to
--     bite: all 6,060 live rows are NULL and the single writer (kit_life
--     _travel_v32_set) already writes now_utc().
--   * measure.observed_at is NOT constrained, because it legitimately carries
--     two grades today (measured live: 6,035 date-only '2026-09-07' from the
--     nutrition lane, 25 canonical instants from travel).  A RAISE here would
--     break a working lane.  Instead the view FLOORS every non-canonical grade
--     to the start of its own UTC day before comparing (the GRADE FLOOR in
--     v_domain_state).  That makes the residual error at most 24h and always in
--     the FAIL-CLOSED direction: a local-time or date-only stamp can read as
--     older than it is, never as newer.  The first cut had the opposite sign --
--     a local stamp in KST read up to 9h fail-OPEN.
--
-- ── PLANNED IS NOT ACTUAL (regrill B2) ───────────────────────────────────
-- The value test now carries `m.basis = 'actual'`, the filter the sibling view
-- v_place_link_measure has had since 036.  Without it a row saying "on
-- 2026-12-31 I intend to train 4 times" counted as a value and the domain read
-- 'managing' off a thing that has not happened.  A plan is not a reflection.
--
-- ── A CRITERION HAS A LOWER BOUND AND AN AS-OF (regrill B3) ──────────────
-- The first cut read valid_until and ignored valid_from, so a target whose
-- window OPENS in 2027 was already a criterion today -- and §5's succession
-- discipline (stamp the old row, insert the successor) therefore produced TWO
-- live criteria for one metric.  The window is now closed at both ends.
--
-- The read path splits in two, one rule stated once:
--   v_criterion_span  every criterion with its window and its succession
--                     columns (version / valid_from / valid_until /
--                     supersedes) EXPOSED, and no clock applied.  This is the
--                     AS-OF read path: a consumer asking what was true last
--                     Tuesday selects from here with its own instant --
--                       WHERE (valid_from  IS NULL OR valid_from  <= :asof)
--                         AND (valid_until IS NULL OR valid_until >  :asof)
--                     which is exactly what v_criterion does with 'now'.
--   v_criterion       that same span filtered to now.  ONE window onto what
--                     counts as a comparison basis right now.
-- HONEST LIMIT: the as-of read is exact for the validity window and only
-- approximate for rung_state / focus_state / stage.lifecycle / status, which
-- carry no history in this schema.  Reconstructing last Tuesday's verdict is
-- therefore exact on the NUMBER and current-state on the LIFECYCLE.  Saying so
-- here is cheaper than a consumer discovering it.
--
-- ── RULES GET THE SAME TIME AS GOALS (regrill B4) ────────────────────────
-- 2 of the 3 criteria the owner actually tracks today are rules
-- (kcal_intake, weekly_sessions on the live ledger_goal), and the first cut
-- gave `commitment` no validity columns at all -- so revising a rule was BY
-- DEFINITION an in-place UPDATE, and raising today's ceiling silently turned
-- last Sunday's verdict from OVER to ok.  `commitment` now carries the same
-- version / valid_from / valid_until / supersedes quad, and v_criterion
-- filters BOTH arms on it.
--
-- And the discipline is ENFORCED, not merely documented: trg_*_no_inplace_edit
-- refuses an UPDATE that changes the comparison numbers (metric / lo / hi /
-- target_low / target_high) on either arm.  Stamping valid_until and inserting
-- a successor is still one UPDATE plus one INSERT and stays allowed; rung_state
-- and status transitions stay allowed.  The cost, accepted: any tool that
-- "just edits the target" must be rewritten to supersede, and a test fixture
-- can no longer flip a criterion's number in place -- it deletes and re-inserts.
--
-- ── domain.status HAS A ROSTER (regrill B7 / grill C-7) ──────────────────
-- v_domain_state filters `d.status = 'active'`, so one typo ('actvie') deleted
-- a whole domain from the answer.  That is a fourth, unnamed state -- ABSENCE,
-- which is worse than silence because silence is visible.  CHECK closes it.
--
-- ── S1: THE HOME OF A VALUE IS `measure`, FULL STOP ──────────────────────
-- Owner decision, taken here rather than deferred: the judgement reads values
-- from `measure` and from nowhere else.  There is no per-metric value-source
-- registry and none is coming.  THE COST, MEASURED, STATED PLAINLY:
--   * metric_log's 26 tokens (weight_kg / body_fat_pct / skeletal_muscle_kg …)
--     have 0 measure rows.  Body composition is therefore NOT sayable, and the
--     domain that owns it reads 'collecting' until that lane writes `measure`.
--   * workouts (79 rows) and spend_entry (1,603 rows) likewise: training volume
--     and spend are values in this database but not values to this judgement.
--   * the three criteria on the live ledger_goal (body_fat_pct, kcal_intake,
--     weekly_sessions) have 0 rows in metric_def and 0 in measure, so even
--     after the ledger_goal transfer no domain flips to 'managing' until the
--     owning module registers the metric and writes measure rows.
-- The alternative (a registry saying "this metric's values live in that table")
-- buys a faster 'managing' at the price of N value shapes, N freshness idioms
-- and N places for the sign to invert again.  One home, one shape, one bug.
--
-- ── SEEDING (SPEC §6.1) ──────────────────────────────────────────────────
-- The first cut inserted zero rows, so the layer shipped with nine empty seats
-- and the view returned NO rows at all -- not even 'unknown'.  Absence, again.
-- This file seeds exactly three things, and only what the data proves:
--   domain        the four v3.2 seed tokens, unconditionally.  A domain with no
--                 door reads 'unknown' = "I do not know whether values reach me
--                 here", which is a true sentence and a visible one.  Seeding
--                 only the domains that HAVE evidence would hide the ones that
--                 do not, and hiding is the failure mode this fixes.
--   inflow        one door per lane whose rows ARRIVED on more than one
--                 distinct day.  Arrival = created_at, the moment the row was
--                 actually written -- NEVER the event date the row claims
--                 (SPEC §6.1a).  Measured live: spend_entry's 1,603 rows all
--                 arrived inside 2 minutes 22 seconds on 2026-08-20, one single
--                 batch import, and by `date` they would look like a year of
--                 daily arrivals.  A batch import proves a machine ran once; a
--                 door is a claim about what keeps arriving.  So the ledger
--                 domain correctly gets NO door and reads 'unknown'.
--                 NOTE ON N: SPEC §12-1 leaves the "arrived within N days"
--                 window open and this file does NOT close it.  It does not
--                 need to: §6.2 forbids auto-CLOSING a door for silence (two
--                 weeks of travel must not retire a door), so recency is never
--                 what opens or closes one.  The only false positive to exclude
--                 is the batch import, and that is a question of SHAPE (one
--                 arrival day) not of recency.  N stays open.
--                 kind / face_when / face_what / face_how stay NULL and
--                 declared_by='migration' marks the declaration INCOMPLETE
--                 (SPEC §6.1f / grill G-1): a seeder that guessed a face_when
--                 token would freeze the undecided vocabulary as a side effect.
--   domain_metric each active metric_def is attached to the domain its own
--                 owner_module names.  This reads a fact already stored
--                 (metric_def.owner_module), it does not invent a mapping.
--                 Body composition (SPEC §6.1d, X-8) has no such fact and is
--                 therefore NOT attached -- that mapping stays the owner's.
-- Nothing about a GOAL is seeded: the ledger_goal transfer stays out (it is the
-- store kit_life.py still reads and writes), and stage tokens are the owner's
-- life-phase vocabulary, so a migration that minted one would be freezing a
-- guess in exactly the way §6.1f forbids.  Consequence, stated up front: on the
-- landing day no domain can read 'managing'.  That is SPEC §6.1c ("문을 심으면
-- 모름 -> 쌓는 중; 관리 중 은 그 뒤"), and it is the truth.
--
-- FAIL-CLOSED (grill C-5).  v_criterion_span INNER JOINs stage, so a goal with
-- no stage_token is NOT a criterion.  Not reflecting is silence; reflecting
-- wrongly is a lie.
--
-- NAME (SPEC §9).  The door table is `inflow`, not `intake`: in THIS database
-- `intake` already means calorie intake (metric_axis.axis_key='intake' 섭취,
-- and block_delta component='intake'), and schema.sql itself cites that word
-- collision as a past bug.
--
-- PARITY.  schema.sql (fresh mint) carries the identical statements, so a
-- freshly minted database and a migrated one have identical normalized
-- sqlite_master -- pinned by test_mig037_judgment_layer.py.
--
-- GUARD.  Two agents share one physical database, so the second run must not be
-- a second application.  The guard refuses the whole file unless user_version
-- is EXACTLY 36, BEFORE any transaction or write: exit != 0, database
-- untouched.  The first cut only refused >= 37, so a database that had skipped
-- 021~036 took the 37 stamp with exit 0 and then died at query time with "no
-- such table: measure" (regrill S2).  Both directions are refused now.  The
-- views are DROP-then-CREATE rather than CREATE IF NOT EXISTS, so a name that
-- already exists with a STALE definition is replaced instead of silently kept
-- (regrill S3); views hold no data, so dropping one costs nothing.
.bail on
.timeout 5000
-- GUARD: refuse unless this database is exactly at 36.  RAISE needs a trigger
-- body; the INSERT selects a row only when user_version <> 36, so at 36 this
-- whole block is a no-op.  Temp objects die with the connection.
CREATE TEMP TABLE _guard_037(seen_user_version);
CREATE TEMP TRIGGER _guard_037_refuse BEFORE INSERT ON _guard_037 BEGIN
  SELECT RAISE(FAIL, 'migration 037 refused: this database is not at user_version 36 -- either 037 was already applied (>= 37) or the 021~036 chain has not run (< 36). Nothing changed');
END;
INSERT INTO _guard_037
  SELECT user_version FROM pragma_user_version WHERE user_version <> 36;
DROP TABLE temp._guard_037;
BEGIN IMMEDIATE;

-- ── Judgment layer -- migration 037 (user_version 37) ─────────────────────
-- 1. Seats.  stage before goal (FK), domain before inflow/domain_metric,
--    goal before goal_target/commitment/goal_thesis, thesis before goal_thesis,
--    metric_freshness after nothing (it FKs metric_def, which predates 037).
CREATE TABLE IF NOT EXISTS stage (
  token       TEXT PRIMARY KEY,
  lifecycle   TEXT NOT NULL CHECK (lifecycle IN ('open','held','closed','void')),
  sort_rank   INTEGER,
  origin      TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
CREATE TABLE IF NOT EXISTS domain (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  token        TEXT NOT NULL UNIQUE,
  module_token TEXT,
  note         TEXT,
  origin       TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user')),
  -- regrill B7 / grill C-7: without this roster one typo ('actvie') drops the
  -- whole domain out of v_domain_state -- an unnamed fourth state, ABSENCE,
  -- which is worse than silence because silence is visible.
  status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS inflow (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  domain_id    INTEGER NOT NULL REFERENCES domain(id) ON DELETE CASCADE,
  kind         TEXT CHECK (kind IN ('auto','utterance')),
  module_token TEXT,
  face_when    TEXT,
  face_what    TEXT,
  face_how     TEXT,
  status       TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active','paused','retired')),
  declared_by  TEXT NOT NULL DEFAULT 'module'
               CHECK (declared_by IN ('module','user','migration')),
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_inflow_domain ON inflow(domain_id, status);
CREATE TABLE IF NOT EXISTS goal (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  title        TEXT NOT NULL,
  horizon_kind TEXT NOT NULL
               CHECK (horizon_kind IN ('thesis_bound','bounded','periodic')),
  horizon_date TEXT,
  stage_token  TEXT REFERENCES stage(token),
  focus_state  TEXT NOT NULL DEFAULT 'backlog'
               CHECK (focus_state IN ('focus','backlog','dormant')),
  priority_rank INTEGER,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS goal_target (
  id           INTEGER PRIMARY KEY,
  goal_id      INTEGER NOT NULL REFERENCES goal(id) ON DELETE CASCADE,
  metric       TEXT    NOT NULL REFERENCES metric_def(token),
  baseline_value REAL,
  baseline_as_of TEXT,
  target_low   REAL,
  target_high  REAL,
  version      INTEGER NOT NULL DEFAULT 1,
  valid_from   TEXT,
  valid_until  TEXT,
  supersedes   INTEGER REFERENCES goal_target(id),
  applies_when TEXT,
  rung_state   TEXT NOT NULL DEFAULT 'intent'
               CHECK (rung_state IN ('intent','baseline_pending','target_proposed',
                                     'confirmed','tracking','met','retired')),
  CHECK (target_low IS NOT NULL OR target_high IS NOT NULL),
  CHECK (target_low IS NULL OR target_high IS NULL OR target_low <= target_high),
  -- regrill B6: the grade is enforced by the TABLE, not by a comment.  A
  -- local-time stamp compared against a '...T...Z' now reads as live until
  -- local midnight (' ' 0x20 < 'T' 0x54) -- up to 9h fail-OPEN in KST.
  CHECK (valid_from IS NULL OR valid_from GLOB
         '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
  CHECK (valid_until IS NULL OR valid_until GLOB
         '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
  CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from < valid_until)
);
CREATE INDEX IF NOT EXISTS ix_goal_target_goal ON goal_target(goal_id);
CREATE TABLE IF NOT EXISTS commitment (
  id          INTEGER PRIMARY KEY,
  ulid        TEXT NOT NULL UNIQUE,
  goal_id     INTEGER REFERENCES goal(id) ON DELETE SET NULL,
  title       TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('keep','limit','window')),
  metric      TEXT REFERENCES metric_def(token),
  lo          REAL,
  hi          REAL,
  period      TEXT CHECK (period IN ('day','week','month','quarter','year')),
  enforcement TEXT NOT NULL DEFAULT 'inform'
              CHECK (enforcement IN ('block','hold','inform')),
  spec        TEXT,
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active','paused','retired')),
  -- regrill B4: the rule arm gets the same four columns the goal arm has.  2 of
  -- the 3 criteria the owner tracks today are rules, and without these a rule
  -- revision was BY DEFINITION an in-place UPDATE that rewrote last week's
  -- verdict (measured: OVER -> ok on an already-measured day).
  version     INTEGER NOT NULL DEFAULT 1,
  valid_from  TEXT,
  valid_until TEXT,
  supersedes  INTEGER REFERENCES commitment(id),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (valid_from IS NULL OR valid_from GLOB
         '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
  CHECK (valid_until IS NULL OR valid_until GLOB
         '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
  CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from < valid_until)
);
CREATE TABLE IF NOT EXISTS domain_metric (
  domain_id   INTEGER NOT NULL REFERENCES domain(id) ON DELETE CASCADE,
  metric      TEXT    NOT NULL REFERENCES metric_def(token) ON DELETE CASCADE,
  PRIMARY KEY (domain_id, metric)
);
-- regrill B1.  "How stale is stale" is a per-metric PRODUCT judgement (체지방
-- arrives every ~9.8 days, a meal every day), so it is DATA, not a constant in
-- a view.  declared_by keeps a number nobody chose visibly a number nobody
-- chose.  A metric with NO row here is never fresh -- fail-CLOSED, so an
-- undeclared interval produces silence, never a false '관리 중'.
CREATE TABLE IF NOT EXISTS metric_freshness (
  metric            TEXT PRIMARY KEY REFERENCES metric_def(token) ON DELETE CASCADE,
  stale_after_hours REAL NOT NULL CHECK (stale_after_hours > 0),
  declared_by       TEXT NOT NULL DEFAULT 'module'
                    CHECK (declared_by IN ('module','user','migration')),
  note              TEXT,
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS thesis (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  text         TEXT NOT NULL,
  stated_on    TEXT,
  emerged_from INTEGER REFERENCES event(id),
  status       TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active','pivoted','retired')),
  superseded_by INTEGER REFERENCES thesis(id),
  owner        TEXT NOT NULL DEFAULT 'user' CHECK (owner = 'user'),
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS goal_thesis (
  goal_id   INTEGER NOT NULL REFERENCES goal(id)    ON DELETE CASCADE,
  thesis_id INTEGER NOT NULL REFERENCES thesis(id)  ON DELETE CASCADE,
  PRIMARY KEY (goal_id, thesis_id)
);
-- 2. The succession discipline, ENFORCED (regrill B4).  A criterion in force is
--    never edited: the comparison numbers are immutable, so a revision must
--    stamp valid_until on this row and INSERT a successor.  Stamping is an
--    UPDATE of valid_until (not listed below, so it passes), and rung_state /
--    status transitions pass too -- only the NUMBERS are frozen.
CREATE TRIGGER IF NOT EXISTS trg_goal_target_no_inplace_edit
BEFORE UPDATE OF metric, target_low, target_high ON goal_target
WHEN NEW.metric     IS NOT OLD.metric
  OR NEW.target_low IS NOT OLD.target_low
  OR NEW.target_high IS NOT OLD.target_high
BEGIN
  SELECT RAISE(ABORT, 'goal_target: a criterion in force is never edited in place -- stamp valid_until on this row and INSERT a successor (version+1, supersedes = this id)');
END;
CREATE TRIGGER IF NOT EXISTS trg_commitment_no_inplace_edit
BEFORE UPDATE OF metric, lo, hi ON commitment
WHEN NEW.metric IS NOT OLD.metric
  OR NEW.lo     IS NOT OLD.lo
  OR NEW.hi     IS NOT OLD.hi
BEGIN
  SELECT RAISE(ABORT, 'commitment: a rule in force is never edited in place -- stamp valid_until on this row and INSERT a successor (version+1, supersedes = this id)');
END;
-- 3. Stored time-string grade on the succession stamp (regrill B6).  measure
--    predates 037, so a CHECK would need a table rebuild; a trigger enforces the
--    same rule additively.  Safe: every live row is NULL here and the single
--    writer (kit_life _travel_v32_set) already emits now_utc().  observed_at is
--    deliberately NOT constrained -- it legitimately carries two grades today --
--    and the view floors the non-canonical grade instead (fail-CLOSED).
CREATE TRIGGER IF NOT EXISTS trg_measure_valid_until_grade_ins
BEFORE INSERT ON measure
WHEN NEW.valid_until IS NOT NULL
 AND NEW.valid_until NOT GLOB
     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
BEGIN
  SELECT RAISE(ABORT, 'measure.valid_until must be a canonical UTC-Z instant (YYYY-MM-DDTHH:MM:SSZ): a local-time stamp compares as still-in-force until local midnight');
END;
CREATE TRIGGER IF NOT EXISTS trg_measure_valid_until_grade_upd
BEFORE UPDATE OF valid_until ON measure
WHEN NEW.valid_until IS NOT NULL
 AND NEW.valid_until NOT GLOB
     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
BEGIN
  SELECT RAISE(ABORT, 'measure.valid_until must be a canonical UTC-Z instant (YYYY-MM-DDTHH:MM:SSZ): a local-time stamp compares as still-in-force until local midnight');
END;
-- 4. The three read windows.  v_criterion_span states every non-time rule ONCE
--    and applies no clock (the AS-OF path); v_criterion is that span at 'now';
--    v_domain_state judges the three states by asking whether a sentence can be
--    built.  DROP-then-CREATE, not IF NOT EXISTS: a name that already exists
--    with a stale definition must be REPLACED, not silently kept (regrill S3).
--    Views hold no data, so dropping one costs nothing.
DROP VIEW IF EXISTS v_domain_state;
DROP VIEW IF EXISTS v_criterion;
DROP VIEW IF EXISTS v_criterion_span;
CREATE VIEW v_criterion_span AS
  SELECT 'goal' AS source, gt.id AS criterion_id, gt.metric AS metric,
         gt.target_low AS lo, gt.target_high AS hi, gt.goal_id AS goal_id,
         gt.version AS version, gt.valid_from AS valid_from,
         gt.valid_until AS valid_until, gt.supersedes AS supersedes
    FROM goal_target gt
    JOIN goal g   ON g.id = gt.goal_id
    JOIN stage s  ON s.token = g.stage_token
   WHERE gt.rung_state = 'tracking'
     AND g.focus_state <> 'dormant'
     AND s.lifecycle = 'open'
     AND (gt.target_low IS NOT NULL OR gt.target_high IS NOT NULL)
  UNION ALL
  SELECT 'rule', c.id, c.metric, c.lo, c.hi, c.goal_id,
         c.version, c.valid_from, c.valid_until, c.supersedes
    FROM commitment c
   WHERE c.status = 'active'
     AND c.metric IS NOT NULL
     AND (c.lo IS NOT NULL OR c.hi IS NOT NULL);
CREATE VIEW v_criterion AS
  SELECT source, criterion_id, metric, lo, hi, goal_id,
         version, valid_from, valid_until, supersedes
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
  -- GRADE FLOOR: observed_at carries two grades in this estate (canonical
  -- instants from travel, date-only stamps from nutrition).  Anything that is
  -- not a canonical instant is floored to 00:00:00Z of its own date, so the
  -- residual error is at most 24h and always toward STALE, never toward fresh.
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
-- 5. Seeding (SPEC §6.1).  Three inserts, each reading a fact this database
--    already holds.  Nothing about a goal, a stage, a face token or a freshness
--    interval is seeded -- those are the owner's, and a migration that minted
--    one would freeze a guess (SPEC §6.1f, §12-1).
-- 5a. The domain roster, unconditionally.  A domain with no door reads
--     'unknown' = "I do not know whether values reach me here" -- true, and
--     VISIBLE.  Seeding only the domains that have evidence would hide the ones
--     that do not, which is the absence this cut exists to end.
INSERT OR IGNORE INTO domain(ulid, token, origin, status, note) VALUES
  ('SEED-DOMAIN-TRAINING','training','seed','active','v3.2 seed roster (SPEC 6.1d)'),
  ('SEED-DOMAIN-DIET',    'diet',    'seed','active','v3.2 seed roster (SPEC 6.1d)'),
  ('SEED-DOMAIN-LEDGER',  'ledger',  'seed','active','v3.2 seed roster (SPEC 6.1d)'),
  ('SEED-DOMAIN-LIFE',    'life',    'seed','active','v3.2 seed roster (SPEC 6.1d)');
-- 5b. Doors, from ARRIVAL evidence only.  Arrival = created_at (when the row was
--     actually written), NEVER the event date the row claims (SPEC §6.1a): by
--     `date`, spend_entry looks like a year of daily arrivals; by created_at it
--     is 1,603 rows in 2 minutes 22 seconds on one day -- a batch import, which
--     proves a machine ran once, not that a door is open.  The discriminator is
--     therefore SHAPE (more than one arrival day), not recency -- so the N-day
--     window of SPEC §12-1 stays open and unguessed, and §6.2 (a door is closed
--     by a person, never by silence) keeps recency out of this decision.
--     kind / face_* stay NULL; declared_by='migration' marks the declaration
--     INCOMPLETE, to be completed by the owning module on first write.
--  b1. block rows, grouped by the module that wrote them (a stored fact).
INSERT OR IGNORE INTO inflow(ulid, domain_id, module_token, declared_by, status)
SELECT 'SEED-INFLOW-BLOCK-' || d.token, d.id, b.source_module, 'migration', 'active'
  FROM (SELECT source_module,
               COUNT(DISTINCT substr(created_at, 1, 10)) AS arrival_days
          FROM block
         WHERE created_at IS NOT NULL AND source_module IS NOT NULL
         GROUP BY source_module) b
  JOIN domain d ON d.token = b.source_module
 WHERE b.arrival_days > 1;
--  b2. workouts -> training.  Mapped by SOURCE TABLE, never by tag string
--      (SPEC §6.1d: the live `health` tag splits across two domains).
--      module_token stays NULL -- which module owns this lane is not a fact
--      this file holds, and inventing one would deepen grill G-2/S4.
INSERT OR IGNORE INTO inflow(ulid, domain_id, declared_by, status)
SELECT 'SEED-INFLOW-WORKOUTS-' || d.token, d.id, 'migration', 'active'
  FROM domain d
 WHERE d.token = 'training'
   AND (SELECT COUNT(DISTINCT substr(created_at, 1, 10))
          FROM workouts WHERE created_at IS NOT NULL) > 1;
--  b3. spend_entry -> ledger.  Written down deliberately and expected to insert
--      NOTHING on this estate: a lane that is offered to the rule and produces
--      no door is what proves the rule discriminates.  If spend ever starts
--      arriving conversationally, this same statement opens the door on the
--      next mint -- no code change.
INSERT OR IGNORE INTO inflow(ulid, domain_id, declared_by, status)
SELECT 'SEED-INFLOW-SPEND-' || d.token, d.id, 'migration', 'active'
  FROM domain d
 WHERE d.token = 'ledger'
   AND (SELECT COUNT(DISTINCT substr(created_at, 1, 10))
          FROM spend_entry WHERE created_at IS NOT NULL) > 1;
-- 5c. metric -> domain, read off metric_def.owner_module, a fact already
--     stored.  Body composition (metric_log's 26 tokens) has NO such fact --
--     SPEC §6.1d leaves that mapping to the owner -- so it is not attached, and
--     it holds 0 measure rows anyway (S1).
INSERT OR IGNORE INTO domain_metric(domain_id, metric)
SELECT d.id, md.token
  FROM metric_def md
  JOIN domain d ON d.token = md.owner_module
 WHERE md.status = 'active';

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 36 (no half-migrated laundering -- 023 discipline).
PRAGMA user_version = 37;
COMMIT;
