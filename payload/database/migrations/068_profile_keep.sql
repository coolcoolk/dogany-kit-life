-- 068_profile_keep.sql  (target user_version 68)
--
-- reversible: YES.  Rollback = DROP TRIGGER trg_config_profile_keep_rename;
--   DROP TRIGGER trg_config_profile_keep_delete; DROP TABLE profile_keep;
--   PRAGMA user_version = 67.  Additive: one table, two triggers on config,
--   one seed row.  No existing row is read or rewritten.
--
-- ==========================================================================
-- WHAT.  A config key named in profile_keep is a STABLE PROFILE FACT: no
-- reset may archive (rename to 'archived.<...>.<key>') or delete it.  Its
-- value still changes the normal way (config-set / an InBody that prints
-- height) -- only the key's removal is refused.
--
-- WHY (owner 2026-10-06 21:2x, "why can't the life agent get my height, I gave the health agent my
-- InBody"): height_cm existed only as
-- archived.health-reset-20260927-231520.height_cm=170.  The 2026-09-27
-- health reset (an operator tool outside the kit) renamed it away together
-- with the goal / diet keys, so body-brief, BMR and the goal interview had
-- no height for nine days while the InBody rows (BMI 28.5 at 82.3 kg ->
-- 170 cm) sat right there.  The owner's ruling: a health / goal reset must
-- not archive stable profile facts.  A reset tool lives outside the kit and
-- writes raw SQL, so the rule has to live in the store, not in prose.
--
-- WHY ABORT, NOT IGNORE.  RAISE(IGNORE) would skip the rename silently and
-- the reset would report the key as archived when it is not (its restore
-- would then miss the archived row and fail).  ABORT stops the reset's
-- statement with a message naming the key; the operator drops the key from
-- the reset's list and re-runs.  Loud, nothing half-done.
--
-- WHY A TABLE, NOT A LIST IN THE TRIGGER.  Which facts are stable is data
-- (sex / birth year may join later).  Removing a key on purpose is two
-- deliberate steps: DELETE FROM profile_keep WHERE key=..., then the
-- change.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_068(seen_user_version);
CREATE TEMP TRIGGER _guard_068_refuse BEFORE INSERT ON _guard_068 BEGIN
  SELECT RAISE(FAIL, 'migration 068 refused: this database is not at user_version 67 -- either 068 was already applied (>= 68) or the chain up to 067 has not run (< 67). Nothing changed');
END;
INSERT INTO _guard_068
  SELECT user_version FROM pragma_user_version WHERE user_version <> 67;
DROP TABLE temp._guard_068;

BEGIN IMMEDIATE;

CREATE TABLE profile_keep (
  key TEXT PRIMARY KEY CHECK (TRIM(key) <> '' AND key NOT LIKE 'archived.%'),
  why TEXT NOT NULL CHECK (TRIM(why) <> '')
);
INSERT INTO profile_keep (key, why) VALUES
  ('height_cm', 'stable profile fact (owner 2026-10-06): lost to the 2026-09-27 health reset');

CREATE TRIGGER trg_config_profile_keep_rename
BEFORE UPDATE OF key ON config
WHEN NEW.key <> OLD.key AND OLD.key IN (SELECT key FROM profile_keep)
BEGIN
  SELECT RAISE(ABORT, 'profile_keep: this config key is a stable profile fact (e.g. height_cm) and is never archived or renamed by a reset -- leave it out of the reset; change its value with config-set');
END;

CREATE TRIGGER trg_config_profile_keep_delete
BEFORE DELETE ON config
WHEN OLD.key IN (SELECT key FROM profile_keep)
BEGIN
  SELECT RAISE(ABORT, 'profile_keep: this config key is a stable profile fact (e.g. height_cm) and is never deleted by a reset -- leave it out of the reset; change its value with config-set');
END;

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 68;
COMMIT;
