-- reversible: additive allocation table/triggers; restore pre-093 backup to reverse.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_093(v);
CREATE TEMP TRIGGER _guard_093_refuse BEFORE INSERT ON _guard_093 BEGIN
 SELECT RAISE(FAIL, 'migration 093 requires user_version 92');
END;
INSERT INTO _guard_093 SELECT user_version FROM pragma_user_version WHERE user_version<>92;
DROP TABLE temp._guard_093;
BEGIN IMMEDIATE;
-- Receipt allocations are economic shares. For an owner payment, amount is
-- the friend's share; for a friend payment, amount is the owner's share.
-- The one cash transfer equals SUM(owner-paid shares)-SUM(friend-paid shares).
CREATE TABLE ledger_receipt_allocation (
 receipt_id INTEGER NOT NULL REFERENCES spend_entry(id),
 expense_id INTEGER NOT NULL REFERENCES spend_entry(id),
 amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>0),
 payer TEXT NOT NULL DEFAULT 'owner' CHECK(payer IN ('owner','friend')),
 share_id INTEGER REFERENCES ledger_share(id),
 previous_closed INTEGER NOT NULL DEFAULT 0 CHECK(previous_closed IN (0,1)),
 PRIMARY KEY(receipt_id,expense_id),
 CHECK(receipt_id<>expense_id),
 CHECK(payer='owner' OR share_id IS NULL)
);
CREATE INDEX idx_ledger_receipt_expense ON ledger_receipt_allocation(expense_id);
CREATE INDEX idx_ledger_receipt_share ON ledger_receipt_allocation(share_id);
CREATE TRIGGER trg_receipt_allocation_insert BEFORE INSERT ON ledger_receipt_allocation BEGIN
 SELECT RAISE(ABORT,'allocation receipt missing') WHERE NOT EXISTS
  (SELECT 1 FROM spend_entry WHERE id=NEW.receipt_id AND amount>0 AND scope<>'business'
   AND kind='transfer' AND class='own_transfer');
 SELECT RAISE(ABORT,'allocation expense invalid') WHERE NOT EXISTS
  (SELECT 1 FROM spend_entry WHERE id=NEW.expense_id AND amount>=NEW.amount
   AND kind='expense' AND COALESCE(class,'spend')='spend' AND scope<>'business');
 SELECT RAISE(ABORT,'allocation share invalid') WHERE NEW.share_id IS NOT NULL AND NOT EXISTS
  (SELECT 1 FROM ledger_share WHERE id=NEW.share_id AND spend_id=NEW.expense_id);
END;
CREATE TRIGGER trg_receipt_allocation_update BEFORE UPDATE ON ledger_receipt_allocation BEGIN
 SELECT RAISE(ABORT,'replace receipt allocations atomically');
END;
CREATE TRIGGER trg_receipt_allocation_release AFTER DELETE ON ledger_receipt_allocation BEGIN
 UPDATE ledger_share SET received=received-OLD.amount,closed=OLD.previous_closed WHERE id=OLD.share_id;
END;
CREATE TRIGGER trg_receipt_evidence_delete BEFORE DELETE ON spend_entry WHEN 1 BEGIN
 UPDATE ledger_preference SET detail=json_set(detail,'$.allocation_revision',
  COALESCE(json_extract(detail,'$.allocation_revision'),0)+1,'$.allocation_status','released',
  '$.unallocated',COALESCE((SELECT amount FROM spend_entry WHERE id=CAST(item AS INTEGER)),0))
 WHERE topic='ledger_review_item' AND CAST(item AS INTEGER) IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE receipt_id=OLD.id OR expense_id=OLD.id);
 DELETE FROM ledger_receipt_allocation WHERE receipt_id IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE receipt_id=OLD.id OR expense_id=OLD.id);
