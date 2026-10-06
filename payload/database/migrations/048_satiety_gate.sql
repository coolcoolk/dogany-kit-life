-- 048_satiety_gate.sql  (target user_version 48)
--
-- reversible: YES, losslessly.  Rollback =
--   (SQLite >= 3.35) ALTER TABLE meal_lookup_receipt DROP COLUMN resolved;
--   PRAGMA user_version = 47;
-- One ADD COLUMN on one table.  No rebuild, no trigger, no view, no seed, no
-- backfill.  Not one existing row is re-keyed or read-repointed: every current
-- row keeps its four values and gets NULL in the new column, which is exactly
-- "this question was never asked of that lookup" (see FAIL-OPEN below).
--
-- ==========================================================================
-- WHY -- ONE HALF OF THE MEAL LOOP IS CODE-ENFORCED AND THE OTHER HALF IS NOT.
--
-- 043 closed a defect of the same shape as this one: the SKILL.md said "look
-- the food up first, estimate only on a miss", prose could not carry that
-- order, the model skipped the step, and estimates landed on top of measured
-- cache values.  The fix was not a louder sentence -- it was a receipt.
-- meal_add refuses a name with no live lookup receipt, so the ordered step is
-- gated at the execution point instead of being asked for in prose.
--
-- The satiety question has the identical shape and never got the identical
-- fix.  meal-log/SKILL.md says: on the LLM-estimate branch, before
-- meal-add commits, ask how full the meal left the user, and store the answer
-- through mealstate.py.  The STORAGE half is code-enforced already
-- (fullness-set refuses any token off the owner's 7-level ladder).  The ASKING
-- half is model judgement, and SKILL.md says so in its own words:
--
--     "no hook gates the estimate-branch meal-add on a satiety answer having
--      been taken.  That missing gate is the gap, named here, not papered."
--
-- Measured on the live instance 2026-09-12: fullness readings = 0.  Not "few"
-- -- zero, across 400+ meal blocks.  The question has never once been asked.
-- Same day, the owner's lunch (5 items, all cache misses, all estimated) was
-- recorded with no satiety prompt: precisely the branch the spec says must
-- ask.  A rule that is followed zero times out of four hundred is not a rule
-- that needs restating; it needs the forcing point its neighbour already has.
--
-- ==========================================================================
-- WHAT THIS MIGRATION DOES -- AND THE ONE THING IT REFUSES TO INFER.
--
-- The gate needs to know one fact at meal_add time: was this name's
-- portion/macros pinned by an exact match (shared cache / barcode / nutrition
-- label / public DB answer), or is it genuinely a guess?  Only the guess
-- branch asks.
--
-- That fact is NOT derivable at the write.  The obvious cheap test -- "is this
-- name in meal_food?" -- is the very test 043 rejected on measurement: the
-- cache key is the normalized input while the recorded display string is a
-- different string, and 101 of 200 live rows (50.5%) do not round-trip.  A
-- gate built on it would ask the satiety question on half the meals that were
-- in fact exact hits.  Asking a question the spec says not to ask is how a
-- gate teaches the caller to reach for the bypass flag.
--
-- So the fact is RECORDED where it is known instead of inferred where it is
-- not.  lookup.py already distinguishes hit from miss -- it is the branch it
-- takes.  This column is that branch, written down:
--
--     resolved = 'exact'     the lookup ANSWERED (shared-cache hit, barcode
--                            product, nutrition label, public-DB item).  The
--                            numbers are pinned.  No satiety question.
--     resolved = 'estimate'  the lookup RAN and found nothing.  Whatever
--                            macros follow are a guess.  Satiety is asked.
--     resolved IS NULL       the question was never put to this receipt: a row
--                            written before 048, or by a caller that does not
--                            set it.
--
-- ==========================================================================
-- FAIL-OPEN, ON PURPOSE, IN THE SAME PLACE 043 PUT IT.
--
-- NULL means "unknown", and unknown resolves to RECORD, never to refuse.  The
-- owner must never be unable to log a meal (DGN-928, and the 043 docstring
-- says it in his register).  A satiety gate that fires on ambiguity would
-- block every pre-048 name and every caller that has not been taught the new
-- argument -- a refusal nobody can act on, which is the refusal that gets
-- bypassed reflexively until the gate is a comment.
--
-- Consequence, stated rather than hidden: the gate covers meals recorded
-- THROUGH the lookup door from 048 onward.  It does not retroactively ask
-- about the 400+ meals already logged, and it cannot ask about a name that
-- reached meal_add without a receipt at all (that path is 043's refusal, not
-- this one's).  NO BACKFILL is possible and none is attempted -- whether a
-- meal from 2026-08-14 was estimated is not a fact this database holds, and
-- inventing it would put a guessed value in a column built to end guessing.
--
-- ==========================================================================
-- WHAT THIS MIGRATION DELIBERATELY DOES NOT DECIDE.
--
-- SKILL.md carries four owner-open TBDs on this feature: which of the 7 levels
-- revise the estimate up vs down vs keep, the adjustment magnitude, the exact
-- trigger condition for composite meals, and the question wording.  None of
-- them are settled here and none of them are needed to close the gap: the gate
-- asks whether an ANSWER EXISTS, never what the answer means.  v0.1 collects.
-- Reading the collected answers back into an estimate adjustment is a later
-- cut, and it will have data to be designed against only if the asking starts
-- now.
--
-- No CHECK constraint on the token set.  The writer (kit_life.lookup_receipt_
-- resolve) validates, the same division of labour 035 used for place.address:
-- a two-token vocabulary that a later cut may widen (a 'label' or 'api' grade
-- is plausible) does not earn a table rebuild to change.

-- ==========================================================================
-- GUARD.  Both-sided, 037/040/044/045/046/047 discipline: refused unless
-- user_version is EXACTLY 47, BEFORE any transaction or write -- exit != 0,
-- database untouched (a re-apply refused; a skipped 021~047 chain refused).
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_048(seen_user_version);
CREATE TEMP TRIGGER _guard_048_refuse BEFORE INSERT ON _guard_048 BEGIN
  SELECT RAISE(FAIL, 'migration 048 refused: this database is not at user_version 47 -- either 048 was already applied (>= 48) or the 021~047 chain has not run (< 47). Nothing changed');
END;
INSERT INTO _guard_048
  SELECT user_version FROM pragma_user_version WHERE user_version <> 47;
DROP TABLE temp._guard_048;
BEGIN IMMEDIATE;

-- A1: the recorded branch.  NULL for every existing row = "never asked".
ALTER TABLE meal_lookup_receipt ADD COLUMN resolved TEXT;

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything above
-- failed, .bail aborted this run, the transaction rolled back, and user_version
-- stays 47.
PRAGMA user_version = 48;
COMMIT;
