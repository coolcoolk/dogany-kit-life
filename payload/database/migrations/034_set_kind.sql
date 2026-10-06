-- 034_set_kind.sql  (target user_version 34)
--
-- reversible: yes, losslessly, WITHOUT a table rebuild.
--   Rollback = the three columns are inert to every pre-034 reader: set_kind is
--   NOT NULL DEFAULT 'working' (every pre-034 INSERT that omits it still
--   succeeds), target_reps and hold_sec are NULLable. No pre-034 statement
--   selects them, and no volume/tonnage query changes -- see the BACKFILL note.
--   SQLite >= 3.35 additionally supports
--     ALTER TABLE workout_sets DROP COLUMN hold_sec;
--     ALTER TABLE workout_sets DROP COLUMN target_reps;
--     ALTER TABLE workout_sets DROP COLUMN set_kind;
--   No row is deleted, re-keyed or inserted-over. The one UPDATE below writes a
--   single new column on existing rows and touches nothing else.
--
-- ==========================================================================
-- WHAT. Every unit of work in a session becomes a `workout_sets` ROW, and the
-- kind of work becomes a COLUMN instead of a place in the code.
--
--   set_kind     prep | ramp | working | hold
--   target_reps  the PRESCRIBED rep target for this set
--   hold_sec     duration for a `hold` set
--
-- Ref: SPEC 2026-08-25-warg-session-hook-harness A2.3.
--
-- ==========================================================================
-- WHY set_kind. Prep sets were a second-class citizen: they lived OUTSIDE the
-- set pipeline entirely, so a prep movement produced no row, no table line and
-- no cue with a load/rep target (spec defects #2 #3). Ramp rungs were inside
-- the pipeline but only by a SIGN CONVENTION on set_seq (>=1 working, 0 the
-- bodyweight/empty-bar primer, -N the Nth ramp rung), which is a fact readable
-- only by the scripts that already know the convention. And the rest-timer
-- keyboard leaked onto working sets because "is this a hold?" had no column to
-- read (#6).
--
-- With one column carrying the kind, the four kinds differ only by a TABLE
-- LOOKUP (in_table / keyboard / counts-toward-volume / cue shape). There is no
-- longer an "outside" for prep to live in, and a timer keyboard is structurally
-- underivable for anything but `hold`.
--
-- Tokens are English by design (SPEC Gate 3): a DB token is a machine value.
-- The Korean the owner sees is produced at render time and never stored here --
-- same discipline as 032 (ASCII key in the key column, label beside it).
--
-- ==========================================================================
-- WHY THE CHECK CONSTRAINT IS HERE.
-- `set_kind` is a NEW column, so its CHECK ships with ALTER TABLE ADD COLUMN
-- and needs NO 12-step table rebuild (a rebuild is only unavoidable when
-- constraining an EXISTING column). Rebuilding `workout_sets` -- the hottest
-- live table, written mid-session -- to gain a constraint would be a far larger
-- risk than the constraint removes.
-- The application-side derivation table raises UnknownSetKindError on an
-- unknown kind, but it is not the only writer: set-add, the arm/table scripts
-- and any future hook write this table through raw SQL. The CHECK is the one
-- barrier every writer passes through.
-- TRADEOFF, stated: a FIFTH kind cannot be added by ALTER later -- it needs the
-- 12-step rebuild. That is accepted deliberately: the roster is closed by spec
-- (prep/ramp/working/hold is the complete taxonomy of a performed unit), and a
-- silently mistyped kind is the more likely and more expensive failure -- it
-- would flow into volume accounting and keyboard derivation unnoticed.
--
-- ==========================================================================
-- WHY target_reps EXISTS (#12). The rep target was a fixed constant while the
-- load moved. Load went up, target stayed 5, the owner did 3, and the readout
-- called that a SHORTFALL and the model then invented a cause for it ("오늘
-- 100kg이 좀 빡빡한 것 같습니다"). The prescription was wrong, not the effort.
-- Storing the target PER SET is what makes "was this target valid for that
-- load" answerable at all; without the column there is nothing to compare and
-- the judgement can only be reconstructed from prose.
-- This migration only creates the column. It does NOT compute targets and does
-- NOT backfill any historical target: inventing a retroactive prescription
-- would manufacture exactly the false "미달" verdicts the column exists to stop.
-- Historical rows keep target_reps NULL, which reads as "no prescription of
-- record" -- not as a missed target.
--
-- ==========================================================================
-- WHY hold_sec IS NULLABLE AND NOT BACKFILLED. A hold's duration is not in
-- this table today; it is read from the program slot (`hold_seconds(slot)` in
-- set-arm). Existing hold rows therefore carry no recoverable duration, and no
-- SQL here can conjure one.
--
-- ==========================================================================
-- BACKFILL -- WHAT IS DONE, AND WHAT IS DELIBERATELY NOT.
--
-- DONE (pure SQL, no application logic, no guess): ramp/primer rows.
--   set-add stores set_seq as an INTEGER (argparse type=int) under a documented
--   convention: >=1 is a WORKING set, 0 is the bodyweight/empty-bar primer, -N
--   is ramp rung N. The 'bw' / 'r1' / 'r2' strings the owner sees in the set
--   table are RENDER LABELS derived from those integers at print time -- they
--   are not stored, so this backfill reads a number, not a parsed label.
--   The mapping is also volume-exact: every existing volume/tonnage query is
--   already `set_seq >= 1`, and the derivation table gives ramp
--   counts_toward_volume = false. Labelling set_seq <= 0 as 'ramp' therefore
--   reproduces today's volume semantics EXACTLY -- not one tonnage figure moves.
--
-- NOT DONE (needs application logic -- follow-up, not a guess here):
--   1. prep vs ramp for the set_seq = 0 rung. A genuine bodyweight primer and
--      the empty-bar primer of a barbell lift are the SAME row shape
--      (set_seq = 0, weight_kg NULL); the disambiguator is a property of the
--      EXERCISE (the ax_engine.is_plate_loaded gate: compound + freeweight +
--      a plate lattice, which excludes fixed dumbbells). That gate is Python,
--      and re-deriving it in SQL here would be a second, drifting copy of it.
--      Prep movements proper (band external rotation, overhead shrug) produced
--      NO ROW AT ALL before this migration (defect #2) -- there is nothing in
--      this table to relabel as 'prep'. 'prep' starts being written going
--      forward, by the hook that finally puts prep inside the pipeline.
--   2. hold rows. Nothing in a workout_sets row marks a hold; it would have to
--      be inferred by joining exercise identity (ax_exercise.measure =
--      'hold_sec'), and the resulting row could not be completed -- hold_sec is
--      unrecoverable (above). A 'hold' row with a NULL hold_sec is unrenderable
--      by the cue contract, so mislabelling would be worse than the default.
--      Leaving them 'working' is also volume-neutral: hold and working both
--      count toward volume, so no aggregate changes either way.
--   Both are data-quality follow-ups on HISTORY only. Neither blocks the
--   forward path: every row written after this migration declares its own kind.
-- ==========================================================================

BEGIN;

-- A1: the kind of work this row is. DEFAULT 'working' keeps every existing
-- writer valid unchanged; the CHECK closes the token domain at the one place
-- all writers converge (see rationale above).
ALTER TABLE workout_sets ADD COLUMN set_kind TEXT NOT NULL DEFAULT 'working'
    CHECK (set_kind IN ('prep','ramp','working','hold'));

-- A2: the prescribed rep target for THIS set, at THIS load. NULL = no
-- prescription of record (all history). Never a constant.
ALTER TABLE workout_sets ADD COLUMN target_reps INTEGER;

-- A3: duration for a `hold` set. NULL for every other kind, and NULL for
-- history (the duration lived in the program slot, not in this table).
ALTER TABLE workout_sets ADD COLUMN hold_sec INTEGER;

-- A4: data-aware backfill of the ramp/primer rows. set_seq <= 0 IS the ramp
-- lane by the stored-integer convention; this is a rewrite of an existing fact
-- into an explicit column, not a new claim about the data. Volume-neutral.
UPDATE workout_sets SET set_kind = 'ramp' WHERE set_seq <= 0;

PRAGMA user_version = 34;

COMMIT;
