-- 071_shop_find.sql  (target user_version 71)
--
-- reversible: YES.  Rollback = DROP TABLE shop_shown; DROP TABLE shop_round;
--   DROP TABLE shop_hunt; DROP TABLE shop_item; DROP TABLE shop_category;
--   the writable_schema swap below with the two strings exchanged
--   (legal only while no near_match_refusal row has domain 'item' or
--   'category'); PRAGMA user_version = 70.  Additive: five new tables, twelve
--   seed rows, one CHECK widened.  No config row is written (051 convention:
--   migrations never seed config; the defaults below live in shop.py).
--
-- ==========================================================================
-- WHAT.  module_shopping phase 1 -- find-things (the maintainer worklog
-- 2026-10-07-module-shopping-SPEC, build notes P1).  The find request is a
-- ledger (D11): the hunt, its rounds and the cards shown in each round are
-- rows, so a "let me look and come back" gap or a restart resumes from the
-- store, not from model memory.
--
--   shop_category  what kind of thing is this?  ASCII token `name`; the
--                  owner sees display_name when the owner named it, else the
--                  i18n label shop.category.<name>.  consumable = does a
--                  thing of this kind run out (rebuy default, P3).
--                  checks_required = which image checks a card of this kind
--                  must pass before it can be shown (engine gate, data not
--                  code: apparel and shoes need wearer/adult/form/colour,
--                  every other kind form/adult).  active = still offered
--                  (retire, never delete while referenced).
--   shop_item      which exact product is this?  shared later by owned /
--                  wish rows (P2/P3).  Price is NOT here: it belongs to the
--                  offer at a moment (shop_shown.price_shown).
--   shop_hunt      what is the owner looking for right now?  ask_verbatim =
--                  the want in the owner's words (A1 goal prose).  At most
--                  ONE open hunt per (category, for_person): the partial
--                  unique index is the find-first target of shop-hunt-open
--                  (it resumes instead of duplicating).  budget_hard = the
--                  owner chose "within budget" on an overrun: later rounds
--                  refuse cards above budget_max.
--   shop_round     what did one round cover?  sources_seen must name at
--                  least one source (the coverage line, defect 3 of the red
--                  jumpsuit session); sources_blocked may be [].  assumption
--                  = the wearer / size basis the round assumed when no fact
--                  was on record (stated in the coverage line, correctable).
--                  partial_reason = why fewer than 3 cards were served.
--   shop_shown     what was put in front of the owner, and what did they
--                  say?  slot = where it sat (engine-assigned rotation),
--                  rank = the model's order (rotation is measurable).
--                  checks = the verification claims that passed; verdict
--                  NULL = no verdict, never read as a dislike.  closest = the
--                  owner's "least bad" card of a round rejected as a whole
--                  (the one follow-up after "none of these"; it steers the
--                  next round, it is not a pick).
--
-- WHY (owner 2026-10-06 21:08 "throw a few and let me pick, not a taste
-- interview first"; 2026-10-07 10:25 module placement; the life agent red jumpsuit
-- session defects: no direction, text-only picks, single undisclosed
-- source, repeated permission asks).  The round shape (3-4 distinct
-- image-verified cards, coverage line, budget overrun) is enforced by the
-- verb layer (shop.py); the CHECKs here are the floor under it.
--
-- near_match_refusal.domain gains 'item' and 'category': shop-item-upsert
-- and shop-cat-add run the kit's one near-variant check before a create, and
-- the owner's "no, a different one" must be rememberable like every other
-- domain's.  Widening a CHECK is one of the edits SQLite documents as safe
-- by rewriting the stored CREATE text (no existing row can violate a
-- constraint that only got looser); same mechanism and reasoning as 049 /
-- 061.  Fixed string in, fixed string out, shape-counted before and after.
--
-- SEED.  Twelve categories (spec data model): apparel top / bottom /
-- onepiece / outer, shoes, haircare, skincare, supplement, food_pantry,
-- gadget, home_goods, gift_misc.  display_name stays NULL (labels live in
-- the i18n bundles).  Config keys READ by shop.py (absent = its default;
-- the owner sets them with config-set, nothing here writes them):
--   shop_round_size     4   cards per round, 3 or 4 (074 set-size constant)
--   shop_sources_kr     default Korean sources (JSON list)
--   shop_sources_global default global sources (JSON list)
--   shop_review_floor   10  review count under which a card's rating is
--                           labelled thin (074 thin-reviews: a product
--                           constant, no source sets it)
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_071(seen_user_version);
CREATE TEMP TRIGGER _guard_071_refuse BEFORE INSERT ON _guard_071 BEGIN
  SELECT RAISE(FAIL, 'migration 071 refused: this database is not at user_version 70 -- either 071 was already applied (>= 71) or the chain up to 070 has not run (< 70). Nothing changed');
