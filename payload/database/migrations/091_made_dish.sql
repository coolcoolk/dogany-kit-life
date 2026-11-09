-- reversible: additive made dishes and meal links; restore the pre-091 backup to reverse tables and energy view.
-- Completed cooking output and atomic meal/leftover accounting.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_091(v);
CREATE TEMP TRIGGER _guard_091_refuse BEFORE INSERT ON _guard_091 BEGIN
 SELECT RAISE(FAIL, 'migration 091 requires user_version 90');
END;
INSERT INTO _guard_091 SELECT user_version FROM pragma_user_version WHERE user_version <> 90;
DROP TABLE temp._guard_091;
BEGIN IMMEDIATE;
CREATE TABLE made_dish (
 id INTEGER PRIMARY KEY,
 owning_agent TEXT NOT NULL,
 session_id TEXT NOT NULL,
 recipe_id TEXT NOT NULL,
 name TEXT NOT NULL,
 aliases TEXT NOT NULL DEFAULT '[]',
 completed_at REAL NOT NULL,
 ingredients TEXT NOT NULL,
 nutrition TEXT NOT NULL,
 missing TEXT NOT NULL DEFAULT '[]',
 estimated INTEGER NOT NULL CHECK(estimated IN (0,1)),
 yield_quantity REAL NOT NULL CHECK(yield_quantity > 0),
 yield_unit TEXT NOT NULL,
 remaining REAL NOT NULL DEFAULT 1 CHECK(remaining >= 0 AND remaining <= 1),
 UNIQUE(owning_agent,session_id,recipe_id)
);
CREATE INDEX ix_made_dish_recall ON made_dish(owning_agent,completed_at);
CREATE TABLE made_dish_meal (
 block_id INTEGER PRIMARY KEY REFERENCES block(id) ON DELETE CASCADE,
 dish_id INTEGER NOT NULL REFERENCES made_dish(id),
 fraction REAL NOT NULL CHECK(fraction > 0 AND fraction <= 1),
 request_key TEXT NOT NULL,
 UNIQUE(dish_id,request_key)
);
CREATE TRIGGER trg_made_dish_meal_restore AFTER DELETE ON made_dish_meal BEGIN
 UPDATE made_dish SET remaining=MIN(1,remaining+OLD.fraction) WHERE id=OLD.dish_id;
END;
DROP VIEW v_daily_energy;
CREATE VIEW v_daily_energy AS
SELECT
  d.date,
  COALESCE(m.intake_kcal, 0)  AS intake_kcal,
  COALESCE(m.protein_g,  0)   AS protein_g,
  COALESCE(m.carb_g,     0)   AS carb_g,
  COALESCE(m.fat_g,      0)   AS fat_g,
  COALESCE(w.burn_kcal,  0)   AS burn_kcal,
  COALESCE(w.workout_min,0)   AS workout_min
FROM
  (SELECT date FROM block WHERE settled_by = 'meal-record'
   UNION SELECT date FROM workouts) d
  LEFT JOIN (
    SELECT b.date AS date,
           SUM(COALESCE((SELECT value FROM measure WHERE block_id=b.id AND metric='kcal_intake' AND source='module:made-dish' LIMIT 1), ROUND(
               COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'protein' LIMIT 1),0)*4
             + COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'fat' LIMIT 1),0)*9
             + (COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'carb' LIMIT 1),0)
                - COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'fiber' LIMIT 1),0))*4
             + COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'alcohol' LIMIT 1),0)*7
           , 2))) AS intake_kcal,
           SUM(COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'protein' LIMIT 1),0)) AS protein_g,
           SUM(COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'carb' LIMIT 1),0))    AS carb_g,
           SUM(COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'fat' LIMIT 1),0))     AS fat_g
    FROM block b WHERE b.settled_by = 'meal-record' GROUP BY b.date
  ) m ON m.date = d.date
  LEFT JOIN (
    SELECT date, SUM(kcal) burn_kcal, SUM(minutes) workout_min
    FROM workouts GROUP BY date
  ) w ON w.date = d.date;
PRAGMA user_version = 91;
COMMIT;
