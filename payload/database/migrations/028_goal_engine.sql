-- 028_goal_engine.sql  (target user_version 28)
-- reversible: effectively YES for the additive bulk (12 NULLABLE ADD COLUMNs on
--   ledger_goal + 5 NEW tables via CREATE ... IF NOT EXISTS + seed rows via
--   INSERT OR IGNORE). ONE non-additive change: DROP INDEX idx_goal_active_layer
--   (the long|mid|short singleton-active partial UNIQUE index). Dropping an index
--   is strictly PERMISSIVE -- it can never fail on existing rows and rewrites no
--   data -- so it is low-risk. Rollback path = re-CREATE the dropped index (the
--   old DDL is preserved in this comment, see step 2) + DROP the 5 new tables +
--   table-rebuild-drop the 12 added columns (SQLite 3.35+ ALTER DROP COLUMN).
--   Until rolled back, a v28 DB is a strict superset of v27 for reads and every
--   pre-028 query ignores the new NULL columns.
--
-- Source: the 2026-08-20 goal-engine data-model SPEC (FINALIZED
--   section; owner decisions D1-refined/D2/D3 folded 2026-08-20 14:31). This is
--   the DATA MODEL + ENGINE half (S1/S4/S5) of the Life-Direction & Goal-
--   Progression engine. SoT = the 2026-08-20 life-direction goal-engine SoT.
--   Lane: kit_life (DGN-708) -- not a framework handoff.
--
-- SCOPE:
--   1. ledger_goal EXTEND (12 additive NULLABLE columns): measurable-rung shape
--      (domain, metric, baseline_value, baseline_as_of, target_value, target_dir,
--      horizon_date, rung_state), the why ladder-up (why_id), and the plurality
--      MANAGEMENT LAYER (owning_agent + domain scoping, focus_state DEFAULT
--      'backlog', priority_rank). No uniqueness. (§S1a / §F1)
--   2. DROP idx_goal_active_layer -- owner D1-refined = goals are PLURAL; no
--      DB-enforced uniqueness at any grain. NOTHING unique replaces it. Add two
--      PLAIN NON-unique helper indexes for the management-layer reads. (§F1)
--   3. NEW tables (5): life_why (§S1b), goal_readiness_rule (§S1c, mirrors
--      ax_projection_rule), goal_metric_source (§S1c registry), hypothesis +
--      hypothesis_evidence (§S1d epistemics substrate).
--   4. SEED: the crew-SHARED BASE ruleset ONLY into goal_readiness_rule (generic
--      @rung.*-bound rules + the R_no_action_stale retire base) via INSERT OR
--      IGNORE. Domain-specific rows (thresholds/weights per domain-agent knowledge
--      warehouse, §F2) are a SEPARATE post-migration authoring pass -- NOT seeded
--      here. goal_metric_source seed rows are also a separate authoring pass
--      (OQ-2 health+spend seed done at authoring time), NOT baked here.
--
-- OWNERSHIP SEAM (D2 layering, §F2): the rule MECHANISM (goal_readiness_rule
--   table + evaluator shape) is crew-SHARED framework/infra; the rule CONTENT
--   (thresholds = domain knowledge) is KIT/warehouse-owned per domain; the target
--   VALUE (ledger_goal.target_value) is user-owned. why OWNERSHIP is a schema
--   fact: life_why.owner CHECK/DEFAULT 'user' (never an agent), managing_agent =
--   the steward (§F3).
--
-- 027 was TAKEN by 027_timeblock_project_id.sql (target user_version 27), so this
--   migration is 028 / user_version 28. It DEPENDS ON 027 (v27). The sibling
--   food-cache SPEC also proposed 028; that is reconciled to 029 (this goal-engine
--   migration is 028, food-cache is 029) so the sequence stays contiguous.
--
-- SAFETY: every added column is NULLABLE (focus_state carries a safe DEFAULT
--   'backlog') so it is invisible to running v27 agent code (they never
--   SELECT/INSERT it). The goal-tool write path feature-detects the columns at
--   runtime (PRAGMA table_info) and no-ops when absent, so the write code is safe
--   to sit in the live tree BEFORE this migration is applied. The index DROP only
--   RELAXES a constraint, so no running write can start failing because of it.
--
-- The ADD COLUMN order + the 5 CREATE TABLE DDL + the seed rows below are byte-
--   matched to the fresh-mint v28 schema.sql so `fresh == migrated` (data level
--   too, via deterministic ULID / created_at on the seed rows).
--
-- LANDING (mandatory, same discipline as 025/026/027; NOT part of authoring this
--   file): apply on live ONLY via the migration runner + code-pin bump
--   (EXPECTED_USER_VERSION 27->28) in one coordinated run. The live kit_life.db is
--   SHARED by the life agent AND the health agent, so both code pins move in the same lockstep. NEVER
--   hand-run `sqlite3 live.db < 028...` -- a manual exec stamps user_version=28
--   while a code pin stays lower -> the SDK fail-closes every verb. Application +
--   lockstep is the kit propagation-editor lane (DGN-708 execution gate --
--   kit_life migration application is the kit lane, not a framework handoff), done as a
--   SEPARATE future step the kit lane decides + runs. The 10001-10003 data lift (prose/
--   JSON numbers -> new columns; set owning_agent to the health agent, domain='health',
--   focus_state='focus'; new 근력 intent rung) and the per-domain rule/metric
--   authoring pass are apply-time steps, NOT DDL, done under the same lockstep run.
--
-- Atomicity wrapper (matches 023-027, DGN-871 NO-GO #1): `.bail on` aborts on the
--   first error; BEGIN IMMEDIATE .. COMMIT makes the ALTERs + index churn + CREATEs
--   + seed + the user_version stamp ONE atomic unit -> a mid-statement failure
--   rolls back and leaves user_version at 27 (no half-migrated laundering).
--   Dot-commands are sqlite3-CLI-only -- the runner is the CLI.
.bail on
.timeout 5000
BEGIN IMMEDIATE;

-- ============================================================================
-- 1. ledger_goal EXTEND -- measurable-rung columns + why link + management layer.
--    All NULLABLE (additive). focus_state carries DEFAULT 'backlog'. No CHECK is
--    attached by ALTER ADD COLUMN (SQLite limitation); the enum domains
--    (target_dir up|down|hold, rung_state intent|.., focus_state focus|backlog|
--    dormant) are enforced by the goal-tool WRITE LAYER, same pattern as 024's
--    display_name / 027's project_id. owning_agent + domain are SCOPING/FILTERING
--    only (NOT uniqueness). (§S1a measurable rung, §F1 management layer)
-- ============================================================================
ALTER TABLE ledger_goal ADD COLUMN domain          TEXT;    -- health|spend|invest|relationship|hobby|growth|.. NULL=legacy
ALTER TABLE ledger_goal ADD COLUMN metric          TEXT;    -- metric-source token (goal_metric_source.metric_token)
ALTER TABLE ledger_goal ADD COLUMN baseline_value  REAL;    -- measured starting value
ALTER TABLE ledger_goal ADD COLUMN baseline_as_of  TEXT;    -- date the baseline was measured (baseline honesty)
ALTER TABLE ledger_goal ADD COLUMN target_value    REAL;    -- the goal number (user-owned)
ALTER TABLE ledger_goal ADD COLUMN target_dir      TEXT;    -- up|down|hold (progress direction) -- write-layer CHECK
ALTER TABLE ledger_goal ADD COLUMN horizon_date    TEXT;    -- goal deadline, INDEPENDENT of layer cadence. NULL=rolling
ALTER TABLE ledger_goal ADD COLUMN rung_state      TEXT;    -- intent|baseline_pending|target_proposed|confirmed|tracking|met|retired
ALTER TABLE ledger_goal ADD COLUMN why_id          INTEGER REFERENCES life_why(id);  -- ladder-up to a crystallized why. NULLABLE
ALTER TABLE ledger_goal ADD COLUMN owning_agent    TEXT;    -- ag|warg|user -- SCOPING/FILTERING (not uniqueness) -- write-layer CHECK
ALTER TABLE ledger_goal ADD COLUMN focus_state     TEXT NOT NULL DEFAULT 'backlog';  -- focus|backlog|dormant -- anti-overwhelm control
ALTER TABLE ledger_goal ADD COLUMN priority_rank   INTEGER; -- per-scope ordering (lower=higher). user-owned, balancer-assisted

-- ============================================================================
-- 2. DROP the singleton-active-per-layer partial UNIQUE index. Owner D1-refined:
--    goals are PLURAL (multiple active goals allowed per domain, per layer, AND
--    at life level). NO DB-enforced uniqueness at any grain; NOTHING unique
--    replaces this. Plurality is governed by the management layer (focus_state +
--    priority_rank + retire-guard rule + Ag-balancer), not a constraint.
--
--    Dropped DDL (preserved for rollback):
--      CREATE UNIQUE INDEX idx_goal_active_layer
--        ON ledger_goal(layer)
--        WHERE status = 'active' AND layer IN ('long','mid','short');
--
--    NOTE: idx_overlay_single_active (event_overlay singleton) is a DIFFERENT
--    index and is NOT touched -- the spec names ONLY idx_goal_active_layer for
--    the drop.
-- ============================================================================
DROP INDEX IF EXISTS idx_goal_active_layer;

-- Plain NON-unique helper indexes for the management-layer reads (§F1). No UNIQUE:
--   focus read  -- the balancer / daily surface reads focus goals in priority order.
--   domain read -- a module reads its own domain strand by status.
CREATE INDEX IF NOT EXISTS idx_goal_focus
    ON ledger_goal(owning_agent, domain, focus_state, priority_rank);
CREATE INDEX IF NOT EXISTS idx_goal_domain_layer
    ON ledger_goal(owning_agent, domain, layer, status);

-- ============================================================================
-- 3a. life_why -- crystallized emergent-why home (persisted-but-always-mutable).
--     A thin promotion overlay on insight (SoT 1 assembly line). owner is a
--     SCHEMA FACT: CHECK/DEFAULT 'user' -- a why is ALWAYS user-owned; agents only
--     STEWARD via managing_agent (§F3). superseded_by = pivot chain (history
--     preserved). confidence is held by the hypothesis substrate (§S1d). (§S1b)
-- ============================================================================
CREATE TABLE IF NOT EXISTS life_why (
    id                    INTEGER PRIMARY KEY,
    ulid                  TEXT NOT NULL UNIQUE,
    text                  TEXT NOT NULL,              -- the why in user language
    scope                 TEXT NOT NULL,              -- module|life
    domain                TEXT,                       -- for module-why; NULL for life-why
    promoted_from_insight INTEGER REFERENCES insight(id),  -- provenance (SoT 1). NULLABLE
    spectrum              TEXT NOT NULL DEFAULT 'casual',   -- casual|aspiration
    status                TEXT NOT NULL DEFAULT 'active',   -- active|pivoted|retired
    superseded_by         INTEGER REFERENCES life_why(id),  -- pivot chain self-FK. NULLABLE
    confidence            REAL,                       -- 0..1, held by hypothesis substrate
    owner                 TEXT NOT NULL DEFAULT 'user',     -- ALWAYS 'user' -- the why is user-owned (D3)
    managing_agent        TEXT,                       -- ag|warg|.. steward (NOT owner). life->ag, module->domain agent
    created_at            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (owner = 'user'),
    CHECK (scope    IN ('module','life')),
    CHECK (spectrum IN ('casual','aspiration')),
    CHECK (status   IN ('active','pivoted','retired')),
    CHECK (managing_agent IS NULL OR managing_agent IN ('ag','warg','user'))
);
CREATE INDEX IF NOT EXISTS idx_life_why_scope
    ON life_why(scope, domain, status);

-- ============================================================================
-- 3b. goal_readiness_rule -- DECLARATIVE readiness rules-as-data. Mirrors
--     ax_projection_rule EXACTLY: code UNIQUE + priority + match_* predicates +
--     eval_kind CHECK enum + eval_param JSON + verdict_on_pass + owning_agent +
--     status. The evaluator (S4) loads rows and computes verdicts; the model only
--     narrates. Rule MECHANISM is crew-SHARED; rule ROWS are authored per-domain
--     (§F2). @rung.* bind tokens keep the shared rows generic. (§S1c)
-- ============================================================================
CREATE TABLE IF NOT EXISTS goal_readiness_rule (
    ulid            TEXT PRIMARY KEY,
    owning_agent    TEXT NOT NULL DEFAULT 'warg',
    code            TEXT NOT NULL UNIQUE,
    priority        INTEGER NOT NULL DEFAULT 100,   -- eval order (lower first)
    match_domain    TEXT,                           -- NULL=any (predicate)
    match_layer     TEXT,                           -- NULL=any, else short|mid|long
    match_metric    TEXT,                           -- NULL=any, else a metric token
    eval_kind       TEXT NOT NULL,                  -- named deterministic evaluator
    eval_param      TEXT,                           -- JSON params (may carry @rung.* bind tokens)
    verdict_on_pass TEXT NOT NULL,                  -- the verdict emitted on pass
    status          TEXT NOT NULL DEFAULT 'active', -- active|superseded
    created_at      TEXT NOT NULL,
    CHECK (owning_agent IN ('ag','warg','user')),
    CHECK (eval_kind IN (
        'completion_rate_gte',
        'volume_target_met',
        'streak_gte',
        'metric_pace_on_or_ahead',
        'metric_reached'
    )),
    CHECK (verdict_on_pass IN (
        'rung_met',
        'levelup_eligible',
        'retire_candidate',
        'downgrade_candidate'
    )),
    CHECK (status IN ('active','superseded'))
);

-- ============================================================================
-- 3c. goal_metric_source -- metric-token registry. The single place that knows
--     "where does metric X's current value come from" (source_table/expr).
--     Reused by the readiness pace evaluator (S4) and the fit/gap engine (S5), so
--     neither is hardcoded (D2). Reuses record.source_table vocabulary. The v1
--     seed rows (health + spend, OQ-2) are a SEPARATE authoring pass, NOT here.
--     (§S1c metric registry)
-- ============================================================================
CREATE TABLE IF NOT EXISTS goal_metric_source (
    metric_token  TEXT PRIMARY KEY,   -- e.g. body_fat_pct, session_count, e1rm_deadlift, monthly_spend
    source_table  TEXT NOT NULL,      -- the real domain table current value reads from
    source_expr   TEXT,               -- column / aggregate expression
    domain        TEXT,               -- health|spend|..
    dir_hint      TEXT,               -- up|down|hold hint for the metric
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (owning_agent IN ('ag','warg','user')),
    CHECK (dir_hint IS NULL OR dir_hint IN ('up','down','hold'))
);

-- ============================================================================
-- 3d. hypothesis -- the epistemics substrate. A falsifiable claim held with
--     CONFIDENCE, corroborated by BEHAVIOR (not words). Elimination-directed
--     (open -> corroborated | falsified). sensitivity='sensitive' (emotion/
--     relationship/health) is stored provisional-only, never asserted (D5,
--     inference-non-storage). (§S1d)
-- ============================================================================
CREATE TABLE IF NOT EXISTS hypothesis (
    id           INTEGER PRIMARY KEY,
    ulid         TEXT NOT NULL UNIQUE,
    text         TEXT NOT NULL,                 -- elimination-friendly phrasing (weak prior)
    subject_kind TEXT NOT NULL,                 -- insight|why|goal
    subject_id   INTEGER,                       -- soft ref into insight/life_why/ledger_goal by subject_kind
    confidence   REAL NOT NULL DEFAULT 0,       -- 0..1, starts LOW (weak prior)
    stance       TEXT NOT NULL DEFAULT 'open',  -- open|corroborated|falsified|retired
    domain       TEXT,                          -- for safety gating
    sensitivity  TEXT NOT NULL DEFAULT 'normal',-- normal|sensitive
    owning_agent TEXT NOT NULL DEFAULT 'warg',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (owning_agent IN ('ag','warg','user')),
    CHECK (subject_kind IN ('insight','why','goal')),
    CHECK (stance       IN ('open','corroborated','falsified','retired')),
    CHECK (sensitivity  IN ('normal','sensitive'))
);
CREATE INDEX IF NOT EXISTS idx_hypothesis_subject
    ON hypothesis(subject_kind, subject_id);

-- ============================================================================
-- 3e. hypothesis_evidence -- the APPEND-ONLY behavior ledger. record_id is the
--     load-bearing link: evidence = real domain BEHAVIOR rows, not utterances.
--     behavior CORROBORATES (raises confidence); verbal_affirm only marks
--     not-falsified (~0 delta); verbal_deny can falsify. weight carries the
--     contribution (behavior >> verbal by construction). (§S1d / §S4.3)
-- ============================================================================
CREATE TABLE IF NOT EXISTS hypothesis_evidence (
    id            INTEGER PRIMARY KEY,
    ulid          TEXT NOT NULL UNIQUE,
    hypothesis_id INTEGER NOT NULL REFERENCES hypothesis(id),
    record_id     INTEGER REFERENCES record(id),  -- the behavior evidence (real domain action). NULLABLE
    evidence_kind TEXT NOT NULL,                   -- behavior|verbal_affirm|verbal_deny
    weight        REAL NOT NULL DEFAULT 0,         -- confidence contribution (behavior >> verbal)
    observed_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (evidence_kind IN ('behavior','verbal_affirm','verbal_deny'))
);
CREATE INDEX IF NOT EXISTS idx_hyp_evidence_hyp
    ON hypothesis_evidence(hypothesis_id);

-- ============================================================================
-- 4. SEED -- crew-SHARED BASE ruleset ONLY into goal_readiness_rule. Generic
--    @rung.*-bound rules (readable across domains by target-value alone) + the
--    R_no_action_stale retire-candidate base. Domain-SPECIFIC rows (thresholds/
--    weights per domain: health 운동/식단, life 관계/돈) are authored AUTONOMOUSLY later
--    via each domain's knowledge warehouse (§F2), tagged with that domain's
--    owning_agent -- NOT baked here. INSERT OR IGNORE, deterministic ULID /
--    created_at so fresh(schema.sql) == migrated. @rung.* = a bind token the
--    engine resolves against the evaluated rung's own columns/detail (keeps the
--    base rules generic -- a rule reads the rung's target, not a hardcoded number).
--    the health-agent owning_agent on the base rows is the default steward carrier; the
--    evaluator filters by owning_agent so a domain's rows fire only on its goals.
-- ============================================================================
INSERT OR IGNORE INTO goal_readiness_rule
  (ulid, owning_agent, code, priority, match_domain, match_layer, match_metric,
   eval_kind, eval_param, verdict_on_pass, status, created_at)
VALUES
  ('01K3GOALRULE0000000000BASE1','warg','R_weekly_sessions_met',10,NULL,'short',NULL,
     'completion_rate_gte','{"freq_per_week":"@rung.target_value","min_rate":1.0}',
     'rung_met','active','2026-08-20T00:00:00Z'),
  ('01K3GOALRULE0000000000BASE2','warg','R_weekly_volume_met',20,'health',NULL,NULL,
     'volume_target_met','{"source":"workout_sets"}',
     'rung_met','active','2026-08-20T00:00:00Z'),
  ('01K3GOALRULE0000000000BASE3','warg','R_streak_intact',30,NULL,'short',NULL,
     'streak_gte','{"weeks":"@rung.detail.window_weeks"}',
     'levelup_eligible','active','2026-08-20T00:00:00Z'),
  ('01K3GOALRULE0000000000BASE4','warg','R_metric_on_pace',40,NULL,'mid',NULL,
     'metric_pace_on_or_ahead','{"pace":"date_continuous"}',
     'levelup_eligible','active','2026-08-20T00:00:00Z'),
  ('01K3GOALRULE0000000000BASE5','warg','R_metric_on_pace_long',41,NULL,'long',NULL,
     'metric_pace_on_or_ahead','{"pace":"date_continuous"}',
     'levelup_eligible','active','2026-08-20T00:00:00Z'),
  ('01K3GOALRULE0000000000BASE6','warg','R_metric_reached',50,NULL,NULL,NULL,
     'metric_reached','{}',
     'levelup_eligible','active','2026-08-20T00:00:00Z'),
  ('01K3GOALRULE0000000000BASE7','warg','R_no_action_stale',90,NULL,NULL,NULL,
     'completion_rate_gte','{"below":0.34,"stale_weeks":4}',
     'retire_candidate','active','2026-08-20T00:00:00Z');
-- NOTE: match_layer is a single-token predicate (mirrors ax_projection_rule);
--   the spec's "mid|long" pace rule is materialized as TWO base rows (BASE4 mid,
--   BASE5 long) so each row keeps a clean single-value predicate the evaluator
--   matches directly, rather than smuggling an OR into one column.

