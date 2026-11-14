-- 072_shop_log.sql  (target user_version 72)
--
-- reversible: YES.  Rollback = DROP TRIGGER trg_spend_entry_owned_price;
--   DROP TABLE owned_item_measure; DROP TABLE owned_item; DROP TABLE
--   taste_mark; ALTER TABLE shop_category DROP COLUMN spend_category_id
--   (legal: nothing else names it); DELETE FROM metric_def WHERE token IN
--   (the seven below) AND origin = 'module' AND NOT EXISTS a metric_log row
--   for it; DELETE FROM unit WHERE token IN ('cm','mm') when unreferenced;
--   PRAGMA user_version = 71.  Additive: three new tables, one NULLable
--   column, one trigger, seven vocabulary rows (OR IGNORE), two unit rows
--   (OR IGNORE).  No config row is written (051 convention: shop.py reads
--   shop_ledger_link / shop_cooling_days / shop_cooloff_at / shop_region with
--   code defaults).
--
-- ==========================================================================
-- WHAT.  module_shopping phase 2 -- purchase log, taste memory, garment
-- measurements (the maintainer worklog 2026-10-07-module-shopping-SPEC, build notes P2;
-- owner decisions 12:01-12:07 and the dated 14:03 / 14:25 sections supersede
-- the recommendations).
--
--   taste_mark          what has the owner SAID they like / avoid in a kind
--                       of thing (or confirmed as a tendency)?  Per category,
--                       never global.  axis is a closed token list (the skill
--                       never invents an axis); value = the owner's words.
--                       At most ONE active row per (category, axis, value,
--                       polarity): the partial unique index is the find-first
--                       target of shop-taste-add.  Retire, never overwrite.
--   owned_item          what did the owner buy, and how did it go?  Where
--                       (store name and/or the page link), when, which hunt
--                       it came from, the size label worn, satisfaction / fit
--                       after use, whether they still have it.
--                       PRICE HAS EXACTLY ONE HOME (owner decision 2,
--                       12:05): ledger link on -> the linked spend_entry row
--                       (spend_id, read through the FK, never copied); ledger
--                       off -> owned_item.price.  The table CHECK refuses a
--                       row that carries both.
--   owned_item_measure  the garment's own measurements (KR shops list 실측:
--                       total length, shoulder, chest/waist/hip/thigh/hem
--                       half-width, rise, sleeve), one row per part, cm.  The
--                       primary size predictor (14:03): a candidate's chart is
--                       compared with the owner's best-fitting owned garment
--                       of the same kind first, then body measures, then the
--                       height/weight band.
--   shop_category.spend_category_id   which ledger bucket a purchase of
--                       this kind goes to (S-L2); NULL = ask once at the
--                       first linked purchase, then remembered.
--
-- BODY measurements (chest, waist, hip, shoulder width, inseam, arm length,
-- foot length) are NOT shopping data (14:03 "two different facts, two
-- homes"): they are kit-shared body facts, dated readings in metric_log like
-- height / weight, read by body-brief and by the size draft.  metric_log has
-- no FK, so what this file adds is their VOCABULARY: seven metric_def rows
-- (scope entity, accrual observed, owner_module health -- body_fat_pct's
-- shape) and the two length units they are measured in.  Every token is
-- INSERT OR IGNORE: a row an install already registered (metric-register)
-- stays as it is.  Before this file no girth / length token existed in
-- metric_def (checked 2026-10-07 against schema.sql and 036..071).
--
-- trg_spend_entry_owned_price keeps decision 2 true when the money row goes
-- away: deleting a spend_entry an owned_item points at moves the amount onto
-- owned_item.price and unlinks it in the same statement, so the price of a
-- purchase is never silently lost and never stored twice.  A BEFORE trigger
-- (not the FK's SET NULL alone): pack writers and readers open connections
-- with foreign_keys off, where the FK action never runs.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_072(seen_user_version);
CREATE TEMP TRIGGER _guard_072_refuse BEFORE INSERT ON _guard_072 BEGIN
  SELECT RAISE(FAIL, 'migration 072 refused: this database is not at user_version 71 -- either 072 was already applied (>= 72) or the chain up to 071 has not run (< 71). Nothing changed');
END;
INSERT INTO _guard_072
  SELECT user_version FROM pragma_user_version WHERE user_version <> 71;
DROP TABLE temp._guard_072;

BEGIN IMMEDIATE;

ALTER TABLE shop_category ADD COLUMN spend_category_id INTEGER REFERENCES spend_category(id);

CREATE TABLE taste_mark (
  id          INTEGER PRIMARY KEY,
  ulid        TEXT NOT NULL UNIQUE,
  category_id INTEGER NOT NULL REFERENCES shop_category(id),
  axis        TEXT NOT NULL CHECK (axis IN ('colour','fit','material','brand',
                                            'style','scent','price_band','size')),
  value       TEXT NOT NULL CHECK (TRIM(value) <> ''),
  polarity    TEXT NOT NULL CHECK (polarity IN ('like','avoid')),
  origin      TEXT NOT NULL DEFAULT 'stated'
              CHECK (origin IN ('stated','confirmed_tendency')),
  source_ref  TEXT CHECK (source_ref IS NULL OR TRIM(source_ref) <> ''),
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
  recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  retired_at  TEXT,
  CHECK ((status = 'retired') = (retired_at IS NOT NULL))
);
CREATE UNIQUE INDEX ux_taste_mark_active ON taste_mark(category_id, axis,
  value, polarity) WHERE status = 'active';

CREATE TABLE owned_item (
  id                    INTEGER PRIMARY KEY,
  ulid                  TEXT NOT NULL UNIQUE,
  item_id               INTEGER NOT NULL REFERENCES shop_item(id),
  bought_on             TEXT NOT NULL
                        CHECK (bought_on GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  store                 TEXT CHECK (store IS NULL OR TRIM(store) <> ''),
  link                  TEXT CHECK (link IS NULL OR TRIM(link) <> ''),
  hunt_id               INTEGER REFERENCES shop_hunt(id),
  spend_id              INTEGER REFERENCES spend_entry(id) ON DELETE SET NULL,
  price                 INTEGER CHECK (price IS NULL OR price >= 0),
  size_label            TEXT CHECK (size_label IS NULL OR TRIM(size_label) <> ''),
  satisfaction          TEXT CHECK (satisfaction IS NULL OR satisfaction IN
                                    ('liked','neutral','disliked')),
  satisfaction_verbatim TEXT,
  fit                   TEXT CHECK (fit IS NULL OR fit IN ('small','right','large')),
  status                TEXT NOT NULL DEFAULT 'in_use'
                        CHECK (status IN ('in_use','used_up','returned','gone')),
  status_on             TEXT CHECK (status_on IS NULL OR status_on GLOB
                                    '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (spend_id IS NULL OR price IS NULL),
  CHECK ((status = 'in_use') = (status_on IS NULL))
);
CREATE INDEX ix_owned_item_item ON owned_item(item_id, bought_on);
CREATE INDEX ix_owned_item_spend ON owned_item(spend_id) WHERE spend_id IS NOT NULL;

CREATE TABLE owned_item_measure (
  id            INTEGER PRIMARY KEY,
  owned_item_id INTEGER NOT NULL REFERENCES owned_item(id) ON DELETE CASCADE,
  part          TEXT NOT NULL CHECK (part IN ('total_length','shoulder','chest_half',
                                              'waist_half','hip_half','thigh_half',
                                              'rise','hem_half','sleeve')),
  value_cm      REAL NOT NULL CHECK (value_cm > 0 AND value_cm < 300),
  recorded_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  UNIQUE (owned_item_id, part)
);

CREATE TRIGGER trg_spend_entry_owned_price
BEFORE DELETE ON spend_entry
WHEN EXISTS (SELECT 1 FROM owned_item WHERE spend_id = OLD.id)
BEGIN
  UPDATE owned_item
     SET spend_id = NULL,
         price = CASE WHEN OLD.amount >= 0 THEN OLD.amount ELSE NULL END
   WHERE spend_id = OLD.id;
END;

INSERT OR IGNORE INTO unit(token, dimension, origin) VALUES
  ('cm', 'length', 'module'),
  ('mm', 'length', 'module');

INSERT OR IGNORE INTO metric_def(token, scope_kind, accrual, unit_token,
                                 owner_module, origin, status) VALUES
  ('chest_cm',          'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('waist_cm',          'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('hip_cm',            'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('shoulder_width_cm', 'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('inseam_cm',         'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('arm_length_cm',     'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('foot_length_mm',    'entity', 'observed', 'mm', 'health', 'module', 'active');

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 72;
COMMIT;
