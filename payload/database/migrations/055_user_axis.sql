-- 055_user_axis.sql  (target user_version 55)
--
-- reversible: yes, losslessly for every row that existed before.
--   Rollback = ALTER TABLE metric_axis DROP COLUMN origin / role / why_id /
--   phase_order (SQLite >= 3.35), then PRAGMA user_version = 54. No row is
--   deleted, re-keyed or relabelled here; the only UPDATE sets the new `role`
--   column on four seeded rows. The two coordination goal_metric_source
--   rows (end of file) are plain inserts: rollback deletes them by token.
--
-- ==========================================================================
-- WHAT. An axis (갈래) of a direction becomes something the USER's answer
-- creates, not something the kit ships.
--
-- 033 made the axis list data -- right -- and then seeded four "picture" axes
-- (크기 / 선명도 / 수행능력 / 몸의 연결) that were one person's words from one
-- conversation, into EVERY install. A new user who says "마라톤 완주하고
-- 싶어" was being read against someone else's four-strand picture of a body.
-- That is a universality defect: the kit carried one user's answer as if it
-- were the product's taxonomy (owner ruling 2026-09-26 11:14-11:18).
--
-- After this migration an axis row says WHERE IT CAME FROM:
--   origin       'seed' = shipped by a migration, 'user' = created from the
--                user's own answer during a goal interview (axis-add).
--   role         'picture' = a strand of what the user wants to become,
--                'input' = a readout of what goes in / what happened
--                (training_load, intake, spend). Input axes describe records
--                the kit itself writes; they name no aspiration, so they stay
--                seeded and are universal.
--   why_id       the direction (life_why) the strand was derived from.
--   phase_order  the user's ORDER decision when strands pull against each
--                other (1 = first). NULL = not decided.
--
-- The interview reads only role='input' rows plus origin='user' rows. The four
-- seeded picture rows are left IN PLACE on an existing install (live data is
-- never deleted, and legacy readers still resolve their labels through
-- goal_metric_source.axis_key), marked role='picture' origin='seed' so every
-- reader can tell them apart from the user's own strands.
--
-- FRESH INSTALLS (schema.sql) no longer carry the four picture rows at all,
-- and the three metrics that pointed at them (body_weight, body_fat_pct,
-- skeletal_muscle_kg) start with axis_key NULL until a user strand claims
-- them. This is the first migration whose fresh-mint DATA deliberately
-- differs from a migrated DB: the difference is exactly the rows this file
-- refuses to delete. The DDL is identical (test_mig055_user_axis P1).
--
-- NOT HERE: per-metric pace / balance / cadence knowledge. Those are module
-- cargo in kit_life.py beside _CADENCE_GUIDED (the same shape of fact: a
-- universal property of a metric, with its reason), not user data.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_055(seen_user_version);
CREATE TEMP TRIGGER _guard_055_refuse BEFORE INSERT ON _guard_055 BEGIN
  SELECT RAISE(FAIL, 'migration 055 refused: this database is not at user_version 54 -- either 055 was already applied (>= 55) or the chain up to 054 has not run (< 54). Nothing changed');
END;
INSERT INTO _guard_055
  SELECT user_version FROM pragma_user_version WHERE user_version <> 54;
DROP TABLE temp._guard_055;

BEGIN IMMEDIATE;

CREATE TEMP TABLE _guard_055_pre(seen_count);
CREATE TEMP TRIGGER _guard_055_pre_refuse BEFORE INSERT ON _guard_055_pre BEGIN
  SELECT RAISE(FAIL, 'migration 055 refused: expected metric_axis WITHOUT an origin column -- this is not the shape this file was written against. Nothing changed');
END;
INSERT INTO _guard_055_pre
  SELECT COUNT(*) FROM sqlite_master
   WHERE type='table' AND name='metric_axis'
     AND NOT EXISTS (SELECT 1 FROM pragma_table_info('metric_axis')
                      WHERE name='origin')
  HAVING COUNT(*) <> 1;
DROP TABLE temp._guard_055_pre;

ALTER TABLE metric_axis ADD COLUMN origin TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','user'));
ALTER TABLE metric_axis ADD COLUMN role TEXT NOT NULL DEFAULT 'input' CHECK (role IN ('picture','input'));
ALTER TABLE metric_axis ADD COLUMN why_id INTEGER REFERENCES life_why(id) ON DELETE SET NULL;
ALTER TABLE metric_axis ADD COLUMN phase_order INTEGER;

-- The four seeded picture rows keep their data; only their role is named.
UPDATE metric_axis SET role = 'picture'
 WHERE origin = 'seed'
   AND axis_key IN ('size', 'definition', 'performance', 'coordination');

-- Coordination sub-measures a user strand can own (pack_health E14 / kit
-- follow-up K6). Registered here, not in a 056: 055 is still unreleased on
-- this branch and 056 is taken by the parallel branch dsp/muscle-fatigue-p1
-- (056_muscle_fatigue.sql, guard = user_version 55). source_table 'program'
-- = computed by the health pack from program.yaml; the kit reads no value
-- for these tokens. axis_key NULL until a user strand claims one (axis-add).
-- OR IGNORE: a store that already carries a row keeps it untouched.
INSERT OR IGNORE INTO goal_metric_source VALUES('unilateral_share_sets','program','unilateral_share_sets','health','up','warg','2026-09-26T00:00:00Z',NULL);
INSERT OR IGNORE INTO goal_metric_source VALUES('zero_load_control_share_sets','program','zero_load_control_share_sets','health','up','warg','2026-09-26T00:00:00Z',NULL);

PRAGMA user_version = 55;
COMMIT;
