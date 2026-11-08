-- 073_propernoun_registry.sql  (target user_version 73)
--
-- reversible: YES.  Rollback = DROP VIEW v_proper_noun; DROP TRIGGER for the
--   eight trg_* below; DROP TABLE event_group; DROP TABLE noun_ask; DROP TABLE
--   field_kind_gate; DELETE FROM ledger_guard WHERE guard_key =
--   'noun_ask_daily_cap'; ALTER TABLE person_group DROP COLUMN status; ALTER
--   TABLE person_group DROP COLUMN aliases; the writable_schema swap below with
--   the two strings exchanged (legal only while no near_match_refusal row has
--   domain 'group' or 'sub_place'); PRAGMA user_version = 72.  Additive: three
--   new tables, two NULLable/defaulted columns, one view, eight triggers,
--   eleven seed rows (gate) + one ledger_guard row, one CHECK widened.  No
--   config row is written (051 convention).
--
-- ==========================================================================
-- WHAT.  kit_life proper-noun registry (docs/specs/2026-10-08-propernoun-
-- registry.md, owner decisions 2026-10-08: typing ask cap 3/day, two skips ->
-- never ask that name again, group removal = retire "해체", history kept).
--
-- Founding incidents (2026-10-08): the life agent wrote band 피로물든방 (a person_group,
-- display 피방) into a rehearsal ROOM because the word ends in 방, and minted a
-- duplicate place 사무실 because no resolver read place.display_name.  The
-- entities already ARE the dictionary; what was missing is (a) a home for a
-- group on an appointment, (b) a per-booking home for the room, (c) a rule
-- that says which kind of entity may fill which field, (d) memory of the
-- typing question so it is asked once.
--
--   person_group.aliases  what else does the owner call this group?  JSON
--                         array, the place.aliases shape (no new format).
--   person_group.status   does this group still resolve for new writes?
--                         'gone' = retired ("해체"): past appointments keep
--                         their link, new writes refuse it.  Never DELETE a
--                         referenced group (event_group RESTRICT).
--   event_group           which groups attend this block?  N:M junction,
--                         event_persons' twin.  Members are NOT expanded into
--                         event_persons (the kit may not know them).
--   block_attr 'sub_place_id'  which room is THIS booking in?  An attr token
--                         (no DDL); rooms vary per booking, so the room
--                         belongs to the appointment, never to the place.
--                         Two triggers keep it honest: the room must be a
--                         room OF the block's place (E_ROOM_PARENT), and a
--                         block's place cannot move away from its room.
--   noun_ask              the ask-state of a span that is NOT an entity:
--                         offered (asked, n times), auto_declined (two skips,
--                         never asked again), not_a_name (owner said so).
--                         An answered span becomes an entity and its row is
--                         deleted in the same transaction.
--   field_kind_gate       which entity kinds may fill which field?  Seed rows
--                         are constitution data: UPDATE / DELETE refused by
--                         trigger (only a migration changes them).
--   ledger_guard noun_ask_daily_cap = 3 typing asks per local day (owner).
--   near_match_refusal.domain += group, sub_place.
--   v_proper_noun         the dictionary itself: one row per surface form the
--                         owner may say.  Derived, never stored.
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_073(seen_user_version);
CREATE TEMP TRIGGER _guard_073_refuse BEFORE INSERT ON _guard_073 BEGIN
  SELECT RAISE(FAIL, 'migration 073 refused: this database is not at user_version 72 -- either 073 was already applied (>= 73) or the chain up to 072 has not run (< 72). Nothing changed');
END;
INSERT INTO _guard_073
  SELECT user_version FROM pragma_user_version WHERE user_version <> 72;
DROP TABLE temp._guard_073;

BEGIN IMMEDIATE;

ALTER TABLE person_group ADD COLUMN aliases TEXT;
ALTER TABLE person_group ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','gone'));

