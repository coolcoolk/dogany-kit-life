-- 061_set_scope.sql  (target user_version 61)
--
-- reversible: YES, losslessly while no row uses what it opens. Rollback =
--   DROP TRIGGER trg_workout_sets_reps_counted / _upd; DROP INDEX
--   idx_workout_sets_drill; ALTER TABLE workout_sets DROP COLUMN drill_code
--   (after the index); the writable_schema swap below with the two strings
--   exchanged; PRAGMA user_version = 60.  The swap back is only legal while no
--   row carries slot_num NULL or reps NULL -- once the pack writes session-level
--   rows, going back means choosing a value for them, which is a data decision.
--
-- ==========================================================================
-- WHAT.  Root 5 of the 2026-08-27 warg roots design ("소속"), kit side, plus
-- the two data gaps root 3 left behind (prep load observation, ramp done-tap).
--
-- (1) workout_sets.slot_num becomes NULLABLE.  NULL = the row belongs to the
--     SESSION, not to a program slot (mat_prep x12 / cooldown x4 in prep.yaml
--     carry no live_slot).  No set_scope column: "칸을 만들 게 아니라 NULL 을
--     허용하면 된다" -- the absence of a slot IS the session scope, and a second
--     column saying the same thing is a second clock that can disagree.
--     The pack spelled this slot_num = 0 since 2026-09-10 (set-table
--     SESSION_SLOT_NUM) because the kit column could not hold NULL; 0 stays
--     WRITABLE here (nothing is refused), so a pack still at its current pin
--     keeps working, and the pack moves to NULL at its next pin bump.  No row
--     is rewritten by this file: a 0 written by the running pack after this
--     migration would be missed by any normalisation done here, so the
--     normalisation belongs to the pack cut-over (see CHANGELOG).
--     exercise_name stays NOT NULL on purpose: the pack keys the upsert
--     (UNIQUE(session_id, exercise_name, set_seq)), the prep gate and the
--     per-lane seq allocator on the host lift, and a NULL there would make the
--     UNIQUE blind (SQLite NULLs are distinct).  Volume is already protected by
--     set_kind + the set_seq band, not by the name.
--
-- (2) workout_sets.reps becomes NULLABLE for prep and ramp rows ONLY.  A warm-up
--     rung and a prep drill are "도달 전제" (owner 2026-08-27, B17): the owner
--     taps done, he does not count.  Until now the row had to carry a number
--     anyway, so prep stored the prescription (or a 0 sentinel) in `reps` --
--     a prescribed value dressed as a measurement.  Now: reps NULL = done as
--     prescribed, not counted; target_reps keeps the prescription.  Working and
--     hold rows are still refused without reps, by trigger (INSERT and UPDATE)
--     -- a working set with no reps is not a set, and e1RM/volume read them.
--
-- (3) workout_sets.drill_code TEXT NULL -- the prep.yaml drill `code` a prep
--     row performed.  A prep row's identity was only its position in the band
--     (set_seq -100 - index), which moves whenever prep.yaml is re-ordered, so
--     "what load did he use on this drill last time" had no key.  With the code
--     stored, the observed load (weight_kg on the prep row) is readable next
--     session by `drill-load-last` -- 관측하고 기록, 처방하지 않는다 (root 3
--     item 3).  NULL for every existing row: the code cannot be recovered from
--     a band position after the fact without guessing which prep.yaml was live.
--
-- WHY writable_schema AND NOT THE 12-STEP REBUILD.  Same reasoning as 049:
-- removing a NOT NULL is one of the schema edits SQLite documents as safe to do
-- by rewriting the stored CREATE text (no row can violate a constraint that
-- only got looser), and the rebuild would DROP a table that pain_event.set_ref
-- references (ON DELETE SET NULL -- a DROP under foreign_keys=ON nulls every
-- pain link) and that v_pain_event reads.  Fixed string in, fixed string out,
-- counted before and after.
--
-- ORDER INSIDE THE TRANSACTION MATTERS.  ALTER TABLE ADD COLUMN splices the
-- new column at an offset taken from this connection's PARSED schema; after a
-- writable_schema edit that parse is stale and the splice would land in the
-- wrong place.  So every ordinary DDL runs first (it also bumps the schema
-- cookie, so other connections reparse after COMMIT), the text swap runs last.
--
-- GUARD.  (1) user_version exactly 60.  (2) workout_sets carries each of the
-- two constraint strings EXACTLY once in either its old or its new form (a
-- fresh mint of this schema.sql already has the new form; the swap is then a
-- no-op).  (3) after the swap neither old string survives.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_061(seen_user_version);
CREATE TEMP TRIGGER _guard_061_refuse BEFORE INSERT ON _guard_061 BEGIN
  SELECT RAISE(FAIL, 'migration 061 refused: this database is not at user_version 60 -- either 061 was already applied (>= 61) or the chain up to 060 has not run (< 60). Nothing changed');