END;
CREATE TRIGGER trg_receipt_evidence_edit BEFORE UPDATE OF amount,date,name,method,kind,class,scope,pair_id ON spend_entry WHEN OLD.amount IS NOT NEW.amount OR OLD.date IS NOT NEW.date OR OLD.name IS NOT NEW.name OR OLD.method IS NOT NEW.method OR OLD.kind IS NOT NEW.kind OR OLD.class IS NOT NEW.class OR OLD.scope IS NOT NEW.scope OR OLD.pair_id IS NOT NEW.pair_id BEGIN
 UPDATE ledger_preference SET detail=json_set(detail,'$.allocation_revision',
  COALESCE(json_extract(detail,'$.allocation_revision'),0)+1,'$.allocation_status','released',
  '$.unallocated',COALESCE((SELECT amount FROM spend_entry WHERE id=CAST(item AS INTEGER)),0))
 WHERE topic='ledger_review_item' AND CAST(item AS INTEGER) IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE receipt_id=OLD.id OR expense_id=OLD.id);
 DELETE FROM ledger_receipt_allocation WHERE receipt_id IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE receipt_id=OLD.id OR expense_id=OLD.id);
END;
CREATE TRIGGER trg_receipt_refund_insert AFTER INSERT ON spend_entry
 WHEN NEW.amount<0 AND NEW.pair_id IS NOT NULL BEGIN
 UPDATE ledger_preference SET detail=json_set(detail,'$.allocation_revision',
  COALESCE(json_extract(detail,'$.allocation_revision'),0)+1,'$.allocation_status','released',
  '$.unallocated',COALESCE((SELECT amount FROM spend_entry WHERE id=CAST(item AS INTEGER)),0))
 WHERE topic='ledger_review_item' AND CAST(item AS INTEGER) IN
  (SELECT a.receipt_id FROM ledger_receipt_allocation a JOIN spend_entry e ON e.id=a.expense_id
   WHERE e.pair_id=NEW.pair_id OR a.receipt_id IN (SELECT id FROM spend_entry WHERE pair_id=NEW.pair_id));
 DELETE FROM ledger_receipt_allocation WHERE receipt_id IN
  (SELECT a.receipt_id FROM ledger_receipt_allocation a JOIN spend_entry e ON e.id=a.expense_id
   WHERE e.pair_id=NEW.pair_id OR a.receipt_id IN (SELECT id FROM spend_entry WHERE pair_id=NEW.pair_id));
END;
CREATE TRIGGER trg_receipt_refund_edit AFTER UPDATE OF amount,pair_id ON spend_entry
 WHEN NEW.amount<0 AND NEW.pair_id IS NOT NULL BEGIN
 UPDATE ledger_preference SET detail=json_set(detail,'$.allocation_revision',
  COALESCE(json_extract(detail,'$.allocation_revision'),0)+1,'$.allocation_status','released',
  '$.unallocated',COALESCE((SELECT amount FROM spend_entry WHERE id=CAST(item AS INTEGER)),0))
 WHERE topic='ledger_review_item' AND CAST(item AS INTEGER) IN
  (SELECT a.receipt_id FROM ledger_receipt_allocation a JOIN spend_entry e ON e.id=a.expense_id
   WHERE e.pair_id=NEW.pair_id OR a.receipt_id IN (SELECT id FROM spend_entry WHERE pair_id=NEW.pair_id));
 DELETE FROM ledger_receipt_allocation WHERE receipt_id IN
  (SELECT a.receipt_id FROM ledger_receipt_allocation a JOIN spend_entry e ON e.id=a.expense_id
   WHERE e.pair_id=NEW.pair_id OR a.receipt_id IN (SELECT id FROM spend_entry WHERE pair_id=NEW.pair_id));
END;
CREATE TRIGGER trg_receipt_share_delete BEFORE DELETE ON ledger_share BEGIN
 UPDATE ledger_preference SET detail=json_set(detail,'$.allocation_revision',
  COALESCE(json_extract(detail,'$.allocation_revision'),0)+1,'$.allocation_status','released')
 WHERE topic='ledger_review_item' AND CAST(item AS INTEGER) IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE share_id=OLD.id);
 DELETE FROM ledger_receipt_allocation WHERE receipt_id IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE share_id=OLD.id);
END;
CREATE TRIGGER trg_receipt_preference_delete BEFORE DELETE ON ledger_preference
 WHEN OLD.topic='ledger_review_item' BEGIN
 DELETE FROM ledger_receipt_allocation WHERE receipt_id=CAST(OLD.item AS INTEGER);
END;
PRAGMA user_version=93;
COMMIT;
