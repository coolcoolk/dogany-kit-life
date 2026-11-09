-- reversible: additive settlement tables/views and persons.ledger_no_split; restore pre-086 DB backup to reverse.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_086(v);
CREATE TEMP TRIGGER _guard_086_refuse BEFORE INSERT ON _guard_086 BEGIN
 SELECT RAISE(FAIL, 'migration 086 requires user_version 85');
END;
INSERT INTO _guard_086 SELECT user_version FROM pragma_user_version WHERE user_version <> 85;
DROP TABLE temp._guard_086;
BEGIN IMMEDIATE;
-- 086: owner-picked appointment settlement, additive / no personal seeds.
ALTER TABLE persons ADD COLUMN ledger_no_split INTEGER NOT NULL DEFAULT 0 CHECK (ledger_no_split IN (0,1));
CREATE TABLE ledger_split (
 spend_id INTEGER PRIMARY KEY REFERENCES spend_entry(id),
 appointment_id INTEGER NOT NULL REFERENCES block(id),
 state TEXT NOT NULL DEFAULT 'ask' CHECK(state IN ('ask','how','remainder','amounts','declined','skip','split')),
 owner_share INTEGER CHECK(owner_share >= 0),
 amounts_json TEXT CHECK(amounts_json IS NULL OR json_valid(amounts_json))
);
CREATE TABLE ledger_share (
 id INTEGER PRIMARY KEY,
 spend_id INTEGER NOT NULL REFERENCES ledger_split(spend_id),
 person_id INTEGER NOT NULL REFERENCES persons(id),
 amount INTEGER NOT NULL CHECK(amount >= 0),
 received INTEGER NOT NULL DEFAULT 0 CHECK(received >= 0 AND received <= amount),
 closed INTEGER NOT NULL DEFAULT 0 CHECK(closed IN (0,1)),
 UNIQUE(spend_id,person_id),
 FOREIGN KEY(spend_id) REFERENCES spend_entry(id)
);
CREATE TABLE ledger_settlement_match (
 transfer_id INTEGER PRIMARY KEY REFERENCES spend_entry(id),
 share_id INTEGER REFERENCES ledger_share(id),
 state TEXT NOT NULL CHECK(state IN ('ask','matched','confirmed','unrelated')),
 credited INTEGER NOT NULL DEFAULT 0 CHECK(credited >= 0),
 previous_closed INTEGER NOT NULL DEFAULT 0 CHECK(previous_closed IN (0,1)),
 shown INTEGER NOT NULL DEFAULT 0 CHECK(shown IN (0,1))
);
CREATE TABLE ledger_capture_notice (
 spend_id INTEGER PRIMARY KEY REFERENCES spend_entry(id),
 immediate_shown INTEGER NOT NULL DEFAULT 0 CHECK(immediate_shown IN (0,1))
);
CREATE VIEW v_ledger_share AS
 SELECT h.*, p.name AS person, p.display_name, s.date, b.title AS appointment,
 CASE WHEN h.closed=1 THEN 0 ELSE h.amount-h.received END AS remaining
 FROM ledger_share h JOIN persons p ON p.id=h.person_id
 JOIN spend_entry s ON s.id=h.spend_id JOIN ledger_split l ON l.spend_id=s.id
 JOIN block b ON b.id=l.appointment_id;
CREATE VIEW v_ledger_reimbursement AS
 SELECT spend_id, SUM(received) AS amount FROM ledger_share GROUP BY spend_id;
CREATE TRIGGER trg_ledger_split_spend_gone AFTER DELETE ON spend_entry BEGIN
 UPDATE ledger_share SET received=received-COALESCE((SELECT credited FROM ledger_settlement_match WHERE transfer_id=OLD.id),0),
 closed=0 WHERE id=(SELECT share_id FROM ledger_settlement_match WHERE transfer_id=OLD.id AND credited>0);
 DELETE FROM ledger_settlement_match WHERE transfer_id=OLD.id OR share_id IN (SELECT id FROM ledger_share WHERE spend_id=OLD.id);
 DELETE FROM ledger_share WHERE spend_id=OLD.id;
 DELETE FROM ledger_split WHERE spend_id=OLD.id;
 DELETE FROM ledger_capture_notice WHERE spend_id=OLD.id;
END;
PRAGMA user_version=86;
COMMIT;