CREATE TABLE event_group (
    event_id  INTEGER NOT NULL REFERENCES block(id) ON DELETE CASCADE,
    group_id  INTEGER NOT NULL REFERENCES person_group(id) ON DELETE RESTRICT,
    PRIMARY KEY (event_id, group_id)
);
CREATE INDEX idx_event_group_group ON event_group(group_id);

CREATE TABLE noun_ask (
    query_key   TEXT PRIMARY KEY CHECK (query_key <> ''),
    query_text  TEXT NOT NULL,
    state       TEXT NOT NULL CHECK (state IN ('offered','auto_declined','not_a_name')),
    offered_n   INTEGER NOT NULL DEFAULT 0 CHECK (offered_n >= 0),
    field_token TEXT,
    said        TEXT,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE field_kind_gate (
    field_token TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('person','group','place','sub_place','project','method')),
    PRIMARY KEY (field_token, kind)
);
INSERT INTO field_kind_gate(field_token, kind) VALUES
  ('appt.place', 'place'),
  ('presence.place', 'place'),
  ('task.place', 'place'),
  ('appt.room', 'sub_place'),
  ('appt.participants', 'person'),
  ('appt.participants', 'group'),
  ('block.participants', 'person'),
  ('block.participants', 'group'),
  ('appt.origin', 'place'),
  ('travel.dest', 'place'),
  ('block.project', 'project');

CREATE TRIGGER trg_field_kind_gate_frozen_upd
BEFORE UPDATE ON field_kind_gate
BEGIN
  SELECT RAISE(ABORT, 'field_kind_gate is constitution data (which entity kinds may fill which field): it changes only through a kit migration');
END;
CREATE TRIGGER trg_field_kind_gate_frozen_del
BEFORE DELETE ON field_kind_gate
BEGIN
  SELECT RAISE(ABORT, 'field_kind_gate is constitution data (which entity kinds may fill which field): it changes only through a kit migration');
END;

INSERT OR IGNORE INTO ledger_guard(guard_key, scope, value, unit, enforcement,
                                   inviolable, source, updated_at)
  VALUES ('noun_ask_daily_cap', 'global', 3, 'day', 'hold', 0, 'seed',
          '2026-10-08 00:00:00');

