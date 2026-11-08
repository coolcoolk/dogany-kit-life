-- 033_metric_axis.sql  (target user_version 33)
--
-- reversible: yes, and losslessly.
--   Rollback = DROP TABLE metric_axis;  (goal_metric_source.axis_key may be
--   left in place -- it is NULLable and no pre-033 reader selects it, so the
--   extra column is inert. SQLite >= 3.35 also supports
--   ALTER TABLE goal_metric_source DROP COLUMN axis_key.)
--   No row of goal_metric_source is inserted-over, deleted or re-keyed; the
--   ONE new row is a metric that did not exist before. No ledger_goal row is
--   touched, no metric_log row is touched.
--
-- ==========================================================================
-- WHAT. The AXIS taxonomy stops being a Python tuple and becomes DATA.
--
-- Before this migration the axis list lived in `_METRIC_AXIS`, a hardcoded
-- tuple in database/kit_life.py. The neighbouring comment correctly claimed
-- that a newly registered `e1rm_<lift>` needs "no code edit" -- true for a
-- METRIC, false for an AXIS. Adding an axis meant editing a tuple, which is
-- the same pathology this codebase keeps re-hitting: a thing the owner talks
-- about is representable only by a developer, so it never lands.
--
-- After this migration:
--   metric_axis                     -- the axis list, one row per axis
--   goal_metric_source.axis_key     -- which axis a metric contributes to
-- and `_metric_axis()` READS both. Prefix-family behaviour is preserved: the
-- `e1rm_*` / `max_reps_*` rows carry the axis for every token expanded from
-- them, so a lift this product has never heard of still lands on an axis
-- with no code edit and no new row.
--
-- ==========================================================================
-- WHY THE AXES ARE THESE AXES. The owner stated them verbatim (2026-08-24):
--
--   "보디빌더의 크기와 선명도, 크로스핏터의 수행능력(퍼포먼스, 지구력)과
--    몸의 연결(기능성)"
--
--   size         크기       skeletal muscle mass       <- bodybuilder half
--   definition   선명도     body fat %                 <- bodybuilder half
--   performance  수행능력   performance / endurance    <- crossfitter half
--   coordination 몸의 연결  coordination / functionality <- crossfitter half
--
-- The labels stay Korean on purpose: they are USER-FACING strings the owner
-- himself chose. The keys are ASCII, following the 032 convention (machine
-- key in the key column, human label beside it).
--
-- ==========================================================================
-- WHY 체성분 IS SPLIT. Body fat % and skeletal muscle mass shared ONE axis
-- ("체성분"). Sharing an axis means a goal on either one marks the whole axis
-- covered, so an untracked skeletal-muscle target reads as handled. That is
-- how a stated skeletal-muscle target became "유지·소폭 증가" and then
-- vanished, and how a functional goal ended up being judged on a hypertrophy
-- band. They are now two axes, exactly as the owner names them.
--
-- ==========================================================================
-- WHY 몸의 연결 SHIPS WITH ZERO METRICS. There is no honest measure for
-- coordination in this database yet, and the rule for counting one is a
-- SEPARATE, unstarted piece of work. Inventing a proxy here would repeat the
-- failure this whole migration exists to stop: the owner's word gets flattened
-- into whatever number happened to be nearby.
--
-- An axis with zero metrics is therefore a FIRST-CLASS row, and readers MUST
-- render it as "measurable: not yet" -- never as covered, and never by
-- omitting it. An empty axis that silently disappears from the readout is
-- precisely how this gap survived for months: the old code derived the axis
-- list FROM the metric list, so an axis with no metric could not be named.
--
-- ==========================================================================
-- VOLUME IS THE SIZE AXIS METRIC, NOT A GENERAL GOODNESS SCORE.
-- Volume exists for size; every other contribution has its own
-- measures.  Volume (sets x load) measures SIZE and nothing else.
-- Reading it as a general quality score is what made calf / upper-trap work
-- entered for FUNCTIONAL reasons get scored against hypertrophy volume bands
-- and reported as a shortfall. Each other axis owns its own value; volume
-- must never stand in for performance, definition or coordination.
-- (Volume itself is not registered as a goal metric here -- it is a program
-- readout, not a target the owner sets -- but its AXIS is settled: size.)
-- ==========================================================================

BEGIN;

