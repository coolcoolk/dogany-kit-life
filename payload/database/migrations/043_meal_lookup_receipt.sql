-- 043_meal_lookup_receipt.sql  (target user_version 43)
--
-- reversible: a down-migration is trivially available and does NOT ship --
--   `DROP TABLE meal_lookup_receipt; PRAGMA user_version = 42;`.  The table
--   holds process evidence only (which name the lookup step was handed, and
--   when), never anything the owner typed, so dropping it destroys no owner
--   data.  It also needs no matching code rollback: the reader fails OPEN on
--   a missing table (see below), so a 2.7.0 payload running against a
--   rolled-back database records meals exactly as it did before 043.
--   A FAILED apply needs no rollback either: guard first, single
--   transaction, .bail on, stamp last (023 discipline).
--
-- WHY -- PROSE CARRIES NO EXECUTION ORDER.
--
-- The diet skill's SKILL.md states the order in sentences: resolve the food
-- against the shared cache FIRST, estimate only on a miss.  Measured
-- 2026-09-09 (owner report, DGN-928): the model skips the ordered lookup and
-- writes an LLM estimate OVER a value already sitting in meal_food with a
-- measured label.  That is not a compliance failure to be re-worded.  A step
-- that exists only as a sentence is read associatively, not executed in
-- sequence, so it runs sometimes.  The only fix that holds is structural --
-- the record step has to be physically unreachable until the deterministic
-- lookup step has actually run.  (hot.framework: "rule unfollowed -> design a
-- forcing point", wired into the decision-point it governs.)
--
-- WHY A RECEIPT, AND NOT AN INFERENCE FROM THE CACHE.
--
-- The cheap gate is "this food is in meal_food, so the lookup must have run"
-- -- derive the precondition from state that already exists.  That was
-- attempt 1, and this estate's own data refutes it.  meal_food's PRIMARY KEY
-- is normalize_food_key(key_name); the stored display `name` is a DIFFERENT
-- string, so name -> normalize -> key does not round-trip:
--
--     key   'ttf 3 things for 500kg 파우더'
--     name  'TTF 3 THINGS FOR 500KG (사과맛) 파우더'
--     norm  'ttf 3 things for 500kg 사과맛 파우더'    -> found = False
--
-- Measured over the live cache on 2026-09-09: 101 of 200 meal_food rows
-- (50.5%) do not round-trip.  A presence gate would have refused HALF the
-- foods that are correctly cached, on the very lookups it exists to reward.
-- A gate with false refusals is worse than no gate at all: the model learns
-- to pass the escape flag reflexively and the gate survives in name only.
--
-- So the lookup step RECORDS that it ran, and it stores the caller's string
-- VERBATIM.  name_given is never normalized, on either side, so the reader's
-- comparison round-trips BY CONSTRUCTION -- which is precisely what attempt 1
-- lacked.  It also expresses a failure attempt 1 could not: looked up A,
-- recorded B is now a refusal, because B has no receipt.
--
-- SHAPE, AND WHY EACH COLUMN IS THE WAY IT IS.
--
--   name_given  PRIMARY KEY.  A re-lookup REFRESHES ts in place instead of
--               appending a row -- idempotent by construction, and the table
--               is bounded by distinct food names rather than by meals
--               logged.  The explicit NOT NULL is NOT redundant: in a SQLite
--               rowid table a declared PRIMARY KEY still admits NULL (the
--               documented legacy quirk), and a NULL receipt would match
--               nothing while occupying the one slot that matters.
--   norm_key    DIAGNOSTICS ONLY.  It exists so a human reading this table
--               can SEE the round-trip gap quoted above with their own eyes.
--               Nothing reads it to decide a match, and nothing may start:
--               matching on norm_key IS attempt 1, re-introduced through a
--               column instead of a query.
--   mode        which door wrote it -- 'name' | 'barcode' | 'cache-add'.
--               Deliberately unconstrained: a CHECK would demand a 12-step
--               table rebuild the day a fourth lookup door lands, and an
--               unknown token degrades to "no receipt for this name"
--               (fail-safe), never to a wrong match.
--   ts          canonical UTC-Z, the estate's one instant format (036/038:
--               no second clock).  The TTL test is then a plain
--               lexicographic compare against a cutoff string minted in the
--               same format -- no parsing, no timezone, at read time.
--
-- NO index beyond the PK: every read is a single equality probe on
-- name_given, which the PK already serves.
--
-- NO BACKFILL, deliberately.  Seeding a receipt for each of the 200 cached
-- foods would assert that a lookup ran for each of them, which never
-- happened.  That is the same false attribution 042 had to remove from
-- metric_freshness (declared_by='user' for an answer the owner was never
-- asked to give).  The table starts EMPTY and the first real lookup writes
-- the first row; until then every meal_add either carries the escape flag or
-- is refused with an instruction, which is the honest state.
--
-- GUARD.  Both-sided, 037/040 discipline: refused unless user_version is
-- EXACTLY 42, BEFORE any transaction or write -- exit != 0, database
-- untouched (a re-apply refused; a skipped 021~042 chain refused).
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_043(seen_user_version);
CREATE TEMP TRIGGER _guard_043_refuse BEFORE INSERT ON _guard_043 BEGIN
  SELECT RAISE(FAIL, 'migration 043 refused: this database is not at user_version 42 -- either 043 was already applied (>= 43) or the 021~042 chain has not run (< 42). Nothing changed');
END;
INSERT INTO _guard_043
  SELECT user_version FROM pragma_user_version WHERE user_version <> 42;
DROP TABLE temp._guard_043;
BEGIN IMMEDIATE;

-- The receipt.  One row per distinct food name the lookup step was handed.
CREATE TABLE meal_lookup_receipt (
  name_given TEXT NOT NULL,
  norm_key   TEXT,
  mode       TEXT NOT NULL,
  ts         TEXT NOT NULL,
  PRIMARY KEY (name_given)
);

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 42.
PRAGMA user_version = 43;
COMMIT;
