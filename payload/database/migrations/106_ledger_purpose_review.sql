-- reversible: restore the pre-106 backup. Existing tags/origins are preserved.
-- User-confirmed per-instrument purpose uses origin=user and one shared set
-- of choices. Keep the old SDK vocabulary valid for existing callers/data.
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_106(v);
CREATE TEMP TRIGGER _guard_106_refuse BEFORE INSERT ON _guard_106 BEGIN
 SELECT RAISE(FAIL, 'migration 106 requires user_version 105 and the purpose origin constraint');
END;
INSERT INTO _guard_106 SELECT 1 WHERE
 (SELECT user_version FROM pragma_user_version)<>105 OR
 NOT EXISTS(SELECT 1 FROM sqlite_master WHERE name='pay_method'
 AND instr(sql, 'purpose_origin IN (''owner-stated'',''learned'')')>0);
BEGIN IMMEDIATE;
INSERT OR IGNORE INTO pay_purpose_tag(layer,tag,sort_order) VALUES
 ('account','월급·고정비',10),('account','생활비',11),('account','저축',12),('account','사업',13),('account','안 따짐',14),
 ('card','월급·고정비',10),('card','생활비',11),('card','저축',12),('card','사업',13),('card','안 따짐',14);
-- Widen only this CHECK without rebuilding a table referenced by money rows.
-- Ordinary DDL bumps the schema cookie before the guarded fixed-text swap.
CREATE INDEX IF NOT EXISTS idx_spend_method_date_purpose ON spend_entry(method,date);
PRAGMA writable_schema=ON;
UPDATE sqlite_master SET sql=replace(sql,
 'purpose_origin IN (''owner-stated'',''learned'')',
 'purpose_origin IN (''owner-stated'',''learned'',''user'')')
 WHERE type='table' AND name='pay_method';
PRAGMA writable_schema=OFF;
INSERT INTO _guard_106 SELECT 1 WHERE NOT EXISTS(
 SELECT 1 FROM sqlite_master WHERE name='pay_method'
 AND instr(sql, 'purpose_origin IN (''owner-stated'',''learned'',''user'')')>0);
PRAGMA user_version=106;
COMMIT;
DROP TABLE temp._guard_106;
