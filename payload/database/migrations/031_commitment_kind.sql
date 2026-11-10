-- 031_commitment_kind.sql  (target user_version 31)
-- reversible: yes -- purely additive (two columns + one index). Rollback =
--   DROP INDEX idx_goal_kind; the two columns can be left in place harmlessly
--   (both are ignorable by pre-031 readers). No pre-existing row is rewritten
--   or deleted, so no user data is lost on the way back.
--
-- GOAL vs RULE: the commitment ledger learns what SHAPE a commitment has.
--
-- WHY. ledger_goal held two different kinds of thing on one long/mid/short
-- ladder and called them all goals. "체지방 18%" is an OUTCOME -- you reach it
-- and you are done. "비운동일 1750kcal" and "웨이트 주 4회" are OPERATING RULES
-- -- you maintain them, and satisfying one today does not consume it. Putting
-- both on one ladder is why the horizons came out incoherent (present on mid,
-- absent on long and short) and why progress readouts looked wrong: there is no
-- "how far along" for a thing that never ends.
--
-- THE TEST (one line, and it is the whole model):
--   does the row have an ABSORBING state?
--   goal -> yes. an achievement predicate that, once true, retires the row.
--   rule -> no.  a standing condition evaluated per period; true this period
--                does not retire it.
-- Note this is NOT "goal has a date, rule does not" -- a rule may carry an
-- end date (a 3-month trial). A goal ends by ACHIEVEMENT; a rule ends by
-- EXPIRY or revocation. Terminality is the discriminator, not the date.
--
-- WHY A DISCRIMINATOR AND NOT A ledger_rule TABLE. Argued from how these rows
-- are CONSUMED, not from what is easier to write. Every selection surface is
-- kind-agnostic and already exists on this table: focus_state, priority_rank,
-- why_id, owning_agent, parent_id, the CAS version, the focus cap, the
-- retire-guard, goal_readiness_rule match_* predicates, goal_metric_source
-- resolution, and the interview verb. A second table would force every one of
-- them to UNION two shapes and would split the focus cap in half -- and the
-- focus cap is the entire anti-overwhelm mechanism, while a rule spends
-- attention exactly as a goal does. What actually differs between the two kinds
-- is only the READOUT and which interview fields are required. That is a
-- dispatch concern, not a storage concern.
--
-- WHY NOT detail JSON. The kind is a SELECTION key (goal-list --kind, the
-- focus split, rule match predicates, this index). JSON cannot carry a CHECK
-- and cannot be indexed cheaply -- and burying the new label in free-form JSON
-- reproduces the exact rot being fixed: detail already held target/freq values
-- that never got promoted to columns (see the 2026-08-22 goal-onboarding gap
-- list, GAP-3), so the readiness rules bound to NULL and could never pass.
--
-- rule_period = the period a rule is evaluated over (day|week|month). Combined
-- with the existing target_value/target_dir this is enough to express every
-- v1 rule shape without new value columns:
--   "비운동일 1750kcal"  -> period=day,  target=1750, dir=hold
--   "웨이트 주 4회"       -> period=week, target=4,    dir=up
--   "매일 30분 걷기"      -> period=day,  target=30,   dir=up
--   "주 2회 부모님께 전화" -> period=week, target=2,    dir=up
-- The domain does not matter: an Ag spend rule or a relationship-cadence rule
-- lands on the same two columns.
--
-- SQLite cannot ALTER ADD CONSTRAINT, so the enums are enforced app-side
-- (_COMMITMENT_KINDS / _RULE_PERIODS) exactly as validate_all_day_instants
-- does for the all_day invariant. A fresh mint gets the CHECKs from schema.sql.

BEGIN;

-- DEFAULT 'goal' backfills every existing row as a goal, which is the safe
-- reading: an existing row was authored under a model that only had goals.
-- Reclassification to 'rule' is a deliberate, audited follow-up write.
ALTER TABLE ledger_goal ADD COLUMN commitment_kind TEXT NOT NULL DEFAULT 'goal';
ALTER TABLE ledger_goal ADD COLUMN rule_period     TEXT;

-- the kind is a selection key: this is the index that a JSON field could not be.
CREATE INDEX IF NOT EXISTS idx_goal_kind
    ON ledger_goal(owning_agent, commitment_kind, status, focus_state);

PRAGMA user_version = 31;

COMMIT;
