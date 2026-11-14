-- reversible: additive stock/payment tables; restore the pre-090 backup.
-- No inferred pantry quantities; payment matching never creates money.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_090(v);
CREATE TEMP TRIGGER _guard_090_refuse BEFORE INSERT ON _guard_090 BEGIN
 SELECT RAISE(FAIL, 'migration 090 requires user_version 89');
END;
INSERT INTO _guard_090 SELECT user_version FROM pragma_user_version WHERE user_version <> 89;
DROP TABLE temp._guard_090;
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS shop_capture (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES shop_item(id),
    purpose TEXT NOT NULL CHECK (TRIM(purpose) <> ''),
    event_id INTEGER REFERENCES event(id) ON DELETE SET NULL,
    hunt_id INTEGER REFERENCES shop_hunt(id) ON DELETE SET NULL,
    selected_on TEXT NOT NULL,
    size_label TEXT,
    offer_price INTEGER CHECK (offer_price IS NULL OR offer_price >= 0),
    store TEXT,
    link TEXT,
    status TEXT NOT NULL CHECK (status IN ('picked','in_cart','ordered','bought')),
    owned_id INTEGER REFERENCES owned_item(id) ON DELETE SET NULL,
    note TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE shop_capture_quantity (
 capture_id INTEGER PRIMARY KEY REFERENCES shop_capture(id) ON DELETE CASCADE,
 quantity REAL CHECK(quantity IS NULL OR quantity >= 0),
 unit TEXT NOT NULL DEFAULT 'pack',
 unit_size REAL CHECK(unit_size IS NULL OR unit_size > 0),
 size_unit TEXT
);
CREATE TABLE owned_stock (
 owned_id INTEGER PRIMARY KEY REFERENCES owned_item(id) ON DELETE CASCADE,
 quantity REAL CHECK(quantity IS NULL OR quantity >= 0),
 unit TEXT NOT NULL DEFAULT 'pack',
 unit_size REAL CHECK(unit_size IS NULL OR unit_size > 0),
 size_unit TEXT
);
CREATE TABLE stock_use (
 usage_key TEXT PRIMARY KEY,
 item_id INTEGER NOT NULL REFERENCES shop_item(id),
 quantity REAL,
 unit TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('done','unknown','short')),
 detail TEXT NOT NULL,
 asked INTEGER NOT NULL DEFAULT 0 CHECK(asked IN (0,1))
);
CREATE TABLE shop_payment (
 spend_id INTEGER PRIMARY KEY REFERENCES spend_entry(id) ON DELETE CASCADE,
 state TEXT NOT NULL CHECK(state IN ('matched','ask','ignored')),
 candidates TEXT NOT NULL,
 asked INTEGER NOT NULL DEFAULT 0 CHECK(asked IN (0,1))
);
CREATE TABLE shop_capture_payment (
 capture_id INTEGER PRIMARY KEY REFERENCES shop_capture(id) ON DELETE CASCADE,
 spend_id INTEGER NOT NULL REFERENCES spend_entry(id) ON DELETE CASCADE,
 owned_id INTEGER NOT NULL UNIQUE REFERENCES owned_item(id) ON DELETE CASCADE,
 amount INTEGER NOT NULL CHECK(amount >= 0)
);
CREATE INDEX ix_shop_capture_payment_spend ON shop_capture_payment(spend_id);
CREATE TRIGGER trg_stock_owned_status AFTER UPDATE OF status ON owned_item
 WHEN NEW.status IN ('used_up','returned','gone') BEGIN
 UPDATE owned_stock SET quantity=0 WHERE owned_id=NEW.id;
END;
CREATE TRIGGER trg_stock_owned_delete BEFORE DELETE ON owned_item BEGIN
 DELETE FROM owned_stock WHERE owned_id=OLD.id;
 DELETE FROM shop_capture_payment WHERE owned_id=OLD.id;
END;
CREATE TRIGGER trg_shop_payment_spend_delete BEFORE DELETE ON spend_entry BEGIN
 UPDATE owned_item SET price=(SELECT amount FROM shop_capture_payment WHERE owned_id=owned_item.id),spend_id=NULL
 WHERE id IN (SELECT owned_id FROM shop_capture_payment WHERE spend_id=OLD.id);
 DELETE FROM shop_capture_payment WHERE spend_id=OLD.id;
 DELETE FROM shop_payment WHERE spend_id=OLD.id;
END;
PRAGMA user_version = 90;
COMMIT;
