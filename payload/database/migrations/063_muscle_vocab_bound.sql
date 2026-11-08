-- 063_muscle_vocab_bound.sql  (target user_version 63)
--
-- reversible: YES, losslessly.  Triggers + one view, no table, no column, no
--   row.  Rollback = DROP the eight triggers and v_muscle_vocab_orphan;
--   PRAGMA user_version = 62.
--
-- ==========================================================================
-- WHAT.  Ticket 2026-08-22 warg taxonomy wave-2, R2: the muscle vocabulary is
-- data since 030 (muscle_node), but nothing binds the tables that USE it.
-- exercise_muscle_map.muscle / exercise_subregion_map.subregion join
-- muscle_node.node by bare string equality in Python, with no FK and no
-- CHECK, so the three vocabularies can drift apart again and the failure is
-- silent (a map row naming a node that does not exist just drops out of
-- every muscle aggregate).
--
-- THE BINDING.  Triggers, on every connection:
--   exercise_muscle_map.muscle, exercise_subregion_map.subregion,
--   workout_types.muscle_node  -- INSERT and UPDATE must name a muscle_node.
--   muscle_node -- a node still named by any of the three cannot be DELETEd
--   or renamed (UPDATE OF node).  Retire the users first; that is what an FK
--   means, and it is the direction drift actually came from.
--
-- WHY TRIGGERS, NOT REFERENCES.  (1) foreign_keys is per connection: the
-- pack's seed and readers open plain sqlite3 connections with it off, so a
-- declared FK binds only kit_life's own writes -- the ones least likely to
-- drift.  (2) Adding REFERENCES to an existing column needs the 12-step
-- rebuild (or a writable_schema edit SQLite does not list as safe for an ADD),
-- for two tables every muscle reader joins.  workout_types.muscle_node already
-- declares REFERENCES (056); its triggers here make that declaration hold on
-- the connections where it did not.
--
-- THE ONE EXEMPTION -- an EMPTY vocabulary.  While muscle_node has no row at
-- all, the map triggers do not fire.  A fresh kit mint ships muscle_node
-- empty (taxonomy is pack product knowledge, schema.sql SEED ROWS MOVED OUT),
-- and the health pack's seed (health_taxonomy_seed.sql, pinned pack 3a343c2)
-- inserts its first 53 + 136 map rows BEFORE its 47 muscle_node rows.  With
-- no exemption that seed aborts on every fresh install; with RAISE(IGNORE)
-- instead it would silently drop 189 rows.  There is no vocabulary yet to be
-- bound to, so nothing is being contradicted.  The moment the first node
-- exists, every later write is checked.  Rows written in that window are not
-- hidden either: v_muscle_vocab_orphan lists every map row whose name is not
-- a node, whenever it was written.  The pack drops the need for the
-- exemption by seeding muscle_node first (pack delta, CHANGELOG).
--
-- EXISTING ROWS.  Not rewritten and not refused: a DB that already carries an
-- orphan still migrates, and the orphan is listed by the view (the live crew
-- DB measured 0 / 0 / 0 on 2026-09-30).  A migration that refused over a data
-- question would block every later migration on that install.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_063(seen_user_version);
CREATE TEMP TRIGGER _guard_063_refuse BEFORE INSERT ON _guard_063 BEGIN
  SELECT RAISE(FAIL, 'migration 063 refused: this database is not at user_version 62 -- either 063 was already applied (>= 63) or the chain up to 062 has not run (< 62). Nothing changed');
END;
INSERT INTO _guard_063
  SELECT user_version FROM pragma_user_version WHERE user_version <> 62;
DROP TABLE temp._guard_063;

BEGIN IMMEDIATE;

CREATE TRIGGER trg_exercise_muscle_map_node
BEFORE INSERT ON exercise_muscle_map
WHEN EXISTS (SELECT 1 FROM muscle_node)
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.muscle)
BEGIN
  SELECT RAISE(ABORT, 'exercise_muscle_map refused: muscle is not a muscle_node -- use the taxonomy''s node key (kit_life.sh muscle-vocab)');