END;
INSERT INTO _guard_071
  SELECT user_version FROM pragma_user_version WHERE user_version <> 70;
DROP TABLE temp._guard_071;

-- Guard 2: near_match_refusal carries the domain CHECK exactly once, in its
-- 060 form or already widened (a fresh mint of this schema.sql).
CREATE TEMP TABLE _guard_071_shape(n);
CREATE TEMP TRIGGER _guard_071_shape_refuse BEFORE INSERT ON _guard_071_shape BEGIN
  SELECT RAISE(FAIL, 'migration 071 refused: near_match_refusal does not carry the 060 domain CHECK text exactly once (old or widened form). Nothing changed');
END;
INSERT INTO _guard_071_shape
  SELECT 1 FROM sqlite_master
   WHERE type = 'table' AND name = 'near_match_refusal'
     AND (instr(sql, '(''place'',''person'',''method'',''food'',''exercise'')') > 0)
       + (instr(sql, '(''place'',''person'',''method'',''food'',''exercise'',''item'',''category'')') > 0)
       <> 1;
DROP TABLE temp._guard_071_shape;

BEGIN IMMEDIATE;

CREATE TABLE shop_category (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE
                  CHECK (name <> '' AND name NOT GLOB '*[^a-z0-9_]*'
                         AND name GLOB '[a-z]*'),
  display_name    TEXT CHECK (display_name IS NULL OR TRIM(display_name) <> ''),
  consumable      INTEGER NOT NULL DEFAULT 0 CHECK (consumable IN (0,1)),
  checks_required TEXT NOT NULL DEFAULT '["form","adult"]'
                  CHECK (json_valid(checks_required)
                         AND json_type(checks_required) = 'array'
                         AND json_array_length(checks_required) >= 1),
  active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE shop_item (
  id                  INTEGER PRIMARY KEY,
  ulid                TEXT NOT NULL UNIQUE,
  category_id         INTEGER NOT NULL REFERENCES shop_category(id),
  name                TEXT NOT NULL CHECK (TRIM(name) <> ''),
  brand               TEXT CHECK (brand IS NULL OR TRIM(brand) <> ''),
  variant             TEXT CHECK (variant IS NULL OR TRIM(variant) <> ''),
  url                 TEXT CHECK (url IS NULL OR TRIM(url) <> ''),
  image_ref           TEXT CHECK (image_ref IS NULL OR TRIM(image_ref) <> ''),
  aliases             TEXT NOT NULL DEFAULT '[]'
                      CHECK (json_valid(aliases) AND json_type(aliases) = 'array'),
  consumable_override INTEGER CHECK (consumable_override IS NULL
                                     OR consumable_override IN (0,1)),
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX ix_shop_item_category ON shop_item(category_id);
CREATE INDEX ix_shop_item_url ON shop_item(url) WHERE url IS NOT NULL;

CREATE TABLE shop_hunt (
  id            INTEGER PRIMARY KEY,
  ulid          TEXT NOT NULL UNIQUE,
  category_id   INTEGER NOT NULL REFERENCES shop_category(id),
  ask_verbatim  TEXT NOT NULL CHECK (TRIM(ask_verbatim) <> ''),
  for_person_id INTEGER REFERENCES persons(id),
  budget_max    INTEGER CHECK (budget_max IS NULL OR budget_max > 0),
  budget_hard   INTEGER NOT NULL DEFAULT 0 CHECK (budget_hard IN (0,1)),
  need_by       TEXT CHECK (need_by IS NULL
                            OR need_by GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  state         TEXT NOT NULL DEFAULT 'open'
                CHECK (state IN ('open','picked','to_wish','abandoned','expired')),
  opened_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  closed_at     TEXT,
  CHECK (budget_hard = 0 OR budget_max IS NOT NULL),
  CHECK ((state = 'open') = (closed_at IS NULL) OR state = 'expired')
);
CREATE UNIQUE INDEX ux_shop_hunt_open ON shop_hunt(category_id,
  COALESCE(for_person_id, 0)) WHERE state = 'open';

CREATE TABLE shop_round (
  id              INTEGER PRIMARY KEY,
  hunt_id         INTEGER NOT NULL REFERENCES shop_hunt(id) ON DELETE CASCADE,
  round_no        INTEGER NOT NULL CHECK (round_no >= 1),
  sources_seen    TEXT NOT NULL
                  CHECK (json_valid(sources_seen)
                         AND json_type(sources_seen) = 'array'
                         AND json_array_length(sources_seen) >= 1),
  sources_blocked TEXT NOT NULL DEFAULT '[]'
                  CHECK (json_valid(sources_blocked)
                         AND json_type(sources_blocked) = 'array'),
  assumption      TEXT CHECK (assumption IS NULL OR TRIM(assumption) <> ''),
  partial_reason  TEXT CHECK (partial_reason IS NULL OR TRIM(partial_reason) <> ''),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  UNIQUE (hunt_id, round_no)
);

CREATE TABLE shop_shown (
  id               INTEGER PRIMARY KEY,
  round_id         INTEGER NOT NULL REFERENCES shop_round(id) ON DELETE CASCADE,
  slot             INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 4),
  rank             INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 4),
  item_id          INTEGER NOT NULL REFERENCES shop_item(id),
  direction        TEXT NOT NULL CHECK (TRIM(direction) <> ''),
  price_shown      INTEGER CHECK (price_shown IS NULL OR price_shown >= 0),
  ship_by          TEXT CHECK (ship_by IS NULL
                               OR ship_by GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  source           TEXT NOT NULL CHECK (TRIM(source) <> ''),
  checks           TEXT NOT NULL
                   CHECK (json_valid(checks) AND json_type(checks) = 'object'),
  reasons          TEXT NOT NULL DEFAULT '[]'
                   CHECK (json_valid(reasons) AND json_type(reasons) = 'array'
                          AND json_array_length(reasons) <= 3),
  review_count     INTEGER CHECK (review_count IS NULL OR review_count >= 0),
  verdict          TEXT CHECK (verdict IS NULL OR verdict IN
                     ('picked','rejected','accepted_default','void')),
  reject_scope     TEXT CHECK (reject_scope IS NULL OR reject_scope IN ('round','item')),
  verdict_verbatim TEXT,
  verdict_at       TEXT,
  closest          INTEGER NOT NULL DEFAULT 0 CHECK (closest IN (0,1)),
  UNIQUE (round_id, slot),
  UNIQUE (round_id, rank),
  UNIQUE (round_id, item_id),
  CHECK (reject_scope IS NULL OR verdict = 'rejected'),
  CHECK ((verdict IS NULL) = (verdict_at IS NULL)),
  CHECK (closest = 0 OR verdict = 'rejected')
);
CREATE INDEX ix_shop_shown_item ON shop_shown(item_id);

INSERT INTO shop_category (name, consumable, checks_required, created_at) VALUES
  ('apparel_top',      0, '["wearer","adult","form","colour"]', '2026-10-07T00:00:00Z'),
  ('apparel_bottom',   0, '["wearer","adult","form","colour"]', '2026-10-07T00:00:00Z'),
  ('apparel_onepiece', 0, '["wearer","adult","form","colour"]', '2026-10-07T00:00:00Z'),
  ('apparel_outer',    0, '["wearer","adult","form","colour"]', '2026-10-07T00:00:00Z'),
  ('shoes',            0, '["wearer","adult","form","colour"]', '2026-10-07T00:00:00Z'),
  ('haircare',         1, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('skincare',         1, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('supplement',       1, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('food_pantry',      1, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('gadget',           0, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('home_goods',       0, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('gift_misc',        0, '["form","adult"]', '2026-10-07T00:00:00Z');

-- Text swap LAST (061's order note: ordinary DDL first, the stored-text edit
-- after it, so no ADD COLUMN splices against a stale parse).
PRAGMA writable_schema = ON;
UPDATE sqlite_master
   SET sql = replace(sql,
        '(''place'',''person'',''method'',''food'',''exercise'')',
        '(''place'',''person'',''method'',''food'',''exercise'',''item'',''category'')')
 WHERE type = 'table' AND name = 'near_match_refusal'
   AND instr(sql, '''item'',''category''') = 0;
PRAGMA writable_schema = OFF;

CREATE TEMP TABLE _guard_071_post(n);
CREATE TEMP TRIGGER _guard_071_post_refuse BEFORE INSERT ON _guard_071_post BEGIN
  SELECT RAISE(FAIL, 'migration 071 refused: near_match_refusal does not carry the widened domain CHECK after the swap -- rolled back, nothing changed');
END;
INSERT INTO _guard_071_post
  SELECT 1 FROM sqlite_master
   WHERE type = 'table' AND name = 'near_match_refusal'
     AND instr(sql, '(''place'',''person'',''method'',''food'',''exercise'',''item'',''category'')') = 0;
DROP TABLE temp._guard_071_post;

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 71;
COMMIT;
