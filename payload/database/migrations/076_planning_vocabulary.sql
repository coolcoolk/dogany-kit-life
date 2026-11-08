-- 076_planning_vocabulary.sql (target user_version 76)
-- Additive only: no legacy data copy, no lifecycle inference, no reader cutover.
-- reversible: NO (backup restore only). Restore an isolated pre-076 backup; preserve any new planning rows
-- separately first. Do not drop populated planning history to downgrade.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_076(seen_user_version);
CREATE TEMP TRIGGER _guard_076_refuse BEFORE INSERT ON _guard_076 BEGIN
 SELECT RAISE(FAIL, 'migration 076 refused: this database is not at user_version 75');
END;
INSERT INTO _guard_076 SELECT user_version FROM pragma_user_version WHERE user_version <> 75;
DROP TABLE temp._guard_076;
BEGIN IMMEDIATE;
-- 076: additive planning vocabulary; no reconciliation or reader cutover.
ALTER TABLE projects ADD COLUMN goal_id INTEGER REFERENCES goal(id) ON DELETE RESTRICT;
ALTER TABLE projects ADD COLUMN planning_state TEXT CHECK (planning_state IN ('draft','active','closed'));
ALTER TABLE projects ADD COLUMN closed_at TEXT;
ALTER TABLE projects ADD COLUMN close_reason TEXT CHECK (close_reason IN ('goal_reached','period_over','dropped'));
ALTER TABLE projects ADD COLUMN execution_grading TEXT NOT NULL DEFAULT 'count' CHECK (execution_grading IN ('count','weighted'));
ALTER TABLE goal ADD COLUMN lifecycle TEXT CHECK (lifecycle IN ('active','paused','achieved','failed','stopped','archived'));
ALTER TABLE goal ADD COLUMN closed_at TEXT;
ALTER TABLE goal ADD COLUMN close_reason TEXT;
ALTER TABLE goal ADD COLUMN domain TEXT;
ALTER TABLE goal ADD COLUMN managing_agent TEXT;
ALTER TABLE goal_target ADD COLUMN series_key TEXT;
ALTER TABLE goal_target ADD COLUMN judging_policy TEXT CHECK (judging_policy IN ('first_touch','window_average_in_band'));
ALTER TABLE goal_target ADD COLUMN window_days INTEGER CHECK (window_days > 0);
ALTER TABLE goal_target ADD COLUMN metric_class TEXT CHECK (metric_class IN ('exact','inbody','weight'));
ALTER TABLE goal_target ADD COLUMN policy_origin TEXT CHECK (policy_origin IN ('metric_default','owner'));
ALTER TABLE goal_target ADD COLUMN evidence_entity_id INTEGER REFERENCES entity(id);
ALTER TABLE goal_target ADD COLUMN evidence_domain TEXT;
ALTER TABLE goal_target ADD COLUMN unit_token TEXT REFERENCES unit(token);
ALTER TABLE commitment ADD COLUMN timezone TEXT;
ALTER TABLE commitment ADD COLUMN week_start INTEGER CHECK (week_start BETWEEN 0 AND 6);
ALTER TABLE commitment ADD COLUMN calendar_policy TEXT CHECK (calendar_policy IN ('every_period','weekdays'));
ALTER TABLE thesis ADD COLUMN scope TEXT CHECK (scope IN ('module','life'));
ALTER TABLE thesis ADD COLUMN domain TEXT;
ALTER TABLE thesis ADD COLUMN promoted_from_insight INTEGER REFERENCES insight(id);
ALTER TABLE thesis ADD COLUMN spectrum TEXT CHECK (spectrum IN ('casual','aspiration'));
ALTER TABLE thesis ADD COLUMN confidence REAL;
ALTER TABLE thesis ADD COLUMN managing_agent TEXT;
ALTER TABLE thesis ADD COLUMN label TEXT;
ALTER TABLE thesis ADD COLUMN updated_at TEXT;
ALTER TABLE thesis ADD COLUMN provenance TEXT;
ALTER TABLE thesis ADD COLUMN stated_on_provenance TEXT;

CREATE INDEX ix_projects_goal ON projects(goal_id);
CREATE UNIQUE INDEX ux_goal_target_series_version ON goal_target(goal_id, series_key, version)
 WHERE series_key IS NOT NULL;
CREATE UNIQUE INDEX ux_goal_target_series_current ON goal_target(goal_id, series_key)
 WHERE series_key IS NOT NULL AND valid_until IS NULL;