END;
CREATE TRIGGER trg_exercise_muscle_map_node_upd
BEFORE UPDATE OF muscle ON exercise_muscle_map
WHEN EXISTS (SELECT 1 FROM muscle_node)
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.muscle)
BEGIN
  SELECT RAISE(ABORT, 'exercise_muscle_map refused: muscle is not a muscle_node -- use the taxonomy''s node key (kit_life.sh muscle-vocab)');
END;
CREATE TRIGGER trg_exercise_subregion_map_node
BEFORE INSERT ON exercise_subregion_map
WHEN EXISTS (SELECT 1 FROM muscle_node)
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.subregion)
BEGIN
  SELECT RAISE(ABORT, 'exercise_subregion_map refused: subregion is not a muscle_node -- use the taxonomy''s node key (kit_life.sh muscle-vocab)');
END;
CREATE TRIGGER trg_exercise_subregion_map_node_upd
BEFORE UPDATE OF subregion ON exercise_subregion_map
WHEN EXISTS (SELECT 1 FROM muscle_node)
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.subregion)
BEGIN
  SELECT RAISE(ABORT, 'exercise_subregion_map refused: subregion is not a muscle_node -- use the taxonomy''s node key (kit_life.sh muscle-vocab)');
END;
CREATE TRIGGER trg_workout_types_muscle_node
BEFORE INSERT ON workout_types
WHEN NEW.muscle_node IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.muscle_node)
BEGIN
  SELECT RAISE(ABORT, 'workout_types refused: muscle_node is not a muscle_node');
END;
CREATE TRIGGER trg_workout_types_muscle_node_upd
BEFORE UPDATE OF muscle_node ON workout_types
WHEN NEW.muscle_node IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.muscle_node)
BEGIN
  SELECT RAISE(ABORT, 'workout_types refused: muscle_node is not a muscle_node');
END;
CREATE TRIGGER trg_muscle_node_in_use_del
BEFORE DELETE ON muscle_node
WHEN EXISTS (SELECT 1 FROM exercise_muscle_map WHERE muscle = OLD.node)
  OR EXISTS (SELECT 1 FROM exercise_subregion_map WHERE subregion = OLD.node)
  OR EXISTS (SELECT 1 FROM workout_types WHERE muscle_node = OLD.node)
BEGIN
  SELECT RAISE(ABORT, 'muscle_node refused: the node is still named by exercise_muscle_map / exercise_subregion_map / workout_types -- move those rows first');
END;
CREATE TRIGGER trg_muscle_node_in_use_rename
BEFORE UPDATE OF node ON muscle_node
WHEN NEW.node IS NOT OLD.node
 AND (EXISTS (SELECT 1 FROM exercise_muscle_map WHERE muscle = OLD.node)
  OR EXISTS (SELECT 1 FROM exercise_subregion_map WHERE subregion = OLD.node)
  OR EXISTS (SELECT 1 FROM workout_types WHERE muscle_node = OLD.node))
BEGIN
  SELECT RAISE(ABORT, 'muscle_node refused: the node is still named by exercise_muscle_map / exercise_subregion_map / workout_types -- move those rows first');
END;

-- Every row whose muscle name is not a node, whenever it was written (the
-- empty-vocabulary window, a pre-063 row).  Empty = the vocabularies agree.
CREATE VIEW v_muscle_vocab_orphan AS
SELECT 'exercise_muscle_map' AS table_name, exercise_name AS row_key,
       muscle AS name
  FROM exercise_muscle_map
 WHERE muscle NOT IN (SELECT node FROM muscle_node)
UNION ALL
SELECT 'exercise_subregion_map', exercise_name, subregion
  FROM exercise_subregion_map
 WHERE subregion NOT IN (SELECT node FROM muscle_node)
UNION ALL
SELECT 'workout_types', CAST(id AS TEXT), muscle_node
  FROM workout_types
 WHERE muscle_node IS NOT NULL
   AND muscle_node NOT IN (SELECT node FROM muscle_node);

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 63;
COMMIT;
