-- reversible: restore the pre-107 backup, or drop the additive photo_note / photo_results_sent tables.
-- DGN-2344: kit-owned photo notes and sent-result receipts for record reads.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_107(v);
CREATE TEMP TRIGGER _guard_107_refuse BEFORE INSERT ON _guard_107 BEGIN
 SELECT RAISE(FAIL, 'migration 107 requires user_version 106');
END;
INSERT INTO _guard_107 SELECT 1 WHERE (SELECT user_version FROM pragma_user_version)<>106;
BEGIN IMMEDIATE;
CREATE TABLE photo_note (
 record_id TEXT PRIMARY KEY CHECK(record_id GLOB 'lk_*'),
 photo_source TEXT NOT NULL, photo_kind TEXT NOT NULL, photo_id TEXT NOT NULL,
 note TEXT NOT NULL, place_id INTEGER REFERENCES place(id), episode_id TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX photo_note_records ON photo_note(updated_at,record_id);
CREATE TABLE photo_results_sent (
 record_id TEXT PRIMARY KEY CHECK(record_id GLOB 'lk_*'),
 sent_at TEXT NOT NULL, query TEXT NOT NULL,
 photo_ids_json TEXT NOT NULL CHECK(json_valid(photo_ids_json) AND json_type(photo_ids_json)='array'),
 results_link TEXT NOT NULL, chat_ref TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX photo_results_sent_records ON photo_results_sent(updated_at,record_id);
PRAGMA user_version=107;
COMMIT;
DROP TABLE temp._guard_107;
