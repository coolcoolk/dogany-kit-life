-- 075_ledger_v6.sql  (target user_version 75)
--
-- reversible: YES.  Rollback = DROP TABLE spend_link; DROP TABLE
--   spend_link_rule; DROP TABLE fixed_cost_key; DROP TABLE pay_funding;
--   DROP VIEW v_spend_link; DROP INDEX ux_spend_import_key / ix_spend_pair /
--   ix_spend_stream; then ALTER TABLE ... DROP COLUMN for every column named
--   below (spend_entry 6, pay_method 4, fixed_cost 11); PRAGMA user_version
--   = 74.  Additive: four new tables, one view, three indexes, 21 NULLable
--   or defaulted columns, four triggers.  No existing row is read or
--   rewritten; every existing row reads the new columns NULL / default =
--   exactly the pre-075 meaning (class NULL = a plain spend).  No config row
--   is written (051 convention).
--
-- ==========================================================================
-- WHAT.  The data model of the ledger redesign v6 (owner 2026-10-12 first
-- live run; the maintainer worklog 2026-10-12-ledger-redesign-v6.md, decisions D1-D5
-- + "spend links to MODULES", owner 17:10-17:40).  One migration, five gaps
-- closed:
--
--   spend_entry.class      what KIND of money event is this row?  NULL =
--                     legacy = 'spend'.  spend | income | saving (money put
--                     into savings/securities: the "모은 돈" line inside 나간
--                     돈, D3) | saving_back (pulled back out: "모아둔 데서
--                     꺼낸 돈", never income, D3) | loan | own_transfer (own
--                     account -> own account: a move, D2) | topup (own
--                     account -> own easy-pay balance) | card_bill (account
--                     -> own card issuer) | pass_through (corporate card
--                     settled the same day, in and out) | meal_point (company
--                     meal points, D5: only the part above counts).
--                     Money out for the diagnosis = spend + saving + loan;
--                     own_transfer / topup / card_bill / pass_through /
--                     meal_point / saving_back are never income or expense.
--                     Rows of those classes carry kind='transfer' and NO
--                     money shadow (block + delta): every pre-075 reader
--                     filters kind IN ('expense','income'), so the transfer
--                     rows are invisible to them by construction (risk 2.7).
--   spend_entry.pair_id    two rows that are ONE money event (a transfer's two
--                     legs, a wallet row + its funding-source row, an inflow
--                     and its same-day card settlement).
--   spend_entry.import_key the normalized identity of an imported payment
--                     (date|time|signed amount|merchant|method, + the
--                     occurrence count).  UNIQUE per source; a re-export, or
--                     an overlapping export, never lands a row twice.
--   spend_entry.stream_id  the recurring stream (fixed_cost row) this
--                     occurrence belongs to.
--   spend_entry.at_time / src_cat   the export's HH:MM and its own
--                     "대분류/소분류" -- evidence the pairing and the
--                     category map read; never shown to the owner.
--
--   pay_method.layer / canonical_id / scope_default / dust   three layers in
--                     one table (account | card | wallet = easy-pay); a
--                     bank-text variant is an ALIAS of its canonical method
--                     (canonical_id), not a method; business accounts default
--                     personal; dust (a 5-won piggy bank, a dormant account)
--                     is never rendered.
--   pay_funding       N:M: which sources an easy-pay wallet draws from
--                     (account / card / own balance / points / employer meal
--                     points), with the default and first/last seen.  Top-ups
--                     are transfers; the wallet row carries the real
--                     merchant, the funding row becomes own_transfer.
--
--   fixed_cost.*      a recurring STREAM, on the existing table: class,
--                     current_amount (the LAST month, never a window mean),
--                     last_seen, expected_next, state (active | late |
--                     changed | stop_suspected | stopped), changed_from /
--                     changed_at, keep_class (must | reducible | droppable =
--                     포기 못 함 / 줄일 수 있음 / 포기 가능), keep_by (engine
--                     proposes, owner confirms), stopped_at / stopped_by.
--                     stopped_by accepts ONLY 'owner': a stream leaves a total
--                     on the owner's word and on nothing else (the CHECK
--                     ties state='stopped' to it).
--   fixed_cost_key    every merchant key that feeds a stream (one bill, many
--                     bank-text names: 원리금 / IBK 대출상환 / 집 상환금).
--
--   spend_link / spend_link_rule   spend <-> MODULE entity (owner 17:10).
--                     ONE link table keyed (spend, entity kind, entity ref):
--                     person (relationship), venue / workout (health: gym
--                     fee, PT), meal / supplement (diet), gift (relationship
--                     / shopping), shop_item / owned_item (shopping).  The
--                     row stays the owner's spend (amount, scope unchanged);
--                     the link adds the domain axis, so every module reads
--                     "money spent on my domain" (v_spend_link).  Standing
--                     rules (spend_link_rule.match_json) apply to past and
--                     future rows; a rule-made link is source='rule', an
--                     owner-said link source='owner'.
--
-- WHY A CLASS COLUMN, NOT A SECOND TABLE.  The pre-075 readers (spend-week,
-- spend-month, the retro, the weekly) already read spend_entry and already
-- filter kind; a transfer typed kind='transfer' vanishes from them with no
-- change to any of them, and the v6 verbs read class.  A second table would
-- make every reader choose which clock to believe (036/039).
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_075(seen_user_version);
CREATE TEMP TRIGGER _guard_075_refuse BEFORE INSERT ON _guard_075 BEGIN
  SELECT RAISE(FAIL, 'migration 075 refused: this database is not at user_version 74 -- either 075 was already applied (>= 75) or the chain up to 074 has not run (< 74). Nothing changed');
