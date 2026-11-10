-- 013_persons_gender.sql  (target user_version 13)
-- reversible: yes
-- Source: Ag 012_persons_gender (renumbered; file-number == target uv).
-- persons.gender. v12 -> v13.

ALTER TABLE persons ADD COLUMN gender TEXT;

PRAGMA user_version = 13;
