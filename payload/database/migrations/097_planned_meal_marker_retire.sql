-- reversible: restore the pre-097 snapshot; planned meal rows are not touched.
-- 097: the planned meal row IS the open state (owner 2026-10-22). The owner-task
-- "회고: <끼니>(<식품명>) 예정대로 드셨는지 확인" that diet-plan opened was the
-- assistant's own bookkeeping in the owner's list. Settle every still-open one as
-- abandoned (linked by planned_meal_ulid, or the exact legacy title); the planned
-- meal rows stay and are asked about by the evening wrap. The mirror push for the
-- settled rows is queued by `kit_life.sh meal-planned-marker-retire`.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_097(v);
CREATE TEMP TRIGGER _guard_097_refuse BEFORE INSERT ON _guard_097 BEGIN
 SELECT RAISE(FAIL, 'migration 097 requires user_version 96');
END;
INSERT INTO _guard_097 SELECT user_version FROM pragma_user_version WHERE user_version<>96;
DROP TABLE temp._guard_097;
BEGIN IMMEDIATE;
CREATE TEMP TABLE _marker_097 AS
SELECT b.id AS id FROM block b
 WHERE b.kind_token='task' AND b.settled_at IS NULL
   AND (EXISTS (SELECT 1 FROM block_attr a WHERE a.block_id=b.id
                AND a.attr_token='planned_meal_ulid')
        OR (b.title LIKE '회고: %' AND b.title LIKE '% 예정대로 드셨는지 확인')
        OR (b.title LIKE 'Review: %' AND b.title LIKE '% — did you eat it as planned?'));
INSERT OR REPLACE INTO block_attr(block_id, attr_token, value, source)
SELECT m.id, 'version',
       CAST(COALESCE((SELECT CAST(value AS INTEGER) FROM block_attr x
                      WHERE x.block_id=m.id AND x.attr_token='version'), 0) + 1 AS TEXT),
       'user'
  FROM _marker_097 m;
UPDATE block SET settled_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                 settled_outcome='abandoned', settled_by='meal-planned',
                 settled_verdict='unknown'
 WHERE id IN (SELECT id FROM _marker_097);
DROP TABLE temp._marker_097;
PRAGMA user_version = 97;
COMMIT;
