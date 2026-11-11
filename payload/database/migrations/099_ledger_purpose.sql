-- reversible: restore the pre-099 snapshot.
-- Owner 2026-10-25: purpose belongs to every instrument, with card inheritance
-- at read time. Vocabulary is data; custom purpose words belong in the memo.
-- Funding sub describes the route (balance/points/etc), not the source kind.
-- Existing is_default already has the one-main-per-wallet unique index: main
-- is its public alias, not a second flag that could disagree with it.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_099(v);
CREATE TEMP TRIGGER _guard_099_refuse BEFORE INSERT ON _guard_099 BEGIN
 SELECT RAISE(FAIL, 'migration 099 requires user_version 98');
END;
INSERT INTO _guard_099 SELECT user_version FROM pragma_user_version WHERE user_version<>98;
DROP TABLE temp._guard_099;
BEGIN IMMEDIATE;
ALTER TABLE pay_method ADD COLUMN purpose_tags TEXT
 CHECK (purpose_tags IS NULL OR CASE WHEN json_valid(purpose_tags)
 THEN json_type(purpose_tags)='array' AND json_array_length(purpose_tags)>0 ELSE 0 END);
ALTER TABLE pay_method ADD COLUMN purpose_memo TEXT;
ALTER TABLE pay_method ADD COLUMN purpose_origin TEXT
 CHECK (purpose_origin IS NULL OR purpose_origin IN ('owner-stated','learned'));
CREATE TABLE pay_purpose_tag (
 layer TEXT NOT NULL CHECK (layer IN ('account','card','wallet')),
 tag TEXT NOT NULL CHECK (length(trim(tag))>0),
 sort_order INTEGER NOT NULL,
 PRIMARY KEY (layer, tag)
);
INSERT INTO pay_purpose_tag(layer,tag,sort_order) VALUES
 ('account','생활비',1),('account','저축·투자',2),('account','사업',3),
 ('card','주력',1),('card','고정비·구독',2),('card','혜택용',3),
 ('wallet','주력',1),('wallet','고정비·구독',2),('wallet','혜택용',3);
ALTER TABLE pay_funding ADD COLUMN source_kind TEXT
 CHECK (source_kind IS NULL OR source_kind IN ('card','account'));
UPDATE pay_funding SET source_kind=(
 SELECT CASE WHEN layer IN ('card','account') THEN layer
 WHEN kind IN ('credit_card','debit_card') THEN 'card'
 WHEN kind IN ('account','securities','savings','cash') THEN 'account' END
 FROM pay_method WHERE id=pay_funding.source_id);
-- Legacy/import writers omit source_kind; keep their inserts valid and typed.
CREATE TRIGGER trg_pay_funding_kind_insert AFTER INSERT ON pay_funding
 WHEN NEW.source_kind IS NULL BEGIN
 UPDATE pay_funding SET source_kind=(
  SELECT CASE WHEN layer IN ('card','account') THEN layer
  WHEN kind IN ('credit_card','debit_card') THEN 'card'
  WHEN kind IN ('account','securities','savings','cash') THEN 'account' END
  FROM pay_method WHERE id=NEW.source_id)
 WHERE wallet_id=NEW.wallet_id AND source_id=NEW.source_id AND sub=NEW.sub;
END;
PRAGMA user_version = 99;
COMMIT;
