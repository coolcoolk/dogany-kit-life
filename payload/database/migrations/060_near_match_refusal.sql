-- 060_near_match_refusal.sql  (target user_version 60)
--
-- reversible: YES, losslessly. Additive only: one new table, no seed rows,
--   no existing row or column changed. Rollback = DROP TABLE
--   near_match_refusal; PRAGMA user_version = 59;  (the only loss is the
--   owner's remembered "not the same" answers -- the question comes back once.)
--
-- ==========================================================================
-- WHAT. The memory of the owner's "No" to a near-match question
-- (near_match.py).  Every name/alias create path now asks ONE question when
-- the new string looks like an existing row ("혹시 학동유디치과랑 같은
-- 곳인가요?").  Yes -> the string becomes an alias of that row, and exact
-- matching finds it from then on, so Yes needs no table.  No -> the create
-- goes ahead with an explicit confirmation, and THIS row is what stops the
-- same pair being asked again (owner 2026-09-28: every resolver must catch the
-- variants he produces from memory -- and a question answered twice is the
-- failure this whole change exists to end).
--
-- KEY.  (domain, query_key, existing_ref).  query_key is near_match.compact()
-- of the string the owner said -- NFKC, casefold, no whitespace/punctuation --
-- so "유디 학동치과" and "유디학동치과" are one answer.  existing_ref is TEXT
-- because the domains key their rows differently (place/person/method: the
-- integer id; food: the meal_food key; exercise: the snake_case name).  No FK:
-- five parent tables, and a refusal naming a row that later dies is harmless
-- (the candidate it suppresses no longer exists).
-- domain is CHECKed: a typo would store a refusal nothing ever reads.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_060(seen_user_version);
CREATE TEMP TRIGGER _guard_060_refuse BEFORE INSERT ON _guard_060 BEGIN
  SELECT RAISE(FAIL, 'migration 060 refused: this database is not at user_version 59 -- either 060 was already applied (>= 60) or the chain up to 059 has not run (< 59). Nothing changed');
END;
INSERT INTO _guard_060
  SELECT user_version FROM pragma_user_version WHERE user_version <> 59;
DROP TABLE temp._guard_060;

BEGIN IMMEDIATE;

CREATE TABLE near_match_refusal (
    domain       TEXT NOT NULL CHECK (domain IN
                   ('place','person','method','food','exercise')),
    query_key    TEXT NOT NULL CHECK (query_key <> ''),
    existing_ref TEXT NOT NULL CHECK (existing_ref <> ''),
    query_text   TEXT NOT NULL,
    said         TEXT,
    refused_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (domain, query_key, existing_ref)
);

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 60;
COMMIT;