END;
INSERT INTO _guard_075
  SELECT user_version FROM pragma_user_version WHERE user_version <> 74;
DROP TABLE temp._guard_075;

BEGIN IMMEDIATE;

-- ---- spend_entry ---------------------------------------------------------
ALTER TABLE spend_entry ADD COLUMN class TEXT
    CHECK (class IS NULL OR class IN ('spend','income','saving','saving_back',
           'loan','own_transfer','topup','card_bill','pass_through',
           'meal_point'));
ALTER TABLE spend_entry ADD COLUMN pair_id INTEGER;
ALTER TABLE spend_entry ADD COLUMN import_key TEXT
    CHECK (import_key IS NULL OR TRIM(import_key) <> '');
ALTER TABLE spend_entry ADD COLUMN stream_id INTEGER;
ALTER TABLE spend_entry ADD COLUMN at_time TEXT
    CHECK (at_time IS NULL OR at_time GLOB '[0-2][0-9]:[0-5][0-9]');
ALTER TABLE spend_entry ADD COLUMN src_cat TEXT;
CREATE UNIQUE INDEX ux_spend_import_key ON spend_entry(source, import_key)
  WHERE import_key IS NOT NULL;
CREATE INDEX ix_spend_pair ON spend_entry(pair_id) WHERE pair_id IS NOT NULL;
CREATE INDEX ix_spend_stream ON spend_entry(stream_id)
  WHERE stream_id IS NOT NULL;

-- ---- pay_method + pay_funding -------------------------------------------
ALTER TABLE pay_method ADD COLUMN layer TEXT
    CHECK (layer IS NULL OR layer IN ('account','card','wallet'));
ALTER TABLE pay_method ADD COLUMN canonical_id INTEGER;
ALTER TABLE pay_method ADD COLUMN scope_default TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope_default IN ('personal','business'));
ALTER TABLE pay_method ADD COLUMN dust INTEGER NOT NULL DEFAULT 0
    CHECK (dust IN (0,1));

CREATE TABLE pay_funding (
  wallet_id  INTEGER NOT NULL REFERENCES pay_method(id),
  source_id  INTEGER NOT NULL REFERENCES pay_method(id),
  sub        TEXT NOT NULL CHECK (sub IN ('balance','account','card','points','meal')),
  is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0,1)),
  first_seen TEXT,
  last_seen  TEXT,
  PRIMARY KEY (wallet_id, source_id, sub),
  CHECK (wallet_id <> source_id)
);
CREATE UNIQUE INDEX ux_pay_funding_default ON pay_funding(wallet_id)
  WHERE is_default = 1;

-- ---- fixed_cost as a stream ---------------------------------------------
ALTER TABLE fixed_cost ADD COLUMN class TEXT
    CHECK (class IS NULL OR class IN ('income','housing','loan','insurance',
           'utility','telecom','subscription','saving','tax','other'));
