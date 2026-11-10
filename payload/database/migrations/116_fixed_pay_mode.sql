-- Per-field provenance: stream source is not the payment-mode source.
-- Existing explicit values are owner facts. Inference runs on reconciliation.
-- reversible: ALTER TABLE fixed_cost DROP COLUMN pay_mode_source; PRAGMA user_version=115;
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_116(v);
CREATE TEMP TRIGGER _guard_116_refuse BEFORE INSERT ON _guard_116 BEGIN
 SELECT RAISE(FAIL, 'migration 116 requires user_version 115');
END;
INSERT INTO _guard_116 SELECT 1 WHERE (SELECT user_version FROM pragma_user_version)<>115;
BEGIN IMMEDIATE;
ALTER TABLE fixed_cost ADD COLUMN pay_mode_source TEXT
 CHECK (pay_mode_source IS NULL OR pay_mode_source IN ('inferred','owner'));
UPDATE fixed_cost SET pay_mode_source='owner' WHERE pay_mode IS NOT NULL;
PRAGMA user_version=116;
COMMIT;
DROP TABLE _guard_116;
