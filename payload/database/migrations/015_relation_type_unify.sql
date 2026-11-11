-- 015_relation_type_unify.sql  (target user_version 15)
-- reversible: no
-- Source: Ag 014_relation_type_unify (renumbered; file-number == target uv).
-- persons.relation ko -> en data UPDATE. v14 -> v15. Pure data UPDATE;
-- idempotent (re-running finds no ko rows). No-op on a fresh mint (0 persons).
-- reversible:no -- the ko originals are not preserved once rewritten to en.

UPDATE persons SET relation='close_friend'
  WHERE relation='친구' AND intimacy_id IN (3,4);
UPDATE persons SET relation='friend'
  WHERE relation='친구' AND intimacy_id IN (5,6);
UPDATE persons SET relation='acquaintance'
  WHERE relation='친구' AND intimacy_id=7;
UPDATE persons SET relation='friend'
  WHERE relation='친구' AND intimacy_id IS NULL;
UPDATE persons SET relation='business'
  WHERE relation='비즈니스';
UPDATE persons SET relation='partner'
  WHERE relation='연인';
UPDATE persons SET relation='family'
  WHERE relation='가족&친척';

PRAGMA user_version = 15;
