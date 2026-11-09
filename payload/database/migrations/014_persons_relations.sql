-- 014_persons_relations.sql  (target user_version 14)
-- reversible: yes
-- Source: Ag 013_persons_relations (renumbered; file-number == target uv).
-- persons_relations table + pair index. v13 -> v14. DDL guarded IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS persons_relations (
  id            INTEGER PRIMARY KEY,
  person_a      INTEGER NOT NULL REFERENCES persons(id),
  person_b      INTEGER NOT NULL REFERENCES persons(id),
  relation_type TEXT NOT NULL DEFAULT 'acquaintance',
  note          TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  CHECK (person_a < person_b)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_persons_relations_pair
  ON persons_relations(person_a, person_b, relation_type);

PRAGMA user_version = 14;
