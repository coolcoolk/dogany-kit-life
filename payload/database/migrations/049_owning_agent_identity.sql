-- 049_owning_agent_identity.sql  (target user_version 49)
--
-- reversible: YES, exactly. Rollback = re-run this file's UPDATE with the two
--   strings swapped, then PRAGMA user_version = 48. No row is read, written,
--   moved or re-keyed by this migration; only twelve CHECK-constraint TEXTS in
--   sqlite_master change, and every value that satisfied the old text also
--   satisfies the new one (proof below).
--
-- ==========================================================================
-- WHY -- THE SCHEMA HARDCODES A RETIRED AGENT'S NAME, AND THAT NAME IS THE
-- COLUMN THAT TELLS TWO CREW MEMBERS APART.
--
-- `owning_agent` is the discriminator on the crew-shared kit_life.db: it is
-- what says "this ax_machine row is Warg's gym, not Ag's". Twelve tables
-- constrain it with a CLOSED enumeration:
--
--     CHECK (owning_agent IN ('ag','warg','user'))
--
-- Warg was retired. Any newly minted agent carries its own slug, and the slug
-- is not in that list, so the constraint does not merely mislabel it -- it
-- REFUSES it:
--
--     sqlite3.IntegrityError: CHECK constraint failed:
--         owning_agent IN ('ag','warg','user')
--
-- Measured 2026-09-13 on a fresh mint (freshmint simulation, dsp-...-6949
-- section 5): the probe's first two insert attempts into ax_exercise died on
-- exactly this constraint. Today that is LATENT for the health lane only
-- because nothing writes ax_exercise at all. pack_health 0.11.x seeds that
-- table, and the moment it does, one of two things happens:
--   * the seed is refused outright (new agent, own slug), or
--   * the pack keeps writing the literal 'warg' to get past the CHECK, and a
--     freshly minted health agent's rows land under the RETIRED instance's
--     identity -- and a second health agent collides with the first.
-- Neither is a schema this estate can mint on. The enumeration is the defect.
--
-- ==========================================================================
-- WHAT THIS MIGRATION DOES -- AND WHAT IT REFUSES TO DO.
--
-- It replaces the closed enumeration with the GRAMMAR the column actually
-- means, on all twelve tables:
--
--     CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*')
--
-- i.e. "a non-empty instance slug". It does NOT open the column to free text
-- (a space, a NULL-ish '', a quote, a path fragment and a CJK display label are
-- all still refused), and it does not invent a registry table: there is no
-- crew roster in this DB to point a FK at, and inventing one here would be a
-- data model decision smuggled in on a constraint fix.
--
-- THE CHARACTER SET IS WIDER THAN [a-z0-9_], AND THAT IS A CORRECTION.
-- An earlier draft of this file wrote the grammar as [a-z0-9_] and justified it
-- as "the same grammar mint.sh enforces on DOGANY_AGENT_NAME". That claim is
-- false and was measured false on 2026-09-14: mint.sh enforces NO grammar at
-- all. scripts/mint.sh:189 reads
--     [ -n "$AGENT_NAME" ] || AGENT_NAME="$(basename "$PROJECT_ROOT")"
-- and validates nothing thereafter, so a perfectly ordinary mint --
--     mint.sh --root ~/dogany/Health-Bot
-- -- produces the slug `Health-Bot`, which [a-z0-9_] REFUSES. A constraint
-- written to reject the names the minting tool actually produces is the same
-- defect as the enumeration it replaces, one generation later. [A-Za-z0-9_-] is
-- what a directory basename can carry and still discriminate.
--
-- WIDENING, PROVEN. Every string the old text accepted the new text accepts:
--   'ag'   -> non-empty, all chars in [a-z0-9_]  -> passes
--   'warg' -> same                                -> passes
--   'user' -> same                                -> passes
-- So no existing row can be invalidated, on any lineage, and no backfill,
-- repair or quarantine pass is needed or shipped. The direction is one-way
-- looser; that is the whole change.
--
-- ==========================================================================
-- WHY writable_schema AND NOT A TWELVE-TABLE REBUILD.
--
-- SQLite cannot ALTER a CHECK. The two ways to change one are (a) the
-- twelve-step table rebuild, (b) rewriting the stored schema text.
--
-- (a) was rejected on measured risk, not taste. The twelve tables include
-- five FK TARGETS of each other (ax_exercise <- ax_projection_slot /
-- ax_exercise_tag, ax_machine <- ax_exercise / ax_projection_slot,
-- ax_projection_rule <- ax_projection_slot, ax_tag <- ax_exercise_tag,
-- us_step <- ax_projection_slot). A rebuild of that set means twelve
-- DROP TABLEs with foreign_keys=OFF, copying every row of a live 10MB
-- database, and hand-recreating every index on the way back. Each of those is
-- a way to lose data while changing a constraint that touches none.
--
-- (b) moves zero rows, drops zero indexes, drops zero triggers and cannot
-- re-key anything: it is a REPLACE() over the CREATE TABLE text, matched on
-- the exact 45-character constraint string, guarded to hit exactly twelve
-- rows and refused if it would hit any other number. The cost of (b) is that
-- a malformed replacement would only be discovered when the schema is next
-- parsed -- so the replacement is a fixed string swap (nothing is generated),
-- the schema cookie is bumped so every other connection reparses immediately,
-- and the APPLY NOTE that ships with this file requires
-- `PRAGMA integrity_check` + a reopen + a real INSERT before the run is
-- called done.
--
-- The cookie bump is the CREATE/DROP of a throwaway table at the TOP of the
-- transaction, before the schema text is touched: two ordinary DDL statements
-- against an un-edited schema, whose only job is to leave the schema cookie
-- higher than the value any other connection has cached. Bumping it after the
-- sqlite_master edit would mean issuing DDL against a schema this connection
-- can no longer trust.
--
-- ==========================================================================
-- WHAT THIS MIGRATION DELIBERATELY LEAVES ALONE.
--
-- The column DEFAULT. All twelve tables declare
--     owning_agent TEXT NOT NULL DEFAULT 'warg'
-- and that default still says 'warg' after this file runs. It is left because
-- there is no per-database value a DDL default could carry -- the instance slug
-- is not knowable at CREATE TABLE time -- and because dropping the default
-- outright (making every omitting INSERT fail NOT NULL) is a behaviour change
-- to writers this file has no business making while it is changing a
-- constraint. It is reachable only by an INSERT that omits the column, and as
-- of 2026-09-14 no production writer in kit_life.py or in pack_health's
-- ax_engine.py does (verified by reading all of them; every one names
-- owning_agent explicitly and passes a resolved slug). Named here rather than
-- left to be rediscovered.
--
-- why.managing_agent. Line ~978 of schema.sql carries a SECOND closed
-- enumeration, CHECK (managing_agent IS NULL OR managing_agent IN
-- ('ag','warg','user')), on a different column of a different table. It has the
-- same shape and will need the same repair, but it is not owning_agent, it is
-- not one of the twelve, and widening it is not what this file was opened for.
-- kit_life._WHY_AGENTS mirrors it in code and degrades to NULL rather than
-- tripping it, so it fails soft where owning_agent failed hard.
--
-- ==========================================================================
-- PARITY. schema.sql (fresh mint) carries the identical twelve CHECK texts
-- and stamps 49. A fresh mint is therefore born able to write under its own
-- slug; only pre-049 databases need this file.
--
-- ==========================================================================
-- GUARD. Three-sided:
--   1. user_version must be EXACTLY 48, checked BEFORE any transaction.
--   2. the twelve owning_agent tables must be PRESENT and must carry one of the
--      two known constraint texts BEFORE the swap. A lineage missing tables, or
--      carrying a third text nobody wrote, is refused rather than half-migrated.
--   3. ZERO rows may still carry the old text AFTER the swap.
--
-- GUARD 2 COUNTS SHAPE, NOT STATE -- a correction, 2026-09-14. The first draft
-- of this file required all twelve to still carry the OLD text. That reads as
-- stricter and is actually a false refusal: the estate's own chain-parity test
-- (tests/test_mig037_judgment_layer.py) builds a synthetic lineage by minting
-- from HEAD schema.sql, rewinding user_version to 36, dropping the 047 objects
-- and re-stepping 037..head -- so when 049 runs there, eleven tables already
-- carry the NEW text (they came from HEAD schema.sql) and exactly one, the
-- pain_event that 047 just recreated, carries the old one. `= 12 old` refused
-- that, took the whole chain down with it, and refused it for a reason that has
-- nothing to do with what 049 changes. Counting the twelve tables and letting
-- each carry either known text accepts every real pre-049 database (at
-- user_version 48 all twelve are old -- guard 1 has already established that)
-- and the ladder's own mixed intermediate, while still refusing a DB that is
-- not the twelve-table shape. What the FINAL state must be is not weakened at
-- all: guard 3 still demands zero old texts after the swap.
-- Any of the three failing aborts under .bail with the transaction rolled
-- back and user_version still 48.
--
-- ==========================================================================
-- APPLY NOTE -- WHO RUNS THIS, IN WHAT ORDER, AND WHAT TO CHECK AFTER.
--
-- NOTHING IN THIS COMMIT HAS BEEN APPLIED TO ANY DATABASE. The shared file
-- ~/.dogany/crews/kit_life/kit_life.db was not opened while this was authored
-- (verified by mtime, before and after). What ships is this file, the matching
-- schema.sql texts, and the EXPECTED_USER_VERSION 48->49 pin. Applying it is
-- the kit owner's act, in the main session.
--
-- THE SHARED DB IS CREW PROPERTY, NOT INSTANCE PROPERTY. Measured 2026-09-14:
--     ~/.dogany/agents/Ag/database/kit_life.db    -> crews/kit_life/kit_life.db
--     ~/.dogany/agents/Warg/database/kit_life.db  -> crews/kit_life/kit_life.db
--   both at EXPECTED_USER_VERSION 48.
-- ONE physical file, so PRAGMA user_version is crew-scoped. pack_install.sh's
-- _kit_crew_lockstep_guard (DGN-1436) enforces exactly this: it REFUSES to
-- migrate while any discovered crew member's database/kit_life.py still pins a
-- different version. So the order below is not a preference, it is the only
-- order the installer will accept.
--
--   0. BACKUP the shared DB first. This migration is reversible by design (the
--      rollback is one UPDATE with the two strings swapped plus
--      `PRAGMA user_version = 48`), but a file-level copy costs nothing and is
--      the only thing that covers a writable_schema edit going wrong in a way
--      the guards do not catch:
--        cp ~/.dogany/crews/kit_life/kit_life.db \
--           ~/.dogany/crews/kit_life/kit_life.db.pre049
--
--   1. Land the payload on EVERY crew member that mounts the shared DB, so all
--      pins read 49 before anything migrates. Today that is Ag and Warg:
--        scripts/pack/pack_install.sh Ag   <Ag-root>   --pack kit_life --upgrade
--        scripts/pack/pack_install.sh Warg <Warg-root> --pack kit_life --upgrade
--      The first of these also runs the kit-migrate step; if a member still
--      lags, the guard fails the install CLOSED and nothing is written. That is
--      the designed outcome -- re-run after the lagging member is updated.
--
--   2. VERIFY, in this order. Each line is a fact, not a reassurance:
--        sqlite3 <db> 'PRAGMA user_version;'                  -> 49
--        sqlite3 <db> "SELECT COUNT(*) FROM sqlite_master
--                      WHERE type='table' AND instr(sql,
--                      'CHECK (owning_agent IN (''ag'',''warg'',''user''))')>0;"
--                                                             -> 0
--        sqlite3 <db> 'PRAGMA integrity_check;'               -> ok
--        sqlite3 <db> 'PRAGMA foreign_key_check;'             -> (no output)
--      Then REOPEN (a new process, not the migrating connection) and do a REAL
--      write under a slug that is not in the old enumeration -- the swap is a
--      schema-text edit, so "it parses on reopen" is the thing actually under
--      test:
--        sqlite3 <db> "INSERT INTO ax_tag VALUES('01TEST049VERIFY0000000001',
--          'verify049','muscle','_probe049',NULL,'active','2026-01-01');"
--        sqlite3 <db> "DELETE FROM ax_tag WHERE value='_probe049';"
--      An IntegrityError on that INSERT means the swap did not take; a syntax
--      error on reopen means it took badly. Either way: restore the .pre049 copy.
--
--   3. THEN, and only then, run the pack_health taxonomy seed. Run it BEFORE
--      the migration and its ax_exercise rows land under the legacy slug
--      ('warg') instead of the instance -- the seed says so on stderr rather
--      than doing it quietly, but re-running after the migration does NOT
--      re-stamp rows that already exist (INSERT OR IGNORE never clobbers).
--      Order matters here and nowhere else:
--        python3 <root>/service/health/health_taxonomy_seed.py --db <db> --check
--        python3 <root>/service/health/health_taxonomy_seed.py --db <db> --apply
--      or, equivalently and idempotently, the pack's own lane:
--        <root>/routines/bundle/health-install-ensure.sh
--
-- ROLLBACK, if it is ever needed: run this file's UPDATE with the two strings
-- swapped, then `PRAGMA user_version = 48`, then step every crew member's pin
-- back to 48 -- same lockstep, opposite direction. No row is touched either way.
.bail on
.timeout 5000

