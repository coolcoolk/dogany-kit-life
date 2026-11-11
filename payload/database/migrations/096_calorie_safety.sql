-- reversible: additive seed + guard triggers; restore the pre-096 snapshot to reverse.
-- 096: repair the missing kit seed and protect it from every reset/wipe.
-- The 2026-10-20 operator ledger wipe included the shared health constraints.
-- No existing row is rewritten. Restore the pre-096 snapshot to reverse.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_096(v);
CREATE TEMP TRIGGER _guard_096_refuse BEFORE INSERT ON _guard_096 BEGIN
 SELECT RAISE(FAIL, 'migration 096 requires user_version 95');
END;
INSERT INTO _guard_096 SELECT user_version FROM pragma_user_version WHERE user_version<>95;
DROP TABLE temp._guard_096;
BEGIN IMMEDIATE;
-- Kit-owned calorie safety policies. Insert only absent keys: never replace
-- an existing policy, its identity, provenance, or owner-adjusted value.
INSERT INTO ledger_constraint
    (ulid, class, key, value, goal_id, inviolable, status, source, recorded_at)
SELECT '01KAR4T9G00000000000000001', 'safety', 'max_deficit_cap', '750',
       NULL, 1, 'active', 'seed', strftime('%Y-%m-%dT%H:%M:%SZ','now')
WHERE NOT EXISTS (SELECT 1 FROM ledger_constraint WHERE key='max_deficit_cap');
INSERT INTO ledger_constraint
    (ulid, class, key, value, goal_id, inviolable, status, source, recorded_at)
SELECT '01KAR4T9G00000000000000002', 'safety', 'calorie_floor',
       '{"floor_female":1200,"floor_male":1500,"floor_undeclared":1500,"bmr_base_factor":0.8,"binding":"weekly_avg_effective"}',
       NULL, 1, 'active', 'seed', strftime('%Y-%m-%dT%H:%M:%SZ','now')
WHERE NOT EXISTS (SELECT 1 FROM ledger_constraint WHERE key='calorie_floor');

-- Every reset/wipe connection is covered, even with foreign_keys=OFF.
-- ABORT rolls back the whole DELETE statement, rather than silently skipping
-- policies and reporting a successful wipe. Callers must roll back their reset.
CREATE TRIGGER trg_constraint_safety_delete BEFORE DELETE ON ledger_constraint
WHEN OLD.class='safety' AND OLD.inviolable=1 BEGIN
 SELECT RAISE(ABORT, 'inviolable safety constraint cannot be deleted');
END;
CREATE TRIGGER trg_constraint_safety_keep
BEFORE UPDATE OF class, key, inviolable, status ON ledger_constraint
WHEN OLD.class='safety' AND OLD.inviolable=1
 AND (NEW.class IS NOT OLD.class OR NEW.key IS NOT OLD.key
      OR NEW.inviolable IS NOT OLD.inviolable OR NEW.status IS NOT OLD.status) BEGIN
 SELECT RAISE(ABORT, 'inviolable safety constraint cannot be demoted or retired');
END;

-- SQLite REPLACE can perform implicit deletes without firing DELETE triggers
-- when recursive_triggers is OFF (the default). Refuse its PK/ULID collision
-- before SQLite reaches that deletion, independent of connection PRAGMAs.
CREATE TRIGGER trg_constraint_safety_replace BEFORE INSERT ON ledger_constraint
WHEN EXISTS (SELECT 1 FROM ledger_constraint
             WHERE class='safety' AND inviolable=1
               AND (id=NEW.id OR ulid=NEW.ulid)) BEGIN
 SELECT RAISE(ABORT, 'inviolable safety constraint cannot be replaced');
END;

PRAGMA user_version = 96;
COMMIT;
