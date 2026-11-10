-- reversible: restore the pre-098 snapshot. No occurrence-time backfill.
-- Report identity and occurrence time are separate; unknown time remains NULL.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_098(v);
CREATE TEMP TRIGGER _guard_098_refuse BEFORE INSERT ON _guard_098 BEGIN
 SELECT RAISE(FAIL, 'migration 098 requires user_version 97');
END;
INSERT INTO _guard_098 SELECT user_version FROM pragma_user_version WHERE user_version<>97;
DROP TABLE temp._guard_098;
BEGIN IMMEDIATE;
ALTER TABLE event ADD COLUMN occurred_on TEXT;
ALTER TABLE event ADD COLUMN occurred_at TEXT;
CREATE TABLE experience_capture_key (
 capture_key TEXT PRIMARY KEY,
 event_id INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE
);
CREATE INDEX ix_event_occurrence ON event(occurred_on);
CREATE TRIGGER trg_experience_made_dish_delete BEFORE DELETE ON made_dish BEGIN
 DELETE FROM record WHERE source_table='made_dish' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_owned_item_delete BEFORE DELETE ON owned_item BEGIN
 DELETE FROM record WHERE source_table='owned_item' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_owned_stock_delete BEFORE DELETE ON owned_stock BEGIN
 DELETE FROM record WHERE source_table='owned_stock' AND source_id=OLD.owned_id;
END;
CREATE TRIGGER trg_experience_shop_capture_delete BEFORE DELETE ON shop_capture BEGIN
 DELETE FROM record WHERE source_table='shop_capture' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_shop_item_delete BEFORE DELETE ON shop_item BEGIN
 DELETE FROM record WHERE source_table='shop_item' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_ledger_resource_delete BEFORE DELETE ON ledger_resource BEGIN
 DELETE FROM record WHERE source_table='ledger_resource' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_entity_delete BEFORE DELETE ON entity BEGIN
 DELETE FROM record WHERE source_table='entity' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_stock_use_delete BEFORE DELETE ON stock_use BEGIN
 DELETE FROM record WHERE source_table='stock_use' AND source_id=OLD.rowid;
END;
PRAGMA user_version = 98;
COMMIT;