-- guard 1: version
CREATE TEMP TABLE _guard_049(seen_user_version);
CREATE TEMP TRIGGER _guard_049_refuse BEFORE INSERT ON _guard_049 BEGIN
  SELECT RAISE(FAIL, 'migration 049 refused: this database is not at user_version 48 -- either 049 was already applied (>= 49) or the 021~048 chain has not run (< 48). Nothing changed');
END;
INSERT INTO _guard_049
  SELECT user_version FROM pragma_user_version WHERE user_version <> 48;
DROP TABLE temp._guard_049;

BEGIN IMMEDIATE;

-- guard 2: the twelve tables carrying the closed enumeration must be there.
CREATE TEMP TABLE _guard_049_pre(seen_count);
CREATE TEMP TRIGGER _guard_049_pre_refuse BEFORE INSERT ON _guard_049_pre BEGIN
  SELECT RAISE(FAIL, 'migration 049 refused: expected exactly 12 tables constraining owning_agent with one of the two known texts (the closed enumeration, or this file''s slug grammar) -- found a different number. This is not the twelve-table shape this file was written against. Nothing changed');
END;
INSERT INTO _guard_049_pre
  SELECT COUNT(*) FROM sqlite_master
   WHERE type='table'
     AND (instr(sql, 'CHECK (owning_agent IN (''ag'',''warg'',''user''))') > 0
          OR instr(sql, 'CHECK (owning_agent <> '''' AND owning_agent NOT GLOB ''*[^A-Za-z0-9_-]*'')') > 0)
  HAVING COUNT(*) <> 12;
DROP TABLE temp._guard_049_pre;

-- Schema-cookie bump (see header). Two ordinary DDL statements against the
-- UN-edited schema, so every other connection reparses after COMMIT.
CREATE TABLE _mig049_cookie(x);
DROP TABLE _mig049_cookie;

-- The swap. Fixed string in, fixed string out -- nothing is generated.
PRAGMA writable_schema = ON;
UPDATE sqlite_master
   SET sql = replace(sql,
        'CHECK (owning_agent IN (''ag'',''warg'',''user''))',
        'CHECK (owning_agent <> '''' AND owning_agent NOT GLOB ''*[^A-Za-z0-9_-]*'')')
 WHERE type='table'
   AND instr(sql, 'CHECK (owning_agent IN (''ag'',''warg'',''user''))') > 0;
PRAGMA writable_schema = OFF;

-- guard 3: nothing may still carry the old text.
CREATE TEMP TABLE _guard_049_post(seen_count);
CREATE TEMP TRIGGER _guard_049_post_refuse BEFORE INSERT ON _guard_049_post BEGIN
  SELECT RAISE(FAIL, 'migration 049 refused: the closed enumeration survived the swap on at least one table -- rolled back, nothing changed');
END;
INSERT INTO _guard_049_post
  SELECT COUNT(*) FROM sqlite_master
   WHERE type='table'
     AND instr(sql, 'CHECK (owning_agent IN (''ag'',''warg'',''user''))') > 0
  HAVING COUNT(*) <> 0;
DROP TABLE temp._guard_049_post;

-- Lockstep stamp. MUST be the last statement before COMMIT: if anything above
-- failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 48.
PRAGMA user_version = 49;
COMMIT;
