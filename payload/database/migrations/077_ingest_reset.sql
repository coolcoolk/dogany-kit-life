-- 077_ingest_reset.sql (target user_version 77)
-- Additive archive and optional ingest provenance; no existing rows change.
-- WHY: ingest_ref is a dedupe ledger, not proof of which owner fields an
-- adapter changed. Preserve explicit before/after evidence for reversible reset.
-- reversible: YES before first use, DROP these three empty tables and set
-- user_version=76. After use, undo resets and retain provenance before downgrade.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_077(seen_user_version);
CREATE TEMP TRIGGER _guard_077_refuse BEFORE INSERT ON _guard_077 BEGIN
 SELECT RAISE(FAIL, 'migration 077 refused: this database is not at user_version 76');
END;
INSERT INTO _guard_077 SELECT user_version FROM pragma_user_version WHERE user_version <> 76;
DROP TABLE temp._guard_077;
BEGIN IMMEDIATE;
-- Provenance is optional for old adapters. Missing evidence is never a
-- license to delete an owner row. Archive rows preserve SQLite value types.
CREATE TABLE ingest_effect (
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  ext_id TEXT NOT NULL,
  target_table TEXT NOT NULL,
  target_id TEXT NOT NULL,
  effect_json TEXT NOT NULL CHECK (json_valid(effect_json)),
  PRIMARY KEY (source, kind, ext_id),
  FOREIGN KEY (source, kind, ext_id) REFERENCES ingest_ref(source, kind, ext_id)
);
CREATE TABLE archive_ingest_reset_runs (
  reset_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  kinds_json TEXT NOT NULL,
  archived_at TEXT NOT NULL,
  undone_at TEXT
);
CREATE TABLE archive_ingest_reset_rows (
  reset_id TEXT NOT NULL REFERENCES archive_ingest_reset_runs(reset_id),
  ordinal INTEGER NOT NULL,
  table_name TEXT NOT NULL,
  key_json TEXT NOT NULL,
  before_json TEXT NOT NULL,
  after_json TEXT,
  archive_rowid INTEGER NOT NULL,
  PRIMARY KEY (reset_id, ordinal)
);
PRAGMA user_version = 77;
COMMIT;
