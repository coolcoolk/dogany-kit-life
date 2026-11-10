-- 046_kcal_week_criterion.sql  (target user_version 46)
--
-- reversible: NO down-migration ships.  ONE UPDATE on ONE owner-declared
--   column, no schema change, no row created or destroyed.  Hand-rollback:
--   UPDATE ledger_goal SET rule_period='day' WHERE metric='kcal_intake'
--   AND commitment_kind='rule'; DELETE the commitment row that the module
--   projected from it (it did not exist before 046 -- the criterion was
--   PARKED); PRAGMA user_version = 45.  A FAILED apply needs no rollback:
--   guard first, single transaction, .bail on, stamp last (023 discipline).
--
-- WHY -- THE CALORIE RULE WAS STORED IN A UNIT ITS OWN CONTENT CONTRADICTS.
--
-- ledger_goal 10002 ("완만한 리컴프 컷") carries rule_period='day' and, in
-- its own detail JSON, "applies_on": "rest_day" with "freq_per_week": 4.
-- Those two statements cannot both be honoured: judged daily, the rule
-- fires on the four days a week it says it does not apply to, and declares
-- 미달 on every one of them.  CRITERION-LAYERING §3 already names
-- this row as the estate's own worked example of the defect --
--   "도메인을 킷 표현력에 맞춰 왜곡하는 것.  kcal_intake 를 period=day 로
--    저장한 것이 그것이다.  그 규칙의 applies_on 은 rest_day 이고 주 4일은
--    운동일이라, 일 단위 판정은 적용되지도 않는 날에 대해 미달을 선언하게
--    된다."
-- -- and §2 states the corrected model in the owner's own terms: 조준점 ≠
-- 판정점.  1,750 is the DAILY AIMING POINT the owner steers by; the
-- JUDGEMENT is the week (the strict accounting is read
-- weekly, not daily).
--
-- 044 deliberately refused to migrate this row ("kcal_intake's period=day
-- row in particular is a MIS-EXPRESSION ... and must not be migrated as if
-- it were a spec"), and 045 left it alone.  That refusal was right THEN:
-- the kit could express a period criterion but the DIET lane wrote no
-- value into measure, and the deficit-vs-surplus asymmetry had no owner
-- ruling behind it.  Both blockers are gone -- 2.15.0's diet lane writes
-- kcal_intake into measure (the 2.13.0 workouts idiom) and the module now
-- derives the weekly band from the owner's own declared numbers -- so the
-- unit correction is the last thing standing between a declared rule and a
-- judged one.
--
-- WHAT LANDS.  ONE column on ONE row: rule_period 'day' -> 'week'.
--
-- What does NOT land, on purpose:
--   * target_value STAYS 1750 and target_dir STAYS 'hold'.  That pair is
--     the owner's DAILY aiming point and the migration has no business
--     overwriting it with a derived weekly number -- a stored derivative
--     goes stale the moment freq_per_week or maintenance_kcal changes, and
--     CRITERION-LAYERING §4 forbids fitting the store to today's values.
--     The weekly BAND is DERIVED at projection time by the diet module
--     from detail's own fields (maintenance_kcal / base_target_kcal /
--     freq_per_week / bmr_kcal); see _kcal_week_band in kit_life.py.
--   * NO metric_def row for kcal_intake.  Metric registration is module
--     cargo written at the point of need (_criterion_metric_ensure, the
--     042 discipline) -- a migration that seeded it would freeze a guess
--     in data, which is exactly what 041 did and 042 undid.
--   * NO measure backfill.  The kcal value shadow is projected by
--     criterion-reconcile (_meal_shadow_reconcile), the same seam the
--     workouts shadow uses since 2.13.0 -- SQL cannot read the declared
--     AGENT_TZ_OFFSET_HOURS, so a migration writing observed_at would mint
--     a fourth clock (038, owner directive 2026-09-08 06:48).
--   * NO commitment row.  _criterion_project mints it, so goal-add /
--     goal-set / criterion-reconcile can never disagree about the band.
--   * NOTHING for weekly_sessions or body_fat_pct.  Their rows are correct.
--
-- The UPDATE is guarded on the exact ('day') incumbent, so an owner edit
-- landing between 045 and this run is never trampled, and a fresh mint
-- (schema.sql: zero ledger_goal rows) is a legitimate no-op.
--
-- GUARD.  Both-sided, 037/040/044/045 discipline: refused unless
-- user_version is EXACTLY 45, BEFORE any transaction or write -- exit != 0,
-- database untouched (a re-apply refused; a skipped 021~045 chain refused).
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_046(seen_user_version);
CREATE TEMP TRIGGER _guard_046_refuse BEFORE INSERT ON _guard_046 BEGIN
  SELECT RAISE(FAIL, 'migration 046 refused: this database is not at user_version 45 -- either 046 was already applied (>= 46) or the 021~045 chain has not run (< 45). Nothing changed');
END;
INSERT INTO _guard_046
  SELECT user_version FROM pragma_user_version WHERE user_version <> 45;
DROP TABLE temp._guard_046;
BEGIN IMMEDIATE;

-- The unit correction.  Scoped by SHAPE (metric + rule arm + the exact
-- incumbent value), never by row id: the same mis-expression on a second
-- estate's row is the same defect, and an owner who already fixed it by
-- hand must not be overwritten.
UPDATE ledger_goal
   SET rule_period = 'week'
 WHERE metric          = 'kcal_intake'
   AND commitment_kind = 'rule'
   AND rule_period     = 'day';

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 45.
PRAGMA user_version = 46;
COMMIT;
