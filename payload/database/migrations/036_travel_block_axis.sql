-- 036_travel_block_axis.sql  (target user_version 36)
--
-- reversible: NO down-migration ships -- this is an axis replacement, not a
--   column add.  What makes it recoverable instead:
--   (1) the four consumed v35 sources are frozen VERBATIM in this same
--       database as _l35_timeblock / _l35_travel_edge / _l35_place / _l35_meals,
--       so the DATA is recoverable by hand even after a successful apply;
--   (2) the runner writes a pre-migration backup (<kit>.db.v35.bak-<ts>) before
--       applying, which is the practical rollback;
--   (3) a FAILED apply needs no rollback at all -- fault injection measured that
--       an aborted run leaves user_version at 35 with a dump byte-identical to
--       the pristine pre-run database (.bail on + single transaction, the stamp
--       is the last statement).
--   Re-application is refused outright (user_version >= 36 guard at the top).
-- The v3.2 travel/block axis lands as an IN-PLACE migration: an existing
-- v35 database upgrades on this machine, with its data, idempotently.  This
-- replaces the authoring-workspace path (schema-v3.2.sql + compat-v3.2.sql +
-- a hand-run transform-v3.2.py), which upgraded exactly one machine and was
-- rejected as a shipping mechanism.  transform-v3.2.py remains the reference
-- for the transfer semantics ported below.
--
-- SCOPE (the CODE is the contract -- reconciled against kit_life.py's actual
-- v3.2 branches, not against the whole authoring schema):
--   MIGRATED  timeblock  -> block + block_attr (+ project_block links,
--             derived_from ulid->id); the old vocabulary stays readable
--             through a read-only compat VIEW named timeblock.
--   MIGRATED  travel_edge + place node minutes -> place_link + measure;
--             compat VIEW named travel_edge.
--   MIGRATED  meals -> record blocks (settled_by='meal-record') + measure +
--             block_delta; the live meal lane already writes/reads this way.
--   REBUILT   place (keeps every v35 column incl. name -- the mirror reads
--             it -- and gains token/status); node minute values do NOT come
--             along: the single home of a travel number is measure.
--   REBUILT   sub_event / event_persons / event_timeblock: same columns,
--             FK repointed timeblock(id) -> block(id) (an FK at a view
--             fails every DML under foreign_keys=ON).
--   KEPT      everything else (persons, workouts, spend_entry, metric_log,
--             event, ledger_*, goal engine, routine_def, module tables...):
--             those lanes have not moved to the new axis yet and their code
--             still reads/writes the old tables.  Migrating them here would
--             break the running product; they move when their code moves.
--
-- PARKS: the four consumed sources are frozen VERBATIM as _l35_timeblock /
-- _l35_travel_edge / _l35_place / _l35_meals (the in-place analogue of the
-- transform's COPY-ONLY discipline).  Values with no seat on the new axis
-- (timeblock.area_id, place aliases/note minutes, meal area_id, X-9 node/edge
-- disagreements, ...) survive there, recoverable, under a prefix no live
-- reader resolves -- preserved without serving stale data.
--
-- PROVENANCE: every migrated number is stamped value_origin='unknown' (D-5:
-- no retroactive inference); the token ships CLOSED (writable=0), is opened
-- for the duration of this file, and closed again at the end -- the schema's
-- own triggers then refuse any new 'unknown' write.
--
-- TIME IDIOM (review F1): every 'now' comparison and every DEFAULT this file
-- emits uses strftime('%Y-%m-%dT%H:%M:%SZ','now').  datetime('now',
-- 'localtime') compared against a stored '...T...Z' string reads expired
-- rows as live until local midnight ('T' 0x54 sorts after ' ' 0x20).
--
-- LOCALTIME CONVERSION: meals.created_at is stored as localtime
-- 'YYYY-MM-DD HH:MM:SS'; the settlement instant on its record block must be
-- canonical UTC-Z, so it converts via strftime(...,'utc') -- the HOST's
-- timezone, where transform-v3.2.py read MIRROR_TZ from config (a config
-- read is not expressible in SQL).  Apply this migration on the instance
-- machine, whose OS timezone is the instance timezone.
--
-- RE-APPLICATION (2026-09-05 review F2/F3): a GUARD below refuses the whole
-- file when user_version >= 36, BEFORE any transaction or write.  A second
-- application used to rebuild place from park + pre-drop copy, which wiped
-- the token of any place added between passes (unfindable, name='') and
-- reverted a status='gone' set between passes to 'active'.  The runner
-- (pack_install.sh kit-migrate) is forward-only and version-pinned, so a
-- second application only ever happens under recovery by hand -- exactly
-- when interim writes exist and the revert would hurt.  A clean rc=0 no-op
-- is not expressible in one SQL file (DDL has no conditional form), so the
-- contract is: second apply STOPS at the guard with an explicit message,
-- exit != 0, database byte-identical.  A partial first application rolls
-- back wholly (single transaction), leaving user_version 35, so the guard
-- never blocks a legitimate retry.
.bail on
.timeout 5000
-- GUARD: refuse re-application.  RAISE needs a trigger body; the INSERT
-- selects a row only when user_version >= 36, so at 35 this whole block is
-- a no-op.  Temp objects die with the connection.  Empirically verified
-- through the production channel (sqlite3 -bail db < file): rc=1 at 36 with
-- the message below and zero changes; transparent at 35.
CREATE TEMP TABLE _guard_036(already_applied);
CREATE TEMP TRIGGER _guard_036_refuse BEFORE INSERT ON _guard_036 BEGIN
  SELECT RAISE(FAIL, 'migration 036 already applied (user_version >= 36) -- re-application refused, nothing changed');
END;
INSERT INTO _guard_036
  SELECT 1 FROM pragma_user_version WHERE user_version >= 36;
DROP TABLE temp._guard_036;
PRAGMA legacy_alter_table=OFF;
BEGIN IMMEDIATE;

-- 1. Freeze the sources.  meals gets a husk first (a second application has
-- nothing named meals; timeblock/travel_edge still exist as the compat views
-- and place always exists, so CREATE..AS SELECT parses on both passes and
-- no-ops when the park already holds the data).
CREATE TABLE IF NOT EXISTS "meals" (
  id         INTEGER PRIMARY KEY,
  date       TEXT NOT NULL,
  meal       TEXT,
  name       TEXT NOT NULL,
  grams      REAL,
  carb       REAL NOT NULL DEFAULT 0,
  protein    REAL NOT NULL DEFAULT 0,
  fat        REAL NOT NULL DEFAULT 0,
  fiber      REAL NOT NULL DEFAULT 0,
  sugar      REAL NOT NULL DEFAULT 0,
  alt_sugar  REAL NOT NULL DEFAULT 0,
  alcohol    REAL NOT NULL DEFAULT 0,
  kcal       REAL GENERATED ALWAYS AS
               (protein*4 + fat*9 + (carb-fiber)*4 + alcohol*7) STORED,
  area_id    INTEGER REFERENCES areas(id),
  notion_id  TEXT,
  owning_agent TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
, aa_protein  REAL NOT NULL DEFAULT 0, sodium_mg   REAL NOT NULL DEFAULT 0, caffeine_mg REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS _l35_timeblock   AS SELECT * FROM timeblock;
CREATE TABLE IF NOT EXISTS _l35_travel_edge AS SELECT * FROM travel_edge;
CREATE TABLE IF NOT EXISTS _l35_place       AS SELECT * FROM place;
CREATE TABLE IF NOT EXISTS _l35_meals       AS SELECT * FROM meals;

-- 2. Retire the old names.  DROP VIEW on a same-named TABLE (first pass) and
-- DROP TABLE on a same-named VIEW (second pass) both hard-error, so the view
-- layer is removed type-agnostically: a view row in sqlite_master owns no
-- btree, so deleting the row leaks nothing, and after it every name below is
-- either a plain table or absent -- DROP TABLE IF EXISTS is then safe on
-- both passes.  Every view this file (re)creates is in the list, so section
-- 10 can use plain CREATE VIEW.
PRAGMA writable_schema=ON;
DELETE FROM sqlite_master WHERE type = 'view' AND name IN
  ('timeblock', 'travel_edge', 'v_daily_energy', 'v_block_span', 'v_block_state', 'v_place_link_measure', 'v_event_resolution');
PRAGMA writable_schema=RESET;
DROP TABLE IF EXISTS timeblock;
DROP TABLE IF EXISTS travel_edge;
DROP TABLE IF EXISTS meals;

-- 3. Rebuild place: v35 columns kept (name/aliases/note/display_name/...),
-- token/status added.  Node minute values are NOT copied -- their migrated
-- home is measure (section 6); the originals stay in _l35_place.  No
-- pre-drop recovery snapshot: the top-of-file guard makes a second pass
-- unreachable, and the old _r36_place merge was exactly the F2/F3 hazard
-- (it recovered interim rows WITHOUT token/status).
DROP TABLE place;
CREATE TABLE place (
    id           INTEGER PRIMARY KEY,
    ulid         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,            -- canonical user-language name (mirror reads this)
    aliases      TEXT,                     -- JSON array, location-text match
    to_min       INTEGER,                  -- DEAD on v3.2 (single home: measure); kept for legacy INSERT shape
    from_min     INTEGER,                  -- DEAD on v3.2
    prep_min     INTEGER,                  -- DEAD on v3.2
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    is_base      INTEGER NOT NULL DEFAULT 0,
    granularity  TEXT,
    display_name TEXT,
    address      TEXT,
    -- v3.2 (036): ASCII stable key; the human string lives in label.
    token        TEXT NOT NULL DEFAULT '',
    -- v3.2 (036): disappearance is status, never DELETE (the dividing axis
    -- has soemyeol, not completion; past blocks / ax_machine.gym_id must
    -- keep resolving).
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','gone')),
    CHECK (to_min IS NULL OR to_min % 15 = 0),
    CHECK (from_min IS NULL OR from_min % 15 = 0),
    CHECK (prep_min IS NULL OR (prep_min >= 0 AND prep_min % 15 = 0))
);
CREATE UNIQUE INDEX idx_place_base
    ON place(is_base) WHERE is_base = 1;
INSERT INTO place(id, ulid, name, aliases, note, created_at, updated_at,
                  is_base, granularity, display_name, address, token, status)
WITH RECURSIVE
  _slug_raw(id, nm, i, n, acc) AS (
    SELECT id, COALESCE(name, ''), 0, length(COALESCE(name, '')), ''
    FROM (SELECT id, name FROM _l35_place)
    UNION ALL
    SELECT id, nm, i+1, n,
      CASE
        WHEN substr(nm, i+1, 1) GLOB '[a-zA-Z0-9]' OR substr(nm, i+1, 1) = '-'
          THEN acc || lower(substr(nm, i+1, 1))
        WHEN acc = '' OR substr(acc, -1, 1) = '_' THEN acc
        ELSE acc || '_'
      END
    FROM _slug_raw WHERE i < n
  ),
  _slugged(id, s) AS (
    SELECT id, rtrim(acc, '_') FROM _slug_raw WHERE i = n
  ),
  toks(id, tok) AS (
    SELECT s1.id,
      CASE WHEN s1.s = '' THEN 'place_' || s1.id
           WHEN EXISTS (SELECT 1 FROM _slugged s2
                         WHERE s2.s = s1.s AND s2.id < s1.id)
             THEN s1.s || '_' || s1.id
           ELSE s1.s END
    FROM _slugged s1
  )
SELECT p.id, p.ulid, p.name, p.aliases, p.note, p.created_at, p.updated_at,
       p.is_base, p.granularity, p.display_name, p.address, t.tok, 'active'
FROM _l35_place p JOIN toks t ON t.id = p.id;

-- 4. Repoint the three FK referrers of the old timeblock table at block(id).
-- Column sets are identical, so the tmp copy round-trips losslessly on both
-- passes (block ids preserve timeblock ids -- see section 7).
CREATE TABLE IF NOT EXISTS _r36_sub_event AS SELECT * FROM sub_event;
DROP TABLE sub_event;
CREATE TABLE sub_event (
    id           INTEGER PRIMARY KEY,
    ulid         TEXT NOT NULL UNIQUE,
    -- same-DB FK is INTEGER id (G9 rule: only cross-agent/host refs must be ulid).
    event_id     INTEGER NOT NULL REFERENCES block(id),
    owning_agent TEXT NOT NULL,
    kind         TEXT,
    ref          TEXT,                            -- L2 domain row ulid (e.g. workout session)
    done         INTEGER NOT NULL DEFAULT 0,
    tombstone    INTEGER NOT NULL DEFAULT 0,      -- delete != complete; invisible to derivation
    created_at   TEXT,
    settled_at   TEXT,
    CHECK (done IN (0,1)),
    CHECK (tombstone IN (0,1))
);
INSERT INTO sub_event SELECT * FROM _r36_sub_event;
DROP TABLE _r36_sub_event;
CREATE INDEX idx_sub_event_parent ON sub_event(event_id, tombstone);
CREATE INDEX idx_sub_event_owner ON sub_event(owning_agent, tombstone);
CREATE TABLE IF NOT EXISTS _r36_event_persons AS SELECT * FROM event_persons;
DROP TABLE event_persons;
CREATE TABLE event_persons (
    event_id  INTEGER NOT NULL REFERENCES block(id)   ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, person_id)
);
INSERT INTO event_persons SELECT * FROM _r36_event_persons;
DROP TABLE _r36_event_persons;
CREATE INDEX idx_event_persons_person
    ON event_persons(person_id);
CREATE TABLE IF NOT EXISTS _r36_event_timeblock AS SELECT * FROM event_timeblock;
DROP TABLE event_timeblock;
-- added_at DEFAULT is UTC-Z per the header idiom (was a verbatim v35
-- localtime carry; NOTHING reads added_at anywhere, so only NEW default
-- stamps change format -- existing localtime values are carried, not
-- rewritten).  Kept OUTSIDE the DDL: in-statement comments persist in
-- sqlite_master and would break fresh-vs-migrated parity.
CREATE TABLE event_timeblock (
  event_id     INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  timeblock_id INTEGER NOT NULL REFERENCES block(id) ON DELETE CASCADE,
  added_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (event_id, timeblock_id)
);
INSERT INTO event_timeblock SELECT * FROM _r36_event_timeblock;
DROP TABLE _r36_event_timeblock;
CREATE INDEX idx_event_timeblock_tb ON event_timeblock(timeblock_id);

-- 5. The v3.2 axis objects and registry seeds.
-- ── v3.2 travel/block axis -- migration 036 (user_version 36) ─────────────
-- The store the v3.2 code paths in kit_life.py actually use (the CODE is the
-- contract): block + block_attr (the execution-axis atom, replacing the
-- timeblock table), place_link + measure (travel knowledge as dated, sourced,
-- expirable measurements), plus the registries those FK into.  Authoring
-- source: the v3.2 authoring schema; objects the code does
-- not touch yet (domain/entity_attr/goal/... axes) deliberately do NOT ship.
-- entity and generator ship as dormant FK targets only (measure.entity_id,
-- block_delta.entity_id, block.generator_id would otherwise point at a
-- missing table and every DML would fail under foreign_keys=ON).
CREATE TABLE IF NOT EXISTS label (
  namespace   TEXT NOT NULL,
  token       TEXT NOT NULL,
  lang        TEXT NOT NULL,
  display     TEXT NOT NULL,
  PRIMARY KEY (namespace, token, lang)
);
CREATE TABLE IF NOT EXISTS resource (
  token         TEXT PRIMARY KEY,
  stock_bearing INTEGER NOT NULL DEFAULT 1 CHECK (stock_bearing IN (0,1)),
  default_unit  TEXT,
  origin        TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user')),
  status        TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS unit (
  token       TEXT PRIMARY KEY,
  dimension   TEXT,
  origin      TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
CREATE TABLE IF NOT EXISTS block_outcome (
  token           TEXT PRIMARY KEY,
  counts_as_flow  INTEGER NOT NULL DEFAULT 0 CHECK (counts_as_flow IN (0,1)),
  origin          TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
CREATE TABLE IF NOT EXISTS block_kind (
  token    TEXT PRIMARY KEY,
  expires  INTEGER NOT NULL DEFAULT 1 CHECK (expires IN (0,1)),
  origin   TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
CREATE TABLE IF NOT EXISTS settle_verdict (
  token  TEXT PRIMARY KEY,
  origin TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
CREATE TABLE IF NOT EXISTS provenance (
  token         TEXT PRIMARY KEY,
  is_estimate   INTEGER NOT NULL DEFAULT 0 CHECK (is_estimate IN (0,1)),
  recomputable  INTEGER NOT NULL DEFAULT 0 CHECK (recomputable IN (0,1)),
  writable      INTEGER NOT NULL DEFAULT 1 CHECK (writable IN (0,1)),
  origin        TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
-- Dormant FK target (no live write path yet; the entity axis has not landed).
CREATE TABLE IF NOT EXISTS entity (
  id          INTEGER PRIMARY KEY,
  ulid        TEXT NOT NULL UNIQUE,
  token       TEXT NOT NULL,
  owner_entity_id INTEGER REFERENCES entity(id) ON DELETE SET NULL,
  fungible    INTEGER NOT NULL DEFAULT 0 CHECK (fungible IN (0,1)),
  origin      TEXT NOT NULL DEFAULT 'user' CHECK (origin IN ('seed','module','user')),
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active','dormant','gone')),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_entity_owner ON entity(owner_entity_id);
-- Dormant FK target (routine_def/fixed_cost remain the live declaration
-- stores until the roller ports; blocks keep their recurrence_id attr).
CREATE TABLE IF NOT EXISTS generator (
  id          INTEGER PRIMARY KEY,
  ulid        TEXT NOT NULL UNIQUE,
  resource    TEXT NOT NULL REFERENCES resource(token),
  title       TEXT NOT NULL,
  recurrence  TEXT NOT NULL,
  spec        TEXT,
  version     INTEGER NOT NULL DEFAULT 1,
  effective_from TEXT, valid_until TEXT,
  supersedes  INTEGER REFERENCES generator(id),
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active','paused','retired')),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
-- The execution-axis storage atom (B1: no stored status -- v_block_state
-- derives it; settlement is four-as-one, enforced by the paired CHECKs).
CREATE TABLE IF NOT EXISTS block (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  source_module TEXT NOT NULL,
  title        TEXT,
  start_at     TEXT, end_at TEXT,
  date         TEXT,
  settled_at      TEXT,
  settled_outcome TEXT REFERENCES block_outcome(token),
  settled_by      TEXT,
  settled_verdict TEXT REFERENCES settle_verdict(token),
  kind_token   TEXT REFERENCES block_kind(token),
  contained_by INTEGER REFERENCES block(id),
  derived_from INTEGER REFERENCES block(id),
  generator_id INTEGER REFERENCES generator(id),
  rec_date     TEXT,
  place_id     INTEGER REFERENCES place(id),
  external_id  TEXT, external_etag TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK ((settled_at IS NULL) = (settled_outcome IS NULL)),
  CHECK ((settled_at IS NULL) = (settled_by      IS NULL)),
  CHECK ((settled_at IS NULL) = (settled_verdict IS NULL)),
  CHECK (start_at IS NULL OR date IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_block_time    ON block(start_at);
CREATE INDEX IF NOT EXISTS ix_block_settled ON block(settled_at);
CREATE INDEX IF NOT EXISTS ix_block_module  ON block(source_module, date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_block_gen_occurrence ON block(generator_id, rec_date)
  WHERE generator_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS block_attr (
  block_id    INTEGER NOT NULL REFERENCES block(id) ON DELETE CASCADE,
  attr_token  TEXT NOT NULL,
  value       TEXT NOT NULL,
  source      TEXT NOT NULL DEFAULT 'user',
  PRIMARY KEY (block_id, attr_token, source)
);
CREATE TABLE IF NOT EXISTS metric_def (
  token        TEXT PRIMARY KEY,
  scope_kind   TEXT NOT NULL CHECK (scope_kind IN ('entity','domain','block','place_link')),
  accrual      TEXT NOT NULL CHECK (accrual IN ('accrued','observed','derived')),
  unit_token   TEXT REFERENCES unit(token),
  source_expr  TEXT,
  requires_entity_token TEXT,
  owner_module TEXT,
  origin       TEXT NOT NULL DEFAULT 'module' CHECK (origin IN ('seed','module','user')),
  status       TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS block_delta (
  id          INTEGER PRIMARY KEY,
  block_id    INTEGER NOT NULL REFERENCES block(id)      ON DELETE CASCADE,
  resource    TEXT    NOT NULL REFERENCES resource(token),
  entity_id   INTEGER          REFERENCES entity(id)     ON DELETE SET NULL,
  component   TEXT    NOT NULL DEFAULT '',
  amount      REAL    NOT NULL,
  unit        TEXT    NOT NULL REFERENCES unit(token),
  value_origin TEXT   NOT NULL REFERENCES provenance(token)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_block_delta ON block_delta(block_id, resource, component,
                                                 COALESCE(entity_id, -1));
CREATE INDEX IF NOT EXISTS ix_block_delta_entity ON block_delta(entity_id, resource);
CREATE TRIGGER IF NOT EXISTS trg_delta_time_has_no_entity
BEFORE INSERT ON block_delta
WHEN NEW.entity_id IS NOT NULL
 AND (SELECT stock_bearing FROM resource WHERE token = NEW.resource) = 0
BEGIN
  SELECT RAISE(ABORT, 'stock_bearing=0 resource cannot bear an entity (time is the block)');
END;
CREATE TRIGGER IF NOT EXISTS trg_delta_time_has_no_entity_upd
BEFORE UPDATE OF entity_id, resource ON block_delta
WHEN NEW.entity_id IS NOT NULL
 AND (SELECT stock_bearing FROM resource WHERE token = NEW.resource) = 0
BEGIN
  SELECT RAISE(ABORT, 'stock_bearing=0 resource cannot bear an entity (time is the block)');
END;
CREATE TRIGGER IF NOT EXISTS trg_delta_origin_writable_ins
BEFORE INSERT ON block_delta
WHEN (SELECT writable FROM provenance WHERE token = NEW.value_origin) = 0
BEGIN
  SELECT RAISE(ABORT, 'value_origin token is migration-only; a new write must state a real origin');
END;
CREATE TRIGGER IF NOT EXISTS trg_delta_origin_writable_upd
BEFORE UPDATE OF value_origin ON block_delta
WHEN (SELECT writable FROM provenance WHERE token = NEW.value_origin) = 0
BEGIN
  SELECT RAISE(ABORT, 'value_origin token is migration-only; a new write must state a real origin');
END;
-- Travel knowledge: the ordered pair is the identity; every number about the
-- pair is a measure row (single home; place.to_min/from_min/prep_min are dead).
CREATE TABLE IF NOT EXISTS place_link (
  id            INTEGER PRIMARY KEY,
  from_place_id INTEGER NOT NULL REFERENCES place(id),
  to_place_id   INTEGER NOT NULL REFERENCES place(id),
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (from_place_id != to_place_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_place_link_pair ON place_link(from_place_id, to_place_id);
CREATE TABLE IF NOT EXISTS measure (
  id          INTEGER PRIMARY KEY,
  metric      TEXT NOT NULL REFERENCES metric_def(token),
  value       REAL NOT NULL,
  unit        TEXT REFERENCES unit(token),
  qualifier   TEXT,
  basis       TEXT NOT NULL DEFAULT 'actual' CHECK (basis IN ('actual','planned')),
  block_id    INTEGER REFERENCES block(id)  ON DELETE CASCADE,
  entity_id   INTEGER REFERENCES entity(id) ON DELETE CASCADE,
  place_link_id INTEGER REFERENCES place_link(id) ON DELETE CASCADE,
  seq         INTEGER,
  observed_at TEXT NOT NULL,
  source      TEXT NOT NULL,
  confidence  REAL,
  valid_until TEXT,
  note        TEXT,
  value_origin TEXT NOT NULL REFERENCES provenance(token),
  CHECK (block_id IS NOT NULL OR entity_id IS NOT NULL OR place_link_id IS NOT NULL)
);
CREATE TRIGGER IF NOT EXISTS trg_measure_origin_writable_ins
BEFORE INSERT ON measure
WHEN (SELECT writable FROM provenance WHERE token = NEW.value_origin) = 0
BEGIN
  SELECT RAISE(ABORT, 'value_origin token is migration-only; a new write must state a real origin');
END;
CREATE TRIGGER IF NOT EXISTS trg_measure_origin_writable_upd
BEFORE UPDATE OF value_origin ON measure
WHEN (SELECT writable FROM provenance WHERE token = NEW.value_origin) = 0
BEGIN
  SELECT RAISE(ABORT, 'value_origin token is migration-only; a new write must state a real origin');
END;
CREATE INDEX IF NOT EXISTS ix_measure_metric ON measure(metric, observed_at);
CREATE INDEX IF NOT EXISTS ix_measure_block  ON measure(block_id, metric);
CREATE INDEX IF NOT EXISTS ix_measure_entity ON measure(entity_id, metric, observed_at);
CREATE INDEX IF NOT EXISTS ix_measure_place_link ON measure(place_link_id, metric, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_measure_row ON measure(
  COALESCE(block_id,-1), COALESCE(entity_id,-1), COALESCE(place_link_id,-1),
  metric, COALESCE(seq,-1), basis, observed_at);
-- The achievement-axis link the silent auto-link writes on v3.2
-- (kit_life project auto-link; the compat view reads project_id back from
-- here).  References projects(id) -- the live table on this estate; the
-- authoring `project` table has not landed.
CREATE TABLE IF NOT EXISTS project_block (
  project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  block_id    INTEGER NOT NULL REFERENCES block(id)    ON DELETE CASCADE,
  PRIMARY KEY (project_id, block_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_project_block_1n ON project_block(block_id);

-- v3.2 registry seeds (rows are vocabulary; users may rename/delete -- the
-- machinery reads flags, not tokens).
INSERT OR IGNORE INTO resource(token, stock_bearing, default_unit) VALUES
  ('time',      0, 'min'),
  ('money',     1, 'krw'),
  ('energy',    1, 'kcal'),
  ('nutrition', 1, 'kcal');
INSERT OR IGNORE INTO unit(token, dimension) VALUES
  ('min','time'), ('s','time'), ('krw','currency'), ('kcal','energy'),
  ('kg','mass'), ('g','mass'), ('count','count'), ('pct','ratio'), ('rir','scale');
INSERT OR IGNORE INTO unit(token, dimension, origin) VALUES ('mg','mass','module');
INSERT OR IGNORE INTO block_outcome(token, counts_as_flow) VALUES
  ('done',      1),
  ('abandoned', 0);
INSERT OR IGNORE INTO block_kind(token, expires) VALUES
  ('task',        1),
  ('appointment', 1),
  ('presence',    0),
  ('record',      0);
INSERT OR IGNORE INTO settle_verdict(token) VALUES ('user'), ('machine'), ('unknown');
INSERT OR IGNORE INTO provenance(token, is_estimate, recomputable, writable, origin) VALUES
  ('measured', 0, 0, 1, 'seed'),
  ('declared', 0, 0, 1, 'seed'),
  ('computed', 0, 1, 1, 'seed'),
  ('default',  1, 1, 1, 'seed'),
  ('unknown',  1, 0, 0, 'seed');
INSERT OR IGNORE INTO label(namespace, token, lang, display) VALUES
  ('provenance','measured','ko','측정됨'), ('provenance','declared','ko','직접 입력'),
  ('provenance','computed','ko','계산됨'), ('provenance','default','ko','기본값'),
  ('provenance','unknown','ko','미상'),
  ('resource','time','ko','시간'), ('resource','money','ko','돈'),
  ('resource','energy','ko','에너지'), ('resource','nutrition','ko','영양');
-- Module metric cargo the live write paths FK into (measure.metric NOT NULL
-- REFERENCES metric_def): the travel lane re-registers these defensively on
-- write; the meal lane does not, so they must exist before the first meal.
INSERT OR IGNORE INTO metric_def(token, scope_kind, accrual, unit_token, owner_module, origin) VALUES
  ('travel_min',      'place_link', 'observed', 'min', 'life', 'module'),
  ('travel_prep_min', 'place_link', 'observed', 'min', 'life', 'module'),
  ('carb',          'block', 'accrued', 'g',  'diet', 'module'),
  ('protein',       'block', 'accrued', 'g',  'diet', 'module'),
  ('fat',           'block', 'accrued', 'g',  'diet', 'module'),
  ('fiber',         'block', 'accrued', 'g',  'diet', 'module'),
  ('sugar',         'block', 'accrued', 'g',  'diet', 'module'),
  ('alt_sugar',     'block', 'accrued', 'g',  'diet', 'module'),
  ('alcohol',       'block', 'accrued', 'g',  'diet', 'module'),
  ('aa_protein',    'block', 'accrued', 'g',  'diet', 'module'),
  ('sodium',        'block', 'accrued', 'mg', 'diet', 'module'),
  ('caffeine',      'block', 'accrued', 'mg', 'diet', 'module'),
  ('portion_grams', 'block', 'accrued', 'g',  'diet', 'module');

-- 6. Travel knowledge -> place_link + measure (transform-v3.2.py section 2b).
-- Every migrated number: value_origin='unknown' (D-5), observed_at = the
-- SOURCE row's own updated_at (F-4: real source data, not the migration
-- clock).  The migration-only token opens here and closes in section 9.
UPDATE provenance SET writable = 1 WHERE token = 'unknown';
-- 6a. edge rows migrate 1:1, ids preserved.
INSERT OR IGNORE INTO place_link(id, from_place_id, to_place_id, created_at)
SELECT e.id, e.from_place_id, e.to_place_id, e.created_at
FROM _l35_travel_edge e;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, place_link_id,
                              observed_at, source, value_origin)
SELECT 'travel_min', e.minutes, 'min', 'actual', e.id, e.updated_at,
       'migration', 'unknown'
FROM _l35_travel_edge e;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, place_link_id,
                              observed_at, source, value_origin)
SELECT 'travel_prep_min', e.prep_min, 'min', 'actual', e.id, e.updated_at,
       'migration', 'unknown'
FROM _l35_travel_edge e WHERE e.prep_min IS NOT NULL;
-- 6b. node columns (base-relative to_min/from_min/prep_min): a value whose
-- pair has NO edge mints the pair + measure itself; a value that AGREES with
-- its edge is already carried; a value that DISAGREES stays parked in
-- _l35_place (X-9).  No distinct base -> parked.
-- X-9 divergence rule, stated as the CHOICE it is (2026-09-05 review F4):
-- when node and edge disagree, the EDGE is served and the node value parks.
-- That can keep the STALER declaration on the read surface -- real case:
-- 신논현역, node 30 (updated 08-24) vs edge 60 (updated 08-22), where the
-- parked 30 is the NEWER one.  Chosen anyway because it matches what the
-- v35 resolver already served: the deriver resolved from the edge (DGN-623),
-- so serving the edge changes ZERO derived travel blocks; recency-picking
-- would have changed live derivations during a migration.  Blast radius is
-- the place read surface / rule prefill only; the parked value is
-- recoverable from _l35_place.
INSERT OR IGNORE INTO place_link(from_place_id, to_place_id, created_at)
SELECT bp.id, p.id, p.created_at
FROM _l35_place p, (SELECT id FROM _l35_place WHERE is_base = 1 LIMIT 1) bp
WHERE p.id != bp.id
  AND (p.to_min IS NOT NULL OR p.prep_min IS NOT NULL)
  AND NOT EXISTS (SELECT 1 FROM _l35_travel_edge e
                  WHERE e.from_place_id = bp.id AND e.to_place_id = p.id);
INSERT OR IGNORE INTO place_link(from_place_id, to_place_id, created_at)
SELECT p.id, bp.id, p.created_at
FROM _l35_place p, (SELECT id FROM _l35_place WHERE is_base = 1 LIMIT 1) bp
WHERE p.id != bp.id AND p.from_min IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM _l35_travel_edge e
                  WHERE e.from_place_id = p.id AND e.to_place_id = bp.id);
INSERT OR IGNORE INTO measure(metric, value, unit, basis, place_link_id,
                              observed_at, source, value_origin)
SELECT 'travel_min', p.to_min, 'min', 'actual', pl.id, p.updated_at,
       'migration', 'unknown'
FROM _l35_place p, (SELECT id FROM _l35_place WHERE is_base = 1 LIMIT 1) bp
JOIN place_link pl ON pl.from_place_id = bp.id AND pl.to_place_id = p.id
WHERE p.id != bp.id AND p.to_min IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM _l35_travel_edge e
                  WHERE e.from_place_id = bp.id AND e.to_place_id = p.id);
INSERT OR IGNORE INTO measure(metric, value, unit, basis, place_link_id,
                              observed_at, source, value_origin)
SELECT 'travel_min', p.from_min, 'min', 'actual', pl.id, p.updated_at,
       'migration', 'unknown'
FROM _l35_place p, (SELECT id FROM _l35_place WHERE is_base = 1 LIMIT 1) bp
JOIN place_link pl ON pl.from_place_id = p.id AND pl.to_place_id = bp.id
WHERE p.id != bp.id AND p.from_min IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM _l35_travel_edge e
                  WHERE e.from_place_id = p.id AND e.to_place_id = bp.id);
-- prep rides the outbound pair (021 seated the declared prep default on the
-- base->place edge); it also mints when the edge exists but carries no prep.
INSERT OR IGNORE INTO measure(metric, value, unit, basis, place_link_id,
                              observed_at, source, value_origin)
SELECT 'travel_prep_min', p.prep_min, 'min', 'actual', pl.id, p.updated_at,
       'migration', 'unknown'
FROM _l35_place p, (SELECT id FROM _l35_place WHERE is_base = 1 LIMIT 1) bp
JOIN place_link pl ON pl.from_place_id = bp.id AND pl.to_place_id = p.id
WHERE p.id != bp.id AND p.prep_min IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM _l35_travel_edge e
                  WHERE e.from_place_id = bp.id AND e.to_place_id = p.id
                    AND e.prep_min IS NOT NULL);
-- the human names of migrated places move to the label layer (v3.2 render
-- authority; place.name stays as the mirror's read column).
INSERT OR IGNORE INTO label(namespace, token, lang, display)
SELECT 'place', p.token, 'ko', l.name
FROM place p JOIN _l35_place l ON l.id = p.id
WHERE l.name IS NOT NULL AND TRIM(l.name) <> '';

-- 7. timeblock -> block (transform-v3.2.py section 5a).  ids and ulids are
-- preserved byte-identically (I1/I6: sub_event.event_id, event_persons.
-- event_id, event_timeblock.timeblock_id keep resolving).  The stored status
-- column is deliberately DROPPED -- it was a cache of derive_status() and
-- v_block_state recomputes it (B1).  settled_at is already canonical UTC-Z
-- on v35 (schema CHECK), so no clock conversion happens on this lane.
-- A settled row with no author gets settled_by='unknown-legacy' (the
-- four-as-one CHECK forbids an ownerless settlement; naming beats dropping
-- the instant).  The verdict axis is stamped from the GRILL-E map;
-- the agent writer-role tokens fall through to 'unknown' on purpose (the same token carried
-- both real abandonment and task-archive cleanup; guessing would fabricate
-- the number the axis exists to stop being wrong about).
INSERT OR IGNORE INTO block(id, ulid, source_module, title, start_at, end_at,
    date, kind_token, settled_at, settled_outcome, settled_by,
    settled_verdict, rec_date, place_id, external_id, external_etag,
    created_at)
SELECT t.id, t.ulid, 'life', t.title, t.start_at, t.end_at,
    NULL,
    t.kind,
    t.settled_at,
    CASE WHEN t.settled_at IS NOT NULL THEN t.settled_outcome END,
    CASE WHEN t.settled_at IS NOT NULL
         THEN COALESCE(t.settled_by, 'unknown-legacy') END,
    CASE WHEN t.settled_at IS NOT NULL THEN
      CASE COALESCE(t.settled_by, 'unknown-legacy')
        WHEN 'gcal-owner-delete'       THEN 'user'
        WHEN 'gtasks-owner-delete'     THEN 'user'
        WHEN 'travel-skip'             THEN 'user'
        WHEN 'travel-skip-cleared'     THEN 'user'
        WHEN 'travel-regen'            THEN 'machine'
        WHEN 'travel-expire'           THEN 'machine'
        WHEN 'travel-anchor-cancelled' THEN 'machine'
        WHEN 'routine-retire'          THEN 'machine'
        WHEN 'rule-regen'              THEN 'machine'
        WHEN 'roller-lapse'            THEN 'machine'
        WHEN 'presence_supersession'   THEN 'machine'
        WHEN 'atomic-land-settle'      THEN 'machine'
        WHEN 'sweep-summary-done'      THEN 'machine'
        WHEN 'gtasks-inbound'          THEN 'machine'
        -- notion-import -> machine covers 73% of real settlements (2,197 of
        -- 2,988 on the probe DB; 2026-09-05 review F9).  Deliberate: many of
        -- those were user-marked-done in Notion and only CARRIED by the
        -- import, but this column records who SETTLED THE ROW HERE -- the
        -- importer -- and D-5 forbids retroactively inferring the human
        -- behind it (the upstream actor was never recorded).  Consequence,
        -- accepted: if the verdict axis is ever read as "how much did the
        -- user actually decide", the machine bucket is inflated by design.
        WHEN 'notion-import'           THEN 'machine'
        WHEN 'skull-today-normalize'   THEN 'machine'
        WHEN 'dgn258-smoke-cleanup'    THEN 'machine'
        WHEN 'dgn258-e2e-cleanup'      THEN 'machine'
        ELSE 'unknown' END
    END,
    t.rec_date, t.place_id,
    COALESCE(t.gcal_event_id, t.notion_id),
    COALESCE(t.gcal_etag, t.gtask_etag),
    t.created_at
FROM _l35_timeblock t;
-- second pass: derived_from was an anchor ULID; block stores the id FK
-- (the compat view converts back).  An unresolvable anchor stays NULL; the
-- raw ulid survives in the park.
UPDATE block SET derived_from = (
  SELECT a.id FROM block a
  WHERE a.ulid = (SELECT t.derived_from FROM _l35_timeblock t
                  WHERE t.ulid = block.ulid))
WHERE derived_from IS NULL
  AND EXISTS (SELECT 1 FROM _l35_timeblock t
              WHERE t.ulid = block.ulid AND t.derived_from IS NOT NULL);
-- project links move to project_block (the compat view reads them back).
INSERT OR IGNORE INTO project_block(project_id, block_id)
SELECT t.project_id, b.id
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.project_id IS NOT NULL;

-- the folded columns (transform-v3.2.py TB_ATTRS; absent-or-empty writes no
-- row -- an attr row holding '' would be a different claim than no attr).
-- source='user' (not 'migration'): block_attr's PK is (block_id, attr_token,
-- source) and every live writer upserts source='user'; a second source value
-- would leave two rows for one attr and make every LIMIT-1 compat read
-- nondeterministic.  Value provenance stays recoverable from the park.
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'schedule_kind', CAST(t.schedule_kind AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.schedule_kind IS NOT NULL AND TRIM(CAST(t.schedule_kind AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'display_tz', CAST(t.display_tz AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.display_tz IS NOT NULL AND TRIM(CAST(t.display_tz AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'open_ended', CAST(t.open_ended AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.open_ended IS NOT NULL AND TRIM(CAST(t.open_ended AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'slot_exclusive', CAST(t.slot_exclusive AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.slot_exclusive IS NOT NULL AND TRIM(CAST(t.slot_exclusive AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'completion_rule', CAST(t.completion_rule AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.completion_rule IS NOT NULL AND TRIM(CAST(t.completion_rule AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'immovable', CAST(t.immovable AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.immovable IS NOT NULL AND TRIM(CAST(t.immovable AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'priority', CAST(t.priority AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.priority IS NOT NULL AND TRIM(CAST(t.priority AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'seq', CAST(t.seq AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.seq IS NOT NULL AND TRIM(CAST(t.seq AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'location', CAST(t.location AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.location IS NOT NULL AND TRIM(CAST(t.location AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'location_url', CAST(t.location_url AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.location_url IS NOT NULL AND TRIM(CAST(t.location_url AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'purpose', CAST(t.purpose AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.purpose IS NOT NULL AND TRIM(CAST(t.purpose AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'summary', CAST(t.summary AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.summary IS NOT NULL AND TRIM(CAST(t.summary AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'rec_exception', CAST(t.rec_exception AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.rec_exception IS NOT NULL AND TRIM(CAST(t.rec_exception AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'notion_id', CAST(t.notion_id AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.notion_id IS NOT NULL AND TRIM(CAST(t.notion_id AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'gcal_event_id', CAST(t.gcal_event_id AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.gcal_event_id IS NOT NULL AND TRIM(CAST(t.gcal_event_id AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'gtask_id', CAST(t.gtask_id AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.gtask_id IS NOT NULL AND TRIM(CAST(t.gtask_id AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'gcal_etag', CAST(t.gcal_etag AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.gcal_etag IS NOT NULL AND TRIM(CAST(t.gcal_etag AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'gtask_etag', CAST(t.gtask_etag AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.gtask_etag IS NOT NULL AND TRIM(CAST(t.gtask_etag AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'notify_policy', CAST(t.notify_policy AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.notify_policy IS NOT NULL AND TRIM(CAST(t.notify_policy AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'block_class', CAST(t.block_class AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.block_class IS NOT NULL AND TRIM(CAST(t.block_class AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'derived_role', CAST(t.derived_role AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.derived_role IS NOT NULL AND TRIM(CAST(t.derived_role AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'derived_pinned', CAST(t.derived_pinned AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.derived_pinned IS NOT NULL AND TRIM(CAST(t.derived_pinned AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'derived_delta', CAST(t.derived_delta AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.derived_delta IS NOT NULL AND TRIM(CAST(t.derived_delta AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'time_mode', CAST(t.time_mode AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.time_mode IS NOT NULL AND TRIM(CAST(t.time_mode AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'resolver_state', CAST(t.resolver_state AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.resolver_state IS NOT NULL AND TRIM(CAST(t.resolver_state AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'note', CAST(t.note AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.note IS NOT NULL AND TRIM(CAST(t.note AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'version', CAST(t.version AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.version IS NOT NULL AND TRIM(CAST(t.version AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'created_by', CAST(t.created_by AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.created_by IS NOT NULL AND TRIM(CAST(t.created_by AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'is_routine', CAST(t.is_routine AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.is_routine IS NOT NULL AND TRIM(CAST(t.is_routine AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'updated_at', CAST(t.updated_at AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.updated_at IS NOT NULL AND TRIM(CAST(t.updated_at AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'owning_agent', CAST(t.owning_agent AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.owning_agent IS NOT NULL AND TRIM(CAST(t.owning_agent AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'completion_n', CAST(t.completion_n AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.completion_n IS NOT NULL AND TRIM(CAST(t.completion_n AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'earliest', CAST(t.earliest AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.earliest IS NOT NULL AND TRIM(CAST(t.earliest AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'latest', CAST(t.latest AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.latest IS NOT NULL AND TRIM(CAST(t.latest AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'anchor', CAST(t.anchor AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.anchor IS NOT NULL AND TRIM(CAST(t.anchor AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'notify_lead_min', CAST(t.notify_lead_min AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.notify_lead_min IS NOT NULL AND TRIM(CAST(t.notify_lead_min AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'exec_context', CAST(t.exec_context AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.exec_context IS NOT NULL AND TRIM(CAST(t.exec_context AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'placement_rule', CAST(t.placement_rule AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.placement_rule IS NOT NULL AND TRIM(CAST(t.placement_rule AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'placement_fallback', CAST(t.placement_fallback AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.placement_fallback IS NOT NULL AND TRIM(CAST(t.placement_fallback AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'placement_depends_on', CAST(t.placement_depends_on AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.placement_depends_on IS NOT NULL AND TRIM(CAST(t.placement_depends_on AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'attn_demand', CAST(t.attn_demand AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.attn_demand IS NOT NULL AND TRIM(CAST(t.attn_demand AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'body_demand', CAST(t.body_demand AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.body_demand IS NOT NULL AND TRIM(CAST(t.body_demand AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'recurrence_id', CAST(t.recurrence_id AS TEXT), 'user'
FROM _l35_timeblock t JOIN block b ON b.ulid = t.ulid
WHERE t.recurrence_id IS NOT NULL AND TRIM(CAST(t.recurrence_id AS TEXT)) <> '';

-- 8. meals -> record blocks (transform-v3.2.py section 5c; the live meal
-- lane already writes this exact shape).  Synthesized ulids are
-- deterministic 'x-meal-<id>' -- the 'x-' prefix keeps a manufactured
-- identity distinguishable from a calendar one and the compat timeblock
-- view filters them out.  (Deviation from the transform: no sha1 tail --
-- not expressible in SQL -- and no fixed id offsets: block ids
-- auto-assign above the preserved timeblock ids, so no id-space preflight
-- is needed.)  kcal is the park's materialized GENERATED value (net-carb
-- formula preserved; a measured 0.0 stays a measured zero, IS NOT NULL).
WITH conv AS (
  SELECT id,
         CASE WHEN created_at LIKE '%Z' AND instr(created_at, 'T') > 0
              THEN created_at
              ELSE strftime('%Y-%m-%dT%H:%M:%SZ', created_at, 'utc') END AS s_at
  FROM _l35_meals)
INSERT OR IGNORE INTO block(ulid, source_module, title, date, kind_token,
    settled_at, settled_outcome, settled_by, settled_verdict, created_at)
SELECT 'x-meal-' || m.id, 'diet', m.name, m.date, 'record',
    c.s_at,
    CASE WHEN c.s_at IS NOT NULL THEN 'done' END,
    CASE WHEN c.s_at IS NOT NULL THEN 'meal-record' END,
    CASE WHEN c.s_at IS NOT NULL THEN 'machine' END,
    m.created_at
FROM _l35_meals m JOIN conv c ON c.id = m.id;
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'meal_slot', CAST(m.meal AS TEXT), 'user'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.meal IS NOT NULL AND TRIM(CAST(m.meal AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'notion_id', CAST(m.notion_id AS TEXT), 'user'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.notion_id IS NOT NULL AND TRIM(CAST(m.notion_id AS TEXT)) <> '';
INSERT OR IGNORE INTO block_attr(block_id, attr_token, value, source)
SELECT b.id, 'owning_agent', CAST(m.owning_agent AS TEXT), 'user'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.owning_agent IS NOT NULL AND TRIM(CAST(m.owning_agent AS TEXT)) <> '';

INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'carb', m.carb, 'g', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.carb IS NOT NULL;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'protein', m.protein, 'g', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.protein IS NOT NULL;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'fat', m.fat, 'g', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.fat IS NOT NULL;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'fiber', m.fiber, 'g', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.fiber IS NOT NULL;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'sugar', m.sugar, 'g', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.sugar IS NOT NULL;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'alt_sugar', m.alt_sugar, 'g', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.alt_sugar IS NOT NULL;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'alcohol', m.alcohol, 'g', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.alcohol IS NOT NULL;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'aa_protein', m.aa_protein, 'g', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.aa_protein IS NOT NULL;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'sodium', m.sodium_mg, 'mg', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.sodium_mg IS NOT NULL;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'caffeine', m.caffeine_mg, 'mg', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.caffeine_mg IS NOT NULL;
INSERT OR IGNORE INTO measure(metric, value, unit, basis, block_id,
                              observed_at, source, value_origin)
SELECT 'portion_grams', m.grams, 'g', 'actual', b.id, m.date, 'module:diet', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.grams IS NOT NULL;

INSERT OR IGNORE INTO block_delta(block_id, resource, amount, unit, component,
                                  value_origin)
SELECT b.id, 'nutrition', ROUND(m.kcal, 1), 'kcal', 'intake', 'unknown'
FROM _l35_meals m JOIN block b ON b.ulid = 'x-meal-' || m.id
WHERE m.kcal IS NOT NULL;

-- 9. Close the migration-only provenance token (D-5): from here the schema's
-- own triggers refuse any new 'unknown' measure/delta write.
UPDATE provenance SET writable = 0 WHERE token = 'unknown';

-- 10. Read surfaces (derived state + the compat vocabulary).
-- Names freed in section 2 on both passes; plain CREATE VIEW.
CREATE VIEW v_block_span AS
SELECT b.id AS block_id, b.start_at,
       CASE WHEN b.end_at IS NULL THEN '~~~~~~~~~~~~~~~~~~~~'
            WHEN b.end_at = b.start_at
              THEN strftime('%Y-%m-%dT%H:%M:%SZ', b.start_at, '+1 second')
            ELSE b.end_at END AS eff_end
FROM block b
WHERE b.start_at IS NOT NULL;
CREATE VIEW v_block_state AS
SELECT b.id AS block_id,
       CASE
         WHEN b.settled_at IS NOT NULL THEN b.settled_outcome
         WHEN COALESCE(k.expires, 0) = 1
              AND s.eff_end IS NOT NULL AND s.eff_end <> '~~~~~~~~~~~~~~~~~~~~'
              AND strftime('%Y-%m-%dT%H:%M:%SZ','now') >= s.eff_end THEN 'expired'
         ELSE 'open'
       END AS state_token
FROM block b
LEFT JOIN v_block_span s ON s.block_id = b.id
LEFT JOIN block_kind   k ON k.token    = b.kind_token;
CREATE VIEW v_place_link_measure AS
SELECT m.place_link_id, pl.from_place_id, pl.to_place_id,
       m.metric, m.value, m.unit, m.basis, m.value_origin, m.source,
       m.observed_at, m.valid_until
FROM measure m
JOIN place_link pl ON pl.id = m.place_link_id
JOIN place pf ON pf.id = pl.from_place_id AND pf.status = 'active'
JOIN place pt ON pt.id = pl.to_place_id  AND pt.status = 'active'
WHERE (m.valid_until IS NULL
       OR m.valid_until > strftime('%Y-%m-%dT%H:%M:%SZ','now'))
  AND m.id = (SELECT m2.id FROM measure m2
               WHERE m2.place_link_id = m.place_link_id
                 AND m2.metric = m.metric AND m2.basis = m.basis
                 AND (m2.valid_until IS NULL
                      OR m2.valid_until > strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ORDER BY m2.observed_at DESC, m2.id DESC LIMIT 1);
CREATE VIEW timeblock AS
SELECT b.id                AS id,
       b.ulid              AS ulid,
       b.title             AS title,
       b.start_at          AS start_at,
       b.end_at            AS end_at,
       b.kind_token        AS kind,
       s.state_token       AS status,
       b.settled_at        AS settled_at,
       b.settled_outcome   AS settled_outcome,
       b.settled_by        AS settled_by,
       b.place_id          AS place_id,
       b.rec_date          AS rec_date,
       b.created_at        AS created_at,
       (SELECT a.ulid FROM block a WHERE a.id = b.derived_from) AS derived_from,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'schedule_kind' LIMIT 1) AS schedule_kind,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'completion_rule' LIMIT 1) AS completion_rule,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'display_tz' LIMIT 1) AS display_tz,
       CAST((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'slot_exclusive' LIMIT 1) AS INTEGER)
                                       AS slot_exclusive,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'open_ended' LIMIT 1),
              CASE WHEN b.start_at IS NOT NULL AND b.end_at IS NULL
                   THEN 1 ELSE 0 END) AS INTEGER)
                                       AS open_ended,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'location' LIMIT 1) AS location,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'location_url' LIMIT 1) AS location_url,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'purpose' LIMIT 1) AS purpose,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'summary' LIMIT 1) AS summary,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gtask_id' LIMIT 1) AS gtask_id,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'notify_policy' LIMIT 1) AS notify_policy,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'block_class' LIMIT 1) AS block_class,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'derived_role' LIMIT 1) AS derived_role,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'derived_pinned' LIMIT 1), 0) AS INTEGER)
                                       AS derived_pinned,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'derived_delta' LIMIT 1) AS derived_delta,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'time_mode' LIMIT 1) AS time_mode,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'note' LIMIT 1) AS note,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'version' LIMIT 1), 0) AS INTEGER)
                                       AS version,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'updated_at' LIMIT 1) AS updated_at,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'owning_agent' LIMIT 1) AS owning_agent,
       CAST((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'notify_lead_min' LIMIT 1) AS INTEGER)
                                       AS notify_lead_min,
       (SELECT pb.project_id FROM project_block pb WHERE pb.block_id = b.id) AS project_id,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'recurrence_id' LIMIT 1) AS recurrence_id,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'rec_exception' LIMIT 1), 0) AS INTEGER)
                                       AS rec_exception,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'created_by' LIMIT 1) AS created_by,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'is_routine' LIMIT 1), 0) AS INTEGER)
                                       AS is_routine,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gcal_event_id' LIMIT 1) AS gcal_event_id,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gcal_etag' LIMIT 1) AS gcal_etag,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gtask_etag' LIMIT 1) AS gtask_etag,
       NULL                AS area_id   -- UNMAPPED(O-7); old readers may still name it
FROM block b
LEFT JOIN v_block_state s ON s.block_id = b.id
WHERE b.ulid NOT LIKE 'x-%';
CREATE VIEW travel_edge AS
SELECT pl.id            AS id,
       pl.from_place_id AS from_place_id,
       pl.to_place_id   AS to_place_id,
       CAST(tm.value AS INTEGER) AS minutes,
       CAST(pm.value AS INTEGER) AS prep_min,
       pl.created_at    AS created_at,
       COALESCE(MAX(tm.observed_at, pm.observed_at), tm.observed_at) AS updated_at
FROM place_link pl
JOIN v_place_link_measure tm
     ON tm.place_link_id = pl.id AND tm.metric = 'travel_min'
    AND tm.basis = 'actual'
LEFT JOIN v_place_link_measure pm
     ON pm.place_link_id = pl.id AND pm.metric = 'travel_prep_min'
    AND pm.basis = 'actual';
CREATE VIEW v_daily_energy AS
SELECT
  d.date,
  COALESCE(m.intake_kcal, 0)  AS intake_kcal,
  COALESCE(m.protein_g,  0)   AS protein_g,
  COALESCE(m.carb_g,     0)   AS carb_g,
  COALESCE(m.fat_g,      0)   AS fat_g,
  COALESCE(w.burn_kcal,  0)   AS burn_kcal,
  COALESCE(w.workout_min,0)   AS workout_min
FROM
  (SELECT date FROM block WHERE settled_by = 'meal-record'
   UNION SELECT date FROM workouts) d
  LEFT JOIN (
    SELECT b.date AS date,
           SUM(ROUND(
               COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'protein' LIMIT 1),0)*4
             + COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'fat' LIMIT 1),0)*9
             + (COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'carb' LIMIT 1),0)
                - COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'fiber' LIMIT 1),0))*4
             + COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'alcohol' LIMIT 1),0)*7
           , 2)) AS intake_kcal,
           SUM(COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'protein' LIMIT 1),0)) AS protein_g,
           SUM(COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'carb' LIMIT 1),0))    AS carb_g,
           SUM(COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'fat' LIMIT 1),0))     AS fat_g
    FROM block b WHERE b.settled_by = 'meal-record' GROUP BY b.date
  ) m ON m.date = d.date
  LEFT JOIN (
    SELECT date, SUM(kcal) burn_kcal, SUM(minutes) workout_min
    FROM workouts GROUP BY date
  ) w ON w.date = d.date;
CREATE VIEW v_event_resolution AS
SELECT e.id, e.ulid, e.kind, e.time_mode,
  CASE
    WHEN e.settled_outcome = 'abandoned' THEN 'skipped'
    WHEN e.settled_outcome = 'done' THEN
         CASE WHEN e.time_mode = 'anchored' THEN 'happened' ELSE 'done' END
    WHEN e.time_mode = 'anchored'
         AND (CASE WHEN e.end_at IS NULL THEN '~~~~~~~~~~~~~~~~~~~~'
                   WHEN e.end_at = e.start_at THEN
                        strftime('%Y-%m-%dT%H:%M:%SZ', e.start_at, '+1 second')
                   ELSE e.end_at END)
             <= strftime('%Y-%m-%dT%H:%M:%SZ','now')
      THEN 'missed'
    ELSE 'open'
  END AS resolution
FROM timeblock e
WHERE e.kind != 'presence';

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 35 (no half-migrated laundering -- 023 discipline).
PRAGMA user_version = 36;
COMMIT;
