-- 059_intake.sql  (target user_version 59)
--
-- reversible: YES, losslessly for every row that existed before intake is
--   used.  Rollback = DROP TRIGGER intake_measure_entity,
--   intake_measure_entity_upd, intake_block_delete, intake_block_edited,
--   intake_delta_edited, intake_delta_added, intake_delta_removed,
--   spend_entry_id_floor_track, spend_entry_id_floor, spend_id_floor_monotonic,
--   spend_id_floor_kept; DROP INDEX ix_block_delta_amount, ix_intake_link_block,
--   ux_intake_reversal; DROP TABLE intake_link, intake_item, intake_match_rule,
--   intake_source; DELETE FROM ledger_guard WHERE guard_key IN
--   ('spend_id_floor','import_auto_accept'); PRAGMA user_version = 58.
--   (dup_window stays normalized: 1 day == the prior NULL intent.)
--
-- ADDITIVE.  No column of any existing table changes and no existing row
-- moves, with one exception: the stock UNSET dup_window guard row
-- (value NULL, unit not 'day') is normalized to 1 day.  Existing tables gain
-- triggers (block, block_delta, spend_entry, ledger_guard) and one index
-- (block_delta(resource, amount)); ledger_guard gains two seed rows.
--
-- ==========================================================================
-- WHAT. The kit-owned cross-resource intake layer (ported from
-- dsp/kit-2280-retroline, where it was 055_intake; renumbered after main's
-- 058 and re-guarded in the 057 style).  Packs register channels
-- (intake_source) and match policy (intake_match_rule) as DATA; evidence
-- arriving from a channel (intake_item, idempotent by UNIQUE(source,
-- source_ref)) waits as pending until it lands in the existing resource model
-- (a block with a money delta, or an entity-scoped measure) through
-- intake_link.  Verb bodies: database/intake.py.  Contract: INTAKE.md.
--
-- RELATION TO 057 ingest_ref.  Different layers, no FK between them.
-- ingest_ref records that an EXTERNAL EVENT (transporter source/kind/ext_id)
-- was consumed, under user consent, no backfill.  intake_item is the
-- evidence/review queue for a RESOURCE landing (pending -> confirmed/excluded
-- with candidates).  A module that reads a transporter kind into intake may
-- write both: ingest-put for the event, intake-add for the evidence
-- (source_ref = the receiver event id).  See INTAKE.md.
--
-- spend_id_floor: spend_entry has no AUTOINCREMENT and cannot gain it without
-- a rebuild, so a monotonic, undeletable ledger_guard floor records the
-- highest id ever deleted; spend_add allocates above it and an insert at or
-- below it aborts (archived ids and their x-spend-N shadows are never reused).
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_059(seen_user_version);
CREATE TEMP TRIGGER _guard_059_refuse BEFORE INSERT ON _guard_059 BEGIN
  SELECT RAISE(FAIL, 'migration 059 refused: this database is not at user_version 58 -- either 059 was already applied (>= 59) or the chain up to 058 has not run (< 58). Nothing changed');
END;
INSERT INTO _guard_059
  SELECT user_version FROM pragma_user_version WHERE user_version <> 58;
DROP TABLE temp._guard_059;

PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;

