-- 051_ui_label_gate.sql  (target user_version 51)
--
-- reversible: YES, exactly.  Rollback = `DROP TABLE ui_gate; DROP TABLE
--   ui_label; PRAGMA user_version = 50;`  This file creates two NEW tables and
--   seeds only those two tables.  It reads no pre-existing row, writes no
--   pre-existing row, re-keys nothing, drops nothing, and alters no existing
--   constraint text -- so there is no state a rollback would have to repair.
--   A FAILED apply needs no rollback either: single transaction, .bail on,
--   the version stamp last (023 discipline).
--
-- ==========================================================================
-- WHY.  THE SPEECH REGISTER BINDS ONLY THE MODEL.  TOOL SURFACES ARE CODE,
-- AND CODE CANNOT READ A SENTENCE.
--
-- kit_life 2.22.0 and pack_health 0.12.0 landed a DOMAIN SPEECH REGISTER: the
-- model speaks plainly by default and promotes to precision vocabulary (RIR,
-- sub-muscle names, tempo) only on two triggers.  That register has exactly
-- one home (the pack's workout-program SKILL.md) and it is correct -- for what
-- it binds.  What it binds is PROSE THE MODEL COMPOSES.
--
-- It does not bind the set table, the set-logging keyboard, the next-set hint
-- line or the floor warning, because those strings are not composed by anyone
-- -- they are RENDERED by deterministic code (the pack's
-- service/health/{set-table,set-arm,set-add,exercise-enter}).  A renderer
-- cannot read a register rule, so today an UN-PROMOTED user still meets `RIR`
-- in the set table.  That residue is written down in the pack's register
-- section; it is a known hole, not a quiet one.
--
-- This is the estate's recurring pathology in its exact canonical form: the
-- rule lives in prose, the executor is a model, and there is no machine.  Same
-- diagnosis as 040's containment guard and 050's room-is-not-a-place trigger,
-- and the same answer (D2): A RULE THAT MUST HOLD IS A STRUCTURE, NOT A
-- SENTENCE.  So the gate ships as DATA a renderer can query, and this file is
-- the seat that data sits in.
--
-- The design is not invented here.  It is fixed by
--   the 2026-08-24 RIR label-knob and level-gating design  (§2, §3)
-- which specifies these two tables, their columns, their seed rows and the
-- three invariants below.  That ticket has been open and unlanded since
-- 2026-08-24; the pack's service/health/ui_labels.py already reads `ui_label`
-- optionally and falls back to its own DEFAULT_LABELS when the table is
-- absent.  So the label half is LIVE PACK CODE WITH NO TABLE UNDER IT.  This
-- migration puts the table under it.
--
-- ==========================================================================
-- WHAT LANDS -- TWO TABLES, ONE AXIS.
--
--   ui_label   WHAT A THING IS CALLED.  label_key is the stable identifier
--              (the field name, the DB column, the --rir flag, the JSON key);
--              `display` is the name the owner READS, and it is a KNOB, treated
--              like a subagent's displayname.
--              A subagent has an internal id and a displayname;
--              changing the displayname never touches the id.  So if the owner
--              ever says "call it 여유", that is ONE UPDATE, not ten code edits
--              -- which is the whole reason the 2026-08-23 substitution rollout
--              cost what it cost.
--
--   ui_gate    WHAT A SURFACE SHOWS AT A GIVEN LEVEL.  One row per (surface,
--              level).  `visible` is the gate, `label_key` points at the name
--              to use, `explain` names an explanation lane, `note` carries the
--              human reason the row is what it is.
--
-- They are ONE table pair and not two features, because the label and the
-- level are the same axis: a beginner does not see the RIR column at all, so
-- what that column is CALLED is a question that does not arise for them.  Two
-- tables in two migrations would put the definition in two places, which is
-- the defect this estate keeps paying for.
--
-- ui_label's COLUMNS ARE DICTATED BY THE INCUMBENT READER, not by taste.  The
-- pack's ui_labels._lookup() already issues, today, in shipped code:
--     SELECT display, gloss FROM ui_label WHERE label_key = ?
-- so those three names are load-bearing.  A migration that renamed them would
-- be creating a table the live reader cannot read.
--
-- ==========================================================================
-- THE THREE INVARIANTS (design §3-3), AND WHICH MACHINE HOLDS EACH.
--
--   I1  THE GATE VERDICT COMES FROM ui_gate AND FROM NOTHING ELSE.  A
--       `if level == 'beginner':` in render code is a FAILURE, not a
--       shortcut -- exactly one function may know the level strings.  This
--       one is held in the PACK (its resolver + its call sites) and is the
--       acceptance criterion of the pack-side dispatch that follows this
--       file.  It is named here so the seat and the rule stay in one
--       document.
--
--   I2  THE GATING RULE IS NOT WRITTEN IN SKILL.md.  The model echoes what
--       the tool emitted, verbatim; it does not decide what to hide.  Also a
--       pack-side obligation, also named here for the same reason.
--
--   I3  ONE GATE PER SURFACE, AND THE PAIR MUST AGREE.  If
--       set_table.rir_col is off while set_arm.step2_rir is on, the owner
--       gets a keyboard asking for a number that has no column to land in.
--       THIS one is held HERE, twice: guard 3 below refuses the migration if
--       the seed ever stops satisfying it, and
--       tests/test_mig051_ui_gate.py fails if a later edit breaks it.
--       "시드가 짝을 맞추고 테스트가 그 정합을 검증한다" is the design's
--       sentence; the guard is what makes it not merely a sentence.
--
-- ==========================================================================
-- THE LEVEL KEY IS `workout_level`, AND THIS FILE DOES NOT WRITE IT.
--
-- The level itself is a `config` row, not a ui_gate column -- the gate table
-- says what each level SEES, the config row says which level THIS INSTANCE
-- is.  The canonical key name is `workout_level`: the pack's live reader
-- (exercise-enter:832) already searches
--     CARD_LEVEL_KEYS = ("workout_level", "user_level", "training_level")
-- in that order, so this file PROMOTES THE FIRST to the canonical name and
-- leaves the other two as read-aliases.  Nothing has to change on either side
-- for that promotion to take effect; it is a naming ruling, recorded here
-- because there was no document that held it.
--
-- NO `INSERT INTO config` SHIPS IN THIS FILE, and that is a convention, not an
-- omission.  Measured on this tree 2026-09-18: across all 40 shipped
-- migrations (012..050) and across schema.sql, the number of statements that
-- write a `config` row is ZERO.  schema.sql seeds REGISTRY tables only
-- (spend_category, ledger_guard, goal_readiness_rule, the base place row);
-- `config` is per-instance OWNER DATA -- the live table's 18 keys are body
-- weight, body fat, goals.  A migration is not a writer of owner data.
--
-- The design ticket's literal line was
--     INSERT OR REPLACE INTO config VALUES ('workout_level','intermediate');
-- and OR REPLACE is the second reason not to ship it: on any instance where
-- the owner had already chosen a level, that statement silently destroys the
-- choice (and its updated_at) while calling itself a schema migration.
--
-- Nothing is lost by leaving it out, because THE ABSENT KEY ALREADY RESOLVES
-- TO THE RIGHT ANSWER.  exercise-enter:833 declares
-- CARD_LEVEL_DEFAULT = "intermediate", so a missing row reads `intermediate`
-- -- which is the owner's own level (design §3-4) and today's behaviour
-- byte for byte.  The owner's screen does not move on this cut, which is what
-- §3-4 requires.  The owner-facing way to set it is the verb that already
-- exists for exactly this, not a migration:
--     database/kit_life.sh config-set workout_level=beginner
--
-- ==========================================================================
-- THE TWO `explain` CELLS ARE SEEDED NULL, AGAINST THE DESIGN, ON A LATER
-- OWNER RULING.  Named loudly because it is the one place this file does not
-- copy the 2026-08-24 seed table.
--
-- The design seeds explain='on_demand' on set_table.rir_col and
-- explain='first_use' on set_arm.step2_rir, both pointing at an RIR
-- explanation carried in ui_label.gloss.  On 2026-09-11 the owner RETIRED
-- that explanation, verbatim: "Rir 설명 rir입력후에 나오면 어떡하냐.. 그리고
-- 이건 이해한사람한테만 나오는거니 그냥 빼버려".  The pack removed the gloss
-- and retired the confirm-fold that rendered it, and its own file records the
-- instruction as a REMOVAL, not a relocation.
--
-- `explain` is not descriptive data.  It is an INSTRUCTION TO A RENDERER
-- ("build an explanation lane here").  Seeding the design's values would ship,
-- as data, an order to rebuild the thing the owner ordered taken out -- a rule
-- in the database contradicting a later owner ruling, which is the precise
-- failure mode this whole file exists to end.  So both cells are NULL, the
-- `note` column on those two rows carries the design's original value and the
-- ruling that displaced it, and restoring either is ONE UPDATE the day an
-- explanation lane is approved again.  NULL is also the only value that keeps
-- this cut's promise of zero owner-visible change: nothing explains anything
-- today, and nothing explains anything after this file runs.
--
-- The `explain` COLUMN still ships (the design fixes the shape, and the slot
-- is where an approved lane would land).  It is simply empty on every seeded
-- row today, and the CHECK documents the two values it may ever hold.
--
-- ==========================================================================
-- WHAT IS DELIBERATELY NOT HERE.
--
--   * EVERY RENDERER.  set-table / set-arm / set-add / exercise-enter live in
--     dogany-agentpack-health-trainer, not in this repo.  This file lands the
--     seat; the pack-side dispatch lands the resolver (level() / gate()) and
--     the six call sites, and raises its requires_kit floor to >=2.23.0.
--     Until it does, NOTHING reads ui_gate and nothing changes on screen --
--     which is the correct intermediate state, not a half-landing.
--   * A FOREIGN KEY from ui_gate.label_key to ui_label.label_key.  Two of the
--     seeded keys (`muscle_coarse`, `muscle_unit`) are VOCABULARY MODES, not
--     display names -- there is no string an owner reads for "coarse mode", so
--     an FK would force minting ui_label rows whose `display` column has no
--     meaning.  The reference is by name and the reader is total: the pack's
--     _lookup() already falls back to DEFAULT_LABELS for an absent row, so a
--     key with no row degrades exactly like a database with no table, which is
--     the behaviour that file was written to guarantee.
--   * ui_label ROWS BEYOND `rir`.  The pack's DEFAULT_LABELS also carries the
--     H7 `layer.*` domain vocabulary.  Those are pack-owned words; the kit
--     owns the seat, not the dictionary.  The pack seeds them if and when it
--     wants them turnable.
--   * ANY TRIGGER.  I3 is enforced by guard 3 at apply time and by the test
--     at edit time.  A trigger on a six-row configuration table would be
--     machinery guarding a table nobody writes at runtime.
--
-- ==========================================================================
-- DESIGN §5 IS SETTLED HERE, AND THE COST IS NAMED.  The ticket left open
-- whether these two tables belong to the health lane or to the shared kit
-- schema, and flagged the downside of the second: a non-health instance (Ag)
-- inherits tables it will never read.  Owner-approved cut composition
-- 2026-09-18 settles it as KIT SCHEMA, and the reason is that the alternative
-- is worse in the exact way this file is about -- a second schema authority
-- on one physical database.  kit_life's migration lineage is a SINGLE LINEAR
-- sequence with one author (VERSIONING, "Migration lineage is
-- kit-only"); a behaviour pack publishing its own table on the shared file
-- is the `011_income` vs `011_soge` collision class, and this estate has paid
-- that bill once already.
--
-- The cost is real and is not hidden: a non-health instance carries two
-- tables and thirteen rows describing surfaces it does not render.  They are
-- inert (nothing reads ui_gate until the pack-side cut lands, and then only
-- that pack reads it), they are small, and `surface` is namespaced so a
-- second domain's rows can land beside them rather than on top of them.
-- Thirteen dormant rows is the price of one schema author, and it is the
-- cheaper side.
--
-- ==========================================================================
-- READER SAFETY (measured, not assumed).  Two new tables, zero changes to any
-- existing object.  `ui_label` / `ui_gate` appear ZERO times in this payload
-- before this commit, so no reader's shape moves and no positional INSERT
-- changes arity.  The one reader anywhere in the estate is the pack's
-- ui_labels._lookup(), which is written to tolerate BOTH states and whose
-- result for the seeded row is identical to its no-table fallback
-- (display 'RIR', gloss '').
--
-- ==========================================================================
-- APPLY NOTE.  NOTHING IN THIS COMMIT HAS BEEN APPLIED TO ANY DATABASE.
-- 049 and 050 are ahead of it in the same unapplied run.  The crew-shared DB
-- (~/.dogany/crews/kit_life/kit_life.db) is ONE physical file, so every crew
-- member's database/kit_life.py must pin 51 before the installer will migrate
-- (pack_install.sh _kit_crew_lockstep_guard).  Order:
--   0. cp <db> <db>.pre051
--   1. land the payload on every crew member (pins -> 51), then migrate the
--      chain in order: 049, 050, 051
--   2. verify:  PRAGMA user_version                      -> 51
--               PRAGMA integrity_check                   -> ok
--               PRAGMA foreign_key_check                 -> (no output)
--               SELECT COUNT(*) FROM ui_gate             -> 12
--               SELECT COUNT(*) FROM ui_label            -> 1
--               SELECT display FROM ui_label
--                 WHERE label_key='rir'                  -> RIR
--   3. the pack-side cut (>=0.13.0) lands AFTER this, never before: its tests
--      build against this schema.sql.
--
-- ==========================================================================
-- GUARD.  Three-sided:
--   1. user_version must be EXACTLY 50, checked BEFORE any transaction.
--   2. NEITHER table may already exist -- a lineage that already carries one
--      of them was built by something that is not this file, and is refused
--      rather than merged into.
--   3. AFTER the seed, invariant I3 must hold: every surface carries EXACTLY
--      the two levels, and every surface in the `rir` family agrees with
--      every other one at each level.  A seed that cannot satisfy it is
--      refused, not half-landed.
-- Any of the three failing aborts under .bail with the transaction rolled
-- back and user_version still 50.
.bail on
.timeout 5000

-- guard 1: version
CREATE TEMP TABLE _guard_051(seen_user_version);
CREATE TEMP TRIGGER _guard_051_refuse BEFORE INSERT ON _guard_051 BEGIN
  SELECT RAISE(FAIL, 'migration 051 refused: this database is not at user_version 50 -- either 051 was already applied (>= 51) or the 021~050 chain has not run (< 50). Nothing changed');
END;
INSERT INTO _guard_051
  SELECT user_version FROM pragma_user_version WHERE user_version <> 50;
DROP TABLE temp._guard_051;

-- guard 2: neither table may pre-exist.
CREATE TEMP TABLE _guard_051_pre(seen_count);
CREATE TEMP TRIGGER _guard_051_pre_refuse BEFORE INSERT ON _guard_051_pre BEGIN
  SELECT RAISE(FAIL, 'migration 051 refused: ui_label and/or ui_gate already exists on this database. Whatever created it was not this file, and this file will not merge into it. Nothing changed');
END;
INSERT INTO _guard_051_pre
  SELECT COUNT(*) FROM sqlite_master
   WHERE type='table' AND name IN ('ui_label','ui_gate')
  HAVING COUNT(*) <> 0;
DROP TABLE temp._guard_051_pre;

BEGIN IMMEDIATE;

-- ── 1. THE NAME: what a thing is CALLED (the knob) ───────────────────────
-- label_key is the STABLE identifier and never changes; `display` is the only
-- column an owner preference may move.  updated_at matches `config`'s idiom
-- (datetime('now','localtime')) rather than the UTC strftime used by the
-- travel/place tables: this table is a settings surface that sits beside
-- config, and a settings timestamp is read by a person.
CREATE TABLE ui_label (
  label_key  TEXT PRIMARY KEY,
  display    TEXT NOT NULL CHECK (TRIM(display) <> ''),
  gloss      TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- The one seeded name.  display='RIR' is the DEFAULT and the knob at once:
-- it is what the surfaces read today, so seeding it changes nothing, and it
-- is a row, so changing it is an UPDATE instead of a rollout.
--     UPDATE ui_label SET display='여유' WHERE label_key='rir';
-- gloss is NULL BY OWNER RULING 2026-09-11 ("그냥 빼버려"), not by oversight
-- -- see the header.  The pack's _lookup() maps a NULL gloss onto its own
-- DEFAULT_LABELS value, which is '' since the same ruling, so the seeded row
-- and the no-table fallback produce byte-identical output.
INSERT INTO ui_label (label_key, display, gloss) VALUES
  ('rir', 'RIR', NULL);

-- ── 2. THE GATE: what a surface shows at a level ─────────────────────────
-- The CHECKs are the shape of the axis, not decoration.  `level` is a CLOSED
-- two-value enumeration ON PURPOSE, and that is not the mistake 049 undid:
-- 049's closed list was an INSTANCE IDENTITY (an open set -- a new agent
-- carries a slug nobody enumerated), while this is a PRODUCT AXIS whose
-- cardinality is what makes invariant I3 definable at all.  "The pair must
-- agree" has no meaning unless the set of levels is known.
CREATE TABLE ui_gate (
  -- the surface identifier the RENDERER looks itself up by.  Stable string,
  -- <module>.<surface>; it is an API between this table and the pack.
  surface   TEXT NOT NULL CHECK (TRIM(surface) <> ''),
  level     TEXT NOT NULL CHECK (level IN ('beginner','intermediate')),
  visible   INTEGER NOT NULL DEFAULT 1 CHECK (visible IN (0,1)),
  -- ui_label reference BY NAME (no FK -- see the header).  NULL = this
  -- surface shows nothing that needs a name at this level.
  label_key TEXT CHECK (label_key IS NULL OR TRIM(label_key) <> ''),
  -- which explanation lane, when one is approved.  EMPTY on every seeded row
  -- today by the 2026-09-11 owner ruling; the CHECK is what the column may
  -- ever hold, not what it does hold.
  explain   TEXT CHECK (explain IS NULL OR explain IN ('first_use','on_demand')),
  note      TEXT,
  PRIMARY KEY (surface, level)
);

-- The seed IS the beginner/intermediate difference -- all of it.  There is no
-- second place where a level changes a surface, and there must never be one.
-- intermediate == today's behaviour on every row, which is why this cut does
-- not move the owner's screen (design §3-4: the owner is intermediate+).
INSERT INTO ui_gate (surface, level, visible, label_key, explain, note) VALUES
  ('set_table.rir_col',   'beginner',     0, NULL,  NULL,
   '세트표에서 RIR 컬럼 자체를 뺀다 -- 빈 칸을 남기지 않는다. 헤더/보더/행이 같은 튜플을 쓰므로 격자가 4칸으로 좁아진다.'),
  ('set_table.rir_col',   'intermediate', 1, 'rir', NULL,
   '컬럼 표시 = 현행. 설계(2026-08-24)는 여기 explain=on_demand (첫 표 메타줄 범례)였으나 2026-09-11 오너 룰링("그냥 빼버려")이 RIR 설명 경로를 폐기해 NULL 로 시드한다. 설명 레인이 다시 승인되면 UPDATE 한 줄.'),

  ('set_arm.step2_rir',   'beginner',     0, NULL,  NULL,
   '키보드가 1단계(반복수)만. 새 모양을 발명하지 말 것 -- 램프 1단계 arm 이 이미 그 모양이고 CONFIRM_FMT_SKIP 이 짝이다.'),
  ('set_arm.step2_rir',   'intermediate', 1, 'rir', NULL,
   '2단계(반복수 -> RIR) = 현행. 설계는 여기 explain=first_use (첫 세션 fold 펼침)였으나 그 fold 자체가 2026-09-11 오너 룰링으로 폐기됐다(타이밍: 값을 이미 기록한 뒤에 설명 / 대상 역전: 키보드를 통과한 사람만 도달). 이동이 아니라 제거다.'),

  ('set_arm.confirm_rir', 'beginner',     0, NULL,  NULL,
   '확인문구에서 RIR 절 생략 -- CONFIRM_FMT_SKIP.'),
  ('set_arm.confirm_rir', 'intermediate', 1, 'rir', NULL,
   '"… · RIR 2 기록" = 현행.'),

  ('spec_line.rir',       'beginner',     0, NULL,  NULL,
   'NEXT_HINT 스펙에서 RIR 괄호 생략. 리졸버가 빈 문자열을 내면 스펙 빌더가 이미 절을 생략한다 -- 정상 경로와 오버라이드 경로(F19/F21) 둘 다 같은 리졸버를 거쳐야 한다.'),
  ('spec_line.rir',       'intermediate', 1, 'rir', NULL,
   '"벤치프레스 60kg × 8 (RIR 1-2)" = 현행.'),

  ('set_add.floor_warn',  'beginner',     0, NULL,  NULL,
   '경고는 계속 낸다(안전). 숨기는 것은 RIR 수치뿐 -- 문장은 "다음 세트는 한 템포 일찍 끊고 무게를 올린다"로. visible=0 은 경고의 침묵이 아니라 RIR 어휘의 부재를 뜻한다. 설계 §5 미결: 안전 경고를 게이팅하는 것이 맞는지 재검토 대상.'),
  ('set_add.floor_warn',  'intermediate', 1, 'rir', NULL,
   '수치 포함 현행 문구. 이 표면은 치환 롤아웃이 건드리지 않아 날 것의 RIR 을 내고 있었다 -- 리졸버 경유로 통일되면 한 세션에 두 이름이 공존하던 불일치도 같이 없어진다.'),

  ('card.muscle_vocab',   'beginner',     1, 'muscle_coarse', NULL,
   '가슴/등/다리. 이 표면은 초급에서도 visible=1 이다 -- 감추는 게이트가 아니라 어휘를 고르는 게이트이기 때문. label_key 는 표시명이 아니라 어휘 MODE 라서 ui_label 행이 없다(FK 를 걸지 않은 이유, 헤더 참조).'),
  ('card.muscle_vocab',   'intermediate', 1, 'muscle_unit',   NULL,
   '가자미/대퇴사두 = 현행. 이 한 쌍만 이미 배선돼 있다(exercise-enter card_user_level) -- 나머지 다섯 표면은 팩 쪽 컷이 붙인다.');

-- guard 3: invariant I3, both halves.
--
-- 3a. ONE GATE PER SURFACE: exactly two rows, one per level.  A surface with
--     only one level is a surface whose other level falls through to whatever
--     the renderer's fallback happens to be -- i.e. a gate that is not a gate.
CREATE TEMP TABLE _guard_051_pairs(seen_count);
CREATE TEMP TRIGGER _guard_051_pairs_refuse BEFORE INSERT ON _guard_051_pairs BEGIN
  SELECT RAISE(FAIL, 'migration 051 refused: at least one ui_gate surface does not carry EXACTLY the two levels (beginner, intermediate). A half-seeded surface silently falls through to a renderer default -- rolled back, nothing changed');
END;
INSERT INTO _guard_051_pairs
  SELECT COUNT(*) FROM (
    SELECT surface FROM ui_gate
     GROUP BY surface
    HAVING COUNT(*) <> 2 OR COUNT(DISTINCT level) <> 2)
  HAVING COUNT(*) <> 0;
DROP TABLE temp._guard_051_pairs;

-- 3b. THE PAIR MUST AGREE: every surface that carries the `rir` name at any
--     level belongs to one family, and within a level the family is all-on or
--     all-off.  The forbidden state is "표엔 없는데 키보드는 묻는" -- a
--     keyboard asking for a number that has no column to land in.
CREATE TEMP TABLE _guard_051_family(seen_count);
CREATE TEMP TRIGGER _guard_051_family_refuse BEFORE INSERT ON _guard_051_family BEGIN
  SELECT RAISE(FAIL, 'migration 051 refused: the `rir` surface family disagrees within a level -- one surface would show RIR while another hides it (the keyboard asks for a number the table has no column for). Rolled back, nothing changed');
END;
INSERT INTO _guard_051_family
  SELECT COUNT(*) FROM (
    SELECT level FROM ui_gate
     WHERE surface IN (SELECT surface FROM ui_gate WHERE label_key = 'rir')
     GROUP BY level
    HAVING COUNT(DISTINCT visible) <> 1)
  HAVING COUNT(*) <> 0;
DROP TABLE temp._guard_051_family;

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 50.
PRAGMA user_version = 51;
COMMIT;
