-- 053_carry_item.sql  (target user_version 53)
--
-- reversible: YES, losslessly.  Rollback (SQLite >= 3.35) =
--   DROP INDEX ix_sub_event_prep_ref;
--   DROP INDEX ux_sub_event_live_prep_item;
--   DROP TABLE routine_prep_item;
--   DROP TABLE carry_item;
--   PRAGMA user_version = 52;
-- Two new tables + two partial indexes on the pre-existing sub_event table,
-- no rebuild of any live table, no seed, no backfill.  sub_event gains no
-- column -- its existing (event_id, kind, ref, done, tombstone) shape
-- already carries a prep-item fact (R4 below); the two indexes only harden
-- it against duplicate live rows and speed the reverse lookup.
--
-- ==========================================================================
-- WHY -- THE KIT HAS NO ROW FOR A THING THE OWNER CARRIES.
--
-- 2026-09-21 resource-axis equipment design, owner quote
-- (10:56): "이것도 결국 우리 자원/기기 이런것과 합이 맞아야해. 운동갈때 벨트
-- 스트랩 챙기는것처럼. 근데 내가 가방에 이거 다 넣어놔서 그냥 가방 챙기라고
-- 해도되고 이런 포함관계도있고.." -- the owner needs to say "그냥 가방 챙겨"
-- once and have every item inside answered for, and "벨트 빼놨어" once and
-- have only the belt reappear.  Neither sentence has anywhere to land today.
--
-- Verified against canonical schema.sql (design report dsp-20260921-175846-
-- 0457, 2026-09-21): three tables already touch "resource" and none of them
-- is this fact. ledger_resource is a planning-ledger ASSERTION (what the
-- owner says he has, in prose form), resource is a flow/stock TAXONOMY
-- block_delta already keys off (a closed vocabulary of resource TYPES, not
-- owned instances), and entity is a dormant real-world-target table that
-- would blur payment methods, bodies, and portable possessions into one
-- shape if extended.  None of the three carries a portable possession's
-- display name, current physical containment, or a three-valued possession
-- state -- the narrow gap survives every one of those three counter-claims.
--
-- R1 (design notes): resources the owner carries across domains belong to the
--   KIT, not any one domain module -- a strap crosses workout/travel/
--   schedule, so no single module dictionary can own it.
-- R2 (design notes): the boundary is possession, not location -- carry_item is
--   what the owner OWNS and CARRIES (벨트/스트랩/여권/가방); facility
--   equipment (a gym's cable machine) stays exercise-dictionary data
--   (exercise_movement_grip.equipment / exercise_catalog.equipment_need).
--   Duplicating that boundary here would let the same fact live in two
--   places and drift.
-- R3 (design notes): containment is ONE parent column, a nullable
--   self-FK -- same shape as the already-shipped entity.owner_entity_id
--   (schema.sql), ON DELETE SET NULL so deleting a container does not
--   erase what it held, it just un-contains it.
-- R4 (design notes): "packed for this event" is NOT a new label column
--   on some event table -- it is sub_event(kind='prep_item',
--   ref=carry_item.ulid, done).  sub_event.ref is already documented as an
--   L2 domain-row ulid with no FK (polymorphic by design: it already points
--   at workout sessions and other L2 rows), and done already has a boolean
--   CHECK.  Adding a carry_item_id column instead would duplicate ref and
--   force a rebuild of an intentionally polymorphic table to specialize it
--   for one kind.
--
-- ==========================================================================
-- WHAT THIS MIGRATION DOES.
--
-- carry_item -- one row per portable possession.
--   possession is a closed THREE-state enum (has/has_not/unknown), not a
--   nullable boolean -- a boolean's NULL collapses to false at every call
--   site that forgets to check for it, and "모른다" must never silently
--   become "없다" (the owner's workout-module rule already insists UNKNOWN
--   is not the same fact as "not used").
--   parent_id is CURRENT physical containment only, not a remembered "usual
--   home" -- pulling an item out of a bag is parent_id=NULL, no second
--   table.  Cycles cannot be stopped by a row-local CHECK (a CHECK sees only
--   the row being written, never the chain above it) -- the guard is the
--   tool layer's job (kit_life.py carry_item_pack / _carry_chain_has_cycle),
--   in the same division of labour as event.parent_id's depth/cycle guard
--   (040_event_parent_containment: "2층 깊이와 순환 가드는 도구층이지 스키마
--   제약이 아니다").
--   Three CHECKs encode the invariants the tool layer must never violate:
--     (a) parent_id IS NULL OR possession = 'has' -- you cannot be said to
--         have PACKED a thing you do not currently have.
--     (b) released_on IS NULL OR acquired_on IS NULL OR released_on >=
--         acquired_on -- ownership span sanity, only checked when both ends
--         are known.
--     (c) possession <> 'has' OR released_on IS NULL -- a release date only
--         means something once possession has actually left 'has'.
--   status (active/retired), not physical deletion -- same convention as
--   place.status; a retired item stops being offered but its history
--   (routine defaults that once pointed at it, prep rows already settled)
--   is not torn out from under it.
--   owning_agent NOT NULL follows routine_def/sub_event's own column
--   (schema.sql) -- inventory rows need an owner like every other DGN-1010
--   table does.
--
-- routine_prep_item -- the routine's default packing list, a JUNCTION not a
--   scalar column, because one routine can name several items (재즈방 =
--   every time: 악보 + 메트로놈).  This is the same shape idea the
--   design report traced back from an incorrect claim: there is no
--   routine_def.prep_min to extend (that column does not exist; the live
--   declaration is travel_rule.prep_min, anchored indirectly via
--   anchor_type='def'/anchor_ulid=routine_def.ulid) -- defaults for a
--   recurring declaration still belong beside routine_def, they just need a
--   real many-to-many table, not a borrowed column.  ON DELETE CASCADE on
--   routine_def_id: deleting a routine takes its own default list with it.
--   No ON DELETE on carry_item_id: retiring or deleting a referenced item is
--   a decision that must surface, not silently prune a routine's list.
--
-- sub_event hardening (no new column, two partial indexes only):
--   ux_sub_event_live_prep_item makes (event_id, ref) UNIQUE among live
--   ('prep_item', tombstone=0) rows -- without it, a re-run of routine
--   materialization or a double carry_item attach on the same event would
--   silently duplicate the same required item.  ix_sub_event_prep_ref makes
--   "which events still need this item" a real index scan instead of a
--   full-table filter -- ref carries no FK (sub_event is deliberately
--   polymorphic across L2 domains), so every prep writer must resolve an
--   active carry_item itself and every reader joins inward; a dangling or
--   wrong-kind ref then disappears from prep-list (fails CLOSED) rather
--   than rendering invented text.
--
-- NO SEED ROWS.  Item names (악보, 벨트, 가방) are owner data, not product
-- vocabulary -- the same rule 051 states for its own seed and 052 states for
-- retro_line: this migration ships the shape, never the owner's things.
--
-- ==========================================================================
-- WHAT THIS MIGRATION DELIBERATELY DOES NOT DECIDE (design report §7).
--
-- "usual home" vs "currently out" is ONE column (parent_id = current state
-- only) -- a remembered default container, if ever wanted, is a later
-- home_container_id column this migration does not pre-build.
-- Historical possession changes are NOT tracked -- current possession +
-- acquired/released dates is judged enough for this cut; an append-only
-- carry_possession_fact table is a later table if audit history is asked
-- for.
-- Nested-container depth is UNBOUNDED (bag inside a suitcase inside a car
-- is legal) -- only cycles are refused, at the tool layer.
-- Materialized routine defaults are NOT backfilled onto already-materialized
-- future instances when routine_prep_item changes -- same exception policy
-- routine_roller.py already applies to owner-edited occurrences (a change
-- to the routine's rule takes effect on the NEXT materialization, not by
-- rewriting rows that already exist).
--
-- ==========================================================================
-- GUARD.  user_version must be EXACTLY 52, checked BEFORE any transaction --
-- a re-apply (>= 53) or a skipped chain (< 52) is refused, database
-- untouched, same 037/040/044/045/046/047/048/051/052 discipline.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_053(seen_user_version);
CREATE TEMP TRIGGER _guard_053_refuse BEFORE INSERT ON _guard_053 BEGIN
  SELECT RAISE(FAIL, 'migration 053 refused: this database is not at user_version 52 -- either 053 was already applied (>= 53) or the 021~052 chain has not run (< 52). Nothing changed');
END;
INSERT INTO _guard_053
  SELECT user_version FROM pragma_user_version WHERE user_version <> 52;
DROP TABLE temp._guard_053;
BEGIN IMMEDIATE;

CREATE TABLE carry_item (
  id               INTEGER PRIMARY KEY,
  ulid             TEXT NOT NULL UNIQUE,
  name             TEXT NOT NULL CHECK (TRIM(name) <> ''),
  item_kind        TEXT NOT NULL DEFAULT 'item'
                   CHECK (item_kind IN ('item','container')),
  parent_id        INTEGER REFERENCES carry_item(id) ON DELETE SET NULL,
  possession       TEXT NOT NULL DEFAULT 'unknown'
                   CHECK (possession IN ('has','has_not','unknown')),
  acquired_on      TEXT,
  released_on      TEXT,
  possession_as_of TEXT,
  source           TEXT NOT NULL,
  owning_agent     TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active','retired')),
  created_at       TEXT NOT NULL
                   DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at       TEXT NOT NULL
                   DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),

  CHECK (parent_id IS NULL OR possession = 'has'),
  CHECK (released_on IS NULL OR acquired_on IS NULL
         OR released_on >= acquired_on),
  CHECK (possession <> 'has' OR released_on IS NULL)
);

CREATE INDEX ix_carry_item_parent
  ON carry_item(parent_id);

CREATE INDEX ix_carry_item_state
  ON carry_item(status, possession);

CREATE TABLE routine_prep_item (
  routine_def_id INTEGER NOT NULL
                 REFERENCES routine_def(id) ON DELETE CASCADE,
  carry_item_id  INTEGER NOT NULL
                 REFERENCES carry_item(id),
  source         TEXT NOT NULL,
  created_at     TEXT NOT NULL
                 DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (routine_def_id, carry_item_id)
);

CREATE INDEX ix_routine_prep_item_item
  ON routine_prep_item(carry_item_id);

CREATE UNIQUE INDEX ux_sub_event_live_prep_item
  ON sub_event(event_id, ref)
  WHERE kind = 'prep_item' AND tombstone = 0;

CREATE INDEX ix_sub_event_prep_ref
  ON sub_event(ref)
  WHERE kind = 'prep_item' AND tombstone = 0;

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 52.
PRAGMA user_version = 53;
COMMIT;