-- Intake is evidence awaiting a landing in the existing resource model.
CREATE TABLE intake_source (
    key TEXT PRIMARY KEY,
    resource TEXT NOT NULL REFERENCES resource(token),
    owning_module TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    last_read_at TEXT,
    value_origin TEXT NOT NULL REFERENCES provenance(token)
);
CREATE TABLE intake_match_rule (
    resource TEXT PRIMARY KEY REFERENCES resource(token),
    owning_module TEXT NOT NULL,
    rule_json TEXT NOT NULL CHECK(json_valid(rule_json)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);
CREATE TABLE intake_item (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL REFERENCES intake_source(key),
    source_ref TEXT NOT NULL CHECK(source_ref <> ''),
    target TEXT NOT NULL CHECK(target IN ('block','measure')),
    raw_payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    data_json TEXT NOT NULL CHECK(json_valid(data_json)),
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','confirmed','excluded')),
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(source, source_ref),
    UNIQUE(id, target)
);
CREATE TABLE intake_link (
    item_id INTEGER PRIMARY KEY,
    target TEXT NOT NULL CHECK(target IN ('block','measure')),
    block_id INTEGER REFERENCES block(id),
    measure_id INTEGER REFERENCES measure(id),
    reversal_of INTEGER REFERENCES block(id),
    FOREIGN KEY(item_id, target) REFERENCES intake_item(id, target),
    CHECK((target='block' AND block_id IS NOT NULL AND measure_id IS NULL)
       OR (target='measure' AND measure_id IS NOT NULL AND block_id IS NULL)),
    CHECK(reversal_of IS NULL OR (target='block' AND reversal_of <> block_id))
);
CREATE INDEX ix_intake_link_block ON intake_link(block_id);
CREATE UNIQUE INDEX ux_intake_reversal ON intake_link(reversal_of) WHERE reversal_of IS NOT NULL;
CREATE TRIGGER intake_measure_entity BEFORE INSERT ON intake_link
WHEN NEW.target='measure' AND NOT EXISTS(SELECT 1 FROM measure WHERE id=NEW.measure_id AND entity_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'state intake requires entity-scoped measure'); END;
CREATE TRIGGER intake_measure_entity_upd BEFORE UPDATE ON intake_link
WHEN NEW.target='measure' AND NOT EXISTS(SELECT 1 FROM measure WHERE id=NEW.measure_id AND entity_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'state intake requires entity-scoped measure'); END;
-- Deleting a landing block never dangles or blocks: every delete path
-- (spend-del, experience-delete-source, calendar _tb_delete, reconcilers)
-- goes through this trigger, so no helper can forget intake. The evidence
-- stays, excluded; a reversal whose original vanished returns to review.
CREATE TRIGGER intake_block_delete BEFORE DELETE ON block
WHEN EXISTS(SELECT 1 FROM intake_link WHERE block_id=OLD.id OR reversal_of=OLD.id)
BEGIN
  UPDATE intake_item SET state='excluded', reason='target_deleted'
   WHERE id IN (SELECT item_id FROM intake_link WHERE block_id=OLD.id);
  DELETE FROM intake_link WHERE block_id=OLD.id;
  UPDATE intake_item SET state='pending', reason='reversal_target_deleted'
   WHERE id IN (SELECT item_id FROM intake_link WHERE reversal_of=OLD.id);
  UPDATE intake_link SET reversal_of=NULL WHERE reversal_of=OLD.id;
END;
-- Editing a landing block keeps the link but records the divergence.
CREATE TRIGGER intake_block_edited AFTER UPDATE OF date, title ON block
WHEN OLD.date IS NOT NEW.date OR OLD.title IS NOT NEW.title
BEGIN
  UPDATE intake_item SET reason='target_edited' WHERE state='confirmed'
   AND id IN (SELECT item_id FROM intake_link WHERE block_id=NEW.id OR reversal_of=NEW.id);
END;
CREATE TRIGGER intake_delta_edited AFTER UPDATE OF amount, entity_id, unit, resource ON block_delta
WHEN 'money' IN (OLD.resource, NEW.resource) AND (OLD.amount IS NOT NEW.amount
  OR OLD.entity_id IS NOT NEW.entity_id OR OLD.unit IS NOT NEW.unit OR OLD.resource IS NOT NEW.resource)
BEGIN
  UPDATE intake_item SET reason='target_edited' WHERE state='confirmed'
   AND id IN (SELECT item_id FROM intake_link WHERE block_id=NEW.block_id OR reversal_of=NEW.block_id);
END;
CREATE TRIGGER intake_delta_added AFTER INSERT ON block_delta WHEN NEW.resource='money'
BEGIN
  UPDATE intake_item SET reason='target_edited' WHERE state='confirmed'
   AND id IN (SELECT item_id FROM intake_link WHERE block_id=NEW.block_id OR reversal_of=NEW.block_id);
END;
CREATE TRIGGER intake_delta_removed AFTER DELETE ON block_delta WHEN OLD.resource='money'
BEGIN
  UPDATE intake_item SET reason='target_edited' WHERE state='confirmed'
   AND id IN (SELECT item_id FROM intake_link WHERE block_id=OLD.block_id OR reversal_of=OLD.block_id);
END;
-- Candidate lookup is date range + signed amount, never a full scan.
CREATE INDEX ix_block_delta_amount ON block_delta(resource, amount);
-- spend_entry has no AUTOINCREMENT and cannot gain it without a rebuild, and
-- sqlite_sequence is ignored for such tables. A floor row records the highest
-- id ever deleted; an insert at or below it aborts, so archived/deleted ids
-- (and their x-spend-N shadows and dumps) are never reused. spend_add
-- allocates above the floor explicitly.
INSERT OR IGNORE INTO ledger_guard(guard_key,scope,value,unit,enforcement,inviolable,source)
VALUES('spend_id_floor','global',0,'id','block',1,'seed');
CREATE TRIGGER spend_entry_id_floor_track AFTER DELETE ON spend_entry
BEGIN
  UPDATE ledger_guard SET value=MAX(COALESCE(value,0), OLD.id)
   WHERE guard_key='spend_id_floor' AND scope='global';
END;
CREATE TRIGGER spend_entry_id_floor AFTER INSERT ON spend_entry
WHEN NEW.id <= COALESCE((SELECT value FROM ledger_guard WHERE guard_key='spend_id_floor' AND scope='global'), 0)
BEGIN SELECT RAISE(ABORT, 'spend_entry id at or below spend_id_floor; ids are never reused'); END;
CREATE TRIGGER spend_id_floor_monotonic BEFORE UPDATE ON ledger_guard
WHEN OLD.guard_key='spend_id_floor' AND OLD.scope='global'
 AND (NEW.guard_key IS NOT OLD.guard_key OR NEW.scope IS NOT OLD.scope OR NEW.value IS NULL OR NEW.value < OLD.value)
BEGIN SELECT RAISE(ABORT, 'spend_id_floor only rises'); END;
CREATE TRIGGER spend_id_floor_kept BEFORE DELETE ON ledger_guard
WHEN OLD.guard_key='spend_id_floor' AND OLD.scope='global'
BEGIN SELECT RAISE(ABORT, 'spend_id_floor cannot be removed'); END;
INSERT OR IGNORE INTO ledger_guard(guard_key,scope,value,unit,enforcement,source)
VALUES('import_auto_accept','global',0,'flag','block','seed');
-- Only the stock unset dup_window (NULL, non-day unit) is normalized. A
-- configured non-day value is left as found; intake refuses loudly on it.
UPDATE ledger_guard SET value=1,unit='day',updated_at=datetime('now','localtime')
 WHERE guard_key='dup_window' AND scope='global' AND value IS NULL AND unit IS NOT 'day';
INSERT OR IGNORE INTO ledger_guard(guard_key,scope,value,unit,enforcement,source)
VALUES('dup_window','global',1,'day','hold','seed');

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 59;
COMMIT;