END;
INSERT INTO _guard_061
  SELECT user_version FROM pragma_user_version WHERE user_version <> 60;
DROP TABLE temp._guard_061;

BEGIN IMMEDIATE;

CREATE TEMP TABLE _guard_061_pre(n);
CREATE TEMP TRIGGER _guard_061_pre_refuse BEFORE INSERT ON _guard_061_pre BEGIN
  SELECT RAISE(FAIL, 'migration 061 refused: workout_sets does not carry "slot_num      INTEGER NOT NULL," / "reps          INTEGER NOT NULL," (or their 061 forms) exactly once each -- not the shape this file was written against. Nothing changed');
END;
INSERT INTO _guard_061_pre
  SELECT 1 FROM sqlite_master
   WHERE type = 'table' AND name = 'workout_sets'
     AND NOT (
       (length(sql) - length(replace(sql, 'slot_num      INTEGER NOT NULL,', '')))
         / length('slot_num      INTEGER NOT NULL,')
       + (length(sql) - length(replace(sql, 'slot_num      INTEGER,', '')))
         / length('slot_num      INTEGER,') = 1
       AND
       (length(sql) - length(replace(sql, 'reps          INTEGER NOT NULL,', '')))
         / length('reps          INTEGER NOT NULL,')
       + (length(sql) - length(replace(sql, 'reps          INTEGER,', '')))
         / length('reps          INTEGER,') = 1);
DROP TABLE temp._guard_061_pre;

-- (3) the drill a prep row performed.  Ordinary DDL, BEFORE the text swap.
ALTER TABLE workout_sets ADD COLUMN drill_code TEXT;
CREATE INDEX idx_workout_sets_drill
    ON workout_sets(drill_code, recorded_at) WHERE drill_code IS NOT NULL;

-- (2) reps may be absent only where the owner taps instead of counting.
CREATE TRIGGER trg_workout_sets_reps_counted
BEFORE INSERT ON workout_sets
WHEN NEW.reps IS NULL AND NEW.set_kind NOT IN ('prep','ramp')
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: reps is required for a working/hold set -- only a prep or ramp row may be a done-tap (reps NULL, target_reps = the prescription)');
END;
CREATE TRIGGER trg_workout_sets_reps_counted_upd
BEFORE UPDATE OF reps, set_kind ON workout_sets
WHEN NEW.reps IS NULL AND NEW.set_kind NOT IN ('prep','ramp')
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: reps is required for a working/hold set -- only a prep or ramp row may be a done-tap (reps NULL, target_reps = the prescription)');
END;

-- (1) + (2) the text swap.  Fixed strings; nothing is generated.
PRAGMA writable_schema = ON;
UPDATE sqlite_master
   SET sql = replace(replace(sql,
        'slot_num      INTEGER NOT NULL,', 'slot_num      INTEGER,'),
        'reps          INTEGER NOT NULL,', 'reps          INTEGER,')
 WHERE type = 'table' AND name = 'workout_sets';
PRAGMA writable_schema = OFF;

CREATE TEMP TABLE _guard_061_post(n);
CREATE TEMP TRIGGER _guard_061_post_refuse BEFORE INSERT ON _guard_061_post BEGIN
  SELECT RAISE(FAIL, 'migration 061 refused: a NOT NULL survived the swap on workout_sets -- rolled back, nothing changed');
END;
INSERT INTO _guard_061_post
  SELECT 1 FROM sqlite_master
   WHERE type = 'table' AND name = 'workout_sets'
     AND (instr(sql, 'slot_num      INTEGER NOT NULL,') > 0
          OR instr(sql, 'reps          INTEGER NOT NULL,') > 0);
DROP TABLE temp._guard_061_post;

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 61;
COMMIT;