-- NULL planning_state/lifecycle/policy = unreconciled legacy, never guessed.
-- Existing projects are untouched. Only typed planning_state='active' is gated:
-- legacy project-add (status active, planning_state NULL, no goal/period) must
-- keep working until the writer cutover; that path is unreconciled legacy.
CREATE TRIGGER trg_project_planning_ins BEFORE INSERT ON projects
WHEN NEW.planning_state = 'active'
 AND (NEW.goal_id IS NULL OR NOT EXISTS (SELECT 1 FROM goal WHERE id=NEW.goal_id)
 OR NEW.start_date IS NULL OR NEW.end_date IS NULL
 OR date(NEW.start_date, '+0 days') IS NOT NEW.start_date
 OR date(NEW.end_date, '+0 days') IS NOT NEW.end_date OR NEW.end_date < NEW.start_date)
BEGIN SELECT RAISE(ABORT, 'active project requires goal and valid inclusive period'); END;
CREATE TRIGGER trg_project_planning_upd BEFORE UPDATE OF planning_state, goal_id, start_date, end_date, status ON projects
WHEN NEW.planning_state = 'active'
 AND (NEW.goal_id IS NULL OR NOT EXISTS (SELECT 1 FROM goal WHERE id=NEW.goal_id)
 OR NEW.start_date IS NULL OR NEW.end_date IS NULL
 OR date(NEW.start_date, '+0 days') IS NOT NEW.start_date
 OR date(NEW.end_date, '+0 days') IS NOT NEW.end_date OR NEW.end_date < NEW.start_date)
BEGIN SELECT RAISE(ABORT, 'active project requires goal and valid inclusive period'); END;
CREATE TRIGGER trg_project_closure_ins BEFORE INSERT ON projects
WHEN (NEW.closed_at IS NULL) <> (NEW.close_reason IS NULL)
 OR (NEW.planning_state = 'closed' AND NEW.closed_at IS NULL)
 OR (NEW.planning_state IS NOT 'closed' AND NEW.closed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'project closure requires state, time and reason'); END;
CREATE TRIGGER trg_goal_closure_ins BEFORE INSERT ON goal
WHEN (NEW.lifecycle IN ('achieved','failed','stopped','archived') AND NEW.close_reason IS NULL)
 OR (NEW.lifecycle IN ('active','paused') AND (NEW.closed_at IS NOT NULL OR NEW.close_reason IS NOT NULL))
BEGIN SELECT RAISE(ABORT, 'goal closure requires explicit lifecycle and reason'); END;
CREATE TRIGGER trg_target_policy_ins BEFORE INSERT ON goal_target
WHEN (NEW.judging_policy = 'window_average_in_band' AND
      (NEW.window_days IS NULL OR NEW.target_low IS NULL OR NEW.target_high IS NULL))
 OR (NEW.judging_policy = 'first_touch' AND NEW.window_days IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'invalid target judging policy'); END;
CREATE TRIGGER trg_commitment_goal_ins BEFORE INSERT ON commitment
WHEN NEW.status='active' AND (NEW.goal_id IS NULL OR NOT EXISTS (SELECT 1 FROM goal WHERE id=NEW.goal_id))
BEGIN SELECT RAISE(ABORT, 'active rule requires a goal'); END;
CREATE TRIGGER trg_project_closure_upd BEFORE UPDATE ON projects
WHEN (NEW.closed_at IS NULL) <> (NEW.close_reason IS NULL)
 OR (NEW.planning_state = 'closed' AND NEW.closed_at IS NULL)
 OR (NEW.planning_state IS NOT 'closed' AND NEW.closed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'project closure requires state, time and reason'); END;
CREATE TRIGGER trg_goal_closure_upd BEFORE UPDATE ON goal
WHEN (NEW.lifecycle IN ('achieved','failed','stopped','archived') AND NEW.close_reason IS NULL)
 OR (NEW.lifecycle IN ('active','paused') AND (NEW.closed_at IS NOT NULL OR NEW.close_reason IS NOT NULL))
BEGIN SELECT RAISE(ABORT, 'goal closure requires explicit lifecycle and reason'); END;
CREATE TRIGGER trg_target_policy_upd BEFORE UPDATE ON goal_target
WHEN (NEW.judging_policy = 'window_average_in_band' AND
      (NEW.window_days IS NULL OR NEW.target_low IS NULL OR NEW.target_high IS NULL))
 OR (NEW.judging_policy = 'first_touch' AND NEW.window_days IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'invalid target judging policy'); END;
