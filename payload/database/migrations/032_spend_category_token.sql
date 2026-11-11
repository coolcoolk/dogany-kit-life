-- 032_spend_category_token.sql  (target user_version 32)
-- reversible: yes -- the pre-032 label is copied verbatim into display_name
--   BEFORE name is rewritten, so display_name IS the rollback asset.
--   Rollback = UPDATE spend_category SET name = display_name;
--   (optionally followed by UPDATE spend_category SET display_name = NULL;).
--   No row is inserted, deleted or re-keyed: every id is preserved, so every
--   spend_entry.category_id and fixed_cost.category_id reference survives
--   untouched and no orphan can be produced by this migration.
--
-- WHAT. spend_category.name stops being a Korean label and becomes a stable
-- ASCII key. The Korean label moves to display_name -- the column that
-- 017_i18n_tokens added and then never populated (all 11 live rows were NULL).
--
-- WHY THIS TABLE FIRST. Every human-facing reader already projects
-- COALESCE(display_name, name) -- 8 sites in kit_life.py, comments included.
-- Filling display_name with the current name and only then rewriting name is
-- therefore a structural zero-delta for the owner: the COALESCE resolves to
-- display_name exactly where it used to resolve to name, character for
-- character. The i18n win is real (a machine key that is not a natural
-- language) and the render is provably unchanged.
--
-- SEED RECONCILIATION. schema.sql shipped a 17-row seed that the live table
-- never matched (a different 11-row set, different ids, different kinds).
-- The live set is the truth; schema.sql is rewritten to the same 11 rows in
-- this change so a fresh mint reproduces the live shape. The dropped/added
-- rows are itemised in the landing notes -- this migration deliberately does
-- NOT rewrite the pre-032 17-row seed vocabulary, because no live database
-- carries it. A database still holding the old seed must be reconciled by
-- hand rather than by an untested blind UPDATE.
--
-- WRITE PATH. spend_category_get_or_create() is repointed in the same change:
-- it now resolves a caller label against name OR display_name, and registers
-- an unknown label as (name = ASCII key, display_name = label). Without that
-- repoint the owner saying a new category name would insert a Korean value
-- back into name and unwind this migration within a day.

BEGIN;

-- B1: preserve the current label. This MUST run before B2 -- B2 without B1
-- destroys the Korean label with no way back.
UPDATE spend_category
   SET display_name = name
 WHERE display_name IS NULL OR display_name = '';

-- B2: tokenize name. Matched on display_name (not on name), which makes the
-- statement idempotent: a second run finds the same rows and writes the same
-- key. Every target key is new, so name's UNIQUE index cannot collide.
UPDATE spend_category SET name = 'food'         WHERE display_name = '식생활';
UPDATE spend_category SET name = 'cafe_snack'   WHERE display_name = '카페/간식';
UPDATE spend_category SET name = 'household'    WHERE display_name = '생활용품';
UPDATE spend_category SET name = 'housing'      WHERE display_name = '주거/인프라';
UPDATE spend_category SET name = 'health'       WHERE display_name = '건강';
UPDATE spend_category SET name = 'transport'    WHERE display_name = '이동';
UPDATE spend_category SET name = 'leisure'      WHERE display_name = '콘텐츠/취미';
UPDATE spend_category SET name = 'learning'     WHERE display_name = '학습';
UPDATE spend_category SET name = 'travel'       WHERE display_name = '여행';
UPDATE spend_category SET name = 'relationship' WHERE display_name = '관계';
UPDATE spend_category SET name = 'etc'          WHERE display_name = '기타';

PRAGMA user_version = 32;

COMMIT;
