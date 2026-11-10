-- 021_travel_edge.sql  (target user_version 21)
-- reversible: yes  (new-table add + pure back-fill; rollback = DROP travel_edge)
-- Source: DGN-623 root fix -- movement spatial awareness (T-012 restore).
-- v20 -> v21.
--
-- DEFECT: one-way movement duration was modeled as a NODE property
-- (place.to_min / from_min), implicitly base(집)-origin. The deriver then
-- always departed from 집, ignoring where the user actually IS at the event
-- time (weekday evening -> 회사, not 집). Fix: movement duration is an EDGE
-- property of an ORDERED (from_place, to_place) pair. The deriver resolves the
-- origin from the presence layer at event time and looks up the edge.
--
-- This migration is PURE-ADDITIVE:
--   * new table travel_edge (from_place_id, to_place_id, minutes, prep_min)
--   * back-fill existing base-origin durations into edges so nothing is lost:
--       - place.to_min present  -> edge (base -> place)  minutes = to_min
--       - place.from_min present -> edge (place -> base) minutes = from_min
--     prep_min rides the outbound (base->place) edge (declared prep default).
-- place.to_min / from_min columns are LEFT IN PLACE (read by legacy paths /
-- back-compat); the deriver now prefers a travel_edge row when one exists and
-- falls back to the node columns only for the base-origin case.
--
-- The CREATE TABLE / INDEX DDL below is byte-matched to the fresh-mint v21
-- schema.sql so `fresh == migrated`.

-- ============================================================================
-- travel_edge: one-way movement duration for an ORDERED place pair.
-- ============================================================================
CREATE TABLE IF NOT EXISTS travel_edge (
    id            INTEGER PRIMARY KEY,
    from_place_id INTEGER NOT NULL REFERENCES place(id),
    to_place_id   INTEGER NOT NULL REFERENCES place(id),
    minutes       INTEGER NOT NULL,          -- one-way travel, grid-ceiled
    prep_min      INTEGER,                   -- declared prep default (nullable)
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    CHECK (from_place_id != to_place_id),
    CHECK (minutes >= 0 AND minutes % 15 = 0),
    CHECK (prep_min IS NULL OR (prep_min >= 0 AND prep_min % 15 = 0))
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_travel_edge_pair
    ON travel_edge(from_place_id, to_place_id);

-- ----------------------------------------------------------------------------
-- Back-fill: outbound base -> place edges from place.to_min.
--   base = the is_base=1 place (집). A place that IS the base is skipped
--   (self-edge forbidden). Idempotent via INSERT OR IGNORE on the unique pair.
-- ----------------------------------------------------------------------------
INSERT OR IGNORE INTO travel_edge
    (from_place_id, to_place_id, minutes, prep_min, created_at, updated_at)
SELECT b.id, p.id, p.to_min, p.prep_min,
       '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z'
FROM place p
JOIN place b ON b.is_base = 1
WHERE p.to_min IS NOT NULL
  AND p.id != b.id;

-- ----------------------------------------------------------------------------
-- Back-fill: homeward place -> base edges from place.from_min.
-- ----------------------------------------------------------------------------
INSERT OR IGNORE INTO travel_edge
    (from_place_id, to_place_id, minutes, prep_min, created_at, updated_at)
SELECT p.id, b.id, p.from_min, NULL,
       '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z'
FROM place p
JOIN place b ON b.is_base = 1
WHERE p.from_min IS NOT NULL
  AND p.id != b.id;

PRAGMA user_version = 21;