CREATE TRIGGER trg_commitment_goal_upd BEFORE UPDATE ON commitment
WHEN NEW.status='active' AND (NEW.goal_id IS NULL OR NOT EXISTS (SELECT 1 FROM goal WHERE id=NEW.goal_id))
BEGIN SELECT RAISE(ABORT, 'active rule requires a goal'); END;
CREATE TRIGGER trg_planning_goal_delete BEFORE DELETE ON goal
WHEN EXISTS (SELECT 1 FROM commitment WHERE goal_id=OLD.id)
 OR EXISTS (SELECT 1 FROM projects WHERE goal_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'goal is referenced by planning'); END;
DROP TRIGGER trg_goal_target_no_inplace_edit;
CREATE TRIGGER trg_goal_target_no_inplace_edit BEFORE UPDATE ON goal_target
WHEN NEW.goal_id IS NOT OLD.goal_id OR NEW.metric IS NOT OLD.metric OR NEW.target_low IS NOT OLD.target_low OR NEW.target_high IS NOT OLD.target_high OR NEW.baseline_value IS NOT OLD.baseline_value OR NEW.baseline_as_of IS NOT OLD.baseline_as_of OR NEW.series_key IS NOT OLD.series_key OR NEW.judging_policy IS NOT OLD.judging_policy OR NEW.window_days IS NOT OLD.window_days OR NEW.metric_class IS NOT OLD.metric_class OR NEW.policy_origin IS NOT OLD.policy_origin OR NEW.evidence_entity_id IS NOT OLD.evidence_entity_id OR NEW.evidence_domain IS NOT OLD.evidence_domain OR NEW.unit_token IS NOT OLD.unit_token OR NEW.applies_when IS NOT OLD.applies_when OR NEW.valid_from IS NOT OLD.valid_from OR NEW.version IS NOT OLD.version OR NEW.supersedes IS NOT OLD.supersedes
BEGIN SELECT RAISE(ABORT, 'goal_target: insert a successor version'); END;
DROP TRIGGER trg_commitment_no_inplace_edit;
CREATE TRIGGER trg_commitment_no_inplace_edit BEFORE UPDATE ON commitment
WHEN NEW.goal_id IS NOT OLD.goal_id OR NEW.metric IS NOT OLD.metric OR NEW.lo IS NOT OLD.lo OR NEW.hi IS NOT OLD.hi OR NEW.kind IS NOT OLD.kind OR NEW.period IS NOT OLD.period OR NEW.enforcement IS NOT OLD.enforcement OR NEW.spec IS NOT OLD.spec OR NEW.timezone IS NOT OLD.timezone OR NEW.week_start IS NOT OLD.week_start OR NEW.calendar_policy IS NOT OLD.calendar_policy OR NEW.valid_from IS NOT OLD.valid_from OR NEW.version IS NOT OLD.version OR NEW.supersedes IS NOT OLD.supersedes
BEGIN SELECT RAISE(ABORT, 'commitment: insert a successor version'); END;

CREATE TABLE commitment_evidence (
 commitment_id INTEGER NOT NULL REFERENCES commitment(id) ON DELETE RESTRICT,
 local_date TEXT NOT NULL CHECK (date(local_date, '+0 days') IS local_date),
 result TEXT NOT NULL CHECK (result IN ('kept','broken','not_logged','not_applicable')),
 source_ref TEXT NOT NULL CHECK (length(trim(source_ref)) > 0),
 evaluator_version TEXT NOT NULL CHECK (length(trim(evaluator_version)) > 0),
 recorded_at TEXT NOT NULL,
 PRIMARY KEY (commitment_id, local_date)
);
-- A commitment row IS its temporal version. For weekly quotas local_date is
-- the declared week start, never seven synthetic daily successes.
CREATE TRIGGER trg_commitment_evidence_immutable BEFORE UPDATE ON commitment_evidence
BEGIN SELECT RAISE(ABORT, 'evidence is immutable; conflicting retry needs reconciliation'); END;

