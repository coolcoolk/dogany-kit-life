-- reversible: DROP routine_placement and its four triggers; restore user_version 91.
-- Additive and re-appliable at 92; no existing definitions or instances change.
-- Validated JSON keeps the union, explicit priority, and fallback atomic.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_092(v);
CREATE TEMP TRIGGER _guard_092_refuse BEFORE INSERT ON _guard_092 BEGIN
 SELECT RAISE(FAIL, 'migration 092 requires user_version 91 or 92');
END;
INSERT INTO _guard_092 SELECT user_version FROM pragma_user_version WHERE user_version NOT IN (91,92);
DROP TABLE temp._guard_092;
BEGIN IMMEDIATE;
-- 092: atomic placement union; NULL/absent keeps all legacy placement logic.
CREATE TABLE IF NOT EXISTS routine_placement (
    routine_id INTEGER PRIMARY KEY REFERENCES routine_def(id) ON DELETE CASCADE,
    spec TEXT NOT NULL CHECK (json_valid(spec))
        CHECK (json_type(spec) = 'object')
        CHECK (COALESCE(json_type(spec, '$.alternatives') = 'array', 0))
        CHECK (json_array_length(spec, '$.alternatives') > 0)
);
CREATE TRIGGER IF NOT EXISTS trg_routine_placement_insert
BEFORE INSERT ON routine_placement BEGIN
    SELECT RAISE(ABORT, 'alternatives require a timed anywhere task without legacy ride fields')
    WHERE NOT EXISTS (SELECT 1 FROM routine_def WHERE id=NEW.routine_id
        AND kind='task' AND schedule_kind='timed' AND place_id IS NULL
        AND placement IS NULL AND fallback_at IS NULL
        AND ride_not_before IS NULL AND ride_prefer IS NULL);
END;
CREATE TRIGGER IF NOT EXISTS trg_routine_placement_update
BEFORE UPDATE ON routine_placement BEGIN
    SELECT RAISE(ABORT, 'alternatives require a timed anywhere task without legacy ride fields')
    WHERE NOT EXISTS (SELECT 1 FROM routine_def WHERE id=NEW.routine_id
        AND kind='task' AND schedule_kind='timed' AND place_id IS NULL
        AND placement IS NULL AND fallback_at IS NULL
        AND ride_not_before IS NULL AND ride_prefer IS NULL);
END;
CREATE TRIGGER IF NOT EXISTS trg_routine_placement_parent
BEFORE UPDATE ON routine_def WHEN EXISTS
    (SELECT 1 FROM routine_placement WHERE routine_id=OLD.id) BEGIN
    SELECT RAISE(ABORT, 'clear alternatives before changing placement kind')
    WHERE NEW.kind!='task' OR NEW.schedule_kind!='timed'
        OR NEW.place_id IS NOT NULL OR NEW.placement IS NOT NULL
        OR NEW.fallback_at IS NOT NULL OR NEW.ride_not_before IS NOT NULL
        OR NEW.ride_prefer IS NOT NULL;
END;
CREATE TRIGGER IF NOT EXISTS trg_routine_placement_delete
AFTER DELETE ON routine_def BEGIN
    DELETE FROM routine_placement WHERE routine_id=OLD.id;
END;

PRAGMA user_version = 92;
COMMIT;
