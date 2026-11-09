-- 069_map_region.sql  (target user_version 69)
--
-- reversible: YES.  Rollback = DROP TABLE place_region; DROP TABLE
--   map_region; PRAGMA user_version = 68.  Additive: two new tables and one
--   seed row; no existing table / column / trigger touched.
--
-- ==========================================================================
-- WHAT.  Which map app opens a move's directions, chosen PER LEG by where
-- the leg is.
--   map_region    one row per region (ISO 3166-1 alpha-2): the local map
--                 apps people there use for directions (JSON array of
--                 provider tokens), an optional label in the user's
--                 language, and an optional coordinate box used to place a
--                 coordinate in the region.
--   place_region  one row per place: the region the place is in, when it
--                 was looked up (agent lookup on PLACE_REGION_NEEDED, or the
--                 owner said it).  A place with no row is placed by its
--                 coordinates (066 place_coord) against the region boxes.
--
-- WHY (owner 2026-10-06 08:47-08:53, prep / travel alert map buttons):
--   "this is the Korean locale; global should be something like Google"
--   "when I'm abroad it has to be Google too"
--   "Google is the global default and it is settable by locale"
--   "let the user choose on first use / default Google, 'this place is in
--    Korea so Kakao and Naver work too' / and not hardcoded: look the region
--    up"
-- So: Google everywhere by default (it needs no coordinates); a region's
-- local providers are DATA (this table), looked up and stored, never a
-- per-country branch in code; the code keeps only the link builders (a
-- provider can only be offered if the kit can build its link); the user's
-- pick is stored per region (config map_provider_<CC>), asked once per
-- region on first use; a leg whose two ends are in different regions (or
-- an unknown one) gets Google.
--
-- SEED.  KR -> ["kakao", "naver"] with its box (33.0..38.7 N, 124.5..132.0
-- E): the region the 066 buttons were built for, and the region every
-- coordinate the Naver route resolve stored is in.  The box is coarse (it
-- also covers Tsushima); a place_region row always wins over the box.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_069(seen_user_version);
CREATE TEMP TRIGGER _guard_069_refuse BEFORE INSERT ON _guard_069 BEGIN
  SELECT RAISE(FAIL, 'migration 069 refused: this database is not at user_version 68 -- either 069 was already applied (>= 69) or the chain up to 068 has not run (< 68). Nothing changed');
END;
INSERT INTO _guard_069
  SELECT user_version FROM pragma_user_version WHERE user_version <> 68;
DROP TABLE temp._guard_069;

BEGIN IMMEDIATE;

CREATE TABLE map_region (
  region     TEXT PRIMARY KEY CHECK (region GLOB '[A-Z][A-Z]'),
  label      TEXT CHECK (label IS NULL OR TRIM(label) <> ''),
  providers  TEXT NOT NULL DEFAULT '[]'
             CHECK (json_valid(providers) AND json_type(providers) = 'array'),
  lat_min    REAL, lat_max REAL, lng_min REAL, lng_max REAL,
  source     TEXT NOT NULL CHECK (TRIM(source) <> ''),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK ((lat_min IS NULL AND lat_max IS NULL AND lng_min IS NULL
          AND lng_max IS NULL)
      OR (lat_min IS NOT NULL AND lat_max IS NOT NULL
          AND lng_min IS NOT NULL AND lng_max IS NOT NULL
          AND lat_min >= -90 AND lat_max <= 90 AND lat_min < lat_max
          AND lng_min >= -180 AND lng_max <= 180 AND lng_min < lng_max))
);

CREATE TABLE place_region (
  place_id   INTEGER PRIMARY KEY REFERENCES place(id) ON DELETE CASCADE,
  region     TEXT NOT NULL CHECK (region GLOB '[A-Z][A-Z]'),
  source     TEXT NOT NULL CHECK (TRIM(source) <> ''),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

INSERT INTO map_region (region, label, providers, lat_min, lat_max, lng_min,
                        lng_max, source, updated_at)
VALUES ('KR', NULL, '["kakao","naver"]', 33.0, 38.7, 124.5, 132.0, 'seed',
        '2026-10-07T00:00:00Z');

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 69;
COMMIT;
