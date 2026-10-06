-- 026_event_experience_overlay.sql  (target user_version 26)
-- reversible: NO (roll-forward). Adds a 6-table EXPERIENCE OVERLAY on top of
--   timeblock. timeblock is UNCHANGED (DGN-783 lock: primary time-unit storage,
--   sync/notify/resolver/recurrence/state stay there). Event references/contains
--   timeblock via a junction (containment, NOT projection) and binds Record N:M.
--
-- SPEC: the 2026-08-15 event-experience module spec (8 sections,
--   D1-D8 doctrine-checked) + DGN-897 (framework schema seam) + HIERARCHY-MODEL
--   (canon 0744a9a). Lane: the framework authors BOTH the migration SQL AND the
--   byte-matched fresh schema.sql edit (C3 parity companion, grill BLOCKER-1);
--   the kit reviews, applies, lockstep-propagates (EXPECTED_USER_VERSION 25->26,
--   DGN-708), writes the SKILL 3-split delta, lands to Ag.
--
-- DEPENDS ON 025 (legacy drop, v25). AUTHOR-TIME GATE (DGN-895 grill): 024 (kit
--   WIP) + 025 (framework) must be committed FIRST so this lands as 026 -- the file
--   number/pin/user_version 3-point lockstep must not renumber under it.
--
-- LANDING (mandatory, same as 025): apply on live ONLY via update.sh (runner +
--   code-pin bump in one run). NEVER hand-run `sqlite3 live.db < 026...` -- a
--   manual exec stamps user_version=26 while the code pin stays 25 -> kit_life.py
--   fail-closes every event verb (full SDK stop). See DGN-895.
--
-- KEY DESIGN -- NON-DESTRUCTIVE INVARIANT (A8, backed at schema level):
--   Every FK is ON DELETE CASCADE flowing DOWNWARD from a container/evidence
--   parent (event / record / timeblock) INTO a junction row only. So:
--     * delete an event  -> its record_event + event_timeblock LINKS vanish;
--       the underlying record / timeblock / artifact rows are UNTOUCHED.
--     * delete a timeblock (via its own lane) -> the event_timeblock LINK
--       vanishes; the event survives.
--   No FK ever propagates UPWARD, so a link/overlay delete can never destroy the
--   primary timeblock/record data. The tool layer additionally hard-gates
--   delete-event to unlink-only (A8); the schema here is the backstop.
--
-- POLYMORPHIC record pointer: record.(source_table, source_id) points at an
--   existing type-table row. source_table token = the REAL table name; the
--   authoritative registry is workouts / metric_log / spend_entry / artifact
--   (NOT 'metric'/'spend' -- those tables DO NOT exist; grill MAJOR-2. The kit owns
--   the canonical token registry = real table names, optionally a CHECK). Those
--   legacy rows have no ulid (grill S1) -> the pointer avoids a ulid retrofit.
--   Polymorphic FK is impossible in SQLite, so two integrity duties fall to the
--   TOOL LAYER and MUST be honored or evidence mis-binds (grill-reproduced):
--     (1) deleting a source row MUST delete its record row in the SAME tx --
--         else the record dangles and a later rowid reuse silently re-binds it.
--     (2) find-first/upsert (A3 INSERT OR IGNORE on (source_table,source_id))
--         requires every writer to use the identical real-table token.
--   UNIQUE (source_table, source_id) backs the dedup; artifact.id is
--   AUTOINCREMENT so a deleted artifact's id is never reused (delete-artifact,
--   spec Gate 2, was the one in-spec rowid-reuse vector).
--   NOTE: the non-destructive FK cascade is REAL only on the SDK connection
--   (kit_life.py opens foreign_keys=ON); a raw sqlite3 CLI has FK OFF by default
--   -> manual surgery can dangle links (another reason for the landing ban above).
--
-- insight.evidence = JSON array of event ulids (provenance). Chosen over a
--   junction table to hold the 6-table contract. CAUTION (grill MINOR-3):
--   delete-event DOES remove the event row (A8 non-destructive protects the
--   underlying timeblock/record originals, NOT the event row itself), so an
--   evidence ulid CAN dangle -> the tool layer MUST re-check referencing insights
--   on delete-event (or promote to an insight_event junction). Sensitive distill
--   needs source evidence (spec A8), so a lost anchor is doctrine-relevant.
--   insight = crew-shared SoT (kit_life.db) + owning_agent tag for the per-instance
--   regenerable access filter (A2-pivot (c)); the vector re-index is each memory
--   engine's view, NOT a migration target -- only this shared table ships.
--
-- SKULL SPEC SIGN-OFF NEEDED (grill MAJOR-3, design kept -- doc drift only):
--   * event/record/artifact/insight each carry a `ulid` (NOT NULL UNIQUE); spec
--     A2 + DGN-897 listed insight WITHOUT ulid. ulid identity is correct, but the
--     spec text + distill tool must acknowledge it (tool sets ulid) or the first
--     insert fails NOT NULL.
--   * evidence stores event ULIDs (reuse-resistant); spec said "event ids".
--
-- Task correction (B2, DGN-579/894): NO new kind values. timeblock.kind stays
--   {task,appointment,presence}. event.kind_hint is a COARSE optional label on
--   the experience container, NOT timeblock.kind, NOT an actionable class.
--
-- Atomicity wrapper (matches 024/025, DGN-871 NO-GO #1): `.bail on` aborts on
--   first error; BEGIN IMMEDIATE .. COMMIT makes all CREATEs PLUS the
--   user_version stamp one atomic unit -> a mid-statement failure rolls back and
--   leaves user_version at 25 (no half-migrated laundering).
.bail on
.timeout 5000
BEGIN IMMEDIATE;

-- 1. event -- experience container
CREATE TABLE event (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  title        TEXT NOT NULL,
  kind_hint    TEXT,            -- coarse optional hint; NOT timeblock.kind (B2)
  note         TEXT,
  reaction     TEXT,            -- subjective facet, NULLable; DGN-892 non-autonomy is a tool gate, schema only stores
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  owning_agent TEXT NOT NULL DEFAULT 'ag'
);
-- 2. record -- polymorphic evidence pointer to an existing type-table row
CREATE TABLE record (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  source_table TEXT NOT NULL,   -- real table token: workouts | metric_log | spend_entry | artifact (Skull owns registry)
  source_id    INTEGER NOT NULL,
  ts           TEXT,
  owning_agent TEXT NOT NULL DEFAULT 'ag',
  UNIQUE (source_table, source_id)   -- find-first/upsert dedup (A3)
);
-- 3. artifact -- homeless evidence (photo, free memo): a record source with no owning type-table
CREATE TABLE artifact (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,   -- AUTOINCREMENT: never reuse a deleted artifact id (grill MAJOR-1)
  ulid         TEXT NOT NULL UNIQUE,
  kind         TEXT NOT NULL,   -- 'photo' | 'note' | ...
  ref          TEXT,            -- path / uri / inline text ref
  ts           TEXT,
  owning_agent TEXT NOT NULL DEFAULT 'ag'
);
-- 4. record_event -- N:M evidence <-> experience
CREATE TABLE record_event (
  record_id INTEGER NOT NULL REFERENCES record(id) ON DELETE CASCADE,
  event_id  INTEGER NOT NULL REFERENCES event(id)  ON DELETE CASCADE,
  role      TEXT NOT NULL DEFAULT 'incidental' CHECK (role IN ('primary','incidental')),
  weight    REAL,
  source    TEXT,
  added_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (record_id, event_id)
);
CREATE INDEX idx_record_event_event ON record_event(event_id);

-- 5. event_timeblock -- N:M containment (experience CONTAINS timeblock)
CREATE TABLE event_timeblock (
  event_id     INTEGER NOT NULL REFERENCES event(id)     ON DELETE CASCADE,
  timeblock_id INTEGER NOT NULL REFERENCES timeblock(id) ON DELETE CASCADE,
  added_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (event_id, timeblock_id)
);
CREATE INDEX idx_event_timeblock_tb ON event_timeblock(timeblock_id);

-- 6. insight -- distill output = crew-shared SoT (owning_agent access filter)
CREATE TABLE insight (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  text         TEXT NOT NULL,
  evidence     TEXT CHECK (evidence IS NULL OR json_valid(evidence)),  -- JSON array of event ulids (provenance)
  owning_agent TEXT NOT NULL DEFAULT 'ag',
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
PRAGMA user_version = 26;

-- Close the atomicity wrapper. Any failure above -> .bail aborted, no COMMIT,
-- rollback, user_version stays 25.
COMMIT;
