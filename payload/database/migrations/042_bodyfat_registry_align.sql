-- 042_bodyfat_registry_align.sql  (target user_version 42)
--
-- reversible: NO down-migration ships.  Two UPDATEs on rows 041 itself wrote,
--   no schema change, no user data touched.  Hand-rollback: set unit_token
--   back to '%' and stale_after_hours back to 336.0 / declared_by='user' on
--   metric_def+metric_freshness where metric='body_fat_pct', PRAGMA
--   user_version = 41.  A FAILED apply needs no rollback: single transaction,
--   .bail on, stamp last (023 discipline).
--
-- WHY -- 041 INVENTED TWO VALUES THE PRODUCT ALREADY HAD.
--
-- kit_life.py carries a guided-metric registry for body composition:
--     'weight_kg':          (168.0, _CADENCE_BODYCOMP_WHY, 'kg',  'health', 1)
--     'body_fat_pct':       (168.0, _CADENCE_BODYCOMP_WHY, 'pct', 'health', 1)
--     'skeletal_muscle_kg': (168.0, ...)      'fat_mass_kg': (168.0, ...)
-- That registry is the mechanism that ASKS the owner for a cadence and then
-- registers the answer.  041 did not look for it and seeded its own numbers:
--     unit_token         '%'    vs registry 'pct'
--     stale_after_hours  336.0  vs registry 168.0
--     declared_by        'user' vs a default awaiting the owner's answer
--
-- The unit clash is unambiguous: two tokens for one unit is the same
-- "two homes" defect this cut exists to remove, and the freshness test
-- (T2, database/tests/test_freshness_hooks.py) pins 'pct' as the value the
-- ask-flow writes.  041's '%' made that test RED.
--
-- The cadence is subtler and worth stating plainly.  041 derived 336h from
-- the owner's measured InBody gaps (median 13d) and stamped declared_by='user'.
-- Both halves were wrong:
--   * the number was invented while the product already had 168.0 with a
--     documented WHY constant -- the "already in the codebase?" rung was
--     skipped;
--   * declared_by='user' CLAIMS the owner answered a question he was never
--     asked.  That is a false attribution sitting in the ledger, and it also
--     pre-empts the guided ask (a declared row means the flow stops asking).
-- The owner's 2026-09-07 ruling was that body-composition cadence is an
-- ONBOARDING FIELD, not a constant.  Honouring that means the seed must be
-- the module's DEFAULT (declared_by='module'), left for the ask to upgrade to
-- 'user' -- not a fabricated user answer.
--
-- Effect on the judgement layer: none today.  168h still covers the latest
-- reading (2026-09-09), so body_fat_pct stays fresh and training stays
-- 'managing'.  If the owner later goes 8+ days between readings the metric
-- goes stale and the domain honestly falls back to 'collecting' -- which is
-- what a 7-day declared cadence MEANS.  041's 336h would have hidden that.

BEGIN;

-- 1. One unit token for one unit.  'pct' is the incumbent (kit_life.py
--    registry + the ask-flow's own write path); '%' was 041's invention.
UPDATE metric_def
   SET unit_token = 'pct'
 WHERE token = 'body_fat_pct'
   AND unit_token = '%';

-- 2. The cadence returns to the module default and stops claiming the owner
--    declared it.  Guarded on the exact values 041 wrote, so a REAL owner
--    answer landing between 041 and this migration is never overwritten.
UPDATE metric_freshness
   SET stale_after_hours = 168.0,
       declared_by       = 'module',
       note              = 'module default (kit_life guided-metric registry); onboarding ask upgrades this to declared_by=user'
 WHERE metric = 'body_fat_pct'
   AND stale_after_hours = 336.0
   AND declared_by = 'user';

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 41.
PRAGMA user_version = 42;
COMMIT;
