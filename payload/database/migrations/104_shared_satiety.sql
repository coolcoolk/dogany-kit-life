-- reversible: restore the pre-104 DB snapshot; source JSON files stay untouched.
-- The install hook or first satiety use imports every crew cache atomically.
-- Until its receipts commit, the reader overlays untouched legacy answers.
-- Explicit offline migration can still apply schema + import in one transaction.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_104(v);
CREATE TEMP TRIGGER _guard_104_refuse BEFORE INSERT ON _guard_104 BEGIN
 SELECT RAISE(FAIL, 'migration 104 requires user_version 103');
END;
INSERT INTO _guard_104 SELECT user_version FROM pragma_user_version WHERE user_version<>103;
DROP TABLE temp._guard_104;
BEGIN IMMEDIATE;
-- The owner is one member across agents; agent names are provenance only.
CREATE TABLE satiety_answer (
 member_id TEXT NOT NULL CHECK(length(trim(member_id)) > 0),
 occasion TEXT NOT NULL,
 meal_date TEXT NOT NULL,
 slot TEXT NOT NULL,
 food_ids TEXT NOT NULL CHECK(json_valid(food_ids) AND json_type(food_ids)='array'),
 grams REAL CHECK(grams IS NULL OR grams > 0),
 kcal REAL CHECK(kcal IS NULL OR kcal >= 0),
 level TEXT CHECK(level IS NULL OR level IN
  ('snack','too_little','slightly_light','just_right','slightly_heavy','too_full','stuffed')),
 recorded_at TEXT NOT NULL,
 source_agent TEXT NOT NULL,
 state TEXT NOT NULL CHECK(json_valid(state) AND json_type(state)='object'),
 PRIMARY KEY(member_id, occasion)
);
PRAGMA user_version = 104;
COMMIT;
