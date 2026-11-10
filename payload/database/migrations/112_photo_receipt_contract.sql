-- reversible: restore the pre-112 backup. Existing receipt content is preserved.
-- The pre-112 results-link adapter delivered only through Telegram (episode.md).
-- query remains TEXT: new writes store the structured JSON object. Wrap every
-- old value as text, including text which happens to look like JSON.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_112(v);
CREATE TEMP TRIGGER _guard_112_refuse BEFORE INSERT ON _guard_112 BEGIN
 SELECT RAISE(FAIL, 'migration 112 requires user_version 111');
END;
INSERT INTO _guard_112 SELECT 1 WHERE (SELECT user_version FROM pragma_user_version)<>111;
BEGIN IMMEDIATE;
ALTER TABLE photo_results_sent ADD COLUMN channel TEXT NOT NULL DEFAULT 'telegram' CHECK(length(channel) BETWEEN 1 AND 128);
ALTER TABLE photo_results_sent ADD COLUMN query_text TEXT CHECK(query_text IS NULL OR length(query_text) BETWEEN 1 AND 200);
ALTER TABLE photo_results_sent ADD COLUMN total_matches INTEGER CHECK(total_matches IS NULL OR (typeof(total_matches)='integer' AND total_matches BETWEEN 0 AND 9007199254740991));
UPDATE photo_results_sent SET query_text=CASE WHEN length(query)>0 THEN substr(query,1,200) END,
                              query=json_object('text',query);
PRAGMA user_version=112;
COMMIT;
DROP TABLE temp._guard_112;