CREATE TABLE task (
 block_id INTEGER PRIMARY KEY REFERENCES block(id) ON DELETE RESTRICT,
 checkable_result TEXT NOT NULL CHECK (length(trim(checkable_result)) > 0),
 estimated_minutes REAL CHECK (estimated_minutes > 0),
 estimate_origin TEXT CHECK (estimate_origin IN ('owner','history','agent')),
 estimated_at TEXT,
 estimate_version INTEGER NOT NULL DEFAULT 1 CHECK (estimate_version > 0),
 exec_state TEXT NOT NULL DEFAULT 'open' CHECK (exec_state IN ('open','doing','waiting')),
 waiting_reason TEXT, waiting_since TEXT, review_after TEXT,
 waiting_for TEXT,
 CHECK ((estimated_minutes IS NULL AND estimate_origin IS NULL AND estimated_at IS NULL)
     OR (estimated_minutes IS NOT NULL AND estimate_origin IS NOT NULL AND estimated_at IS NOT NULL)),
 CHECK ((exec_state='waiting' AND length(trim(waiting_reason)) > 0 AND waiting_reason IS NOT NULL AND waiting_since IS NOT NULL)
     OR (exec_state<>'waiting' AND waiting_reason IS NULL AND waiting_since IS NULL AND review_after IS NULL AND waiting_for IS NULL))
);
CREATE TABLE task_estimate_history (
 task_id INTEGER NOT NULL REFERENCES task(block_id),
 version INTEGER NOT NULL, estimated_minutes REAL, origin TEXT, estimated_at TEXT,
 PRIMARY KEY(task_id, version)
);
CREATE TRIGGER trg_task_estimate_insert AFTER INSERT ON task BEGIN
 INSERT INTO task_estimate_history VALUES (NEW.block_id, NEW.estimate_version, NEW.estimated_minutes, NEW.estimate_origin, NEW.estimated_at);
END;
CREATE TRIGGER trg_task_estimate_revision BEFORE UPDATE ON task
WHEN (NEW.estimated_minutes IS NOT OLD.estimated_minutes OR NEW.estimate_origin IS NOT OLD.estimate_origin OR NEW.estimated_at IS NOT OLD.estimated_at OR NEW.estimate_version IS NOT OLD.estimate_version)
 AND NEW.estimate_version <> OLD.estimate_version + 1
BEGIN SELECT RAISE(ABORT, 'estimate edit requires next version'); END;
CREATE TRIGGER trg_task_estimate_update AFTER UPDATE OF estimate_version ON task
WHEN NEW.estimate_version <> OLD.estimate_version BEGIN
 INSERT INTO task_estimate_history VALUES (NEW.block_id, NEW.estimate_version, NEW.estimated_minutes, NEW.estimate_origin, NEW.estimated_at);
END;
CREATE TABLE task_execution (
 task_id INTEGER NOT NULL REFERENCES task(block_id) ON DELETE RESTRICT,
 block_id INTEGER NOT NULL UNIQUE REFERENCES block(id) ON DELETE RESTRICT,
 PRIMARY KEY(task_id, block_id), CHECK (task_id <> block_id)
);
CREATE TRIGGER trg_task_execution_plan BEFORE INSERT ON task_execution
WHEN EXISTS (SELECT 1 FROM task WHERE block_id=NEW.block_id)
BEGIN SELECT RAISE(ABORT, 'task plan cannot be an execution session'); END;
CREATE TRIGGER trg_task_plan_execution BEFORE INSERT ON task
WHEN EXISTS (SELECT 1 FROM task_execution WHERE block_id=NEW.block_id)
BEGIN SELECT RAISE(ABORT, 'execution session cannot be a task plan'); END;
CREATE TRIGGER trg_task_execution_immutable BEFORE UPDATE ON task_execution
BEGIN SELECT RAISE(ABORT, 'execution identity is immutable'); END;

-- Explicit snapshots: later membership, weights or goal edits cannot rewrite
-- a saved retro. Estimates are never completion weights.
CREATE TABLE project_scope (
 id INTEGER PRIMARY KEY,
 project_id INTEGER NOT NULL REFERENCES projects(id),
 revision INTEGER NOT NULL CHECK (revision > 0),
 captured_at TEXT NOT NULL,
 sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0,1)),
 goal_id INTEGER REFERENCES goal(id),
 grading TEXT NOT NULL CHECK (grading IN ('count','weighted')),
 UNIQUE(project_id, revision)
);
CREATE TABLE project_scope_task (
 scope_id INTEGER NOT NULL REFERENCES project_scope(id),
 task_id INTEGER NOT NULL REFERENCES task(block_id),
 result_state TEXT NOT NULL CHECK (result_state IN ('done','open','doing','waiting','cancelled','removed')),
 weight REAL CHECK (weight > 0),
 PRIMARY KEY(scope_id, task_id)
);
CREATE TRIGGER trg_project_scope_immutable BEFORE UPDATE ON project_scope
WHEN OLD.sealed=1 OR NEW.sealed<>1 OR NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id
 OR NEW.revision IS NOT OLD.revision OR NEW.captured_at IS NOT OLD.captured_at OR NEW.goal_id IS NOT OLD.goal_id OR NEW.grading IS NOT OLD.grading