-- A room may not carry the name of a group or a person (founding incident:
-- 피로물든방 is a band).  Compared on the spaceless, lower-cased form.
CREATE TRIGGER trg_sub_place_not_other_kind_ins
BEFORE INSERT ON sub_place
WHEN EXISTS (SELECT 1 FROM person_group g
              WHERE lower(replace(trim(g.name),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR lower(replace(trim(COALESCE(g.display_name,'')),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR (json_valid(g.aliases) AND EXISTS (SELECT 1 FROM json_each(g.aliases) a
                       WHERE lower(replace(trim(a.value),' ','')) = lower(replace(trim(NEW.name),' ','')))))
  OR EXISTS (SELECT 1 FROM persons p
              WHERE lower(replace(trim(p.name),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR lower(replace(trim(COALESCE(p.display_name,'')),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR instr(',' || lower(replace(COALESCE(p.aliases,''),' ','')) || ',',
                          ',' || lower(replace(trim(NEW.name),' ','')) || ',') > 0)
BEGIN
  SELECT RAISE(ABORT, 'CROSS_KIND sub_place refused: that name is registered as a group or a person, so it cannot be a room. Link the group/person as a participant (appt-group / block-person) and register the room under its own name');
END;
CREATE TRIGGER trg_sub_place_not_other_kind_upd
BEFORE UPDATE OF name ON sub_place
WHEN EXISTS (SELECT 1 FROM person_group g
              WHERE lower(replace(trim(g.name),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR lower(replace(trim(COALESCE(g.display_name,'')),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR (json_valid(g.aliases) AND EXISTS (SELECT 1 FROM json_each(g.aliases) a
                       WHERE lower(replace(trim(a.value),' ','')) = lower(replace(trim(NEW.name),' ','')))))
  OR EXISTS (SELECT 1 FROM persons p
              WHERE lower(replace(trim(p.name),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR lower(replace(trim(COALESCE(p.display_name,'')),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR instr(',' || lower(replace(COALESCE(p.aliases,''),' ','')) || ',',
                          ',' || lower(replace(trim(NEW.name),' ','')) || ',') > 0)
BEGIN
  SELECT RAISE(ABORT, 'CROSS_KIND sub_place refused: that name is registered as a group or a person, so it cannot be a room. Link the group/person as a participant (appt-group / block-person) and register the room under its own name');
END;

-- The room of a booking is a room OF that booking's place.
CREATE TRIGGER trg_block_attr_sub_place_ins
BEFORE INSERT ON block_attr
WHEN NEW.attr_token = 'sub_place_id'
 AND NOT EXISTS (SELECT 1 FROM sub_place sp JOIN block b ON b.id = NEW.block_id
                  WHERE CAST(sp.id AS TEXT) = NEW.value AND sp.place_id = b.place_id)
BEGIN
  SELECT RAISE(ABORT, 'E_ROOM_PARENT: block_attr sub_place_id must name a sub_place of the block''s own place (set the place first, or register the room under that place)');
END;
CREATE TRIGGER trg_block_attr_sub_place_upd
BEFORE UPDATE ON block_attr
WHEN NEW.attr_token = 'sub_place_id'
 AND NOT EXISTS (SELECT 1 FROM sub_place sp JOIN block b ON b.id = NEW.block_id
                  WHERE CAST(sp.id AS TEXT) = NEW.value AND sp.place_id = b.place_id)
BEGIN
  SELECT RAISE(ABORT, 'E_ROOM_PARENT: block_attr sub_place_id must name a sub_place of the block''s own place (set the place first, or register the room under that place)');
END;
CREATE TRIGGER trg_block_place_room_consistency
BEFORE UPDATE OF place_id ON block
WHEN EXISTS (SELECT 1 FROM block_attr ba JOIN sub_place sp ON CAST(sp.id AS TEXT) = ba.value
              WHERE ba.block_id = NEW.id AND ba.attr_token = 'sub_place_id'
                AND sp.place_id IS NOT NEW.place_id)
BEGIN
  SELECT RAISE(ABORT, 'E_ROOM_PARENT: this block carries a room of its current place; clear the room (appt-upd <id> room=) before moving it to another place');
END;
CREATE TRIGGER trg_sub_place_in_use_del
BEFORE DELETE ON sub_place
WHEN EXISTS (SELECT 1 FROM block_attr ba
              WHERE ba.attr_token = 'sub_place_id' AND ba.value = CAST(OLD.id AS TEXT))
BEGIN
  SELECT RAISE(ABORT, 'IN_USE sub_place: a booking still names this room (sub-place <place> rm <room> --into <other room> reassigns them)');
END;

-- The dictionary: one row per surface form.  kind / ref_id / surface /
-- surface_role (name|display_name|alias|token|composed) / parent_ref / status.
CREATE VIEW v_proper_noun AS
WITH RECURSIVE person_alias(id, rest, alias) AS (
    SELECT id, COALESCE(aliases, '') || ',', NULL FROM persons
  UNION ALL
    SELECT id, substr(rest, instr(rest, ',') + 1),
           trim(substr(rest, 1, instr(rest, ',') - 1))
      FROM person_alias WHERE rest <> ''
)
SELECT 'person' AS kind, id AS ref_id, name AS surface, 'name' AS surface_role,
       NULL AS parent_ref, 'active' AS status FROM persons
UNION ALL
SELECT 'person', id, display_name, 'display_name', NULL, 'active'
  FROM persons WHERE trim(COALESCE(display_name, '')) <> ''
UNION ALL
SELECT 'person', id, alias, 'alias', NULL, 'active'
  FROM person_alias WHERE alias IS NOT NULL AND alias <> ''
UNION ALL
SELECT 'group', id, name, 'name', NULL, status FROM person_group
UNION ALL
SELECT 'group', id, display_name, 'display_name', NULL, status
  FROM person_group WHERE trim(COALESCE(display_name, '')) <> ''
UNION ALL
SELECT 'group', g.id, a.value, 'alias', NULL, g.status
  FROM person_group g, json_each(CASE WHEN json_valid(g.aliases) THEN g.aliases ELSE '[]' END) a
 WHERE a.type = 'text' AND trim(a.value) <> ''
UNION ALL
SELECT 'place', id, name, 'name', NULL, status FROM place
UNION ALL
SELECT 'place', p.id, l.display, 'name', NULL, p.status
  FROM place p JOIN label l ON l.namespace = 'place' AND l.token = p.token AND l.lang = 'ko'
 WHERE l.display IS NOT p.name
UNION ALL
SELECT 'place', id, display_name, 'display_name', NULL, status
  FROM place WHERE trim(COALESCE(display_name, '')) <> ''
UNION ALL
SELECT 'place', p.id, a.value, 'alias', NULL, p.status
  FROM place p, json_each(CASE WHEN json_valid(p.aliases) THEN p.aliases ELSE '[]' END) a
 WHERE a.type = 'text' AND trim(a.value) <> ''
UNION ALL
SELECT 'sub_place', sp.id, sp.name, 'name', sp.place_id, p.status
  FROM sub_place sp JOIN place p ON p.id = sp.place_id
UNION ALL
SELECT 'sub_place', sp.id, sp.token, 'token', sp.place_id, p.status
  FROM sub_place sp JOIN place p ON p.id = sp.place_id
 WHERE sp.token IS NOT sp.name AND sp.token NOT LIKE 'sub\_%' ESCAPE '\'
UNION ALL
SELECT 'sub_place', sp.id, p.name || ' ' || sp.name, 'composed', sp.place_id, p.status
  FROM sub_place sp JOIN place p ON p.id = sp.place_id
UNION ALL
SELECT 'project', id, title, 'name', NULL,
       CASE WHEN status IN ('done','archived','cancelled','dropped') THEN 'gone' ELSE 'active' END
  FROM projects
UNION ALL
SELECT 'method', id, name, 'name', NULL, 'active' FROM pay_method
UNION ALL
SELECT 'method', m.id, a.value, 'alias', NULL, 'active'
  FROM pay_method m, json_each(CASE WHEN json_valid(m.aliases) THEN m.aliases ELSE '[]' END) a
 WHERE a.type = 'text' AND trim(a.value) <> '';

-- Text swap LAST (061's order note).
PRAGMA writable_schema = ON;
UPDATE sqlite_master
   SET sql = replace(sql,
        '(''place'',''person'',''method'',''food'',''exercise'',''item'',''category'')',
        '(''place'',''person'',''method'',''food'',''exercise'',''item'',''category'',''group'',''sub_place'')')
 WHERE type = 'table' AND name = 'near_match_refusal'
   AND instr(sql, '''group'',''sub_place''') = 0;
PRAGMA writable_schema = OFF;

CREATE TEMP TABLE _guard_073_post(n);
CREATE TEMP TRIGGER _guard_073_post_refuse BEFORE INSERT ON _guard_073_post BEGIN
  SELECT RAISE(FAIL, 'migration 073 refused: near_match_refusal does not carry the widened domain CHECK after the swap -- rolled back, nothing changed');
END;
INSERT INTO _guard_073_post
  SELECT 1 FROM sqlite_master
   WHERE type = 'table' AND name = 'near_match_refusal'
     AND instr(sql, '''group'',''sub_place'')') = 0;
DROP TABLE temp._guard_073_post;

-- Lockstep stamp.  MUST be the last statement before COMMIT.
PRAGMA user_version = 73;
COMMIT;