ALTER TABLE fixed_cost ADD COLUMN current_amount INTEGER;
ALTER TABLE fixed_cost ADD COLUMN last_seen TEXT;
ALTER TABLE fixed_cost ADD COLUMN expected_next TEXT;
ALTER TABLE fixed_cost ADD COLUMN state TEXT NOT NULL DEFAULT 'active'
    CHECK (state IN ('active','late','changed','stop_suspected','stopped'));
ALTER TABLE fixed_cost ADD COLUMN changed_from INTEGER;
ALTER TABLE fixed_cost ADD COLUMN changed_at TEXT;
ALTER TABLE fixed_cost ADD COLUMN keep_class TEXT
    CHECK (keep_class IS NULL OR keep_class IN ('must','reducible','droppable'));
ALTER TABLE fixed_cost ADD COLUMN keep_by TEXT
    CHECK (keep_by IS NULL OR keep_by IN ('engine','owner'));
ALTER TABLE fixed_cost ADD COLUMN stopped_at TEXT;
ALTER TABLE fixed_cost ADD COLUMN stopped_by TEXT
    CHECK ((stopped_by IS NULL OR stopped_by = 'owner')
           AND ((stopped_by IS NOT NULL) = (state = 'stopped')));

CREATE TABLE fixed_cost_key (
  fixed_id INTEGER NOT NULL REFERENCES fixed_cost(id) ON DELETE CASCADE,
  key      TEXT NOT NULL CHECK (TRIM(key) <> ''),
  UNIQUE (key)
);
CREATE INDEX ix_fixed_cost_key_fixed ON fixed_cost_key(fixed_id);
CREATE TRIGGER trg_fixed_cost_gone AFTER DELETE ON fixed_cost
BEGIN
  DELETE FROM fixed_cost_key WHERE fixed_id = OLD.id;
  UPDATE spend_entry SET stream_id = NULL WHERE stream_id = OLD.id;
END;

-- ---- spend <-> module entity ---------------------------------------------
CREATE TABLE spend_link_rule (
  id          INTEGER PRIMARY KEY,
  entity_kind TEXT NOT NULL CHECK (entity_kind IN ('person','venue','workout',
              'meal','supplement','gift','shop_item','owned_item')),
  entity_ref  TEXT NOT NULL CHECK (TRIM(entity_ref) <> ''),
  role        TEXT NOT NULL DEFAULT 'for' CHECK (role IN ('for','with')),
  match_json  TEXT NOT NULL CHECK (json_valid(match_json)
              AND json_type(match_json) = 'object'),
  said        TEXT,
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE spend_link (
  spend_id    INTEGER NOT NULL,
  entity_kind TEXT NOT NULL CHECK (entity_kind IN ('person','venue','workout',
              'meal','supplement','gift','shop_item','owned_item')),
  entity_ref  TEXT NOT NULL CHECK (TRIM(entity_ref) <> ''),
  role        TEXT NOT NULL DEFAULT 'for' CHECK (role IN ('for','with')),
  source      TEXT NOT NULL CHECK (source IN ('owner','rule')),
  rule_id     INTEGER REFERENCES spend_link_rule(id),
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (spend_id, entity_kind, entity_ref),
  CHECK ((source = 'rule') = (rule_id IS NOT NULL))
);
CREATE INDEX ix_spend_link_entity ON spend_link(entity_kind, entity_ref);
-- a spend row that goes takes its links (foreign_keys is off on pack writers)
CREATE TRIGGER trg_spend_link_spend_gone AFTER DELETE ON spend_entry
BEGIN DELETE FROM spend_link WHERE spend_id = OLD.id; END;
-- a person that goes takes the links and standing rules that named them
CREATE TRIGGER trg_spend_link_person_gone AFTER DELETE ON persons
BEGIN
  DELETE FROM spend_link WHERE entity_kind = 'person'
     AND entity_ref = CAST(OLD.id AS TEXT);
  DELETE FROM spend_link_rule WHERE entity_kind = 'person'
     AND entity_ref = CAST(OLD.id AS TEXT);
END;

-- what each module reads: money spent on its domain.  Counts spend-class
-- money only (an owner's spend, whatever the link), never a transfer.
CREATE VIEW v_spend_link AS
SELECT l.entity_kind, l.entity_ref, l.role, l.source AS link_source,
       s.id AS spend_id, s.date, s.amount, s.name, s.method, s.scope,
       COALESCE(s.class, 'spend') AS class
  FROM spend_link l JOIN spend_entry s ON s.id = l.spend_id
 WHERE s.kind = 'expense';

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 75;
COMMIT;
