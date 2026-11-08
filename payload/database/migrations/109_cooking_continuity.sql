-- reversible: restore the pre-109 backup, or drop the additive cooking_appliance / cooking_pace tables and the ledger_resource owned-item column + index.
-- Cooking continuity; carrier kitchen-owned-link renumbered after 108.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_109(v);
CREATE TEMP TRIGGER _guard_109_refuse BEFORE INSERT ON _guard_109 BEGIN
 SELECT RAISE(FAIL, 'migration 109 requires user_version 108');
END;
INSERT INTO _guard_109 SELECT user_version FROM pragma_user_version WHERE user_version <> 108;
DROP TABLE temp._guard_109;
BEGIN IMMEDIATE;
CREATE TABLE cooking_appliance (
 owning_agent TEXT NOT NULL,
 appliance_type TEXT NOT NULL CHECK(appliance_type IN ('oven','airfryer','microwave','burner')),
 modes_json TEXT NOT NULL CHECK(json_valid(modes_json) AND json_type(modes_json)='array'),
 model TEXT,
 owner_words TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 updated_at INTEGER NOT NULL,
 PRIMARY KEY(owning_agent,appliance_type)
);
CREATE TABLE cooking_pace (
 owning_agent TEXT NOT NULL,
 recipe_id TEXT NOT NULL,
 step_id TEXT NOT NULL,
 samples INTEGER NOT NULL CHECK(samples>0),
 avg_sec INTEGER NOT NULL CHECK(avg_sec>0),
 updated_at INTEGER NOT NULL,
 PRIMARY KEY(owning_agent,recipe_id,step_id)
);
ALTER TABLE ledger_resource ADD COLUMN owned_item_id INTEGER REFERENCES owned_item(id);
CREATE INDEX ix_ledger_resource_owned_item ON ledger_resource(owned_item_id) WHERE owned_item_id IS NOT NULL;
PRAGMA user_version=109;

COMMIT;
