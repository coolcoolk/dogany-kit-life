-- 040_event_parent_containment.sql  (target user_version 40)
--
-- reversible: NO down-migration ships.  Additive only -- one nullable column
--   (event.parent_id), one partial index, two exclusion-memory tables, zero
--   backfill (nothing can be backfilled: no parent link and no exclusion has
--   ever been declared).  Hand-rollback is: drop event_record_exclusion +
--   event_child_exclusion + idx_event_parent, rebuild event without
--   parent_id (12-step), PRAGMA user_version = 39.  A FAILED apply needs no
--   rollback: single transaction, .bail on, stamp last (023 discipline).
--
-- WHAT THIS LANDS (cut 3 -- the settled event layer model, owner
-- 2026-09-08 18:39~19:10, cut-3 decisions):
--
--   제주 여행                <- event (layer 1)
--   └─ 저녁                  <- event (layer 2), parent = 제주 여행
--      · 흑돼지 · 소주        <- RECORDS, not a layer
--
--   PARENT   events are TWO layers; records are not a layer.  parent_id is
--            a nullable self-reference, ON DELETE SET NULL: a deleted
--            parent must never delete a child's history -- the child event
--            (and everything attached to it) survives as a root.  The
--            two-layer depth AND the cycle guard are TOOL-layer rules
--            (kit_life.py _parent_link_error), NOT schema constraints --
--            same seat as the RECORD_SOURCE_TABLES whitelist: a future
--            owner reversal on depth is a code change, not a table rebuild.
--            Schema stores; the tool judges (DGN-892 posture).
--   SPAN     NOT stored on event.  An event's span is DERIVED from its
--            event_timeblock-linked blocks (min start_at / max end_at over
--            links where BOTH stamps exist, canonical UTC-Z only,
--            fail-CLOSED) -- a multi-day trip already carries its span on
--            its own block (one block covering every day of the trip),
--            and a stored copy on event would be a
--            second clock that drifts ("time is the block", 036/039
--            discipline).  Cost, stated plainly: a span the owner types
--            directly with NO pre-existing block cannot be expressed --
--            that lane declares the presence block first; columns are not
--            added speculatively for it.
--   EXCLUSION MEMORY (the load-bearing piece).  The owner's containment
--            model is default-INCLUDE ("기간 안에 들면 기본으로 자식.
--            빼는 게 오너 판단") -- so the sweep verb re-runs freely, and
--            the owner's REMOVAL judgement must outlive every re-run or the
--            owner corrects the same lunch forever.  Two memories:
--            event_record_exclusion  (this record is NOT part of this
--                                     event: "이 점심은 회사 일 아님")
--            event_child_exclusion   (this event is NOT a child of that
--                                     parent)
--            Both FK ON DELETE CASCADE both ways: when either endpoint row
--            dies the memory dies with it (nothing left to re-sweep), and
--            keying record exclusions on record.id -- whose own row every
--            block-delete lane clears in the same tx (_block_record_del,
--            026 guard #1) -- means no delete lane gains a new cleanup
--            duty here.  The exclusion is NOT a record_event role token:
--            readers of record_event (query/rollup/distill) stay untouched
--            because an excluded pair simply has no link row.
--
-- GUARD.  Both-sided, 037 discipline: refused unless user_version is
-- EXACTLY 39, BEFORE any transaction or write -- exit != 0, database
-- untouched (re-apply refused; a skipped 021~039 chain refused).
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_040(seen_user_version);
CREATE TEMP TRIGGER _guard_040_refuse BEFORE INSERT ON _guard_040 BEGIN
  SELECT RAISE(FAIL, 'migration 040 refused: this database is not at user_version 39 -- either 040 was already applied (>= 40) or the 021~039 chain has not run (< 39). Nothing changed');
END;
INSERT INTO _guard_040
  SELECT user_version FROM pragma_user_version WHERE user_version <> 39;
DROP TABLE temp._guard_040;
BEGIN IMMEDIATE;

-- 1. The parent seat.  ONE column, self-reference, nullable.  ON DELETE
--    SET NULL: deleting 제주 여행 orphans 저녁 upward, never destroys it.
--    (ADD COLUMN appends the definition to the stored CREATE text;
--    schema.sql carries the identical text inline for sqlite_master
--    parity -- proven by test_mig040_event_parent.py.)
ALTER TABLE event ADD COLUMN parent_id INTEGER REFERENCES event(id) ON DELETE SET NULL;
CREATE INDEX idx_event_parent ON event(parent_id) WHERE parent_id IS NOT NULL;

-- 2. Record exclusion memory.  PK = the pair; note is the owner's stated
--    reason (free text, may be empty -- the row itself is the judgement).
CREATE TABLE event_record_exclusion (
  event_id  INTEGER NOT NULL REFERENCES event(id)  ON DELETE CASCADE,
  record_id INTEGER NOT NULL REFERENCES record(id) ON DELETE CASCADE,
  note      TEXT,
  added_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (event_id, record_id)
);

-- 3. Child exclusion memory.  Same shape for the parent axis.
CREATE TABLE event_child_exclusion (
  parent_id INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  child_id  INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  note      TEXT,
  added_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (parent_id, child_id)
);

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 39.
PRAGMA user_version = 40;
COMMIT;
