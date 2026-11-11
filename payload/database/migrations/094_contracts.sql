-- reversible: additive records only; restore the pre-094 snapshot to reverse.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_094(v);
CREATE TEMP TRIGGER _guard_094_refuse BEFORE INSERT ON _guard_094 BEGIN
 SELECT RAISE(FAIL, 'migration 094 requires user_version 93');
END;
INSERT INTO _guard_094 SELECT user_version FROM pragma_user_version WHERE user_version<>93;
DROP TABLE temp._guard_094;
BEGIN IMMEDIATE;
-- Contract terms survive financial stream resets and merges.
CREATE TABLE contract_record (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner TEXT NOT NULL CHECK(trim(owner)<>''),
 record_key TEXT NOT NULL CHECK(trim(record_key)<>''),
 kind TEXT NOT NULL DEFAULT 'contract',
 title TEXT NOT NULL CHECK(trim(title)<>''),
 provider TEXT,
 facts TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(facts)),
 sources TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(sources)),
 fixed_id INTEGER REFERENCES fixed_cost(id),
 owned_id INTEGER REFERENCES owned_item(id),
 revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 UNIQUE(owner,record_key)
);
CREATE TABLE contract_evidence (
 owner TEXT NOT NULL,
 source_ref TEXT NOT NULL,
 source_slot TEXT NOT NULL DEFAULT '0',
 contract_id INTEGER NOT NULL REFERENCES contract_record(id),
 words TEXT,
 facts TEXT NOT NULL CHECK(json_valid(facts)),
 PRIMARY KEY(owner,source_ref,source_slot)
);
CREATE TABLE contract_proof (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 contract_id INTEGER NOT NULL REFERENCES contract_record(id),
 digest TEXT NOT NULL,
 ref TEXT NOT NULL,
 label TEXT NOT NULL,
 UNIQUE(contract_id,digest)
);
CREATE TABLE contract_reminder (
 contract_id INTEGER PRIMARY KEY REFERENCES contract_record(id),
 block_id INTEGER UNIQUE REFERENCES block(id),
 signature TEXT NOT NULL,
 event TEXT NOT NULL,
 due_at TEXT NOT NULL
);
CREATE INDEX idx_contract_owned ON contract_record(owned_id);
CREATE INDEX idx_contract_fixed ON contract_record(fixed_id);
CREATE TRIGGER trg_contract_fixed_delete BEFORE DELETE ON fixed_cost BEGIN
 UPDATE contract_record SET fixed_id=NULL WHERE fixed_id=OLD.id;
END;
CREATE TRIGGER trg_contract_owned_delete BEFORE DELETE ON owned_item BEGIN
 UPDATE contract_record SET owned_id=NULL,facts=json_set(facts,'$.status','disposed'),revision=revision+1 WHERE owned_id=OLD.id;
END;
CREATE TRIGGER trg_contract_block_delete BEFORE DELETE ON block BEGIN
 UPDATE contract_reminder SET block_id=NULL WHERE block_id=OLD.id;
END;
CREATE TRIGGER trg_contract_links_insert BEFORE INSERT ON contract_record BEGIN
 SELECT RAISE(ABORT,'contract stream missing') WHERE NEW.fixed_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM fixed_cost WHERE id=NEW.fixed_id);
 SELECT RAISE(ABORT,'contract possession missing') WHERE NEW.owned_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM owned_item WHERE id=NEW.owned_id);
END;
CREATE TRIGGER trg_contract_links_update BEFORE UPDATE OF fixed_id,owned_id ON contract_record BEGIN
 SELECT RAISE(ABORT,'contract stream missing') WHERE NEW.fixed_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM fixed_cost WHERE id=NEW.fixed_id);
 SELECT RAISE(ABORT,'contract possession missing') WHERE NEW.owned_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM owned_item WHERE id=NEW.owned_id);
END;
CREATE TRIGGER trg_contract_evidence_insert BEFORE INSERT ON contract_evidence BEGIN
 SELECT RAISE(ABORT,'contract owner mismatch') WHERE NOT EXISTS
 (SELECT 1 FROM contract_record WHERE id=NEW.contract_id AND owner=NEW.owner);
END;
CREATE TRIGGER trg_contract_reminder_insert BEFORE INSERT ON contract_reminder BEGIN
 SELECT RAISE(ABORT,'contract task mismatch') WHERE NOT EXISTS
 (SELECT 1 FROM contract_record c JOIN timeblock t ON t.id=NEW.block_id
  WHERE c.id=NEW.contract_id AND t.kind='task' AND t.owning_agent=c.owner);
END;
CREATE TRIGGER trg_contract_proof_insert BEFORE INSERT ON contract_proof BEGIN
 SELECT RAISE(ABORT,'contract missing') WHERE NOT EXISTS
 (SELECT 1 FROM contract_record WHERE id=NEW.contract_id);
END;
CREATE TRIGGER trg_contract_delete BEFORE DELETE ON contract_record BEGIN
 SELECT RAISE(ABORT,'retire contracts through contract capture; preserve evidence');
END;

PRAGMA user_version=94;
COMMIT;