BEGIN SELECT RAISE(ABORT, 'scope snapshots are immutable'); END;
CREATE TRIGGER trg_project_scope_task_immutable BEFORE UPDATE ON project_scope_task
BEGIN SELECT RAISE(ABORT, 'scope snapshots are immutable'); END;

CREATE TRIGGER trg_scope_member_sealed BEFORE INSERT ON project_scope_task
WHEN (SELECT sealed FROM project_scope WHERE id=NEW.scope_id)=1
BEGIN SELECT RAISE(ABORT, 'scope is sealed'); END;
CREATE TRIGGER trg_scope_seal_weights BEFORE UPDATE OF sealed ON project_scope
WHEN NEW.sealed=1 AND NEW.grading='weighted' AND EXISTS
 (SELECT 1 FROM project_scope_task WHERE scope_id=NEW.id AND weight IS NULL)
BEGIN SELECT RAISE(ABORT, 'weighted scope needs every weight'); END;
CREATE TRIGGER trg_target_series_insert BEFORE INSERT ON goal_target
WHEN NEW.series_key IS NOT NULL AND
 (length(trim(NEW.series_key))=0 OR NEW.judging_policy IS NULL OR NEW.valid_from IS NULL
 OR (NEW.evidence_entity_id IS NULL AND NEW.evidence_domain IS NULL)
 OR (NEW.supersedes IS NULL AND NEW.version<>1)
 OR (NEW.supersedes IS NOT NULL AND NOT EXISTS
    (SELECT 1 FROM goal_target p WHERE p.id=NEW.supersedes AND p.goal_id=NEW.goal_id
       AND p.series_key=NEW.series_key AND NEW.version=p.version+1
       AND p.valid_until=NEW.valid_from)))
BEGIN SELECT RAISE(ABORT, 'typed target requires scoped policy and contiguous successor'); END;
CREATE TRIGGER trg_target_closure_immutable BEFORE UPDATE OF valid_until ON goal_target
WHEN OLD.series_key IS NOT NULL AND OLD.valid_until IS NOT NULL AND NEW.valid_until IS NOT OLD.valid_until
BEGIN SELECT RAISE(ABORT, 'closed target span is immutable'); END;
CREATE TRIGGER trg_rule_closure_immutable BEFORE UPDATE OF valid_until ON commitment
WHEN OLD.timezone IS NOT NULL AND OLD.valid_until IS NOT NULL AND NEW.valid_until IS NOT OLD.valid_until
BEGIN SELECT RAISE(ABORT, 'closed rule span is immutable'); END;
CREATE TRIGGER trg_rule_status_version BEFORE UPDATE OF status ON commitment
WHEN OLD.timezone IS NOT NULL AND NEW.status IS NOT OLD.status
BEGIN SELECT RAISE(ABORT, 'typed rule pause/retire requires successor'); END;
CREATE TRIGGER trg_project_scope_no_delete BEFORE DELETE ON project_scope
BEGIN SELECT RAISE(ABORT, 'planning history cannot be deleted'); END;
CREATE TRIGGER trg_project_scope_task_no_delete BEFORE DELETE ON project_scope_task
BEGIN SELECT RAISE(ABORT, 'planning history cannot be deleted'); END;
CREATE TRIGGER trg_task_estimate_history_no_delete BEFORE DELETE ON task_estimate_history
BEGIN SELECT RAISE(ABORT, 'planning history cannot be deleted'); END;
CREATE TRIGGER trg_commitment_evidence_no_delete BEFORE DELETE ON commitment_evidence
BEGIN SELECT RAISE(ABORT, 'planning history cannot be deleted'); END;
CREATE TRIGGER trg_task_history_immutable BEFORE UPDATE ON task_estimate_history
BEGIN SELECT RAISE(ABORT, 'estimate history is immutable'); END;

PRAGMA user_version = 76;
COMMIT;
