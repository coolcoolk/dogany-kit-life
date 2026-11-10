-- reversible: restore the pre-108 backup, or drop the additive photo_record_state table.
-- DGN-2344: durable global issuance clock and actual tombstone purge boundary.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_108(v);
CREATE TEMP TRIGGER _guard_108_refuse BEFORE INSERT ON _guard_108 BEGIN
 SELECT RAISE(FAIL, 'migration 108 requires user_version 107');
END;
INSERT INTO _guard_108 SELECT 1 WHERE (SELECT user_version FROM pragma_user_version)<>107;
BEGIN IMMEDIATE;
CREATE TABLE photo_record_state (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 last_global_ms INTEGER NOT NULL CHECK(last_global_ms>=0),
 purged_through TEXT
);
-- Seed from all existing rows, including tombstones, independent of type filters.
INSERT INTO photo_record_state
 SELECT 1, COALESCE(MAX(CAST(strftime('%s',updated_at) AS INTEGER)*1000
                           + CAST(substr(updated_at,21,3) AS INTEGER)),0), NULL
 FROM (SELECT updated_at FROM photo_note UNION ALL SELECT updated_at FROM photo_results_sent);
PRAGMA user_version=108;
COMMIT;
DROP TABLE temp._guard_108;