-- ============================================================================
-- 4b. SEED goal_metric_source -- the v1 base metric-source registry (OQ-2:
--     health + spend, both have live source tables). This is the single place
--     that knows "where does metric X's current value come from," reused by the
--     readiness pace evaluator (S4) and the fit/gap engine (S5) so neither is
--     hardcoded (D2). Domain-specific rows beyond this base (invest/relationship/
--     hobby/growth, e1rm_* variants) are a LATER per-domain authoring pass via
--     each domain's knowledge warehouse (§F2), NOT baked here. INSERT OR IGNORE,
--     deterministic created_at so fresh(schema.sql) == migrated.
--       weekly_sessions -> workouts     (COUNT sessions in the trailing 7 days)
--       body_weight     -> metric_log   (latest 'body_weight' measurement)
--       body_fat_pct    -> metric_log   (latest 'body_fat_pct' measurement)
--       monthly_spend   -> spend_entry  (SUM of expense rows in the current month)
--     source_expr on metric_log rows names the metric_log.metric key to read;
--     the workouts/spend_entry handlers are cadence/window aggregates (source_expr
--     is documentary). dir_hint is the natural progress direction of the metric.
-- ============================================================================
INSERT OR IGNORE INTO goal_metric_source
  (metric_token, source_table, source_expr, domain, dir_hint, owning_agent, created_at)
VALUES
  ('weekly_sessions','workouts','count_sessions_trailing_7d','health','up','warg','2026-08-20T00:00:00Z'),
  ('body_weight','metric_log','body_weight','health','down','warg','2026-08-20T00:00:00Z'),
  ('body_fat_pct','metric_log','body_fat_pct','health','down','warg','2026-08-20T00:00:00Z'),
  ('monthly_spend','spend_entry','sum_expense_month','spend','down','ag','2026-08-20T00:00:00Z');

PRAGMA user_version = 28;

-- Close the atomicity wrapper. Any failure above -> .bail aborted, no COMMIT,
-- rollback, user_version stays 27 (no half-migrated laundering).
COMMIT;
