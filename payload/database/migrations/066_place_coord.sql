-- 066_place_coord.sql  (target user_version 66)
--
-- reversible: YES.  Rollback = DROP TABLE place_coord; PRAGMA user_version =
--   65.  Additive: one new table, no existing table / column / trigger touched.
--
-- ==========================================================================
-- WHAT.  Where a place IS on the map, once per place (owner 2026-10-06
-- 07:23-07:28: prep and travel alerts carry [Kakao map] [Naver map] buttons
-- that open directions with both ends filled, the user picks on demand).
--
-- WHY A TABLE.  Neither map opens directions from text alone: the Kakao web
-- route app reads only coordinates (rt=..., /link/by/<mode>/name,lat,lng/...),
-- and the Naver directions page addresses each end as "x,y,name,id,type".  A
-- button that cannot fill both ends is a broken link, which the alert must
-- never carry -- so the coordinates have to be stored.  place_address cannot
-- hold them (its kinds are typeable strings the map autocompletes), and
-- place.address is a derived mirror (050).
--
-- WHERE THEY COME FROM.  Only the existing Naver route resolve
-- (travel-resolve.sh -> place-view/route.sh).  route.sh already lands on the
-- directions page for (origin, dest); that page's URL names both ends.  The
-- resolve stores them here (kit_life.sh place-coord-set).  Nothing is
-- geocoded, guessed, or asked: a place nobody routed has no row, and its
-- alert offers no Naver button.
--
--   lat, lng   WGS84 degrees (Kakao /link/by takes these).
--   naver_ref  the Naver directions endpoint segment exactly as the map
--              wrote it ("x,y,name,id,type"), so the Naver button reopens
--              the same POI the resolve routed.
--   source     who wrote the row ('naver_route' today).
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_066(seen_user_version);
CREATE TEMP TRIGGER _guard_066_refuse BEFORE INSERT ON _guard_066 BEGIN
  SELECT RAISE(FAIL, 'migration 066 refused: this database is not at user_version 65 -- either 066 was already applied (>= 66) or the chain up to 065 has not run (< 65). Nothing changed');
END;
INSERT INTO _guard_066
  SELECT user_version FROM pragma_user_version WHERE user_version <> 65;
DROP TABLE temp._guard_066;

BEGIN IMMEDIATE;

CREATE TABLE place_coord (
  place_id   INTEGER PRIMARY KEY REFERENCES place(id) ON DELETE CASCADE,
  lat        REAL NOT NULL CHECK (lat BETWEEN -90 AND 90),
  lng        REAL NOT NULL CHECK (lng BETWEEN -180 AND 180),
  naver_ref  TEXT CHECK (naver_ref IS NULL OR TRIM(naver_ref) <> ''),
  source     TEXT NOT NULL DEFAULT 'naver_route' CHECK (TRIM(source) <> ''),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 66;
COMMIT;
