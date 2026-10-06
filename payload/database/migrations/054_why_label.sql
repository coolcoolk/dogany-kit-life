-- 054_why_label.sql  (target user_version 54)
--
-- reversible: YES, losslessly.  Rollback (SQLite >= 3.35) =
--   ALTER TABLE life_why DROP COLUMN label;
--   then this file's writable_schema UPDATE with the two CHECK strings
--   swapped, then PRAGMA user_version = 53.
-- One NULLable column appended to life_why, one CHECK text widened on the
-- same table.  No row is read, written, moved or re-keyed: every existing
-- row reads label = NULL, and every managing_agent value that satisfied the
-- old CHECK satisfies the new one (proof below).
--
-- ==========================================================================
-- WHY (1) -- THE ASPIRATION IS STORED AS A SENTENCE, AND IT IS READ BACK
-- INSIDE A SENTENCE.
--
-- Product rule: the aspiration was stored wrong -- it was left as a
-- sentence, and it must be stored as a short label from input time.  The handover brief
-- reads the aspiration into a slot -- "지금 세워두신 '<X>' 방향과 함께, ..."
-- -- and the live row's `text` is the owner's whole utterance (three clauses,
-- two full stops), so the slot rendered a paragraph inside quotes.  The
-- defect is not the renderer: `text` is the SOURCE (stored verbatim on
-- purpose, see kit_life.py "THE TEXT IS THE EVIDENCE") and a source is not
-- slot-ready.  What was missing is the second value the owner described: a
-- SHORT phrase fixed at input time ('튼튼한 코어', '건강하고 이쁜 몸').
-- Canonical rule: DESIGN-SYSTEM.md R9 W12 ("Stored user intent must be
-- slot-ready ... keep the raw text as source").
--
-- So: life_why.label TEXT, NULLable.  `text` is unchanged in meaning and
-- content -- still the raw utterance.  NULL label = "no short phrase agreed
-- yet"; readers that render into a slot omit the aspiration clause rather
-- than quote the raw sentence, and the kit's touchpoint line
-- (WHY-LABEL-ASK) asks the owner once to confirm a proposed phrase.
-- NO BACKFILL: a label is an owner-confirmed phrase, and inventing one for
-- the existing row here would be exactly the silent paraphrase the text
-- column exists to prevent.
--
-- ==========================================================================
-- WHY (2) -- THE SAME LATENT DEFECT 049 FIXED, ON THE COLUMN 049 LEFT.
--
-- 049 replaced the closed enumeration CHECK (owning_agent IN
-- ('ag','warg','user')) on twelve tables with the slug grammar, and named
-- life_why.managing_agent under "WHAT THIS MIGRATION DELIBERATELY LEAVES
-- ALONE": same shape, different column, not what 049 was opened for.  It is
-- what this file is opened for.  A freshly minted health agent carries its
-- own slug, the closed list refuses it, and kit_life._WHY_AGENTS degraded the
-- write to managing_agent = NULL -- a why the new steward could not claim.
-- This file widens it with 049's grammar, verbatim, NULL still allowed:
--
--   old: CHECK (managing_agent IS NULL OR managing_agent IN ('ag','warg','user'))
--   new: CHECK (managing_agent IS NULL OR (managing_agent <> '' AND
--               managing_agent NOT GLOB '*[^A-Za-z0-9_-]*'))
--
-- WIDENING, PROVEN.  NULL passes both.  'ag', 'warg', 'user' are non-empty
-- and every character is in [a-z], so each passes the new text.  No existing
-- row can be invalidated; no repair pass is needed or shipped.
--
-- ==========================================================================
-- HOW -- ALTER for the column, writable_schema for the CHECK (049's method).
--
-- The column is an ordinary ALTER TABLE ADD COLUMN (052's idiom).  SQLite
-- cannot ALTER a CHECK, and a rebuild of life_why means dropping a table that
-- is an FK target (ledger_goal.why_id, life_why.superseded_by) -- so the CHECK
-- is a fixed-string REPLACE() over the stored CREATE TABLE text, guarded to
-- hit exactly one row, exactly as 049 did for its twelve.  ORDER MATTERS: the
-- ALTER runs FIRST, against the un-edited schema, and doubles as the schema
-- cookie bump 049 did with a throwaway table -- every other connection
-- reparses after COMMIT, and no DDL is issued after the text edit.
--
-- ==========================================================================
-- GUARD.  Three-sided, 049 discipline:
--   1. user_version must be EXACTLY 53, checked BEFORE any transaction.
--   2. life_why must be PRESENT, must NOT already carry `label`, and must
--      carry one of the two known managing_agent CHECK texts (old, or this
--      file's) -- SHAPE, not state, for the reason 049's header gives: the
--      estate's chain-parity test (test_mig037_judgment_layer.py) re-steps
--      the ladder over a HEAD-schema mint, where the CHECK is already new.
--   3. ZERO rows may still carry the old CHECK text AFTER the swap.
-- Any failure aborts under .bail, transaction rolled back, user_version 53.
--
-- ==========================================================================
-- APPLY NOTE -- same order and same checks as 049.
--
-- NOTHING IN THIS COMMIT HAS BEEN APPLIED TO ANY DATABASE.  The shared crew
-- DB was opened read-only (mode=ro&immutable=1) to measure the live row and
-- the stored CHECK text, never for writing.  Applying is the kit owner's act.
--
--   0. BACKUP the shared DB first -- this is a writable_schema edit, and a
--      file copy is the only thing that covers a failure the guards miss:
--        cp ~/.dogany/crews/kit_life/kit_life.db \
--           ~/.dogany/crews/kit_life/kit_life.db.pre054
--
--   1. Land the payload on EVERY crew member that mounts the shared DB so all
--      pins read 54 before anything migrates (pack_install.sh's
--      _kit_crew_lockstep_guard refuses otherwise, DGN-1436):
--        scripts/pack/pack_install.sh <Agent> <root> --pack kit_life --upgrade
--
--   2. VERIFY, in this order:
--        sqlite3 <db> 'PRAGMA user_version;'                  -> 54
--        sqlite3 <db> "SELECT COUNT(*) FROM pragma_table_info('life_why')
--                      WHERE name='label';"                   -> 1
--        sqlite3 <db> "SELECT COUNT(*) FROM sqlite_master WHERE instr(sql,
--                      'managing_agent IN (''ag'',''warg'',''user'')')>0;"
--                                                             -> 0
--        sqlite3 <db> 'PRAGMA integrity_check;'               -> ok
--        sqlite3 <db> 'PRAGMA foreign_key_check;'             -> (no output)
--      Then REOPEN (a new process) and do a REAL write under a slug the old
--      list refused -- the swap is a schema-text edit, so "it parses on
--      reopen and accepts the new slug" is the thing under test:
--        sqlite3 <db> "INSERT INTO life_why (ulid,text,scope,managing_agent)
--          VALUES ('01TEST054VERIFY0000000001','_probe054','life','Health-Bot');"
--        sqlite3 <db> "DELETE FROM life_why WHERE text='_probe054';"
--      An IntegrityError means the swap did not take; a syntax error on
--      reopen means it took badly.  Either way: restore the .pre054 copy.
--
--   3. The live row (life_why id 1) keeps label NULL.  Its short phrase is
--      proposed to the owner at the next aspiration touchpoint and written
--      with `kit_life.sh why-label-set 1 "<phrase>"` on confirmation.
.bail on
.timeout 5000

-- guard 1: version
CREATE TEMP TABLE _guard_054(seen_user_version);
CREATE TEMP TRIGGER _guard_054_refuse BEFORE INSERT ON _guard_054 BEGIN
  SELECT RAISE(FAIL, 'migration 054 refused: this database is not at user_version 53 -- either 054 was already applied (>= 54) or the 021~053 chain has not run (< 53). Nothing changed');
END;
INSERT INTO _guard_054
  SELECT user_version FROM pragma_user_version WHERE user_version <> 53;
DROP TABLE temp._guard_054;

BEGIN IMMEDIATE;

-- guard 2: life_why present, no label yet, one of the two known CHECK texts.
CREATE TEMP TABLE _guard_054_pre(seen_count);
CREATE TEMP TRIGGER _guard_054_pre_refuse BEFORE INSERT ON _guard_054_pre BEGIN
  SELECT RAISE(FAIL, 'migration 054 refused: expected life_why WITHOUT a label column and carrying one of the two known managing_agent CHECK texts -- this is not the shape this file was written against. Nothing changed');
END;
INSERT INTO _guard_054_pre
  SELECT COUNT(*) FROM sqlite_master
   WHERE type='table' AND name='life_why'
     AND (instr(sql, 'CHECK (managing_agent IS NULL OR managing_agent IN (''ag'',''warg'',''user''))') > 0
          OR instr(sql, 'CHECK (managing_agent IS NULL OR (managing_agent <> '''' AND managing_agent NOT GLOB ''*[^A-Za-z0-9_-]*''))') > 0)
     AND NOT EXISTS (SELECT 1 FROM pragma_table_info('life_why')
                      WHERE name='label')
  HAVING COUNT(*) <> 1;
DROP TABLE temp._guard_054_pre;

-- The short, slot-ready phrase (W12).  NULL for every existing row.  Also the
-- schema-cookie bump: ordinary DDL against the UN-edited schema (see header).
ALTER TABLE life_why ADD COLUMN label TEXT;

-- The swap.  Fixed string in, fixed string out -- nothing is generated.
PRAGMA writable_schema = ON;
UPDATE sqlite_master
   SET sql = replace(sql,
        'CHECK (managing_agent IS NULL OR managing_agent IN (''ag'',''warg'',''user''))',
        'CHECK (managing_agent IS NULL OR (managing_agent <> '''' AND managing_agent NOT GLOB ''*[^A-Za-z0-9_-]*''))')
 WHERE type='table' AND name='life_why'
   AND instr(sql, 'CHECK (managing_agent IS NULL OR managing_agent IN (''ag'',''warg'',''user''))') > 0;
PRAGMA writable_schema = OFF;

-- guard 3: nothing may still carry the old text.
CREATE TEMP TABLE _guard_054_post(seen_count);
CREATE TEMP TRIGGER _guard_054_post_refuse BEFORE INSERT ON _guard_054_post BEGIN
  SELECT RAISE(FAIL, 'migration 054 refused: the closed managing_agent enumeration survived the swap -- rolled back, nothing changed');
END;
INSERT INTO _guard_054_post
  SELECT COUNT(*) FROM sqlite_master
   WHERE type='table'
     AND instr(sql, 'managing_agent IN (''ag'',''warg'',''user'')') > 0
  HAVING COUNT(*) <> 0;
DROP TABLE temp._guard_054_post;

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 53.
PRAGMA user_version = 54;
COMMIT;
