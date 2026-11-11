-- 012_soge_profile.sql  (target user_version 12)
-- reversible: yes
-- Source: Ag 011_soge_profile (renumbered per LS-1 pack lineage; file-number == target uv).
-- persons soge_* profile columns. v11 -> v12.
-- Also folds persons.soge_pool (a live un-versioned ALTER present in the live
-- Ag DB but absent from the frozen canonical v11 schema; guarded/idempotent).
-- Idempotent: ADD COLUMN is only emitted for missing columns by the runner;
-- when applied by raw SQL on a clean v11 baseline every column is new.

ALTER TABLE persons ADD COLUMN soge_pool INTEGER NOT NULL DEFAULT 0;
ALTER TABLE persons ADD COLUMN soge_photo TEXT;
ALTER TABLE persons ADD COLUMN soge_intro TEXT;
ALTER TABLE persons ADD COLUMN soge_want_must TEXT;
ALTER TABLE persons ADD COLUMN soge_want_opt TEXT;

PRAGMA user_version = 12;
