-- 050_place_address_split.sql  (target user_version 50)
--
-- reversible: NO down-migration ships.  Additive except for ONE in-place edit
--   (the travel_rule CHECK texts, swapped by the 049 writable_schema idiom;
--   the swap is one-way LOOSER, so no existing row can be invalidated).
--   Hand-rollback: DROP the six triggers, DROP sub_place, copy each
--   is_primary=1 place_address value back onto place.address (the mirror
--   already holds exactly that string, so this step is a no-op in practice),
--   DROP place_address, re-run this file's travel_rule UPDATE with the
--   replacement pairs swapped, PRAGMA user_version = 49.  A FAILED apply needs
--   no rollback: single transaction, .bail on, stamp last (023 discipline).
--
-- ==========================================================================
-- WHY.  ONE `place` COLUMN IS CARRYING FOUR DIFFERENT JOBS.
--
-- `place` stores a display name, a JSON alias list and (035) ONE address
-- string.  Measured on the live registry 2026-09-12 (20 rows), that one string
-- is being made to carry four incompatible things at once:
--
--   * a BUILDING NAME plus a FLOOR      (place 1: "<tower> 12층")
--   * a LOT NUMBER (지번)                (place 2: "<dong> 220-15")
--   * a ROOM, escaped into `aliases`     (a church place carrying its main hall
--                                         as a match-alias)
--   * the address as the place NAME      (place 15: a road address, no name)
--
-- and the consequence is not cosmetic.  ONE physical venue splits into several
-- `place` ROWS -- three rows for one rehearsal studio (본점 / 본점 A2룸 /
-- 본점 D룸), two rows for one retreat centre -- and because travel knowledge
-- hangs off place rows (place_link + measure, 036), every duplicate row must
-- have its minutes registered AGAIN, per direction, by hand.
--
-- The bill has already been paid twice.  (1) 2026-09-12: a 45-minute leg to the
-- studio had to be registered a SECOND time because the appointment named a
-- different ROOM of the same building.  (2) 2026-09-11: a wedding stored
-- nothing but a map link, so no travel derived at all and the owner found out
-- by asking.
--
-- ==========================================================================
-- WHAT LANDS -- THREE LAYERS, DECIDED BY THE OWNER (2026-09-12 20:23 / 21:23).
--
--   1. PLACE       = the terminus of a trip.  One name the owner uses.
--                    place_link / measure attach HERE AND NOWHERE ELSE.
--   2. ADDRESS     = the string a MAP resolves.  A place has MANY
--                    (place_address): road / lot / building / map_url, with
--                    exactly one marked is_primary -- the one handed to the
--                    map puller.
--   3. SUB-PLACE   = room · floor · suite · hall (sub_place).  An appointment
--                    points at one.  It is STRUCTURALLY INCAPABLE of being a
--                    travel terminus.
--
-- Layer 3 is the load-bearing one, and "structurally" is the whole word.
-- "Do not register a room as a place" written in a skill file is a 3.4%-
-- adherence document (D9); what ships here is a machine:
--
--   (a) sub_place is a DIFFERENT TABLE from place.  Nothing anywhere
--       references sub_place(id) -- not place_link.from_place_id /
--       to_place_id, not travel_rule.place_id, not block.place_id, all three
--       of which are FKs to place(id).  A sub-place therefore has no seat in
--       which a trip could end.  (Pinned by test_mig050_place_split.py S1,
--       which reads sqlite_master and fails if any column ever gains one.)
--   (b) once a room is DECLARED, minting a place out of that same string is
--       REFUSED by a trigger -- both the bare room name ("D룸") and the
--       composed form the calendar actually produces ("<parent> D룸").  This
--       is the guard that makes 2026-09-12 unrepeatable: the room string that
--       minted a third studio row now aborts, naming the parent place and the
--       sub-place verb.
--
-- Same discipline as 040's containment guard: a rule that MUST hold is a
-- structure, not a sentence (D2).
--
-- ==========================================================================
-- BOTH ROAD-NAME AND LOT ADDRESSES ARE KEPT.  A design with one address
-- column was rejected on measured grounds: the owner's home is already on file
-- as a LOT number, the map puller autocompletes ROAD names far more reliably,
-- and a shared map LINK is a third spelling of the same venue (the wedding).
-- All three are "a string that points at this place", so all three are rows of
-- one table with a kind, and is_primary says which one the map gets.
--
-- ==========================================================================
-- WHY place.address SURVIVES -- AS A MIRROR, NOT AS A SECOND HOME.
--
-- routines/bundle/travel-resolve.sh reads `place.address` in raw SQL, from a
-- shell function, on the route path.  Two stores for one fact is the exact
-- drift 036 removed (place.to_min vs measure).  So place_address is THE home
-- and place.address is a DERIVED MIRROR of the in-force primary, maintained by
-- trigger -- every pre-050 reader keeps working byte for byte, and it cannot go
-- stale because it is not writable:
--
--   trg_place_address_mirror_{ins,upd,del}  push the primary value down.
--   trg_place_address_is_derived_{ins,upd}  REFUSE a direct place.address
--                                           write that disagrees with the
--                                           primary (the mirror's own write
--                                           agrees, so it passes).
--
-- The refusal is deliberately total rather than advisory: a mirror that one
-- writer can desync is a mirror nobody may trust.
--
-- ==========================================================================
-- THE BACKFILL DOES NOT GUESS A KIND (038 DISCIPLINE).
--
-- Every non-blank place.address becomes exactly one place_address row with
-- is_primary=1.  The kind is assigned ONLY where the string's own structure
-- settles it:
--
--   map_url  the value starts http:// or https://.  A URL is not a street.
--   road     the value contains a road-name token immediately followed by a
--            building number -- GLOB '*로 [0-9]*' or '*길 [0-9]*'.  Korean
--            road-name addresses are defined that way (도로명 + 건물번호), and
--            no other address shape produces it.
--   legacy   EVERYTHING ELSE.
--
-- `lot` and `building` are assigned by NOTHING here, on purpose.  The obvious
-- lot rule ("<something>동 <number>") misfires on the shape that is already in
-- the live data -- an apartment block, "…아파트 112동 903호", matches it
-- exactly -- so the rule is not a classification, it is a coin flip with a
-- schema column to land in.  A string this file cannot settle is tagged
-- `legacy`, which is not a failure state: it is a QUESTION the next map
-- resolution answers, and until then the value routes exactly as it did at
-- 049 (it is still the primary, still mirrored to place.address, still what
-- travel-resolve.sh types into the map).  Inventing 'lot' to avoid an honest
-- 'legacy' is 038's manufactured zero wearing a different hat.
--
-- NO sub_place ROW IS CREATED BY THIS FILE.  Splitting "<tower> 12층" into a
-- place and a floor is a JUDGEMENT about one owner's twenty rows, keyed on ids
-- that mean something different on every other instance.  A schema migration
-- that merges live rows by id is a data-loss weapon on any database but one.
-- That work ships as a separate, dry-run-first, fingerprint-guarded tool
-- (database/place_merge.py + a plan file) which refuses to run unless each
-- planned row's ULID matches the plan's recorded fingerprint.
--
-- ==========================================================================
-- travel_rule GAINS A THIRD src VALUE: 'provisional'.
--
-- Owner rule (2026-09-12): "장소 없으면 기본 1시간".  Standing code contract
-- (travel_attach_sweep / TRAVEL_PLACE_NEEDED): never invent a duration -- ask
-- once.  Both are right, and a silent 60 resolves the conflict the wrong way:
-- six months later nothing can say whether that number was measured, declared
-- or guessed.
--
-- So the 60 is STAMPED.  A rule whose duration could not be derived is armed
-- with before_min = 60 and before_src = 'provisional' -- the owner is no longer
-- left with no travel block at all, the question is STILL owed (the outcome
-- stays in the elicit set and TRAVEL_PLACE_NEEDED still prints), and the
-- marker is a column, so `WHERE before_src='provisional'` is exactly the set
-- the sweep re-resolves and overwrites the moment a real place or edge lands.
-- That overwrite is deterministic verb-layer code, not model discretion (D2).
--
-- Three enum CHECKs widen to include 'provisional', and the place_id CHECK
-- widens so a rule with NO place at all (the map-link wedding) may carry the
-- provisional stopgap.  All four are ONE-WAY LOOSER: every string that
-- satisfied the old text satisfies the new one, so no existing row is
-- invalidated and no repair pass ships.  SQLite cannot ALTER a CHECK; the swap
-- is the 049 writable_schema idiom (fixed strings in, fixed strings out,
-- guarded to hit exactly one table, schema cookie bumped first so every other
-- connection reparses) rather than a twelve-step rebuild of a table five
-- other objects FK into.
--
-- ==========================================================================
-- READER SAFETY (measured, not assumed).  Two new tables and one preserved
-- column: no `place` reader changes shape, no place row is re-keyed, no
-- positional place INSERT changes arity.  schema.sql's base-row seed passes
-- NULL for address (it always did), so trg_place_address_is_derived_ins does
-- not fire on a fresh mint.
--
-- WRITER SAFETY.  The only writers of place.address in the payload are
-- travel_place_add / travel_place_upd in kit_life.py; both are repointed at
-- place_address in the same change.  The refusal trigger is what makes that
-- statement enforceable rather than a claim about today's grep.
--
-- ==========================================================================
-- APPLY NOTE.  NOTHING IN THIS COMMIT HAS BEEN APPLIED TO ANY DATABASE.
-- 049 is the current head and is itself NOT YET APPLIED; this file takes 050
-- and refuses to run at any user_version but 49.  The crew-shared DB
-- (~/.dogany/crews/kit_life/kit_life.db) is ONE physical file, so the
-- EXPECTED_USER_VERSION pins of every crew member must read 50 before the
-- installer will migrate (pack_install.sh _kit_crew_lockstep_guard).  Order:
--   0. cp <db> <db>.pre050
--   1. land the payload on every crew member (pins -> 50), migrate 049 then 050
--   2. verify:  PRAGMA user_version            -> 50
--               PRAGMA integrity_check         -> ok
--               PRAGMA foreign_key_check       -> (no output)
--               SELECT COUNT(*) FROM place WHERE address IS NOT NULL
--                 AND address IS NOT (SELECT value FROM place_address
--                   WHERE place_id=place.id AND is_primary=1);   -> 0
--   3. THEN, and only then, the merge tool -- separately, dry-run first:
--        python3 database/place_merge.py --db <db> --plan <plan> --pin
--        python3 database/place_merge.py --db <db> --plan <plan> --dry-run
--        python3 database/place_merge.py --db <db> --plan <plan> --apply
--      --pin is a READ that stamps each planned id's current ULID into the
--      plan and prints the names for the owner to eyeball; --apply refuses
--      unless every fingerprint still matches.
--
-- ==========================================================================
-- GUARD.  Four-sided:
--   1. user_version must be EXACTLY 49, checked BEFORE any transaction.
--   2. travel_rule must be PRESENT and must carry, for each of the four
--      constraints, one of the two known texts (pre-050 or post-050) BEFORE
--      the swap -- shape, not state (see the guard's own note).
--   3. ZERO travel_rule CHECK texts may survive the swap.
--   4. AFTER the backfill, every place row's address must equal its primary
--      place_address value (the mirror invariant this file's triggers exist
--      to hold).  A lineage that cannot satisfy it is refused, not half-done.
-- Any of the four failing aborts under .bail with the transaction rolled back
-- and user_version still 49.
.bail on
.timeout 5000

-- guard 1: version
CREATE TEMP TABLE _guard_050(seen_user_version);
CREATE TEMP TRIGGER _guard_050_refuse BEFORE INSERT ON _guard_050 BEGIN
  SELECT RAISE(FAIL, 'migration 050 refused: this database is not at user_version 49 -- either 050 was already applied (>= 50) or the 021~049 chain has not run (< 49). Nothing changed');
END;
INSERT INTO _guard_050
  SELECT user_version FROM pragma_user_version WHERE user_version <> 49;
DROP TABLE temp._guard_050;

BEGIN IMMEDIATE;

-- guard 2: travel_rule must BE the shape this file was written against.
--
-- IT COUNTS SHAPE, NOT STATE -- the correction 049's guard 2 already had to
-- make, for the same reason, one migration later. Requiring the OLD text reads
-- stricter and is a FALSE refusal: the estate's chain-parity test
-- (tests/test_mig037_judgment_layer.py) builds a synthetic lineage by minting
-- from HEAD schema.sql and re-stepping 037..head, so when 050 runs there
-- travel_rule already carries the NEW text and a `= old` guard would take the
-- whole ladder down over something 050 does not change. Accepting either known
-- text admits every real pre-050 database (at user_version 49 the text is old
-- -- guard 1 has already established that) and the ladder's own intermediate,
-- while still refusing a travel_rule nobody in this lineage wrote. What the
-- FINAL state must be is not weakened: guard 3 still demands zero old texts.
CREATE TEMP TABLE _guard_050_pre(seen_count);
CREATE TEMP TRIGGER _guard_050_pre_refuse BEFORE INSERT ON _guard_050_pre BEGIN
  SELECT RAISE(FAIL, 'migration 050 refused: travel_rule carries neither the pre-050 src/place_id CHECK texts this file replaces nor the post-050 ones it writes. This is not the shape it was written against. Nothing changed');
END;
INSERT INTO _guard_050_pre
  SELECT COUNT(*) FROM sqlite_master
   WHERE type='table' AND name='travel_rule'
     AND (instr(sql, 'CHECK (before_src IN (''place'',''explicit''))') > 0
          OR instr(sql, 'CHECK (before_src IN (''place'',''explicit'',''provisional''))') > 0)
     AND (instr(sql, 'CHECK (after_src IN (''place'',''explicit''))') > 0
          OR instr(sql, 'CHECK (after_src IN (''place'',''explicit'',''provisional''))') > 0)
     AND (instr(sql, 'CHECK (prep_src IN (''place'',''explicit''))') > 0
          OR instr(sql, 'CHECK (prep_src IN (''place'',''explicit'',''provisional''))') > 0)
     AND instr(sql, 'CHECK (place_id IS NOT NULL') > 0
  HAVING COUNT(*) <> 1;
DROP TABLE temp._guard_050_pre;

-- ── 1. ADDRESS: many strings, one primary ────────────────────────────────
-- No UNIQUE on (place_id, kind): a venue legitimately carries two map links or
-- two building names.  The row identity is the VALUE, so re-teaching the same
-- string is a no-op instead of a duplicate.  The partial unique index is the
-- whole is_primary contract: at most one primary per place, enforced by the
-- index rather than by whoever remembers to clear the old one.
CREATE TABLE place_address (
  id         INTEGER PRIMARY KEY,
  place_id   INTEGER NOT NULL REFERENCES place(id) ON DELETE CASCADE,
  -- road     도로명 (road name + building number)
  -- lot      지번
  -- building building / POI name
  -- map_url  a shared map link (naver.me, kakao, google, apple)
  -- legacy   NOT YET CLASSIFIED.  An honest "this file could not tell", to be
  --          re-tagged by the next map resolution.  Never a guess.
  kind       TEXT NOT NULL CHECK (kind IN ('road','lot','building','map_url','legacy')),
  value      TEXT NOT NULL CHECK (TRIM(value) <> ''),
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE UNIQUE INDEX ux_place_address_value ON place_address(place_id, value);
CREATE UNIQUE INDEX ux_place_address_primary ON place_address(place_id) WHERE is_primary = 1;
CREATE INDEX ix_place_address_place ON place_address(place_id);

-- ── 2. SUB-PLACE: the room, which is not a place ─────────────────────────
-- `token` is the ASCII stable key (same job as place.token, 036); `name` is
-- what the owner says.  Nothing in this schema references sub_place(id) -- that
-- absence IS the guarantee that a room cannot terminate a trip, and
-- test_mig050_place_split.py S1 fails if it ever stops being true.
CREATE TABLE sub_place (
  id         INTEGER PRIMARY KEY,
  place_id   INTEGER NOT NULL REFERENCES place(id) ON DELETE CASCADE,
  name       TEXT NOT NULL CHECK (TRIM(name) <> ''),
  token      TEXT NOT NULL CHECK (TRIM(token) <> ''),
  note       TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE UNIQUE INDEX ux_sub_place_name  ON sub_place(place_id, name);
CREATE UNIQUE INDEX ux_sub_place_token ON sub_place(place_id, token);

-- ── 3. BACKFILL (before the triggers exist, so the mirror starts true) ────
-- 3a. A blank-but-not-NULL address is not an address.  NULL it, so the mirror
--     invariant (guard 4) is exactly true for every row rather than true for
--     every row worth checking.
UPDATE place SET address = NULL WHERE address IS NOT NULL AND TRIM(address) = '';

-- 3b. One primary row per addressed place.  kind is read off the string's own
--     structure or left `legacy` -- see the header on why `lot` and `building`
--     are assigned by nothing here.
INSERT INTO place_address(place_id, kind, value, is_primary)
SELECT p.id,
       CASE
         WHEN TRIM(p.address) GLOB 'http://*'  THEN 'map_url'
         WHEN TRIM(p.address) GLOB 'https://*' THEN 'map_url'
         WHEN TRIM(p.address) GLOB '*로 [0-9]*' THEN 'road'
         WHEN TRIM(p.address) GLOB '*길 [0-9]*' THEN 'road'
         ELSE 'legacy'
       END,
       TRIM(p.address),
       1
  FROM place p
 WHERE p.address IS NOT NULL AND TRIM(p.address) <> '';

-- 3c. The stored value is now the TRIMmed one; align the mirror before the
--     refusal trigger exists (afterwards this exact UPDATE is what the mirror
--     trigger itself issues).
UPDATE place SET address = TRIM(address) WHERE address IS NOT NULL;

-- ── 4. THE MIRROR (place.address is derived from here on) ────────────────
CREATE TRIGGER trg_place_address_mirror_ins AFTER INSERT ON place_address BEGIN
  UPDATE place
     SET address = (SELECT pa.value FROM place_address pa
                     WHERE pa.place_id = NEW.place_id AND pa.is_primary = 1)
   WHERE id = NEW.place_id;
END;
CREATE TRIGGER trg_place_address_mirror_upd AFTER UPDATE ON place_address BEGIN
  UPDATE place
     SET address = (SELECT pa.value FROM place_address pa
                     WHERE pa.place_id = NEW.place_id AND pa.is_primary = 1)
   WHERE id = NEW.place_id;
END;
CREATE TRIGGER trg_place_address_mirror_del AFTER DELETE ON place_address BEGIN
  UPDATE place
     SET address = (SELECT pa.value FROM place_address pa
                     WHERE pa.place_id = OLD.place_id AND pa.is_primary = 1)
   WHERE id = OLD.place_id;
END;

-- ── 5. THE MIRROR IS NOT WRITABLE ────────────────────────────────────────
-- `IS NOT` (not `<>`) so a NULL on either side compares correctly: clearing the
-- last address leaves both sides NULL and must PASS.  The mirror triggers above
-- write exactly the value this WHEN clause reads, so they never trip it.
CREATE TRIGGER trg_place_address_is_derived_ins
BEFORE INSERT ON place
WHEN NEW.address IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'place.address is DERIVED since 050 -- it mirrors place_address(is_primary=1). Insert the place with address NULL, then add the address row (place-address-add / travel-place-add address=...)');
END;
CREATE TRIGGER trg_place_address_is_derived_upd
BEFORE UPDATE OF address ON place
WHEN NEW.address IS NOT (SELECT pa.value FROM place_address pa
                          WHERE pa.place_id = NEW.id AND pa.is_primary = 1)
BEGIN
  SELECT RAISE(ABORT, 'place.address is DERIVED since 050 -- it mirrors place_address(is_primary=1). Write the address there (place-address-add / place-address-primary / travel-place-upd address=...), never here');
END;

-- ── 6. A ROOM CANNOT BECOME A PLACE (the load-bearing guard) ─────────────
-- Both forms are refused because both are what the world actually hands us:
-- the bare room name the owner says out loud ("D룸"), and the composed string
-- a calendar entry carries ("<parent> D룸") -- the second is the exact shape
-- that minted a third studio row on 2026-09-12.
-- Equality on the trimmed string, never a LIKE and never a prefix test: fuzzy
-- matching is forbidden in this lane (6) and a prefix rule would refuse a
-- legitimately new venue that merely starts with a known building's name.
CREATE TRIGGER trg_place_not_a_sub_place_ins
BEFORE INSERT ON place
WHEN EXISTS (SELECT 1 FROM sub_place sp JOIN place pp ON pp.id = sp.place_id
              WHERE TRIM(NEW.name) = sp.name
                 OR TRIM(NEW.name) = pp.name || ' ' || sp.name)
BEGIN
  SELECT RAISE(ABORT, 'place refused: that name is already declared as a SUB-PLACE (room/floor/hall) of an existing place. A sub-place is not a travel terminus -- point the appointment at the parent place and register the room with sub-place-add');
END;
CREATE TRIGGER trg_place_not_a_sub_place_upd
BEFORE UPDATE OF name ON place
WHEN EXISTS (SELECT 1 FROM sub_place sp JOIN place pp ON pp.id = sp.place_id
              WHERE sp.place_id <> NEW.id
                AND (TRIM(NEW.name) = sp.name
                     OR TRIM(NEW.name) = pp.name || ' ' || sp.name))
BEGIN
  SELECT RAISE(ABORT, 'place refused: that name is already declared as a SUB-PLACE (room/floor/hall) of another place. A sub-place is not a travel terminus -- point the appointment at the parent place and register the room with sub-place-add');
END;

-- ── 7. travel_rule: the provisional src (049 writable_schema idiom) ──────
-- Schema-cookie bump FIRST, against the un-edited schema, so every other
-- connection reparses after COMMIT.
CREATE TABLE _mig050_cookie(x);
DROP TABLE _mig050_cookie;

PRAGMA writable_schema = ON;
UPDATE sqlite_master
   SET sql = replace(replace(replace(replace(sql,
        'CHECK (before_src IN (''place'',''explicit''))',
        'CHECK (before_src IN (''place'',''explicit'',''provisional''))'),
        'CHECK (after_src IN (''place'',''explicit''))',
        'CHECK (after_src IN (''place'',''explicit'',''provisional''))'),
        'CHECK (prep_src IN (''place'',''explicit''))',
        'CHECK (prep_src IN (''place'',''explicit'',''provisional''))'),
        'CHECK (place_id IS NOT NULL
           OR (before_src = ''explicit'' AND after_src = ''explicit''
               AND prep_src = ''explicit''))',
        'CHECK (place_id IS NOT NULL
           OR (before_src IN (''explicit'',''provisional'')
               AND after_src IN (''explicit'',''provisional'')
               AND prep_src IN (''explicit'',''provisional'')))')
 WHERE type='table' AND name='travel_rule';
PRAGMA writable_schema = OFF;

-- guard 3: no pre-050 CHECK text may survive.
CREATE TEMP TABLE _guard_050_post(seen_count);
CREATE TEMP TRIGGER _guard_050_post_refuse BEFORE INSERT ON _guard_050_post BEGIN
  SELECT RAISE(FAIL, 'migration 050 refused: at least one pre-050 travel_rule CHECK text survived the swap -- rolled back, nothing changed');
END;
INSERT INTO _guard_050_post
  SELECT COUNT(*) FROM sqlite_master
   WHERE type='table' AND name='travel_rule'
     AND (instr(sql, 'CHECK (before_src IN (''place'',''explicit''))') > 0
          OR instr(sql, 'CHECK (after_src IN (''place'',''explicit''))') > 0
          OR instr(sql, 'CHECK (prep_src IN (''place'',''explicit''))') > 0
          OR instr(sql, 'AND prep_src = ''explicit''))') > 0)
  HAVING COUNT(*) <> 0;
DROP TABLE temp._guard_050_post;

-- guard 4: the mirror invariant, every row.
CREATE TEMP TABLE _guard_050_mirror(seen_count);
CREATE TEMP TRIGGER _guard_050_mirror_refuse BEFORE INSERT ON _guard_050_mirror BEGIN
  SELECT RAISE(FAIL, 'migration 050 refused: after the backfill at least one place.address does not equal its primary place_address value -- the mirror this file installs would be born stale. Rolled back, nothing changed');
END;
INSERT INTO _guard_050_mirror
  SELECT COUNT(*) FROM place p
   WHERE p.address IS NOT (SELECT pa.value FROM place_address pa
                            WHERE pa.place_id = p.id AND pa.is_primary = 1)
  HAVING COUNT(*) <> 0;
DROP TABLE temp._guard_050_mirror;

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything above
-- failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 49.
PRAGMA user_version = 50;
COMMIT;