-- A1: the axis taxonomy, as data.
CREATE TABLE metric_axis (
    axis_key      TEXT PRIMARY KEY,          -- stable ASCII machine key
    axis_rank     INTEGER NOT NULL,          -- offer order; also the readout order
    label         TEXT NOT NULL,             -- user-facing label (ko, owner's own word)
    domain        TEXT,                      -- health|spend|.. NULL = every domain
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    note          TEXT,                      -- why this axis exists, in one line
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (owning_agent IN ('ag','warg','user'))
);

-- A2: the owner's four physique axes lead the order, in the order he said
-- them. Rank fixes the offer order, and the offer order is what makes a
-- missing axis visible.
INSERT INTO metric_axis (axis_key, axis_rank, label, domain, owning_agent, note, created_at) VALUES
 ('size',         1, '크기',      'health', 'warg',
  'Skeletal muscle mass. Volume (sets x load) is the training input for THIS axis and no other.',
  '2026-08-24T00:00:00Z'),
 ('definition',   2, '선명도',    'health', 'warg',
  'Body fat %. Split out of the former 체성분 axis so a cut goal cannot mark mass as covered.',
  '2026-08-24T00:00:00Z'),
 ('performance',  3, '수행능력',  'health', 'warg',
  'Output: strength and endurance. Was called 근력; renamed to the owner''s word, which is wider.',
  '2026-08-24T00:00:00Z'),
 ('coordination', 4, '몸의 연결', 'health', 'warg',
  'Coordination / functionality. NO metric yet by design -- reads "measurable: not yet", never covered.',
  '2026-08-24T00:00:00Z'),
 ('training_load',5, '수행량',    'health', 'warg',
  'How much training actually happened. An input axis, not one of the four picture axes.',
  '2026-08-24T00:00:00Z'),
 ('intake',       6, '섭취',      'health', 'warg',
  'What goes in: calories, protein.',
  '2026-08-24T00:00:00Z'),
 ('spend',        7, '지출',      'spend',  'ag',
  'Money. Different domain and different owning agent; scoped out of a health consult.',
  '2026-08-24T00:00:00Z');

-- A3: metrics point at an axis. NULLable on purpose -- an unregistered or
-- legacy token resolves to the 기타 fallback in code rather than blocking.
ALTER TABLE goal_metric_source ADD COLUMN axis_key TEXT REFERENCES metric_axis(axis_key);

-- A4: assign the axis for every metric registered to date. The two `_*` rows
-- are FAMILY rows: their axis_key covers every token expanded from the prefix
-- (e1rm_incline_dumbbell_press, max_reps_pullup, ...), which is what keeps the
-- prefix-match behaviour working with no per-lift row.
UPDATE goal_metric_source SET axis_key = 'definition'    WHERE metric_token = 'body_fat_pct';
-- body_weight is a MASS reading, so it sits on size. Its dir_hint ('down') is a
-- per-goal choice and not a property of the axis. Recorded as a judgement call:
-- the owner named skeletal muscle for 크기 and body fat for 선명도 and did not
-- place total body weight; it has to live somewhere, and mass is what it reads.
UPDATE goal_metric_source SET axis_key = 'size'          WHERE metric_token = 'body_weight';
UPDATE goal_metric_source SET axis_key = 'performance'   WHERE metric_token IN
       ('e1rm_*', 'max_reps_*', 'lift_capacity_kg');
UPDATE goal_metric_source SET axis_key = 'training_load' WHERE metric_token = 'weekly_sessions';
UPDATE goal_metric_source SET axis_key = 'intake'        WHERE metric_token IN
       ('kcal_intake', 'protein_intake');
UPDATE goal_metric_source SET axis_key = 'spend'         WHERE metric_token = 'monthly_spend';

-- A5: register the SIZE metric. It had no token at all until now, which is why
-- a stated skeletal-muscle target had nowhere to be a target.
--   source_expr is the metric_log.metric KEY, and the key the InBody write path
--   actually stores is 'skeletal_muscle_kg' -- verified against live rows
--   (live InBody readings), not guessed from the token name.
-- This migration registers the METRIC only. It does NOT create, modify or
-- re-target any goal: what the owner wants to aim at is his call, in a consult.
INSERT OR IGNORE INTO goal_metric_source
       (metric_token, source_table, source_expr, domain, dir_hint, owning_agent, created_at, axis_key)
VALUES ('skeletal_muscle_kg', 'metric_log', 'skeletal_muscle_kg', 'health', 'up', 'warg',
        '2026-08-24T00:00:00Z', 'size');

PRAGMA user_version = 33;

COMMIT;
