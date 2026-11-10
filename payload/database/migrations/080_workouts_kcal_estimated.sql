-- 080_workouts_kcal_estimated.sql (target user_version 80)
-- workouts.kcal_origin gains 'estimated': a closeout engine estimate for a
-- session with no watch value (owner 2026-10-14 17:10, shown as [추정]).
-- reversible: YES while no row holds kcal_origin='estimated' (re-label or
-- delete those first). Rollback = DROP TRIGGER trg_workouts_estimated_not_over_measured;
-- the writable_schema swap with the two strings exchanged; PRAGMA user_version
-- = the value this file saw (78 or 79). For a byte-identical database rollback,
-- restore the verified pre-080 backup; SQL reversal restores schema/data, not
-- SQLite page layout or header counters.
--
-- WHY. burn_kcal already SUMs workouts.kcal, so estimates count in day/week
-- totals; the origin tells them apart (kit_life burn_estimated_kcal).
-- PRECEDENCE: a measured value always wins.
--   measured over estimated -> allowed (plain UPDATE of kcal + kcal_origin).
--   estimated over measured -> refused by trg_workouts_estimated_not_over_measured.
--
-- NUMBERING: 078 trainer v2 must already be present. 079 record-always-on P1
-- is reserved, so this file accepts 78..79 and stamps 80. The linear chain is
-- 077 -> 078 -> 080; fresh-mint schema.sql carries 80.
.bail on
.timeout 5000

CREATE TEMP TABLE _guard_080(seen_user_version);
CREATE TEMP TRIGGER _guard_080_refuse BEFORE INSERT ON _guard_080 BEGIN
  SELECT RAISE(FAIL, 'migration 080 refused: user_version is not 78..79 -- either 080 was already applied (>= 80) or the chain up to 078 has not run. Nothing changed');
END;
INSERT INTO _guard_080
  SELECT user_version FROM pragma_user_version WHERE user_version NOT BETWEEN 78 AND 79;
DROP TABLE temp._guard_080;

BEGIN IMMEDIATE;

CREATE TEMP TABLE _guard_080_pre(n);
CREATE TEMP TRIGGER _guard_080_pre_refuse BEFORE INSERT ON _guard_080_pre BEGIN
  SELECT RAISE(FAIL, 'migration 080 refused: workouts.kcal_origin CHECK is not the 064 form exactly once. Nothing changed');
END;
INSERT INTO _guard_080_pre
  SELECT 1 FROM sqlite_master
   WHERE type = 'table' AND name = 'workouts'
     AND (length(sql) - length(replace(sql, '(''measured'',''computed'',''default'',''declared'',''unknown'')', '')))
           / length('(''measured'',''computed'',''default'',''declared'',''unknown'')') <> 1;
DROP TABLE temp._guard_080_pre;

PRAGMA writable_schema = ON;
UPDATE sqlite_master
   SET sql = replace(sql, '(''measured'',''computed'',''default'',''declared'',''unknown'')',
                          '(''measured'',''computed'',''default'',''declared'',''unknown'',''estimated'')')
 WHERE type = 'table' AND name = 'workouts';
PRAGMA writable_schema = OFF;

CREATE TEMP TABLE _guard_080_post(n);
CREATE TEMP TRIGGER _guard_080_post_refuse BEFORE INSERT ON _guard_080_post BEGIN
  SELECT RAISE(FAIL, 'migration 080 refused: CHECK did not widen -- rolled back, nothing changed');
END;
INSERT INTO _guard_080_post
  SELECT 1 FROM sqlite_master WHERE type='table' AND name='workouts' AND instr(sql, '''estimated''') = 0;
DROP TABLE temp._guard_080_post;

CREATE TRIGGER trg_workouts_estimated_not_over_measured
BEFORE UPDATE OF kcal, kcal_origin ON workouts
WHEN OLD.kcal_origin = 'measured' AND NEW.kcal_origin = 'estimated'
BEGIN
  SELECT RAISE(ABORT, 'kcal_origin: estimated cannot overwrite measured');
END;

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 80;
COMMIT;
