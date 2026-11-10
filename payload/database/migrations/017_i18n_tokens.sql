-- 017_i18n_tokens.sql  (target user_version 17)
-- reversible: yes
-- Source: Ag 016_i18n_tokens (src .sql archived; DDL from live Ag DB per
-- DGN-791 O-4; renumbered, file-number == target uv).
-- spend_category.display_name. v16 -> v17.

ALTER TABLE spend_category ADD COLUMN display_name TEXT;

PRAGMA user_version = 17;
